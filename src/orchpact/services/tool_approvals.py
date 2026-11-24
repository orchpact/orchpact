"""Durable approval of an exact deterministic external write."""
from ..adapters.database import dumps, ident, loads, now
from ..kernel.errors import DomainError, Forbidden, NotFound
from ..kernel.verification import canonical_digest


AWAITING_APPROVAL = 'awaiting_approval'


class ToolApprovals:
    def __init__(self, service):
        self.service = service
        self.db = service.db

    def prepare(self, c, task, slot, arguments, adapter, binding_digest):
        target = dict(task_id=task['id'], project_id=task['project_id'], generation=task['generation'],
            logical_tool=slot, endpoint_ref=adapter.binding['endpoint_ref'],
            implementation_version=adapter.binding['implementation_version'], binding_digest=binding_digest)
        approval_digest = canonical_digest(dict(target, arguments=arguments))
        old = c.execute('SELECT id,approval_digest,decision,tool_call_id FROM tool_approvals WHERE task_id=%s AND generation=%s',
            (task['id'], task['generation'])).fetchone()
        if old:
            if old['approval_digest'] != approval_digest or old['decision'] != 'approve' or old['tool_call_id']:
                raise DomainError('tool_approval_conflict', '当前工具调用未取得可用的事前批准')
            return old['id'], True

        key = ident()
        c.execute('''INSERT INTO tool_approvals(id,project_id,task_id,attempt_id,generation,logical_tool,
            endpoint_ref,implementation_version,binding_digest,arguments,approval_digest,created)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (key, task['project_id'], task['id'], task['attempt_id'], task['generation'], slot,
             target['endpoint_ref'], target['implementation_version'], binding_digest, dumps(arguments), approval_digest, now()))
        c.execute('UPDATE tasks SET status=%s WHERE id=%s', (AWAITING_APPROVAL, task['id']))
        c.execute('UPDATE attempts SET status=%s,finished=%s WHERE id=%s', (AWAITING_APPROVAL, now(), task['attempt_id']))
        self.db.event(c, task['project_id'], task['id'], 'tool.approval_requested', approval_id=key, logical_tool=slot)
        self.service.reconcile(c, task['project_id'])
        return key, False

    @staticmethod
    def _status(row):
        if row['tool_call_id']:
            return 'used'
        if row['decision'] == 'reject':
            return 'rejected'
        allowed = {'queued', 'waiting', 'running'} if row['decision'] == 'approve' else {AWAITING_APPROVAL}
        if row['generation'] != row['task_generation'] or row['task_status'] not in allowed:
            return 'stale'
        return 'approved' if row['decision'] else 'pending'

    @classmethod
    def _public(cls, row):
        fields = ('id', 'project_id', 'task_id', 'generation', 'logical_tool', 'endpoint_ref',
            'implementation_version', 'created', 'decided_by', 'decided_at', 'tool_call_id')
        return dict({field: row[field] for field in fields}, status=cls._status(row))

    @staticmethod
    def _rows(c, project, key=None, *, include_arguments=False):
        query = '''SELECT a.id,a.project_id,a.task_id,a.generation,a.logical_tool,a.endpoint_ref,
                      a.implementation_version,a.created,a.decision,a.decided_by,a.decided_at,a.tool_call_id,
                      t.generation AS task_generation,t.status AS task_status'''
        if key is not None:
            query += ',a.approval_digest'
        if include_arguments:
            query += ',a.arguments,a.binding_digest'
        query += ' FROM tool_approvals a JOIN tasks t ON t.id=a.task_id WHERE a.project_id=%s'
        if key is not None:
            return c.execute(query+' AND a.id=%s', (project, key)).fetchall()
        return c.execute(query+' ORDER BY a.created,a.id', (project,)).fetchall()

    def summaries(self, c, project):
        return [self._public(row) for row in self._rows(c, project)]

    def _scoped(self, c, ctx, project, key, *, include_arguments=False):
        if ctx.subject_type != 'user':
            raise Forbidden('工具审批仅接受已认证的用户')
        run = self.service.scope(c, ctx, self.service.row(c, 'projects', project))
        if ctx.workspace_id != run['workspace_id']:
            raise NotFound()
        workspace = self.service.access.member(c, run['workspace_id'], self.service.actor(ctx))
        if not ctx.admin and workspace['owner_id'] != ctx.user_id:
            raise Forbidden('只有 Workspace 所有者或管理员可以审批外部写操作')
        rows = self._rows(c, project, key, include_arguments=include_arguments)
        if not rows:
            raise NotFound()
        return rows[0]

    def read(self, ctx, project, key):
        with self.db.connect() as c:
            row = self._scoped(c, ctx, project, key, include_arguments=True)
            return dict(self._public(row), arguments=loads(row['arguments']),
                approval_digest=row['approval_digest'], binding_digest=row['binding_digest'])

    def decide(self, ctx, project, key, approval_digest, decision):
        with self.db.tx() as c:
            row = self._scoped(c, ctx, project, key)
            if (decision not in {'approve','reject'} or row['approval_digest'] != approval_digest
                    or self._status(row) == 'stale'):
                raise DomainError('tool_approval_conflict', '审批摘要、决定或工作代次已失效')
            if row['decision']:
                if row['decision'] == decision and row['decided_by'] == ctx.user_id:
                    return self._public(row)
                raise DomainError('tool_approval_conflict', '这次工具调用已作出审批决定')
            c.execute('UPDATE tool_approvals SET decision=%s,decided_by=%s,decided_at=%s WHERE id=%s',
                (decision, ctx.user_id, now(), key))
            status = 'queued' if decision == 'approve' else 'rejected'
            error = '外部写操作未获批准' if decision == 'reject' else None
            c.execute('UPDATE tasks SET status=%s,error=%s WHERE id=%s', (status, error, row['task_id']))
            self.db.event(c, project, row['task_id'], 'tool.approval_decided',
                approval_id=key, decision=decision, actor=ctx.subject)
            self.service.reconcile(c, project)
            return self._public(self._rows(c, project, key)[0])
