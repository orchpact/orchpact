"""Organization command service. One graph, explicit actors, immutable deliveries."""
from contextlib import nullcontext
from dataclasses import replace
from hashlib import sha256
import json

from ..adapters.database import ident, now, dumps, loads
from ..adapters.identity import Principal
from ..adapters.artifacts import encode_files
from ..kernel.contracts import validate, check_schema, read_json_delivery
from ..kernel.models import ExecutionContext, Decision, Command, ReviewInput, UnitInput, AgentInput
from ..kernel.plan import validate_plan
from ..kernel.errors import DomainError, Forbidden, NotFound
from ..credits import summarize, measure
from .governance import Governance

TERMINAL = {'completed', 'partial', 'failed', 'interrupted', 'cancelled', 'rejected'}


class Platform:
    def __init__(self, db, settings, artifacts, access, configuration):
        self.db, self.settings, self.artifacts = db, settings, artifacts
        self.access, self.configuration = access, configuration
        self.policies = Governance(self)
        from .workflows import WorkflowCatalog
        self.workflows=WorkflowCatalog(self)
        from .validation import DeliveryContracts
        self.contracts = DeliveryContracts(self)
        self.stop_event = None

    @staticmethod
    def actor(ctx):
        return Principal(ctx.user_id, ctx.admin, ctx.workspace_id, ctx.application_id, ctx.application_key_id, ctx.client)

    @staticmethod
    def row(c, table, key):
        if table not in {'projects', 'tasks', 'artifacts', 'attempts', 'units', 'agents', 'invocations', 'requests', 'messages'}:
            raise ValueError('无效资源类型')
        row = c.execute(f'SELECT * FROM {table} WHERE id=%s', (key,)).fetchone()
        if not row:
            raise NotFound()
        value=dict(row)
        if table=='units' and value.get('crew'):value['crew']=loads(value['crew'])
        return value

    def scope(self, c, ctx, project):
        if not isinstance(ctx, ExecutionContext):
            raise Forbidden('缺少显式执行上下文')
        if not ctx.admin:
            if ctx.workspace_id != project['workspace_id'] or (ctx.application_id and ctx.application_id != project['application_id']):
                raise NotFound()
            self.access.member(c, project['workspace_id'], self.actor(ctx))
            binding = c.execute('SELECT require_signature FROM applications WHERE id=%s', (project['application_id'],)).fetchone()
            if binding and binding['require_signature'] and not ctx.application_key_id and ctx.subject_type == 'user':
                raise Forbidden('此应用要求经过签名网关')
        if ctx.project_id and ctx.project_id != project['id']:
            raise NotFound()
        return project

    def system_context(self, project):
        return ExecutionContext('scheduler', project['workspace_id'], project['application_id'],
                                project['created_by'], 'system', project['id'])

    def task_context(self, c, task):
        project = self.row(c, 'projects', task['project_id'])
        return ExecutionContext('task:' + task['id'], project['workspace_id'], project['application_id'],
                                project['created_by'], 'agent', project['id'])

    def unit(self, ctx, data, key=None, c=None):
        value = UnitInput.model_validate(data).model_dump()
        with self.db.tx() if c is None else nullcontext(c) as c:
            self.access.member(c, ctx.workspace_id, self.actor(ctx), owner=not ctx.admin)
            if key and 'crew' not in data:value['crew']=self.row(c,'units',key).get('crew')
            parent = value.get('parent_id')
            seen = {key} if key else set()
            while parent:
                if parent in seen:
                    raise ValueError('组织隶属关系循环')
                seen.add(parent)
                old = self.row(c, 'units', parent)
                if old['workspace_id'] != ctx.workspace_id:
                    raise Forbidden()
                parent = old['parent_id']
            if value['model_id']:
                self.settings.choose(['text', 'json'], value['model_id'])
            if value['crew']:
                for member in value['crew']['members']:
                    self.settings.choose(member['capabilities'],member.get('model_id') or value['model_id'])
            if key:
                old = self.row(c, 'units', key)
                if old['workspace_id'] != ctx.workspace_id:
                    raise Forbidden()
                c.execute('UPDATE units SET name=%s,purpose=%s,parent_id=%s,model_id=%s,prompt=%s,crew=%s,version=version+1 WHERE id=%s',
                          (value['name'], value['purpose'], value['parent_id'], value['model_id'], value['prompt'], dumps(value['crew']) if value['crew'] else None, key))
            else:
                key = ident()
                c.execute('INSERT INTO units(id,name,purpose,parent_id,model_id,prompt,workspace_id,crew) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                          (key, value['name'], value['purpose'], value['parent_id'], value['model_id'], value['prompt'], ctx.workspace_id, dumps(value['crew']) if value['crew'] else None))
            return self.row(c, 'units', key)

    def agent(self, ctx, unit, data, key=None, project=None, c=None):
        value = AgentInput.model_validate(data).model_dump()
        with self.db.tx() if c is None else nullcontext(c) as c:
            organization = self.row(c, 'units', unit)
            if organization['workspace_id'] != ctx.workspace_id:
                raise Forbidden()
            if project:
                self.policies.authorize(c, ctx, 'recruit', self.row(c, 'projects', project))
            else:
                self.access.member(c, ctx.workspace_id, self.actor(ctx), owner=not ctx.admin)
            if project and 'image' in value['capabilities']:
                binding = c.execute('SELECT binding FROM invocations WHERE id=%s', (project,)).fetchone()
                image = loads(binding[0]).get('image_request') if binding else None
                if image:
                    if value['model_id'] and value['model_id'] != image['model_id']:
                        raise ValueError('图片角色 model_id 必须使用委托指定的模型')
                    value['model_id'] = image['model_id']
                    if image['reference_artifact_ids'] and 'image_reference' not in value['capabilities']:
                        value['capabilities'].append('image_reference')
            profile = self.settings.choose(value['capabilities'], value['model_id'])
            if key:
                old = self.row(c, 'agents', key)
                if old['unit_id'] != unit:
                    raise Forbidden()
                c.execute('UPDATE agents SET name=%s,role=%s,prompt=%s,model_id=%s,capabilities=%s,version=version+1 WHERE id=%s',
                          (value['name'], value['role'], value['prompt'], profile['id'], dumps(value['capabilities']), key))
            else:
                if c.execute('SELECT COUNT(*) FROM agents WHERE unit_id=%s AND (project_id IS NULL OR project_id=%s)', (unit, project)).fetchone()[0] >= 12:
                    raise ValueError('组织可用成员已达上限')
                key = ident()
                c.execute('INSERT INTO agents(id,unit_id,project_id,name,role,prompt,model_id,capabilities,created_by,lifetime) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                          (key, unit, project, value['name'], value['role'], value['prompt'], profile['id'], dumps(value['capabilities']), ctx.subject, 'project' if project else 'permanent'))
            return self.row(c, 'agents', key)

    def register_package(self, ctx, manifest, revision=0):
        if not ctx.admin:
            raise Forbidden('组织包登记仅限管理员')
        from ..kernel.packages import validate_package
        manifest = validate_package(manifest)
        app = manifest['id']
        import re
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}', app):
            raise ValueError('组织包 namespace 无效')
        if not manifest.get('operations'):
            raise ValueError('组织包缺少操作')
        for operation in manifest['operations'].values():
            check_schema(operation.get('input_schema', {}))
            check_schema(operation['output_schema'])
        with self.db.tx() as c:
            old = c.execute('SELECT * FROM applications WHERE id=%s', (app,)).fetchone()
            if old and c.execute('SELECT 1 FROM application_bindings WHERE application_id=%s AND revision=%s',(app,old['revision'])).fetchone():
                raise DomainError('application_bound','此应用引用独立工作流，请通过应用绑定更新，不能覆盖内联组织包')
            if (old['revision'] if old else 0) != revision:
                raise DomainError('revision_conflict', '组织包版本冲突')
            value = dict(manifest)
            value.setdefault('version', str(revision + 1))
            portable = manifest.get('format') == 'orchpact-workflow'
            enabled = old['enabled'] if portable and old else int(value.get('enabled', True))
            signature = old['require_signature'] if portable and old else int(value.get('require_signature', True))
            c.execute('INSERT INTO applications(id,name,config,revision,enabled,require_signature) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET name=excluded.name,config=excluded.config,revision=excluded.revision,enabled=excluded.enabled,require_signature=excluded.require_signature',
                      (app, value['name'], dumps(value), revision + 1, enabled, signature))
            self.db.event(c, None, None, 'package.registered', application_id=app, revision=revision + 1)
            return self.application(ctx, app, c)

    def application(self, ctx, app, c=None):
        if ctx.application_id and ctx.application_id != app:
            raise NotFound()
        with self.db.connect() if c is None else nullcontext(c) as c:
            row = c.execute('SELECT * FROM applications WHERE id=%s', (app,)).fetchone()
            if not row:
                raise NotFound('应用组织包尚未登记')
            manifest = loads(row['config'])
            reference=c.execute('SELECT workflow_id,workflow_revision FROM application_bindings WHERE application_id=%s AND revision=%s',(app,row['revision'])).fetchone()
            workflow=None
            if reference:
                asset=self.workflows.resolve(c,reference['workflow_id'],reference['workflow_revision'])
                manifest=asset['definition'];workflow={k:asset[k] for k in ('id','revision','version','sha256')}
            installed=c.execute('SELECT revision,manifest FROM organization_packages WHERE workspace_id=%s AND application_id=%s',(ctx.workspace_id,app)).fetchone()
            return dict(id=app, name=row['name'], revision=row['revision'], enabled=bool(row['enabled']),
                        require_signature=bool(row['require_signature']), operations=manifest['operations'], manifest=manifest,workflow=workflow,
                        installed_revision=installed['revision'] if installed else None,
                        installed_bindings=loads(installed['manifest']).get('_workflow',{}).get('bindings') if installed else None)

    def workflow_definition(self, ctx, app):
        from ..kernel.workflows import export_definition, definition_digest
        with self.db.connect() as c:
            self.access.member(c,ctx.workspace_id,self.actor(ctx))
            binding=self.application(ctx,app,c)
            definition=export_definition(binding['manifest'])
            return dict(definition=definition,revision=binding['revision'],sha256=definition_digest(definition))

    def prepare_package(self, binding, bindings, old=None):
        from ..kernel.workflows import bind_definition, definition_digest
        manifest=binding['manifest']
        if manifest.get('format') == 'orchpact-workflow':
            bindings=bindings if bindings is not None else (loads(old['manifest']).get('_workflow',{}).get('bindings',{}) if old else {})
            result=bind_definition(manifest,bindings,self.settings.choose)
            provenance=binding.get('workflow') or dict(id=manifest['id'],version=manifest['version'],revision=binding['revision'],sha256=definition_digest(manifest))
            result['_workflow']=dict(provenance,application_binding_revision=binding['revision'],bindings=bindings)
        else:
            if bindings:raise ValueError('请先将组织包导出为工作流定义，再绑定能力槽位')
            result=json.loads(dumps(manifest))
        self.settings.choose(['text','json'])
        for unit in result.get('units',[]):
            if unit.get('model_id'):self.settings.choose(['text','json'],unit['model_id'])
            for member in (unit.get('crew') or {}).get('members',[]):
                self.settings.choose(member.get('capabilities',['text']),member.get('model_id') or unit.get('model_id'))
        for agent in result.get('agents',[]):self.settings.choose(agent.get('capabilities',['text']),agent.get('model_id'))
        for operation in result['operations'].values():
            for node in operation.get('execution_plan',{}).get('nodes',[]):
                if node.get('staff'):
                    staff=node['staff'];self.settings.choose(staff.get('capabilities',['text']),staff.get('model_id'))
        return result

    def preview_package(self, ctx, app, bindings=None):
        with self.db.connect() as c:
            self.access.member(c,ctx.workspace_id,self.actor(ctx))
            binding=self.application(ctx,app,c)
            old=c.execute('SELECT * FROM organization_packages WHERE workspace_id=%s AND application_id=%s',(ctx.workspace_id,app)).fetchone()
            manifest=self.prepare_package(binding,bindings,old)
            if old and old['revision']==binding['revision'] and bindings is not None and bindings!=loads(old['manifest']).get('_workflow',{}).get('bindings',{}):
                raise DomainError('binding_conflict','当前工作流版本已绑定；变更资源须登记新修订并显式更新')
            return dict(application_id=app,revision=binding['revision'],installed_revision=old['revision'] if old else None,
                action='unchanged' if old and old['revision']==binding['revision'] else 'upgrade' if old else 'install',
                workflow=manifest.get('_workflow'),
                units=[dict(key=u['key'],name=u['name'],model_id=u.get('model_id') or self.settings.choose(['text','json'])['id']) for u in manifest['units']],
                operations={key:dict(nodes=len(op.get('execution_plan',{}).get('nodes',[])),output_file=op.get('output_file','result.json')) for key,op in manifest['operations'].items()})

    def install_package(self, ctx, app, c=None, expected_revision=None, bindings=None, binding_revision=None):
        with self.db.tx() if c is None else nullcontext(c) as c:
            binding = self.application(ctx, app, c)
            if binding_revision is not None and binding_revision!=binding['revision']:
                raise DomainError('revision_conflict','工作流定义已变化，请重新预览')
            old = c.execute('SELECT * FROM organization_packages WHERE workspace_id=%s AND application_id=%s', (ctx.workspace_id, app)).fetchone()
            if old and expected_revision is None and bindings is None:
                return loads(old['manifest'])
            space=self.access.member(c, ctx.workspace_id, self.actor(ctx), owner=(expected_revision is not None or bindings is not None) and not ctx.admin)
            if expected_revision is not None and (old['revision'] if old else 0)!=expected_revision:
                raise DomainError('revision_conflict','已安装组织包版本已变化')
            if old and old['revision']==binding['revision']:
                if bindings is not None and bindings!=loads(old['manifest']).get('_workflow',{}).get('bindings',{}):
                    raise DomainError('binding_conflict','当前工作流版本已绑定；变更资源须登记新修订并显式更新')
                return loads(old['manifest'])
            manifest=self.prepare_package(binding,bindings,old)
            # Install only catalog-validated data. Ordinary members may initialize it;
            # manual organization edits and explicit upgrades still require the owner.
            install_ctx=replace(ctx,subject=space['owner_id'],user_id=space['owner_id'],application_id=None,application_key_id=None)
            prior={row['alias'].split(':',1)[1]:dict(row) for row in c.execute("SELECT * FROM units WHERE workspace_id=%s AND split_part(alias,':',1)=%s", (ctx.workspace_id,app))}
            if old:
                for alias,row in prior.items():
                    c.execute('UPDATE units SET alias=%s WHERE id=%s',(app+'@'+str(old['revision'])+':'+alias,row['id']))
            units = {}
            pending = list(manifest.get('units', []))
            if not pending:
                pending = [dict(key='entry', name=manifest['name'], purpose=manifest.get('description', manifest['name']), prompt='', parent=None)]
            profile = self.settings.choose(['text', 'json'])
            while pending:
                progress = False
                for item in pending[:]:
                    if item.get('parent') and item['parent'] not in units:
                        continue
                    u = self.unit(install_ctx, dict(name=item['name'], purpose=item['purpose'], prompt=item.get('prompt', ''), parent_id=units.get(item.get('parent')), model_id=item.get('model_id') or prior.get(item['key'],{}).get('model_id') or profile['id'], crew=item.get('crew')), c=c)
                    units[item['key']] = u['id']
                    c.execute('UPDATE units SET alias=%s WHERE id=%s', (app + ':' + item['key'], u['id']))
                    pending.remove(item)
                    progress = True
                if not progress:
                    raise ValueError('组织包隶属引用错误或循环')
            for member in manifest.get('agents', []):
                data = {k: v for k, v in member.items() if k != 'unit'}
                previous=prior.get(member['unit'])
                if previous and not data.get('model_id'):
                    selected=c.execute('SELECT model_id FROM agents WHERE unit_id=%s AND name=%s AND project_id IS NULL',(previous['id'],member['name'])).fetchone()
                    if selected:data['model_id']=selected[0]
                self.agent(install_ctx, units[member['unit']], data, c=c)
            for operation in manifest['operations'].values():
                operation['unit_id'] = units[operation.pop('unit', next(iter(units)))]
                if operation.get('execution_plan'):
                    for node in operation['execution_plan']['nodes']:
                        node['unit_id'] = units[node.pop('unit')] if node.get('unit') else operation['unit_id']
                    validate_plan(operation['execution_plan'])
                operation.setdefault('output_file', 'result.json')
                operation.setdefault('delivery_context_limit', 9000)
            c.execute('INSERT INTO organization_packages VALUES(%s,%s,%s,%s) ON CONFLICT(workspace_id,application_id) DO UPDATE SET revision=excluded.revision,manifest=excluded.manifest', (ctx.workspace_id, app, binding['revision'], dumps(manifest)))
            self.db.event(c, None, None, 'package.installed', workspace_id=ctx.workspace_id, application_id=app, revision=binding['revision'])
            return manifest

    def invoke(self, ctx, app, data, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 100:
            raise ValueError('需要 Idempotency-Key')
        if ctx.application_id and ctx.application_id != app:
            raise Forbidden()
        fingerprint = sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        request_key = sha256((ctx.workspace_id + ':' + key).encode()).hexdigest()
        if len(dumps(data)) > 100000:
            raise ValueError('委托输入超过 10 万字符')
        with self.db.tx() as c:
            old = c.execute('SELECT * FROM invocations WHERE application_id=%s AND request_key=%s', (app, request_key)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise DomainError('idempotency_conflict', '幂等键已用于不同输入')
                return self.invocation(ctx, app, old['id'], c)
            self.access.member(c, ctx.workspace_id, self.actor(ctx))
            binding = self.application(ctx, app, c)
            if not binding['enabled']:
                raise DomainError('application_disabled', '应用已停用')
            self.access.wallets.require_available(c, ctx.user_id)
            manifest = self.install_package(ctx, app, c)
            operation = manifest['operations'].get(data['operation'])
            if not operation:
                raise NotFound('操作不存在')
            validate(data['input'], operation.get('input_schema', {}))
            if data.get('image_request'):
                from ..kernel.models import ImageRequest
                request = ImageRequest.model_validate(data['image_request']).model_dump()
                profile = self.settings.choose(['image'] + (['image_reference'] if request['reference_artifact_ids'] else []), request['model_id'])
                request['model_id'] = profile['id']
                for aid in request['reference_artifact_ids']:
                    artifact = self.row(c, 'artifacts', aid)
                    owner = self.scope(c,ctx,self.row(c, 'projects', artifact['project_id']))
                    if owner['workspace_id'] != ctx.workspace_id or owner['application_id'] != app:
                        raise ValueError('参考图必须属于本 Workspace 与应用')
                    from ..image_providers import image_format
                    image_format(self.artifacts.path(artifact).read_bytes())
                data = dict(data, image_request=request)
            project = ident()
            goal = operation['instruction'] + '\n任务输入：' + dumps(data['input'])
            if data.get('image_request'):
                goal += '\nimage_request：' + dumps(data['image_request'])
            c.execute('INSERT INTO projects(id,name,goal,root_unit_id,created,request_limit,search_limit,workspace_id,created_by,application_id,idempotency_key) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                      (project, binding['name'] + ' · ' + data['operation'], goal, operation['unit_id'], now(), operation.get('request_limit', 32), operation.get('search_limit', 0), ctx.workspace_id, ctx.user_id, app, request_key))
            p = self.row(c, 'projects', project)
            self.policies.initial(c, p, ctx.subject)
            runctx = replace(ctx, project_id=project)
            root = self.new_task(c, runctx, p, operation['unit_id'], goal, '按约定交付完整成果', definition={})
            frozen = dict(operation)
            if manifest.get('_workflow'):frozen['workflow']=manifest['_workflow']
            if data.get('image_request'):
                frozen['image_request'] = data['image_request']
            c.execute('INSERT INTO invocations VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s)', (project, app, data['operation'], root, dumps(data['input']), dumps(data.get('context', {})), dumps(frozen), request_key, fingerprint, now()))
            if operation.get('execution_plan'):
                self.install_plan(c, runctx, self.row(c, 'tasks', root), operation['execution_plan'])
            self.reconcile(c, project)
            return self.invocation(ctx, app, project, c)

    def new_task(self, c, ctx, project, unit, description, expected, parent=None, agent=None, phase='plan', definition=None):
        self.policies.authorize(c, ctx, 'delegate', project)
        if c.execute('SELECT COUNT(*) FROM tasks WHERE project_id=%s', (project['id'],)).fetchone()[0] >= 48:
            raise ValueError('项目工作项已达上限')
        u = self.row(c, 'units', unit)
        if u['workspace_id'] != project['workspace_id']:
            raise Forbidden('不能向其他 Workspace 的组织分派任务')
        from .teams import freeze_team
        participants=(definition or {}).get('participants')
        if participants and not u.get('crew'):raise ValueError('本团队没有岗位声明')
        roster=freeze_team(self.settings,u,participants) if not agent and (not definition or participants) else []
        a = self.row(c, 'agents', agent) if agent else None
        if a and (a['unit_id'] != unit or a['project_id'] not in (None, project['id'])):
            raise Forbidden()
        if roster:a=dict(roster[-1]['agent'],project_id=None)
        profile = roster[-1]['profile'] if roster else self.settings.choose(loads(a['capabilities']) if a else ['text', 'json'], a['model_id'] if a else u['model_id'])
        from ..prompts import leader_prompt, employee_prompt, WORKER_RULES
        prompt = (WORKER_RULES+'\n团队职责：'+u['purpose']+'\n团队提示词：'+u.get('prompt','')) if roster else employee_prompt(a) if a else leader_prompt(u)
        policy = self.policies.snapshot(c, project, unit, dict(description=description, expected=expected, kind='work' if a else 'leader'))
        snapshot = dict(unit=u, agent=a, profile=profile, prompt=prompt, prompt_sha256=sha256(prompt.encode()).hexdigest(), policies=policy)
        from .agent_steps import LIMITS
        from importlib.metadata import version
        snapshot['executor'] = dict(kind='crewai-crew',version=version('crewai'),limits=dict(LIMITS))
        snapshot['crew']=dict(process='sequential',members=roster) if roster else None
        if profile['kind'] == 'image':
            binding = c.execute('SELECT binding FROM invocations WHERE id=%s', (project['id'],)).fetchone()
            image = loads(binding[0]).get('image_request') if binding else None
            if image:
                snapshot['image_request'] = image
        key = ident()
        definition = definition or ({'crew_execution':True} if roster else {})
        kind = 'work' if a else ('group' if definition else 'leader')
        status = 'waiting' if kind == 'group' else 'queued'
        c.execute('INSERT INTO tasks(id,project_id,parent_id,unit_id,agent_id,kind,phase,status,description,expected,snapshot,created,definition,node_key) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                  (key, project['id'], parent, unit, agent, kind, phase, status, description, expected, dumps(snapshot), now(), dumps(definition), definition.get('key')))
        subject = 'task:' + key
        actions = ['call', 'publish', 'ask'] if a else sorted(self.policies.ACTIONS)
        capabilities = sorted(set(loads(a['capabilities'])) | ({'text','json'} if profile['kind']=='text' else set())) if a else sorted({cap for item in self.settings.catalog() for cap in item['capabilities']})
        if roster:capabilities=sorted({cap for member in roster for cap in loads(member['agent']['capabilities'])})
        self.policies.grant(c, ctx, project, subject, actions, capabilities)
        c.execute('INSERT INTO participations VALUES(%s,%s,%s,%s)', (project['id'], subject, unit, a['role'] if a else 'coordinator'))
        self.db.event(c, project['id'], key, 'work.created', unit_id=unit, task_kind=kind)
        return key

    def install_plan(self, c, ctx, root, value, reason='initial plan'):
        p = self.row(c, 'projects', root['project_id'])
        self.policies.authorize(c, ctx, 'plan', p)
        if c.execute('SELECT 1 FROM tasks WHERE parent_id=%s', (root['id'],)).fetchone():
            raise DomainError('plan_conflict', '入口已经有工作项，使用显式计划变更')
        plan, effective = validate_plan(value)
        nodes = {n['key']: n for n in plan['nodes']}
        ids, units = {}, {}
        pending = list(nodes)
        while pending:
            for alias in pending[:]:
                node = nodes[alias]
                if node['parent'] and node['parent'] not in ids:
                    continue
                unit = node['unit_id'] or units.get(node['parent'], root['unit_id'])
                member = None
                if node['staff']:
                    data = dict(node['staff']);data.pop('reason', None)
                    member = self.agent(ctx, unit, data, project=p['id'], c=c)['id']
                ids[alias] = self.new_task(c, ctx, p, unit, node['description'], node['expected'], ids.get(node['parent'], root['id']), member, definition=node)
                units[alias] = unit
                pending.remove(alias)
        for alias, dependencies in effective.items():
            for dep in dependencies:
                c.execute('INSERT INTO dependencies VALUES(%s,%s)', (ids[alias], ids[dep]))
        c.execute("UPDATE tasks SET kind='group',phase='plan',status='waiting',definition=%s WHERE id=%s", (dumps(dict(result_node=plan['result_node'], root=True)), root['id']))
        c.execute('INSERT INTO execution_plans VALUES(%s,%s,%s,0)', (p['id'], root['id'], dumps(plan)))
        c.execute('INSERT INTO plan_revisions VALUES(%s,%s,%s,%s,%s,%s)', (p['id'], p['revision'], dumps(plan), reason, ctx.subject, now()))
        return ids

    def current(self, c, task, attempt):
        row = self.row(c, 'tasks', task)
        if row['status'] != 'running' or row['attempt_id'] != attempt:
            raise DomainError('attempt_expired', '执行尝试已失效')
        return row

    def claim(self):
        with self.db.tx() as c:
            for p in c.execute("SELECT id FROM projects WHERE status NOT IN ('completed','cancelled')").fetchall():
                self.reconcile(c, p['id'])
            active = {r[0]: r[1] for r in c.execute("SELECT project_id,COUNT(*) FROM tasks WHERE status='running' GROUP BY project_id")}
            if sum(active.values()) >= self.settings.workers:
                return None
            rows = c.execute("SELECT t.* FROM tasks t JOIN projects p ON p.id=t.project_id WHERE t.status='queued' AND t.kind!='group' AND p.status!='cancelled' ORDER BY t.created,t.id").fetchall()
            dispatched = {r[0]: r[1] for r in c.execute('SELECT t.project_id,MAX(a.started) FROM attempts a JOIN tasks t ON t.id=a.task_id GROUP BY t.project_id')}
            rows = sorted(rows, key=lambda r: (active.get(r['project_id'], 0), dispatched.get(r['project_id'], 0), r['created'], r['id']))
            for row in rows:
                t = dict(row)
                if active.get(t['project_id'], 0) >= self.settings.max_tasks_per_project:
                    continue
                deps = c.execute('SELECT t.status,t.review FROM dependencies d JOIN tasks t ON t.id=d.depends_on WHERE d.task_id=%s', (t['id'],)).fetchall()
                if any(d['status'] != 'completed' or d['review'] != 'accepted' for d in deps):
                    continue
                if t['turns'] >= 12 or c.execute('SELECT COALESCE(SUM(turns),0) FROM tasks WHERE project_id=%s', (t['project_id'],)).fetchone()[0] >= 96:
                    c.execute("UPDATE tasks SET status='failed',error='执行轮次达到上限' WHERE id=%s", (t['id'],))
                    continue
                attempt = ident();snapshot = loads(t['snapshot'])
                c.execute("UPDATE tasks SET status='running',attempt_id=%s,turns=turns+1 WHERE id=%s", (attempt, t['id']))
                c.execute('INSERT INTO attempts(id,task_id,started,status,prompt,model_id) VALUES(%s,%s,%s,%s,%s,%s)', (attempt, t['id'], now(), 'running', snapshot['prompt'], snapshot['profile']['id']))
                c.execute("UPDATE projects SET status='running' WHERE id=%s",(t['project_id'],))
                self.db.event(c, t['project_id'], t['id'], 'work.running', attempt_id=attempt)
                return self.context(c, self.row(c, 'tasks', t['id']))
        return None

    def context(self, c, task):
        p = self.row(c, 'projects', task['project_id'])
        snap = loads(task['snapshot']);node = loads(task['definition'])
        inputs = [dict(r) for r in c.execute('SELECT t.id,t.description,t.result,t.artifact_id FROM tasks t JOIN dependencies d ON t.id=d.depends_on WHERE d.task_id=%s', (task['id'],))]
        children = [dict(r) for r in c.execute('SELECT * FROM tasks WHERE parent_id=%s', (task['id'],))]
        for item in children:
            item.pop('snapshot', None)
        binding = c.execute('SELECT * FROM invocations WHERE id=%s', (p['id'],)).fetchone()
        operation = loads(binding['binding']) if binding else None
        limit = operation.get('delivery_context_limit', 9000) if operation else 9000
        references = {r['artifact_id'] for r in inputs + children if r.get('artifact_id')}
        deliveries = [self.artifacts.excerpts(self.row(c, 'artifacts', aid), limit) for aid in sorted(references)]
        context = dict(task=task, snapshot=snap, project=p, execution_context=self.task_context(c, task),
                       previous_attempts=[dict(r) for r in c.execute('SELECT id,status,error FROM attempts WHERE task_id=%s AND id!=%s ORDER BY started DESC LIMIT 3', (task['id'], task['attempt_id']))],
                       directory=[{k:v for k,v in r.items() if k!='crew'} for r in c.execute('SELECT * FROM units WHERE workspace_id=%s', (p['workspace_id'],))],
                       members=[dict(r) for r in c.execute('SELECT * FROM agents WHERE unit_id=%s AND (project_id IS NULL OR project_id=%s)', (task['unit_id'], p['id']))],
                       peers=[dict(r) for r in c.execute("SELECT id,unit_id,status,phase FROM tasks WHERE project_id=%s AND kind='leader'", (p['id'],))],
                       children=children, messages=[dict(r) for r in c.execute('SELECT * FROM messages WHERE to_task=%s OR from_task=%s ORDER BY created', (task['id'], task['id']))],
                       inputs=inputs, deliveries=deliveries, models=self.settings.catalog())
        for member in context['members']:
            member['capabilities'] = loads(member['capabilities'])
        if operation:
            context['application'] = dict(application_id=binding['application_id'], operation=binding['operation'], instruction=operation['instruction'], input=loads(binding['input']), output_file=operation['output_file'], output_schema=operation['output_schema'], delivery_context_limit=limit)
            if operation.get('image_request'):
                context['snapshot']['image_request'] = operation['image_request']
        if node.get('staff') or node.get('participants'):
            context['planned_node'] = node
            context['plan_nodes'] = [dict(key=r['node_key'], task_id=r['id'], outputs=list(loads(r['definition']).get('outputs', {}))) for r in c.execute('SELECT id,node_key,definition FROM tasks WHERE project_id=%s AND node_key IS NOT NULL', (p['id'],))]
            if any(not d['complete'] for d in deliveries):
                context['input_error'] = '阶段依赖包含未完整提供的文件，拒绝仅凭摘录执行'
            if sum(len(f['content']) for d in deliveries for f in d['files']) > 240000:
                context['input_error'] = '阶段输入超过上下文额度'
        return context

    def publish(self, c, ctx, task, attempt, summary, files):
        p = self.row(c, 'projects', task['project_id'])
        self.policies.authorize(c, ctx, 'publish', p)
        self.contracts.check(c, task, files)
        self.policies.check_output(task, files)
        artifact = self.artifacts.publish(c, task, attempt, files)
        c.execute("UPDATE tasks SET status='completed',result=%s,artifact_id=%s,error=NULL,review='pending' WHERE id=%s", (summary, artifact, task['id']))
        c.execute("UPDATE attempts SET status='completed',finished=%s WHERE id=%s", (now(), attempt))
        self.db.event(c, p['id'], task['id'], 'artifact.published', artifact_id=artifact)
        return artifact

    def complete_work(self, ctx, task, attempt, summary, files):
        with self.db.tx() as c:
            t = self.current(c, task, attempt)
            if not isinstance(ctx, ExecutionContext) or ctx.subject != 'task:' + task:
                raise Forbidden('执行结果必须由领取该尝试的主体提交')
            self.publish(c, ctx, t, attempt, summary, files)
            self.reconcile(c, t['project_id'])

    def review(self, c, ctx, task, artifact, verdict, feedback, digest=None, automatic=False):
        p = self.row(c, 'projects', task['project_id'])
        self.policies.authorize(c, ctx, 'review', p)
        actual = self.row(c, 'artifacts', artifact)
        if actual['task_id'] != task['id'] or task['artifact_id'] != artifact or (digest and digest != actual['sha256']):
            raise DomainError('artifact_version_conflict', '验收必须引用当前交付版本与哈希')
        independent = any(x['policy']['independent_review'] for x in loads(task['snapshot']).get('policies', []))
        if independent and (ctx.subject == 'task:' + task['id'] or automatic):
            raise Forbidden('此产物需要独立评审')
        c.execute('INSERT INTO reviews VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (ident(), p['id'], task['id'], artifact, actual['sha256'], ctx.subject, verdict, feedback, task['generation'], now()))
        if verdict == 'accept':
            c.execute("UPDATE tasks SET review='accepted' WHERE id=%s", (task['id'],))
        elif verdict == 'waive':
            c.execute("UPDATE tasks SET status='partial',review='waived' WHERE id=%s", (task['id'],))
        elif verdict == 'rework':
            self.invalidate(c, p['id'], {task['id']}, feedback)
        else:
            raise ValueError('无效验收动作')
        self.db.event(c, p['id'], task['id'], 'artifact.reviewed', artifact_id=artifact, reviewer=ctx.subject, verdict=verdict)

    def invalidate(self, c, project, affected, feedback):
        affected = set(affected)
        changed = True
        while changed:
            before = set(affected)
            for r in c.execute('SELECT task_id,depends_on FROM dependencies WHERE task_id IN (SELECT id FROM tasks WHERE project_id=%s)', (project,)):
                if r['depends_on'] in affected:
                    affected.add(r['task_id'])
            for r in c.execute('SELECT id,parent_id FROM tasks WHERE project_id=%s', (project,)):
                if r['id'] in affected and r['parent_id']:
                    affected.add(r['parent_id'])
            changed = before != affected
        for key in affected:
            t = self.row(c, 'tasks', key)
            if t['status'] == 'running':
                raise DomainError('work_in_flight', '受影响的工作仍在执行，请先等待或取消')
            status = 'waiting' if t['kind'] == 'group' else 'queued'
            c.execute("UPDATE tasks SET status=%s,review='pending',artifact_id=NULL,result=NULL,error=NULL,generation=generation+1,description=description||%s WHERE id=%s", (status, '\n返工要求：' + feedback[:4000], key))
        c.execute('UPDATE invocations SET result=NULL WHERE id=%s', (project,))
        c.execute("UPDATE projects SET status='running' WHERE id=%s", (project,))
        self.db.event(c, project, None, 'plan.rework', affected=sorted(affected))

    def reconcile(self, c, project):
        p = self.row(c, 'projects', project)
        if p['status'] == 'cancelled':
            return
        ctx = self.system_context(p)
        for _ in range(50):
            changed = False
            rows = [dict(r) for r in c.execute('SELECT * FROM tasks WHERE project_id=%s ORDER BY created,id', (project,))]
            by_id = {t['id']: t for t in rows}
            for t in rows:
                node = loads(t['definition'])
                if t['kind'] == 'work' and t['status'] in ('queued', 'waiting'):
                    prerequisites = [by_id[r[0]] for r in c.execute('SELECT depends_on FROM dependencies WHERE task_id=%s', (t['id'],))]
                    blocked = any(x['status'] in ('failed', 'interrupted', 'partial', 'cancelled', 'rejected') for x in prerequisites)
                    ready = all(x['status'] == 'completed' and x['review'] == 'accepted' for x in prerequisites)
                    if blocked and t['status'] != 'waiting':
                        c.execute("UPDATE tasks SET status='waiting',error='依赖工作未完整通过' WHERE id=%s", (t['id'],));changed = True
                    elif ready and t['status'] == 'waiting':
                        c.execute("UPDATE tasks SET status='queued',error=NULL WHERE id=%s", (t['id'],));changed = True
                if t['status'] == 'completed' and t['review'] == 'pending' and node:
                    if node.get('gate'):
                        reports = [read_json_delivery(f['data']) for f in self.artifacts.read_files(self.row(c, 'artifacts', t['artifact_id']))]
                        if not reports or reports[0].get('passed') is not True:
                            planrow = c.execute('SELECT * FROM execution_plans WHERE project_id=%s', (project,)).fetchone()
                            plan = loads(planrow['definition'])
                            if planrow['repairs'] >= plan.get('max_repairs', 2):
                                c.execute("UPDATE tasks SET status='failed',error='独立审校未通过，返工额度已用尽' WHERE id=%s", (t['id'],))
                                changed = True
                                continue
                            upstream = set();pending = [t['id']]
                            while pending:
                                current = pending.pop()
                                deps = {r[0] for r in c.execute('SELECT depends_on FROM dependencies WHERE task_id=%s', (current,))}
                                deps |= {x['id'] for x in rows if x['parent_id'] == current}
                                for dep in deps - upstream:
                                    upstream.add(dep);pending.append(dep)
                            requested = {issue.get('node_key') for issue in reports[0].get('issues', []) if isinstance(issue, dict)}
                            selected = {x['id'] for x in rows if x['id'] in upstream and x['node_key'] in requested}
                            if not selected:
                                selected = {x['id'] for x in rows if x['node_key'] in node.get('repair_from', [])}
                            selected = selected or {t['id']}
                            try:
                                self.invalidate(c, project, selected, dumps(reports[0].get('issues', []))[:4000])
                            except DomainError:
                                continue
                            c.execute('UPDATE execution_plans SET repairs=repairs+1 WHERE project_id=%s', (project,))
                            return self.reconcile(c, project)
                    try:
                        self.review(c, ctx, t, t['artifact_id'], 'accept', '确定性契约通过' if not node.get('gate') else '独立审校通过', automatic=not node.get('gate'))
                        changed = True
                    except Forbidden:
                        pass
                if t['kind'] == 'group' and t['status'] not in TERMINAL:
                    children = [x for x in rows if x['parent_id'] == t['id'] and loads(x['definition']).get('active',True)]
                    deps = [by_id[r[0]] for r in c.execute('SELECT depends_on FROM dependencies WHERE task_id=%s', (t['id'],))]
                    if children and all(x['status'] == 'completed' and x['review'] == 'accepted' for x in children + deps):
                        c.execute('SAVEPOINT aggregate_delivery')
                        try:
                            files = []
                            if node.get('root'):
                                final = next(x for x in rows if x['node_key'] == node['result_node'])
                                binding = c.execute('SELECT binding FROM invocations WHERE id=%s', (project,)).fetchone()
                                filename = loads(binding[0])['output_file']
                                files = [f for f in self.artifacts.read_files(self.row(c, 'artifacts', final['artifact_id'])) if f['path'] == filename]
                            else:
                                for child in children:
                                    files.extend(f for f in self.artifacts.read_files(self.row(c, 'artifacts', child['artifact_id'])) if f['path'] in node.get('exports', []))
                            files = files or [dict(path='group.json', content='{}')]
                            attempt = ident()
                            c.execute('INSERT INTO attempts(id,task_id,started,status,prompt,model_id) VALUES(%s,%s,%s,%s,%s,%s)', (attempt, t['id'], now(), 'running', '', 'deterministic'))
                            self.publish(c, ctx, t, attempt, '约定文件原样汇总', files)
                            updated = self.row(c, 'tasks', t['id'])
                            try:
                                self.review(c, ctx, updated, updated['artifact_id'], 'accept', '汇总契约通过', automatic=True)
                            except Forbidden:
                                pass
                            changed = True
                            c.execute('RELEASE aggregate_delivery')
                        except (ValueError,OSError) as exc:
                            c.execute('ROLLBACK TO aggregate_delivery');c.execute('RELEASE aggregate_delivery')
                            error=str(exc)[:1000] if isinstance(exc,ValueError) else type(exc).__name__
                            c.execute("UPDATE tasks SET status='failed',error=%s WHERE id=%s",(error,t['id']))
                            self.db.event(c,project,t['id'],'aggregation.failed',error=error)
                            changed=True
                elif t['kind'] == 'leader' and t['status'] == 'waiting':
                    children = [x for x in rows if x['parent_id'] == t['id'] and loads(x['definition']).get('active',True)]
                    unread = c.execute("SELECT 1 FROM messages WHERE to_task=%s AND seen=0 AND kind IN ('reply','delivery','question')", (t['id'],)).fetchone()
                    unanswered = c.execute("SELECT 1 FROM messages q WHERE q.from_task=%s AND q.kind='question' AND NOT EXISTS(SELECT 1 FROM messages a WHERE a.reply_to=q.id)", (t['id'],)).fetchone()
                    if unread or (children and all(x['status'] in TERMINAL for x in children) and not unanswered):
                        c.execute("UPDATE tasks SET status='queued' WHERE id=%s", (t['id'],));changed = True
            if not changed:
                break
        rows = [dict(r) for r in c.execute('SELECT * FROM tasks WHERE project_id=%s', (project,))]
        root = next(t for t in rows if t['parent_id'] is None)
        if root['status'] == 'completed' and root['review'] == 'accepted':
            status = 'completed'
            binding = c.execute('SELECT binding FROM invocations WHERE id=%s', (project,)).fetchone()
            if binding:
                output = self.result(loads(binding[0]), self.artifacts.read_files(self.row(c, 'artifacts', root['artifact_id'])))
                c.execute('UPDATE invocations SET result=%s WHERE id=%s', (dumps(output), project))
        elif any(t['status'] == 'running' for t in rows):
            status = 'running'
        elif any(t['status'] == 'queued' for t in rows):
            status = 'queued'
        elif any(t['status'] in ('failed', 'interrupted', 'partial', 'rejected') for t in rows):
            status = 'partial' if len(rows) > 1 else 'failed'
        else:
            status = 'waiting'
        c.execute('UPDATE projects SET status=%s WHERE id=%s', (status, project))

    def decide(self, ctx, task, attempt, value):
        decision = Decision.model_validate(value).model_dump()
        with self.db.tx() as c:
            t = self.current(c, task, attempt);p = self.row(c, 'projects', t['project_id'])
            self.policies.authorize(c, ctx, 'plan', p)
            if ctx.subject != 'task:' + task:
                raise Forbidden('决策主体与执行尝试不匹配')
            if t['kind'] != 'leader':
                raise Forbidden('仅规划角色可提出组织决策')
            if t['phase'] == 'intake':
                if decision['action'] not in ('accept', 'reject', 'wait'):
                    raise ValueError('组织先明确承接或拒绝')
                if decision['action'] != 'accept' and (decision['recruits'] or decision['assignments']):
                    raise ValueError('组织承接后才能招募或分派')
                if decision['action'] == 'accept':
                    c.execute("UPDATE tasks SET phase='plan',status='queued' WHERE id=%s", (task,))
                elif decision['action'] == 'reject':
                    c.execute("UPDATE tasks SET status='rejected',error=%s WHERE id=%s", (decision['summary'], task))
            elif decision['action'] in ('accept', 'reject'):
                raise ValueError('此工作已经承接')
            if decision['execution_plan']:
                if decision['action'] != 'plan' or any(decision[k] for k in ('recruits','assignments','reviews','messages','files','source_artifact_id')):
                    raise ValueError('完整计划必须独立提交')
                self.install_plan(c, ctx, t, decision['execution_plan'])
            else:
                for recruit in decision['recruits']:
                    data = dict(recruit);data.pop('reason', None)
                    self.agent(ctx, t['unit_id'], data, project=p['id'], c=c)
                for assignment in decision['assignments']:
                    unit = assignment['unit_id'] or t['unit_id'];member = None
                    if unit == t['unit_id']:
                        match = c.execute('SELECT id FROM agents WHERE unit_id=%s AND (id=%s OR name=%s) AND (project_id IS NULL OR project_id=%s)', (unit, assignment['agent'], assignment['agent'], p['id'])).fetchall()
                        if len(match) != 1:
                            raise ValueError('内部委派必须指向实际成员')
                        member = match[0][0]
                    else:
                        ancestors = {t['unit_id']};ancestor = t
                        while ancestor['parent_id']:
                            ancestor = self.row(c, 'tasks', ancestor['parent_id']);ancestors.add(ancestor['unit_id'])
                        if unit in ancestors or len(ancestors) >= 4:
                            raise ValueError('组织委派循环或深度超限')
                    child = self.new_task(c, ctx, p, unit, assignment['description'], assignment['expected'], task, member, phase='plan' if member else 'intake')
                    ancestors = {task}
                    ancestor = t
                    while ancestor['parent_id']:
                        ancestors.add(ancestor['parent_id']);ancestor = self.row(c,'tasks',ancestor['parent_id'])
                    inherited = [r[0] for r in c.execute('SELECT depends_on FROM dependencies WHERE task_id=%s',(task,))]
                    for dep in set(assignment['depends_on'] + inherited):
                        source = self.row(c, 'tasks', dep)
                        if source['project_id'] != p['id'] or dep in ancestors:
                            raise ValueError('依赖范围无效')
                        c.execute('INSERT INTO dependencies VALUES(%s,%s)', (child, dep))
                for message in decision['messages']:
                    self.policies.authorize(c, ctx, 'ask', p)
                    target = message['to_task'] if message['to_task'] != 'user' else None
                    if target is None and message['kind'] != 'question':
                        raise ValueError('向用户只提交需要回答的问题')
                    if target and self.row(c, 'tasks', target)['project_id'] != p['id']:
                        raise Forbidden()
                    if message['kind'] == 'reply':
                        original = self.row(c, 'messages', message['reply_to'])
                        if original['to_task'] != task or original['from_task'] != target or original['kind'] != 'question':
                            raise ValueError('回复必须关联发给自己的原问题')
                    c.execute('INSERT INTO messages(id,project_id,from_task,to_task,kind,content,reply_to,created) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)', (ident(), p['id'], task, target, message['kind'], message['content'], message['reply_to'], now()))
                for review in decision['reviews']:
                    child = self.row(c, 'tasks', review['task_id'])
                    if child['project_id'] != p['id']:
                        raise Forbidden()
                    if child['artifact_id']:
                        self.review(c, ctx, child, review.get('artifact_id') or child['artifact_id'], review['verdict'], review['feedback'])
                    elif review['verdict'] == 'waive':
                        c.execute("UPDATE tasks SET review='waived' WHERE id=%s", (child['id'],))
                    elif review['verdict'] == 'rework':
                        self.invalidate(c, p['id'], {child['id']}, review['feedback'])
                    else:
                        raise ValueError('任务尚无可验收文件')
                if decision['action'] == 'deliver':
                    children = [dict(r) for r in c.execute('SELECT * FROM tasks WHERE parent_id=%s', (task,))]
                    if any(x['status'] not in TERMINAL or x['review'] == 'pending' for x in children):
                        raise ValueError('尚有未完成或未验收工作')
                    unanswered = c.execute("SELECT 1 FROM messages q WHERE q.project_id=%s AND q.kind='question' AND (q.from_task=%s OR q.to_task=%s) AND NOT EXISTS(SELECT 1 FROM messages a WHERE a.reply_to=q.id)", (p['id'], task, task)).fetchone()
                    if unanswered:
                        raise ValueError('问题尚未回答')
                    files = list(decision['files'])
                    if decision['source_artifact_id']:
                        artifact = self.row(c, 'artifacts', decision['source_artifact_id'])
                        owner = self.row(c, 'tasks', artifact['task_id'])
                        if artifact['project_id'] != p['id'] or owner['artifact_id'] != artifact['id'] or owner['review'] != 'accepted':
                            raise ValueError('引用必须是本项目当前已验收交付')
                        files += self.artifacts.read_files(artifact)
                    if not files:
                        files = [dict(path='result.md', content=decision['result'])]
                    partial = any(x['status'] != 'completed' for x in children)
                    self.publish(c, ctx, dict(t, status='partial') if partial else t, attempt, decision['result'], files)
                    updated = self.row(c, 'tasks', task)
                    if partial:
                        c.execute("UPDATE tasks SET status='partial',review='pending' WHERE id=%s", (task,))
                    else:
                        self.review(c, self.system_context(p), updated, updated['artifact_id'], 'accept', '规划交付契约通过', automatic=True)
                elif decision['action'] == 'accept':
                    active = c.execute("SELECT 1 FROM tasks WHERE parent_id=%s AND status IN ('queued','running','waiting')",(task,)).fetchone()
                    c.execute('UPDATE tasks SET status=%s WHERE id=%s',('waiting' if active else 'queued',task))
                elif decision['action'] not in ('accept', 'reject'):
                    waiting = c.execute("SELECT 1 FROM tasks WHERE parent_id=%s AND status NOT IN ('completed','partial','failed','interrupted','cancelled','rejected')", (task,)).fetchone()
                    questions = c.execute("SELECT 1 FROM messages q WHERE q.from_task=%s AND q.kind='question' AND NOT EXISTS(SELECT 1 FROM messages a WHERE a.reply_to=q.id)", (task,)).fetchone()
                    if not waiting and not questions:
                        raise ValueError('没有等待对象，请分派、提问或交付')
                    c.execute("UPDATE tasks SET status='waiting' WHERE id=%s", (task,))
            c.execute('UPDATE attempts SET response=%s,status=%s,finished=%s WHERE id=%s', (dumps(decision), self.row(c, 'tasks', task)['status'], now(), attempt))
            c.execute('UPDATE messages SET seen=1 WHERE to_task=%s', (task,))
            self.reconcile(c, p['id'])

    def fail(self, ctx, task, attempt, error):
        with self.db.tx() as c:
            t = self.current(c, task, attempt)
            p = self.row(c, 'projects', t['project_id'])
            if not isinstance(ctx, ExecutionContext) or ctx.subject != 'task:' + task or ctx.project_id != p['id'] or ctx.workspace_id != p['workspace_id']:
                raise Forbidden()
            c.execute("UPDATE tasks SET status='failed',error=%s WHERE id=%s", (str(error)[:4000], task))
            c.execute("UPDATE attempts SET status='failed',error=%s,finished=%s WHERE id=%s", (str(error)[:4000], now(), attempt))
            self.db.event(c, t['project_id'], task, 'work.failed', error_type=type(error).__name__)
            self.reconcile(c, t['project_id'])

    @staticmethod
    def result(operation, files, required=True):
        found = [f for f in files if f['path'] == operation['output_file']]
        if not found and not required:
            return None
        if len(found) != 1:
            raise ValueError('交付缺少唯一的约定结果文件')
        f = found[0];value = read_json_delivery(f.get('content', f.get('data')))
        validate(value, operation['output_schema'])
        return value

    def invocation(self, ctx, app, key, c=None):
        with self.db.connect() if c is None else nullcontext(c) as c:
            p = self.scope(c, ctx, self.row(c, 'projects', key))
            r = self.row(c, 'invocations', key)
            if r['application_id'] != app:
                raise NotFound()
            tasks = []
            for row in c.execute('SELECT * FROM tasks WHERE project_id=%s ORDER BY created,id', (key,)):
                t = dict(row);snap = loads(t.pop('snapshot'));node = loads(t.pop('definition'))
                t.update(required=node.get('active',True), name=snap['unit']['name'] if snap.get('crew') else snap['agent']['name'] if snap['agent'] else snap['unit']['name'], recruited=bool(snap['agent'] and snap['agent'].get('project_id')), node_key=t['node_key'], superseded_by=None,
                         requires_independent_review=any(p['policy'].get('independent_review',False) for p in snap.get('policies',[])))
                tasks.append(t)
            root = next(t for t in tasks if t['parent_id'] is None)
            requests = []
            for row in c.execute('''SELECT r.*, ch.state AS charge_state, ch.reserved AS charge_reserved,
                    ch.charged AS charge_settled FROM requests r
                    LEFT JOIN credit_charges ch ON ch.request_id=r.id
                    WHERE r.project_id=%s ORDER BY r.created,r.id''', (key,)):
                value = dict(row)
                charge_state=value.pop('charge_state');reserved=value.pop('charge_reserved');settled=value.pop('charge_settled')
                value['accounting']=dict(state=charge_state or 'untracked',
                    reserved_points=reserved/1_000_000 if reserved is not None else None,
                    settled_points=settled/1_000_000 if settled is not None else None)
                value['credits'] = measure(value);requests.append(value)
            questions = [dict(q) for q in c.execute("SELECT q.* FROM messages q WHERE q.project_id=%s AND q.to_task IS NULL AND q.kind='question' AND NOT EXISTS(SELECT 1 FROM messages a WHERE a.reply_to=q.id)", (key,))]
            plan = c.execute('SELECT repairs FROM execution_plans WHERE project_id=%s', (key,)).fetchone()
            return dict(id=key, project_id=key, root_task_id=root['id'], application_id=app, operation=r['operation'], revision=p['revision'], status=p['status'], context=loads(r['context']),
                        result=loads(r['result']) if r['result'] and p['status'] == 'completed' else None,
                        artifact_id=root['artifact_id'], tasks=tasks, questions=questions, blockers=[t['error'] for t in tasks if t['error']], error=root['error'] or next((t['error'] for t in tasks if t['error']), None),
                        crew_runs=[dict(run,roster=loads(run['roster'])) for run in c.execute('SELECT * FROM crew_runs WHERE project_id=%s ORDER BY created,id',(key,))],
                        agent_steps=[dict(step) for step in c.execute('SELECT * FROM agent_steps WHERE project_id=%s ORDER BY created,ordinal,id',(key,))],
                        requests=requests, usage=summarize(requests), payer_user_id=p['created_by'],workflow=loads(r['binding']).get('workflow'),
                        execution_plan=dict(revision=p['revision'], repairs=plan['repairs']) if plan else None,
                        retryable_tasks=[t['id'] for t in tasks if t['status'] in ('failed', 'interrupted') and t['review'] != 'waived' and t['turns'] < 12])

    def list_invocations(self, ctx, app, limit=100):
        with self.db.connect() as c:
            self.access.member(c, ctx.workspace_id, self.actor(ctx))
            rows = c.execute('SELECT id FROM projects WHERE workspace_id=%s AND application_id=%s ORDER BY created DESC,id DESC LIMIT %s', (ctx.workspace_id, app, limit)).fetchall()
            return [self.invocation(ctx, app, r['id'], c) for r in rows]

    def page_invocations(self, ctx, app, limit=25, cursor=None):
        import base64
        with self.db.connect() as c:
            self.access.member(c,ctx.workspace_id,self.actor(ctx));self.application(ctx,app,c)
            args=[ctx.workspace_id,app];where=''
            if cursor:
                try:
                    created,key=loads(base64.urlsafe_b64decode(cursor.encode()).decode())
                    if type(created) not in (int,float) or not isinstance(key,str):raise ValueError()
                except Exception:raise ValueError('无效分页游标') from None
                where=' AND (p.created<%s OR (p.created=%s AND p.id<%s))';args.extend([created,created,key])
            rows=[dict(r) for r in c.execute('SELECT p.id,p.name,p.status,p.revision,p.created,i.operation FROM projects p JOIN invocations i ON i.id=p.id WHERE p.workspace_id=%s AND p.application_id=%s'+where+' ORDER BY p.created DESC,p.id DESC LIMIT %s',(*args,limit+1))]
            items=rows[:limit]
            next_cursor=base64.urlsafe_b64encode(dumps([items[-1]['created'],items[-1]['id']]).encode()).decode() if len(rows)>limit else None
            return dict(items=items,next_cursor=next_cursor)

    def janitor(self):
        with self.db.connect() as c:
            known={row[0] for row in c.execute('SELECT path FROM artifacts')}
        return self.artifacts.reconcile(known)

    def document(self, ctx, app, key, data=None):
        with self.db.tx() if data is not None else self.db.connect() as c:
            self.access.member(c, ctx.workspace_id, self.actor(ctx));self.application(ctx, app, c)
            row = c.execute('SELECT * FROM workspace_documents WHERE workspace_id=%s AND application_id=%s AND key=%s', (ctx.workspace_id, app, key)).fetchone()
            revision = row['revision'] if row else 0
            if data is not None:
                if revision != data['revision']:
                    raise DomainError('revision_conflict', '文档已修改，请保留输入并重新加载')
                raw = dumps(data['value'])
                if len(raw.encode()) > 1_800_000:
                    raise ValueError('文档超过大小上限')
                c.execute('INSERT INTO workspace_documents VALUES(%s,%s,%s,%s,%s) ON CONFLICT(workspace_id,application_id,key) DO UPDATE SET revision=excluded.revision,value=excluded.value', (ctx.workspace_id, app, key, revision + 1, raw))
                return dict(revision=revision + 1, value=data['value'])
            return dict(revision=revision, value=loads(row['value']) if row else None)

    def command(self, ctx, project, command):
        command = Command.model_validate(command)
        fingerprint = sha256(dumps(command.model_dump()).encode()).hexdigest()
        with self.db.tx() as c:
            p = self.scope(c, ctx, self.row(c, 'projects', project))
            old = c.execute('SELECT * FROM commands WHERE project_id=%s AND subject=%s AND operation_key=%s', (project, ctx.subject, command.key)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise DomainError('idempotency_conflict', '命令幂等键已用于不同内容')
                return loads(old['result'])
            if command.revision is not None and command.revision != p['revision']:
                raise DomainError('revision_conflict', '计划版本已变化')
            action = command.action
            self.policies.authorize(c, ctx, 'ask' if action == 'reply' else action, p)
            if action == 'cancel':
                c.execute("UPDATE projects SET status='cancelled' WHERE id=%s", (project,))
                c.execute("UPDATE tasks SET status='cancelled' WHERE project_id=%s AND status IN ('queued','running','waiting')", (project,))
                c.execute("UPDATE attempts SET status='cancelled',finished=%s WHERE task_id IN (SELECT id FROM tasks WHERE project_id=%s) AND status='running'", (now(), project))
            elif action == 'retry':
                task = self.row(c, 'tasks', command.payload['task_id'])
                if task['project_id'] != project or task['status'] not in ('failed', 'interrupted', 'partial'):
                    raise DomainError('retry_conflict', '任务状态不允许重试')
                if task['turns']>=12:
                    raise DomainError('turn_limit','工作项已达到总执行次数上限')
                self.access.wallets.require_available(c, p['created_by'])
                self.invalidate(c, project, {task['id']}, '显式重试，保留历史回执与原付款人')
            elif action == 'reply':
                q = self.row(c, 'messages', command.payload['question_id'])
                if q['project_id'] != project or q['to_task'] is not None or q['kind'] != 'question':
                    raise Forbidden()
                answer = command.payload['content'].strip()
                if not answer or len(answer) > 8000:
                    raise ValueError('回复不能为空或过长')
                if c.execute('SELECT 1 FROM messages WHERE reply_to=%s', (q['id'],)).fetchone():
                    raise DomainError('answer_conflict', '问题已回答')
                c.execute('INSERT INTO messages(id,project_id,from_task,to_task,kind,content,reply_to,created) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)', (ident(), project, None, q['from_task'], 'reply', answer, q['id'], now()))
            elif action == 'review':
                data = ReviewInput.model_validate(command.payload)
                artifact = self.row(c, 'artifacts', data.artifact_id)
                task = self.row(c, 'tasks', artifact['task_id'])
                if task['project_id'] != project:
                    raise Forbidden()
                self.review(c, ctx, task, data.artifact_id, data.verdict, data.feedback, data.sha256)
            elif action == 'amend':
                self.amend(c, ctx, p, command.payload)
            else:
                raise ValueError('此命令必须由对应执行尝试提交')
            self.reconcile(c, project)
            value = dict(ok=True, revision=self.row(c, 'projects', project)['revision'])
            c.execute('INSERT INTO commands VALUES(%s,%s,%s,%s,%s,%s,%s)', (ident(), project, ctx.subject, command.key, fingerprint, dumps(value), now()))
            self.db.event(c, project, None, 'command.applied', action=action, subject=ctx.subject, operation_key=command.key)
            return value

    def amend(self, c, ctx, project, payload):
        reason = payload.get('reason','')
        if not isinstance(reason,str):raise ValueError('计划变更理由必须是文本')
        reason=reason.strip()
        changes = payload.get('changes', [])
        additions = payload.get('add_nodes', [])
        if not isinstance(reason,str) or not reason or len(reason)>2000 or not isinstance(changes,list) or not isinstance(additions,list) or not (changes or additions) or len(changes) + len(additions) > 20:
            raise ValueError('计划变更需要理由和有界改动')
        for change in changes:
            if not isinstance(change,dict):raise ValueError('节点变更必须是对象')
            for field,limit in [('description',12000),('expected',3000)]:
                if field in change and (not isinstance(change[field],str) or not 1<=len(change[field])<=limit):raise ValueError('变更字段无效：'+field)
            if 'cancel' in change and type(change['cancel']) is not bool:raise ValueError('cancel 必须是布尔值')
            if 'depends_on' in change and (not isinstance(change['depends_on'],list) or len(change['depends_on'])>20 or any(not isinstance(d,str) for d in change['depends_on'])):raise ValueError('依赖清单无效')
            task = self.row(c, 'tasks', change['task_id'])
            if task['project_id'] != project['id'] or task['status'] not in ('queued', 'waiting') or task['turns']:
                raise DomainError('amendment_conflict', '首版只允许调整尚未开始的工作')
            if set(change) - {'task_id', 'description', 'expected', 'depends_on', 'cancel'}:
                raise ValueError('计划变更包含未支持的字段')
            if change.get('cancel'):
                definition = loads(task['definition']);definition['active'] = False
                c.execute("UPDATE tasks SET status='cancelled',definition=%s WHERE id=%s", (dumps(definition),task['id']))
            else:
                c.execute('UPDATE tasks SET description=%s,expected=%s WHERE id=%s', (change.get('description', task['description']), change.get('expected', task['expected']), task['id']))
                if 'depends_on' in change:
                    c.execute('DELETE FROM dependencies WHERE task_id=%s', (task['id'],))
                    for dep in change['depends_on']:
                        target = self.row(c, 'tasks', dep)
                        if target['project_id'] != project['id'] or dep == task['id']:
                            raise ValueError('依赖越界或自循环')
                        c.execute('INSERT INTO dependencies VALUES(%s,%s)', (task['id'], dep))
        if additions:
            from ..kernel.models import PlanNode
            nodes = [PlanNode.model_validate(value).model_dump() for value in additions]
            aliases = {r['node_key']:r['id'] for r in c.execute('SELECT id,node_key FROM tasks WHERE project_id=%s AND node_key IS NOT NULL',(project['id'],))}
            root = c.execute('SELECT id FROM tasks WHERE project_id=%s AND parent_id IS NULL',(project['id'],)).fetchone()[0]
            new_aliases = {n['key'] for n in nodes}
            if len(new_aliases) != len(nodes) or new_aliases & aliases.keys():
                raise ValueError('新增节点别名重复')
            for node in nodes:
                for schema in node['outputs'].values():check_schema(schema)
                encode_files([dict(path=name,content='') for name in node['outputs']] or [dict(path='group.json',content='{}')])
                if (node['staff'] or node['participants']) and (not node['outputs'] or (node['staff'] and 'image' in node['staff']['capabilities'])):
                    raise ValueError('新增原生阶段须声明文本 JSON 交付')
                if node['gate']:
                    schema = next(iter(node['outputs'].values()),{})
                    if not (node['staff'] or node['participants']) or len(node['outputs']) != 1 or 'passed' not in schema.get('required',[]) or schema.get('properties',{}).get('passed',{}).get('type') != 'boolean':
                        raise ValueError('新增审校门须声明 passed 布尔字段')
            pending = list(nodes)
            while pending:
                progress = False
                for node in pending[:]:
                    if node['parent'] and node['parent'] not in aliases:continue
                    parent = self.row(c,'tasks',aliases.get(node['parent'],root))
                    if parent['kind'] == 'work' or parent['status'] in TERMINAL or parent['status'] == 'running':
                        raise DomainError('amendment_conflict','只能向尚未结束或开始执行的协调节点增加工作')
                    unit = node['unit_id'] or parent['unit_id'];member = None
                    if node['staff']:
                        data = dict(node['staff']);data.pop('reason',None)
                        member = self.agent(ctx,unit,data,project=project['id'],c=c)['id']
                    aliases[node['key']] = self.new_task(c,ctx,project,unit,node['description'],node['expected'],parent['id'],member,definition=node)
                    pending.remove(node);progress = True
                if not progress:raise ValueError('新增节点 parent 引用不存在或循环')
            for node in nodes:
                for dep in node['depends_on']:
                    if dep not in aliases:raise ValueError('新增节点依赖不存在')
                    c.execute('INSERT INTO dependencies VALUES(%s,%s)',(aliases[node['key']],aliases[dep]))
        planrow=c.execute('SELECT definition FROM execution_plans WHERE project_id=%s',(project['id'],)).fetchone()
        if planrow:
            plan=loads(planrow[0]);tasks=[dict(r) for r in c.execute('SELECT * FROM tasks WHERE project_id=%s',(project['id'],))]
            by_id={t['id']:t for t in tasks};nodes=[]
            for task in tasks:
                if not task['node_key'] or not loads(task['definition']).get('active',True):continue
                node=loads(task['definition']);node.pop('active',None)
                node.update(description=task['description'],expected=task['expected'],parent=by_id[task['parent_id']]['node_key'] if task['parent_id'] else None)
                node['depends_on']=[by_id[r[0]]['node_key'] for r in c.execute('SELECT depends_on FROM dependencies WHERE task_id=%s',(task['id'],))]
                nodes.append(node)
            plan['nodes']=nodes
            checked,effective=validate_plan(plan)
            aliases={t['node_key']:t['id'] for t in tasks if t['node_key']}
            for key,deps in effective.items():
                for dep in deps:c.execute('INSERT INTO dependencies VALUES(%s,%s) ON CONFLICT DO NOTHING',(aliases[key],aliases[dep]))
            c.execute('UPDATE execution_plans SET definition=%s WHERE project_id=%s',(dumps(checked),project['id']))
        cancelled = {r[0] for r in c.execute("SELECT id FROM tasks WHERE project_id=%s AND (definition::jsonb ->> 'active')='false'",(project['id'],))}
        for row in c.execute('SELECT task_id,depends_on FROM dependencies WHERE task_id IN (SELECT id FROM tasks WHERE project_id=%s)',(project['id'],)):
            if row['task_id'] not in cancelled and row['depends_on'] in cancelled:
                raise ValueError('取消节点前须显式调整仍引用它的依赖')
        edges = {r[0]: set() for r in c.execute('SELECT id FROM tasks WHERE project_id=%s', (project['id'],))}
        for row in c.execute('SELECT task_id,depends_on FROM dependencies WHERE task_id IN (SELECT id FROM tasks WHERE project_id=%s)', (project['id'],)):
            edges[row[0]].add(row[1])
        for row in c.execute('SELECT id,parent_id FROM tasks WHERE project_id=%s', (project['id'],)):
            if row['parent_id']:
                edges[row['parent_id']].add(row['id'])
        done = set()
        while len(done) < len(edges):
            ready = {k for k, deps in edges.items() if k not in done and deps <= done}
            if not ready:
                raise ValueError('计划变更形成依赖循环')
            done |= ready
        revision = project['revision'] + 1
        c.execute('UPDATE projects SET revision=%s WHERE id=%s', (revision, project['id']))
        c.execute('INSERT INTO plan_revisions VALUES(%s,%s,%s,%s,%s,%s)', (project['id'], revision, dumps(payload), reason, ctx.subject, now()))
