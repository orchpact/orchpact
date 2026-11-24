"""Authorized tool effects, durable idempotency, receipts and reconciliation."""
from copy import deepcopy
from hashlib import sha256
import json

from ..adapters.configuration import tool_configuration_digest, tool_policy
from ..adapters.database import dumps, ident, loads, now
from ..adapters.tools import AdapterContext, ToolRegistry, ToolTransportError
from ..adapters.providers import Providers
from ..kernel.contracts import validate
from ..kernel.errors import Forbidden
from ..provider_http import write_receipt
from .tool_approvals import AWAITING_APPROVAL


def digest(value):
    raw = json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
    return sha256(raw).hexdigest()


class ToolCallError(RuntimeError):
    def __init__(self, code, tool_call_id=None, status=None):
        super().__init__(code)
        self.code = code
        self.tool_call_id = tool_call_id
        self.status = status


class ToolRuntime:
    def __init__(self, service, registry=None):
        self.service = service
        self.db = service.db
        self.providers = Providers(service)
        self.registry = registry or ToolRegistry()

    def _path(self, call):
        return self.service.settings.data/'tool-invocations'/call['attempt_id']/(call['id']+'.json')

    def _receipt(self, call):
        try:
            value = loads(self._path(call).read_text())
        except (OSError,ValueError,TypeError,RecursionError):
            return None
        if not isinstance(value,dict) or value.get('tool_call_id') != call['id'] or value.get('request_id') != call['request_id']:
            return None
        if value.get('args_digest') != call['args_digest'] or value.get('binding_digest') != call['binding_digest']:
            return None
        if value.get('status') not in ('completed','failed','unknown'):
            return None
        if value['status'] == 'completed':
            try:
                if value.get('result_digest') != digest(value.get('result')):
                    return None
            except (ValueError, RecursionError):
                return None
        return value

    def _write_receipt(self, call, status, result, error_code):
        path = self._path(call)
        if path.exists():
            raise ToolCallError('receipt_already_exists',call['id'],'unknown')
        value = dict(tool_call_id=call['id'],request_id=call['request_id'],status=status,
            args_digest=call['args_digest'],binding_digest=call['binding_digest'],
            arguments=call['_arguments'],result=result,result_digest=digest(result) if status == 'completed' else None,
            error_code=error_code,created=now())
        write_receipt(path,value)
        return value

    def read_completed_result(self, call):
        receipt = self._receipt(call)
        if (call['status'] != 'completed' or not receipt or receipt['status'] != 'completed'
                or 'result' not in receipt or receipt['result_digest'] != call['result_digest']):
            raise ToolCallError('result_receipt_missing', call['id'], 'unknown')
        return receipt['result']

    @staticmethod
    def public(call, *, replay=False):
        value = {key:call.get(key) for key in ('id','project_id','task_id','attempt_id','logical_tool','interface_version',
            'implementation_version','binding_digest','provider_id','adapter','effect','status','idempotency_key',
            'args_digest','result_digest','request_id','error_code','recovery','created','finished')}
        if replay:
            value['idempotent_replay'] = True
        return value

    def _check_artifacts(self, value, allowed, project):
        if isinstance(value,dict):
            if 'artifact_id' in value:
                artifact_id = value['artifact_id']
                if not isinstance(artifact_id,str) or artifact_id not in allowed:
                    raise Forbidden('工具只能引用当前工作项已授权的产物')
                with self.db.connect() as c:
                    artifact = self.service.row(c,'artifacts',artifact_id)
                if artifact['project_id'] != project:
                    raise Forbidden('工具产物不属于当前项目')
            for item in value.values():
                self._check_artifacts(item,allowed,project)
        elif isinstance(value,list):
            for item in value:
                self._check_artifacts(item,allowed,project)

    def _adapter_context(self, ctx, slot, arguments, call_id, key):
        frozen = ctx['snapshot'].get('tools',{}).get(slot)
        if not frozen:
            raise Forbidden('当前工作项未授权此工具槽位')
        definition = frozen['definition'];binding = frozen['binding'];provider = deepcopy(frozen['provider'])
        provider['tool_policy'] = tool_policy(provider['tool_policy'])
        if binding['configuration_digest'] != tool_configuration_digest(provider):
            raise ToolCallError('snapshot_binding_mismatch')
        current = self.service.configuration.record(provider['id'])
        if not current or not current.get('enabled'):
            raise ToolCallError('provider_disabled')
        if binding['type'] == 'mcp':
            raise ToolCallError('mcp_not_supported')
        if provider.get('pricing') is None:
            raise ToolCallError('pricing_unknown')
        policy = provider['tool_policy']
        if policy['workspace_ids'] and ctx['project']['workspace_id'] not in policy['workspace_ids']:
            raise Forbidden('工具未授权此 Workspace')
        if policy['application_ids'] and ctx['project']['application_id'] not in policy['application_ids']:
            raise Forbidden('工具未授权此应用')
        workflow=ctx.get('application',{}).get('workflow_id')
        if policy.get('workflow_ids') and workflow not in policy['workflow_ids']:
            raise Forbidden('工具未授权此工作流')
        credential = self.service.configuration.secret(provider)
        if policy['require_credential'] and not credential:
            raise ToolCallError('credential_unavailable')
        return AdapterContext(call_id,key,deepcopy(definition),deepcopy(binding),provider,deepcopy(arguments),credential)

    def _validate_result(self, definition, value, ctx):
        if len(dumps(value).encode()) > definition['max_output_bytes']:
            raise ToolCallError('response_too_large')
        validate(value,definition['output_schema'])
        allowed = {item['artifact_id'] for item in ctx.get('deliveries',[])}
        self._check_artifacts(value,allowed,ctx['project']['id'])

    def _apply(self, call, receipt, recovery='none'):
        status = receipt['status']
        with self.db.tx() as c:
            current = c.execute('SELECT * FROM tool_invocations WHERE id=%s',(call['id'],)).fetchone()
            if not current:
                raise ToolCallError('tool_call_not_found',call['id'])
            if current['status'] == 'completed' and status == 'completed':
                return
            result_digest = receipt.get('result_digest') if status == 'completed' else None
            c.execute('UPDATE tool_invocations SET status=%s,result_digest=%s,error_code=%s,recovery=%s,finished=%s WHERE id=%s',
                (status,result_digest,receipt.get('error_code'),recovery,now(),call['id']))
            if call.get('request_id'):
                if status == 'unknown':
                    c.execute('UPDATE requests SET status=%s,usage=%s,response_id=%s,error=%s WHERE id=%s',
                        (status,dumps({'requests':1}),call['id'],receipt.get('error_code'),call['request_id']))
                else:
                    self.providers.record(call['request_id'],status,{'requests':1},call['id'],receipt.get('error_code'),c=c)
            self.db.event(c,call['project_id'],call['task_id'],'tool.invocation_finished',tool_call_id=call['id'],status=status,recovery=recovery)

    def _finish(self, call, status, result=None, error_code=None, recovery='none'):
        receipt = self._write_receipt(call,status,result,error_code)
        self._apply(call,receipt,recovery)

    def invoke(self, ctx, slot, arguments, idempotency_key, *, tool_call_id=None):
        if not isinstance(slot,str) or not isinstance(arguments,dict) or not isinstance(idempotency_key,str) or not 1 <= len(idempotency_key) <= 128 or not idempotency_key.isascii() or not all(char.isalnum() or char in '_.:-' for char in idempotency_key):
            raise ValueError('工具调用需要槽位、JSON 对象参数和1到128字符安全幂等键')
        if tool_call_id is not None and (not isinstance(tool_call_id,str) or not 1 <= len(tool_call_id) <= 128):
            raise ValueError('工具调用标识无效')
        if len(dumps(arguments).encode()) > 64_000:
            raise ValueError('工具参数超过大小限制')
        if getattr(self.service,'stop_event',None) and self.service.stop_event.is_set():
            raise ToolCallError('service_stopping')
        adapter_context = self._adapter_context(ctx,slot,arguments,tool_call_id or ident(),idempotency_key)
        definition = adapter_context.definition
        if definition.get('requires_approval'):
            node = ctx.get('planned_node') or {}
            if ctx.get('_crew_member_key') or node.get('kind') != 'tool' or node.get('tool_ref') != slot:
                raise Forbidden('需要事前审批的工具只允许确定性工具节点调用')
        validate(arguments,definition['input_schema'])
        allowed = {item['artifact_id'] for item in ctx.get('deliveries',[])}
        self._check_artifacts(arguments,allowed,ctx['project']['id'])
        args_digest = digest(arguments);binding_digest = digest(adapter_context.binding)
        with self.db.tx() as c:
            task = self.service.current(c,ctx['task']['id'],ctx['task']['attempt_id'])
            project = self.service.row(c,'projects',task['project_id'])
            started = c.execute('SELECT started FROM attempts WHERE id=%s',(task['attempt_id'],)).fetchone()[0]
            if now() - started >= ctx['snapshot']['executor']['limits']['wall_seconds']:
                raise ValueError('Agent 执行时间达到上限，未提交交付')
            self.service.policies.authorize(c,ctx['execution_context'],'call',project,'tool:'+slot)
            for permission in definition['required_permissions']:
                self.service.policies.authorize(c,ctx['execution_context'],'call',project,permission)
            old = c.execute('SELECT * FROM tool_invocations WHERE project_id=%s AND logical_tool=%s AND idempotency_key=%s',
                (project['id'],slot,idempotency_key)).fetchone()
            if old:
                if old['args_digest'] != args_digest or old['binding_digest'] != binding_digest:
                    raise ToolCallError('idempotency_conflict',old['id'],old['status'])
                if old['status'] == 'completed':
                    receipt = self._receipt(old)
                    if not receipt:
                        raise ToolCallError('result_receipt_missing', old['id'], 'unknown')
                    replay = self.public(old,replay=True)
                    replay['tool_call_id'] = replay.pop('id')
                    return dict(replay,result=receipt['result'])
                raise ToolCallError('tool_call_not_replayable',old['id'],old['status'])
            approval_id = None
            if definition.get('requires_approval'):
                approval_id, approved = self.service.tool_approvals.prepare(c, task, slot, arguments, adapter_context, binding_digest)
                if not approved:
                    return dict(status=AWAITING_APPROVAL, approval_id=approval_id)
            count = c.execute('SELECT COUNT(*) FROM tool_invocations WHERE attempt_id=%s',(task['attempt_id'],)).fetchone()[0]
            if count >= ctx['snapshot']['executor']['limits']['tool_steps']:
                raise ValueError('Agent tool 步骤达到上限，未提交交付')
            active = c.execute("SELECT COUNT(*) FROM tool_invocations WHERE provider_id=%s AND status='started'",(adapter_context.provider['id'],)).fetchone()[0]
            if active >= adapter_context.provider['tool_policy']['max_concurrency']:
                raise ValueError('工具提供者并发调用达到上限')
            call_id = adapter_context.tool_call_id
            request_id = self.providers.reserve(ctx,'tool','tool:'+slot,provider=adapter_context.provider,c=c)
            c.execute('''INSERT INTO tool_invocations(id,project_id,task_id,attempt_id,workspace_id,application_id,
                logical_tool,interface_version,implementation_version,binding_digest,provider_id,adapter,effect,status,
                idempotency_key,args_digest,request_id,created) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'started',%s,%s,%s,%s)''',
                (call_id,project['id'],task['id'],task['attempt_id'],project['workspace_id'],project['application_id'],slot,
                 definition['version'],adapter_context.binding['implementation_version'],binding_digest,adapter_context.provider['id'],
                 adapter_context.binding['type'],definition['effect'],idempotency_key,args_digest,request_id,now()))
            self.db.event(c,project['id'],task['id'],'tool.invocation_started',tool_call_id=call_id,logical_tool=slot,request_id=request_id)
            if approval_id:
                c.execute('UPDATE tool_approvals SET tool_call_id=%s WHERE id=%s', (call_id, approval_id))
        call = dict(id=call_id,project_id=project['id'],task_id=task['id'],attempt_id=task['attempt_id'],
            request_id=request_id,args_digest=args_digest,binding_digest=binding_digest,_arguments=deepcopy(arguments))
        adapter = self.registry.resolve(adapter_context.binding)
        try:
            result = adapter.invoke(adapter_context)
            self._validate_result(definition,result,ctx)
        except Exception as exc:
            if isinstance(exc,(KeyboardInterrupt,SystemExit)):
                raise
            unknown = definition['effect'] == 'external_write' and not getattr(exc,'definite',False)
            status = 'unknown' if unknown else 'failed'
            code = exc.code if isinstance(exc,ToolCallError) else getattr(exc,'code',None) or type(exc).__name__
            self._finish(call,status,error_code=code)
            raise ToolCallError(code,call_id,status) from None
        self._finish(call,'completed',result)
        return dict(tool_call_id=call_id,status='completed',result=result,request_id=request_id,idempotent_replay=False)

    def invoke_node(self, ctx, slot, arguments):
        base='node-'+digest({'task_id':ctx['task']['id'],'slot':slot,'arguments':arguments})
        args_digest=digest(arguments)
        with self.db.connect() as c:
            rows=[dict(row) for row in c.execute('SELECT * FROM tool_invocations WHERE task_id=%s AND logical_tool=%s AND args_digest=%s ORDER BY created,id',
                (ctx['task']['id'],slot,args_digest))]
        completed=next((row for row in reversed(rows) if row['status']=='completed'),None)
        if completed:return self.invoke(ctx,slot,arguments,completed['idempotency_key'])
        blocked=next((row for row in reversed(rows) if row['status'] in ('started','unknown')),None)
        if blocked:return self.invoke(ctx,slot,arguments,blocked['idempotency_key'])
        key=base
        if rows:
            previous=rows[-1]
            definition=ctx['snapshot']['tools'][slot]['definition']
            safe=definition['effect']=='read' and definition['retry_policy']=='safe_only' and ctx['task']['generation']>1
            if not safe:return self.invoke(ctx,slot,arguments,previous['idempotency_key'])
            key=base+'-g'+str(ctx['task']['generation'])
        return self.invoke(ctx,slot,arguments,key)

    def recover(self):
        recovered = 0
        with self.db.connect() as c:
            rows = [dict(row) for row in c.execute("SELECT * FROM tool_invocations WHERE status IN ('started','unknown') ORDER BY created,id")]
        for call in rows:
            receipt = self._receipt(call)
            if receipt and receipt['status'] in ('completed','failed'):
                self._apply(call,receipt,'receipt')
                recovered += 1
            elif call['status'] == 'started':
                with self.db.tx() as c:
                    current = c.execute('SELECT status FROM tool_invocations WHERE id=%s',(call['id'],)).fetchone()
                    if not current or current['status'] != 'started':
                        continue
                    c.execute("UPDATE tool_invocations SET status='unknown',error_code='missing_receipt',finished=%s WHERE id=%s",(now(),call['id']))
                    if call['request_id']:
                        c.execute("UPDATE requests SET status='unknown',usage=%s,response_id=%s,error='missing_receipt' WHERE id=%s",
                            (dumps({'requests':1}),call['id'],call['request_id']))
                    self.db.event(c,call['project_id'],call['task_id'],'tool.invocation_unknown',tool_call_id=call['id'])
        return recovered

    def lookup(self, execution_context, tool_call_id):
        with self.db.connect() as c:
            call = dict(self.service.row(c,'tool_invocations',tool_call_id))
            project = self.service.scope(c,execution_context,self.service.row(c,'projects',call['project_id']))
            task = self.service.row(c,'tasks',call['task_id'])
        if call['status'] != 'unknown':
            return self.public(call)
        snapshot = loads(task['snapshot']);frozen = snapshot.get('tools',{}).get(call['logical_tool'])
        if not frozen or digest(frozen['binding']) != call['binding_digest']:
            raise ToolCallError('snapshot_binding_mismatch',tool_call_id,'unknown')
        receipt = self._receipt(call)
        if not receipt:
            raise ToolCallError('lookup_receipt_missing',tool_call_id,'unknown')
        ctx = dict(snapshot=snapshot,project=project)
        adapter_context = self._adapter_context(ctx,call['logical_tool'],receipt['arguments'],call['id'],call['idempotency_key'])
        adapter = self.registry.resolve(adapter_context.binding)
        if not hasattr(adapter,'lookup'):
            raise ToolCallError('lookup_unsupported',tool_call_id,'unknown')
        call['_arguments'] = receipt['arguments']
        try:
            result = adapter.lookup(adapter_context)
            if result is None:
                return dict(self.public(call),lookup='inconclusive')
            self._validate_result(frozen['definition'],result,ctx)
        except Exception as exc:
            raise ToolCallError(getattr(exc,'code',None) or type(exc).__name__,tool_call_id,'unknown') from None
        evidence = dict(tool_call_id=call['id'],request_id=call['request_id'],status='completed',
            args_digest=call['args_digest'],binding_digest=call['binding_digest'],arguments=receipt['arguments'],
            result=result,result_digest=digest(result),error_code=None,created=now())
        path = self._path(call).with_name(call['id']+'.lookup-'+ident()+'.json')
        write_receipt(path,evidence)
        self._apply(call,evidence,'lookup')
        with self.db.connect() as c:
            call = dict(self.service.row(c,'tool_invocations',tool_call_id))
        return dict(self.public(call),result=result)
