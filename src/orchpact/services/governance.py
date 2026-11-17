"""Explicit authorization and deterministic governance, without fixed departments."""
from ..adapters.database import dumps, loads, now, ident
from ..credits import SCALE, measure, point_micros
from ..kernel.models import GovernancePolicy, ExecutionContext
from ..kernel.contracts import validate, read_json_delivery
from ..kernel.errors import Forbidden, DomainError
from ..adapters.artifacts import encode_files


class Governance:
    ACTIONS = frozenset({'plan', 'amend', 'recruit', 'delegate', 'call', 'publish', 'review', 'ask', 'cancel', 'retry'})

    def __init__(self, service):
        self.s = service

    def grants(self, c, subject, project):
        rows = c.execute('SELECT * FROM mandates WHERE project_id=%s AND subject=%s', (project['id'], subject)).fetchall()
        valid = []
        for row in rows:
            current = row
            seen = set()
            while current:
                if current['id'] in seen or len(seen) >= 5 or current['revoked'] or current['expires'] <= now() or current['project_id'] != project['id']:
                    break
                seen.add(current['id'])
                if not current['parent_id']:
                    valid.append(dict(row))
                    break
                current = c.execute('SELECT * FROM mandates WHERE id=%s', (current['parent_id'],)).fetchone()
        return valid

    def authorize(self, c, ctx, action, project, capability=None):
        if not isinstance(ctx, ExecutionContext):
            raise Forbidden('内核命令必须带显式执行身份')
        if ctx.workspace_id != project['workspace_id'] or (ctx.application_id and ctx.application_id != project['application_id']) or (ctx.project_id and ctx.project_id != project['id']):
            raise Forbidden()
        if ctx.subject_type == 'system' and ctx.project_id == project['id']:
            return None
        if ctx.subject_type == 'user':
            self.s.access.member(c, project['workspace_id'], self.s.actor(ctx))
            if action in {'cancel', 'retry', 'ask'}:
                return None
        elif ctx.subject_type == 'agent':
            self.s.access.member(c, project['workspace_id'], self.s.actor(ctx))
            user = c.execute('SELECT disabled FROM users WHERE id=%s', (project['created_by'],)).fetchone()
            if not user or user['disabled']:
                raise Forbidden('任务发起账号不可用')
        grant = next((g for g in self.grants(c, ctx.subject, project) if action in loads(g['actions']) and (capability is None or capability in loads(g['capabilities']))), None)
        if not grant:
            raise Forbidden('该主体未被授予 ' + action + ' 权限或所需能力')
        return grant

    def grant(self, c, ctx, project, subject, actions, capabilities=(), expires=None, parent=None, budget=None):
        self.authorize(c, ctx, 'delegate', project)
        if not set(actions) <= self.ACTIONS:
            raise ValueError('未知授权动作')
        candidates = [g for g in self.grants(c, ctx.subject, project) if 'delegate' in loads(g['actions'])]
        if parent:
            candidates = [g for g in candidates if g['id'] == parent]
        row = next((g for g in candidates if set(actions) <= set(loads(g['actions'])) and set(capabilities) <= set(loads(g['capabilities']))), None)
        if not row:
            raise Forbidden('转授权不能扩大权限或能力')
        expires = expires if expires is not None else row['expires']
        if expires <= now() or expires > row['expires']:
            raise Forbidden('转授权不能扩大有效期')
        if row['budget'] is not None:
            budget = row['budget'] if budget is None else budget
            if budget > row['budget']:
                raise Forbidden('转授权不能扩大资源额度')
        ancestor, depth = row, 0
        while ancestor['parent_id']:
            depth += 1
            if depth >= 4:
                raise Forbidden('转授权层数已达上限')
            ancestor = c.execute('SELECT * FROM mandates WHERE id=%s', (ancestor['parent_id'],)).fetchone()
        if c.execute('SELECT COUNT(*) FROM mandates WHERE project_id=%s',(project['id'],)).fetchone()[0]>=256:
            raise ValueError('项目授权数量已达上限')
        key = ident()
        c.execute('INSERT INTO mandates VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                  (key, project['workspace_id'], project['id'], subject, ctx.subject, row['id'], dumps(sorted(set(actions))), dumps(sorted(set(capabilities))), budget, expires, 0, now()))
        self.s.db.event(c, project['id'], None, 'mandate.granted', mandate_id=key, subject=subject, issuer=ctx.subject)
        return key

    def initial(self, c, project, subject):
        # Called only by admitted project creation, never from a model proposal.
        key = ident()
        c.execute('INSERT INTO mandates VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                  (key, project['workspace_id'], project['id'], subject, project['created_by'], None,
                   dumps(sorted(self.ACTIONS)), dumps(['text', 'json', 'search', 'image', 'image_reference']),
                   None, now() + 86400 * 30, 0, now()))
        return key

    def read(self, c, scope):
        row = c.execute('SELECT * FROM policies WHERE scope=%s', (scope,)).fetchone()
        return dict(revision=row['revision'], policy=loads(row['policy'])) if row else dict(revision=0, policy=GovernancePolicy().model_dump())

    def save(self, c, scope, revision, value):
        value = GovernancePolicy.model_validate(value).model_dump()
        from ..kernel.contracts import check_schema
        check_schema(value['input_schema'])
        for schema in value['leader_outputs'].values():
            check_schema(schema)
        if self.read(c, scope)['revision'] != revision:
            raise DomainError('revision_conflict', '规则已被修改，请重新加载')
        c.execute('INSERT INTO policies VALUES(%s,%s,%s) ON CONFLICT(scope) DO UPDATE SET revision=excluded.revision,policy=excluded.policy',
                  (scope, revision + 1, dumps(value)))
        self.s.db.event(c, None, None, 'policy.updated', scope=scope, revision=revision + 1)
        return dict(revision=revision + 1, policy=value)

    def snapshot(self, c, project, unit, value):
        scopes = ('platform', 'workspace:' + project['workspace_id'], 'project:' + project['id'], 'unit:' + unit)
        policies = [dict(scope=scope, **self.read(c, scope)) for scope in scopes]
        for row in policies:
            validate(value, row['policy']['input_schema'])
        return policies

    def check_output(self, task, files):
        encoded = dict(encode_files(files))
        for row in loads(task['snapshot']).get('policies', []):
            rule = row['policy']
            if rule['max_output_bytes'] is not None and sum(map(len, encoded.values())) > rule['max_output_bytes']:
                raise ValueError('交付文件超过治理大小限制')
            if task['kind'] not in ('leader', 'group'):
                continue
            for path, schema in rule['leader_outputs'].items():
                if path not in encoded:
                    raise ValueError('交付缺少必需文件：' + path)
                validate(read_json_delivery(encoded[path]), schema)

    def budget_block(self, c, task, reserved, grant=None):
        project = self.s.row(c, 'projects', task['project_id'])
        checks = []
        for scope in ('platform', 'workspace:' + project['workspace_id'], 'project:' + project['id'], 'unit:' + task['unit_id']):
            limit = self.read(c, scope)['policy']['budget_points']
            if limit is None:
                continue
            query = 'SELECT r.* FROM requests r JOIN tasks t ON t.attempt_id=r.attempt_id WHERE r.project_id=%s'
            params = [project['id']]
            if scope.startswith('unit:'):
                query += ' AND t.unit_id=%s';params.append(task['unit_id'])
            # Attempts of earlier generations still count, even after task.attempt_id changes.
            query = query.replace('JOIN tasks t ON t.attempt_id=r.attempt_id', 'JOIN attempts a ON a.id=r.attempt_id JOIN tasks t ON t.id=a.task_id')
            checks.append((limit * SCALE, list(c.execute(query, params))))
        current = grant
        while current:
            if current['budget'] is not None:
                query = "WITH RECURSIVE children(id) AS (SELECT %s UNION ALL SELECT m.id FROM mandates m JOIN children p ON m.parent_id=p.id) SELECT r.* FROM requests r WHERE mandate_id IN (SELECT id FROM children)"
                checks.append((current['budget'] * SCALE, list(c.execute(query, (current['id'],)))))
            current = c.execute('SELECT * FROM mandates WHERE id=%s', (current['parent_id'],)).fetchone() if current['parent_id'] else None
        for limit, rows in checks:
            total = 0
            for row in rows:
                value = dict(row)
                charge = c.execute('SELECT charged,state FROM credit_charges WHERE request_id=%s', (value['id'],)).fetchone()
                if charge and charge['state'] == 'settled':
                    total += charge['charged'];continue
                actual = measure(value)['points'] if value['status'] != 'started' else None
                if actual is None and value['reserved_points_micros'] is None:
                    return dict(summary='已有调用用量无法核算，禁止继续预留')
                total += point_micros(actual) if actual is not None else int(value['reserved_points_micros'])
            if total + reserved > limit:
                return dict(summary='治理或授权预算不足，未发送供应商请求')
        return None
