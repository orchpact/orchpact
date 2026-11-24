"""Candidate verification, immutable evidence and acceptance binding."""
from copy import deepcopy
from threading import Lock

from ..adapters.database import dumps, loads, now
from ..kernel.errors import DomainError
from ..kernel.verification import canonical_digest, verifier_result
from ..provider_http import write_receipt
from .tools import ToolCallError, digest


class VerificationService:
    def __init__(self,service):
        self.s=service;self.db=service.db
        self._step_lock = Lock()

    @staticmethod
    def required(task):
        return bool(loads(task['definition']).get('verification'))

    def contract_digest(self,c,task):
        node=loads(task['definition'])
        if node.get('root'):
            row=c.execute('SELECT binding FROM invocations WHERE id=%s',(task['project_id'],)).fetchone()
            operation=loads(row[0])
            contract={key:operation.get(key) for key in ('output_file','output_schema','output_kind','artifact_output','verification')}
        else:
            contract={key:node.get(key) for key in ('kind','outputs','artifact_outputs','rules','exports','verification')}
        return canonical_digest(contract)

    def candidates(self):
        with self.db.connect() as c:
            rows=[dict(row) for row in c.execute("SELECT * FROM tasks WHERE status='running' AND artifact_id IS NOT NULL ORDER BY created,id")]
        return [task for task in rows if self.required(task)]

    def context(self,task):
        with self.db.connect() as c:
            ctx=self.s.context(c,task)
            artifact=self.s.row(c,'artifacts',task['artifact_id'])
        ctx['deliveries']=[self.s.artifacts.excerpts(artifact)]+ctx['deliveries']
        return ctx,artifact

    def arguments(self,task,artifact,contract,policy):
        return dict(artifact=dict(artifact_id=artifact['id'],sha256=artifact['sha256'],generation=task['generation']),
            contract_digest=contract,parameters=deepcopy(policy['parameters']))

    def key(self,task,artifact,contract,policy):
        return 'verify-'+digest({'task_id':task['id'],'artifact_id':artifact['id'],'contract_digest':contract,
            'tool_ref':policy['tool_ref'],'parameters':policy['parameters']})

    def existing_evidence(self,c,task,contract):
        row=c.execute('SELECT * FROM verification_evidence WHERE task_id=%s AND artifact_id=%s AND generation=%s AND contract_digest=%s ORDER BY created,id LIMIT 1',
            (task['id'],task['artifact_id'],task['generation'],contract)).fetchone()
        return dict(row) if row else None

    def evaluate(self,raw,policy,metadata):
        result=verifier_result(raw)
        identifiers={item['check_id'] for item in result['checks']}
        if not identifiers<=set(metadata['checks']) or any(item['status']=='INCONCLUSIVE' for item in result['checks']):
            status='INCONCLUSIVE'
        elif not set(policy['required_checks'])<=identifiers:
            status='INCONCLUSIVE'
        elif result['status']=='PASS' and all(item['status']=='PASS' for item in result['checks']):
            status='PASS'
        elif result['status']!='INCONCLUSIVE' and any(item['status']=='FAIL' for item in result['checks']):
            status='FAIL'
        else:status='INCONCLUSIVE'
        return status,result['checks'],result

    def record(self,task,artifact,contract,policy,call_id,status,checks,raw):
        frozen=loads(task['snapshot'])['tools'][policy['tool_ref']]
        binding=frozen['binding'];provider=frozen['provider']
        bundle=dict(id=call_id,project_id=task['project_id'],task_id=task['id'],attempt_id=task['attempt_id'],
            artifact_id=artifact['id'],artifact_sha256=artifact['sha256'],generation=task['generation'],contract_digest=contract,
            logical_tool=policy['tool_ref'],provider_id=provider['id'],interface_version=frozen['definition']['version'],
            implementation_version=binding['implementation_version'],binding_digest=digest(binding),status=status,checks=checks,
            result=raw,created=now())
        path=self.s.settings.data/'verification-evidence'/task['attempt_id']/(call_id+'.json')
        if path.exists():
            previous=loads(path.read_text())
            if {key:value for key,value in previous.items() if key not in ('created','bundle_digest')}!={key:value for key,value in bundle.items() if key!='created'}:
                raise DomainError('evidence_conflict','验证证据文件与现有记录不一致')
            bundle=previous
        else:
            bundle['bundle_digest']=canonical_digest(bundle)
            write_receipt(path,bundle)
        fields={key:bundle[key] for key in ('id','project_id','task_id','attempt_id','artifact_id','artifact_sha256','generation',
            'contract_digest','logical_tool','provider_id','interface_version','implementation_version','binding_digest','status','bundle_digest','created')}
        with self.db.tx() as c:
            current=self.s.current(c,task['id'],task['attempt_id'])
            actual=self.s.row(c,'artifacts',current['artifact_id'])
            if actual['id']!=artifact['id'] or actual['sha256']!=artifact['sha256'] or current['generation']!=task['generation'] or self.contract_digest(c,current)!=contract:
                raise DomainError('artifact_version_conflict','验证候选已被替换，拒绝提交旧证据')
            c.execute('''INSERT INTO verification_evidence(id,project_id,task_id,attempt_id,artifact_id,artifact_sha256,generation,
                contract_digest,logical_tool,provider_id,interface_version,implementation_version,binding_digest,tool_call_id,status,checks,bundle_digest,created)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING''',
                (*list(fields.values())[:13],call_id,status,dumps(checks),bundle['bundle_digest'],bundle['created']))
            evidence=self.existing_evidence(c,current,contract)
            if not evidence or evidence['tool_call_id']!=call_id or evidence['bundle_digest']!=bundle['bundle_digest']:
                raise DomainError('evidence_conflict','验证证据与当前候选不一致')
            return evidence

    def finalize(self,task,contract,call_id):
        with self.db.tx() as c:
            current=self.s.current(c,task['id'],task['attempt_id'])
            actual=self.s.row(c,'artifacts',current['artifact_id'])
            evidence=self.existing_evidence(c,current,contract)
            if not evidence or evidence['tool_call_id']!=call_id or actual['sha256']!=evidence['artifact_sha256'] or current['generation']!=evidence['generation']:
                raise DomainError('artifact_version_conflict','验收证据不绑定当前 Artifact 与 generation')
            status=evidence['status']
            if status=='PASS':
                c.execute("UPDATE tasks SET status='completed',error=NULL,review='pending' WHERE id=%s",(current['id'],))
                c.execute("UPDATE attempts SET status='completed',finished=%s WHERE id=%s",(now(),current['attempt_id']))
            else:
                error='可信验证未通过：'+status
                c.execute("UPDATE tasks SET status='failed',error=%s WHERE id=%s",(error,current['id']))
                c.execute("UPDATE attempts SET status='failed',error=%s,finished=%s WHERE id=%s",(error,now(),current['attempt_id']))
            self.db.event(c,current['project_id'],current['id'],'verification.finished',evidence_id=call_id,status=status)
            if status=='FAIL':self.s.repair(c,current['project_id'],current,loads(current['definition']),loads(evidence['checks']),'可信验证未通过')
            self.s.reconcile(c,current['project_id'])

    def process(self,task,replay_only=False):
        with self.db.connect() as c:
            contract=self.contract_digest(c,task)
            evidence=self.existing_evidence(c,task,contract)
            policy=loads(task['definition'])['verification']
            if replay_only and not evidence:
                artifact = self.s.row(c, 'artifacts', task['artifact_id'])
        if evidence:
            self.finalize(task,contract,evidence['tool_call_id']);return True
        if replay_only:
            snapshot = loads(task['snapshot'])
        else:
            ctx, artifact = self.context(task)
            snapshot = ctx['snapshot']
        slot=policy['tool_ref']
        frozen=snapshot.get('tools',{}).get(slot)
        if not frozen or frozen['definition']['effect']!='read' or frozen['binding'].get('verifier',{}).get('trust_level')!='trusted':
            if replay_only:return False
            return self.fail_candidate(ctx,'verification_untrusted')
        arguments=self.arguments(task,artifact,contract,policy);key=self.key(task,artifact,contract,policy)
        call_id=None
        if replay_only:
            with self.db.connect() as c:
                call=c.execute("""SELECT * FROM tool_invocations WHERE project_id=%s AND task_id=%s AND attempt_id=%s AND logical_tool=%s
                    AND idempotency_key=%s AND status='completed' AND args_digest=%s AND binding_digest=%s""",
                    (task['project_id'],task['id'],task['attempt_id'],slot,key,digest(arguments),digest(frozen['binding']))).fetchone()
            if not call:return False
            try:
                raw=self.s.tools.read_completed_result(call)
            except ToolCallError:
                return False
            call_id=call['id']
        else:
            with self.db.connect() as c:
                unknown=c.execute("SELECT id FROM tool_invocations WHERE task_id=%s AND logical_tool=%s AND status='unknown' AND idempotency_key LIKE 'verify-%%' LIMIT 1",
                    (task['id'],slot)).fetchone()
            if unknown:return self.fail_candidate(ctx,'verification_unknown')
        try:
            if not replay_only:
                result=self.s.tools.invoke(ctx,slot,arguments,key)
                call_id=result['tool_call_id'];raw=result['result']
            status,checks,raw=self.evaluate(raw,policy,frozen['binding']['verifier'])
        except ToolCallError as exc:
            if not exc.tool_call_id:
                if exc.code == 'service_stopping':
                    return False
                return self.fail_candidate(ctx,exc.code)
            call_id=exc.tool_call_id;status='INCONCLUSIVE'
            checks=[dict(check_id='verification_execution',status='INCONCLUSIVE',code=exc.code,path=None,field_path=None,message='Verifier 执行未产生可信结果',repair='')]
            raw=dict(error_code=exc.code)
        except (ValueError, TypeError) as exc:
            if call_id is None:
                if isinstance(exc,TypeError):raise
                if isinstance(exc,DomainError) and exc.code=='attempt_expired':raise
                return self.fail_candidate(ctx,'verification_preflight_rejected')
            status='INCONCLUSIVE';code=type(exc).__name__
            checks=[dict(check_id='verification_execution',status='INCONCLUSIVE',code=code,path=None,field_path=None,message='Verifier 输出未通过语义校验',repair='')]
            raw=dict(error_code=code)
        evidence=self.record(task,artifact,contract,policy,call_id,status,checks,raw)
        self.finalize(task,contract,evidence['tool_call_id'])
        return True

    def fail_candidate(self,ctx,code):
        task=ctx['task']
        self.s.fail(ctx['execution_context'],task['id'],task['attempt_id'],'可信验证未完成：'+code)
        return True

    def step(self):
        # Another worker can claim ordinary work while this candidate is being verified.
        if not self._step_lock.acquire(blocking=False):
            return False
        try:
            candidates=self.candidates()
            return self.process(candidates[0]) if candidates else False
        except DomainError as exc:
            if exc.code!='attempt_expired':raise
            return False
        finally:
            self._step_lock.release()

    def recover(self):
        return sum(self.process(task,replay_only=True) for task in self.candidates())

    def assert_accepted(self,c,task,artifact):
        if not self.required(task):return
        contract=self.contract_digest(c,task)
        evidence=self.existing_evidence(c,task,contract)
        if not evidence or evidence['status']!='PASS' or evidence['artifact_sha256']!=artifact['sha256']:
            raise DomainError('verification_required','当前 Artifact 缺少绑定契约与 generation 的 PASS 证据')

    @staticmethod
    def public(row):
        value={key:row[key] for key in ('id','project_id','task_id','attempt_id','artifact_id','artifact_sha256','generation',
            'contract_digest','logical_tool','provider_id','interface_version','implementation_version','binding_digest','tool_call_id','status','bundle_digest','created')}
        value['checks']=loads(row['checks'])
        return value
