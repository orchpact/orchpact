"""Deterministic tool nodes without models, CrewAI objects or external network."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from account_fixtures import create_user
from orchpact.adapters.database import loads
from orchpact.adapters.tools import ToolRegistry, ToolTransportError
from orchpact.kernel.workflows import definition_digest, validate_definition
from orchpact.services.tools import ToolRuntime
from runtime_fixtures import KernelFixture, SCHEMA
from test_tool_runtime import SyntheticTool
from test_workflows import definition


def node(key='render',slot='renderer',path='result.json',**changes):
    value=dict(key=key,kind='tool',unit='entry',description='Run the declared tool',expected=path,
        tool_ref=slot,arguments={'value':{'$ref':'input','path':'value'}},outputs={path:deepcopy(SCHEMA)})
    value.update(changes)
    return value


def envelope(outputs,relaxed=False):
    return {'type':'object','additionalProperties':False,'required':['files'],'properties':{'files':{
        'type':'object','additionalProperties':False,'required':list(outputs),
        'properties':{path:{} if relaxed else deepcopy(SCHEMA) for path in outputs}}}}


def workflow(nodes=None,effect='read',retry_policy='never',relaxed=False):
    value=definition();nodes=nodes or [node()]
    tools={}
    for item in nodes:
        if 'tool_ref' not in item:continue
        slot=item['tool_ref']
        outputs=item['outputs']
        tools[slot]={'interface':'document.render','version':'1','description':'Synthetic deterministic renderer',
            'input_schema':{'type':'object','additionalProperties':False,'required':['value'],'properties':{
                'value':{'type':'integer'},'artifact_id':{'type':'string'}}},
            'output_schema':envelope(outputs,relaxed),'effect':effect,'retry_policy':retry_policy,
            'timeout_ms':20000,'max_output_bytes':100000}
        if effect=='create_artifact':tools[slot]['artifact_contract']={'media_types':['application/json'],'max_files':1,'max_bytes':100000}
    value['resources']={'tools':tools}
    for unit in value['units']:unit.pop('model',None)
    operation=value['operations']['generate']
    operation.update(input_schema=deepcopy(SCHEMA),tool_limit=8,execution_plan={'nodes':nodes,'result_node':nodes[-1]['key'],'max_repairs':2})
    return value


class ToolNodeDefinitionTests(unittest.TestCase):
    def test_legacy_digest_and_explicit_node_kinds(self):
        legacy=definition();checked=validate_definition(legacy)
        self.assertEqual(definition_digest(checked),definition_digest(validate_definition(legacy)))
        self.assertNotIn('kind',checked['operations']['generate']['execution_plan']['nodes'][0])
        agent=definition();agent['operations']['generate']['execution_plan']['nodes'][0]['kind']='agent'
        self.assertEqual(validate_definition(agent)['operations']['generate']['execution_plan']['nodes'][0]['kind'],'agent')

    def test_invalid_tool_declarations_and_references_are_rejected(self):
        cases=[]
        for field in ('artifact_outputs','verification'):
            value=workflow();value['operations']['generate']['execution_plan']['nodes'][0][field]={};cases.append(value)
        value=workflow();value['operations']['generate']['execution_plan']['nodes'][0]['staff']={'name':'bad','role':'bad','reason':'bad'};cases.append(value)
        value=workflow(effect='create_artifact');cases.append(value)
        value=workflow();value['operations']['generate']['execution_plan']['nodes'][0]['arguments']={'value':'bad'};cases.append(value)
        value=workflow();value['operations']['generate']['execution_plan']['nodes'][0]['arguments']={'value':{'$ref':'input','path':'missing'}};cases.append(value)
        value=workflow();value['operations']['generate']['input_schema']['properties']['value']={'type':'string'};cases.append(value)
        value=workflow();value['resources']['tools']['renderer']['output_schema']={'type':'object'};cases.append(value)
        value=workflow([node(),node('other','other')]);value['operations']['generate']['execution_plan']['nodes'][1]['depends_on']=[]
        value['operations']['generate']['execution_plan']['nodes'][1]['arguments']={'value':{'$ref':'json','node':'render','file':'result.json','path':'value'}};cases.append(value)
        value=workflow();value['operations']['generate']['output_kind']='artifact';cases.append(value)
        for index,invalid in enumerate(cases):
            with self.subTest(case=index),self.assertRaises(ValueError):validate_definition(invalid)

    def test_approval_is_only_for_deterministic_external_writes(self):
        value=workflow()
        self.assertNotIn('requires_approval',validate_definition(value)['resources']['tools']['renderer'])
        value['resources']['tools']['renderer']['requires_approval']=True
        with self.assertRaisesRegex(ValueError,'external_write'):
            validate_definition(value)
        value['resources']['tools']['renderer']['effect']='external_write'
        self.assertTrue(validate_definition(value)['resources']['tools']['renderer']['requires_approval'])
        value['agents']=[dict(unit='entry',name='Writer',role='Writer',capabilities=['text','json','tool:renderer'])]
        with self.assertRaisesRegex(ValueError,'确定性工具节点'):
            validate_definition(value)


class ToolNodeTests(KernelFixture):
    def setUp(self):
        super().setUp()
        self.configure_tool()
        self.settings.profiles.clear()
        self.seen=[]
        def render(context):
            self.seen.append(deepcopy(context.arguments))
            return {'files':{path:{'value':context.arguments['value']} for path in context.definition['output_schema']['properties']['files']['required']}}
        self.synthetic=SyntheticTool(result=render)
        self.s.tools=ToolRuntime(self.s,ToolRegistry(builtins={'document.render':self.synthetic}))

    def config(self):
        return self.client.get('/api/v1/admin/configuration').json()

    def configure_tool(self):
        body=self.config();text=next(p for p in body['value']['providers'] if p['id']=='text')
        if self.config()['revision']==0:text['secret']='text-fixture-secret'
        provider=deepcopy(text)
        provider.update(id='tool:renderer',category='tool',label='Renderer',adapter='builtin',base_url='',model='',
            search=False,thinking_disabled=False,image_options={},pricing={'unit':'request','points':2,'unit_size':1},
            tool_interface='document.render',tool_interface_version='1',implementation_version='1.0.0',
            tool_policy={'workspace_ids':[self.workspace],'application_ids':['fixture'],'require_credential':True,'max_concurrency':1},secret='tool-secret')
        body['value']['providers']=[p for p in body['value']['providers'] if p['id']!=provider['id']]+[provider]
        body.pop('restart_required',None)
        response=self.client.put('/api/v1/admin/configuration',json=body)
        self.assertEqual(response.status_code,200,response.text)

    def bindings(self,value):
        return {'models':{},'tools':{slot:{'endpoint_ref':'tool:renderer','implementation_version':'1.0.0'} for slot in value['resources']['tools']}}

    def install(self,value):
        self.register(value)
        response=self.client.post('/api/v1/apps/fixture/install',json={'bindings':self.bindings(value)})
        self.assertEqual(response.status_code,200,response.text)

    def invoke(self,key='tool-node-run',value=7):
        response=self.client.post('/api/v1/apps/fixture/runs',json={'operation':'generate','input':{'value':value},'context':{}},headers={'Idempotency-Key':key})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def retry(self,task_id,key):
        response=self.client.post('/api/v1/work-items/'+task_id+'/retry',headers={'Idempotency-Key':key})
        self.assertEqual(response.status_code,200,response.text)

    def request_approval(self):
        value=workflow(effect='external_write')
        value['resources']['tools']['renderer']['requires_approval']=True
        body=self.config()
        provider=next(p for p in body['value']['providers'] if p['id']=='tool:renderer')
        provider['tool_policy'].update(application_ids=[],workflow_ids=[value['id']])
        body.pop('restart_required',None)
        self.client.put('/api/v1/admin/configuration',json=body).raise_for_status()
        self.client.post('/api/v1/workflows',json=value).raise_for_status()
        endpoint='/api/v1/workflows/'+value['id']
        self.client.post(endpoint+'/install',json=dict(workflow_revision=1,expected_revision=0,
            bindings=self.bindings(value))).raise_for_status()
        run=self.client.post(endpoint+'/runs',json=dict(operation='generate',input={'value':7}),
            headers={'Idempotency-Key':'approve-write'}).raise_for_status().json()
        self.assertTrue(self.app.state.runner.step())
        path='/api/v1/runs/'+run['id']
        view=self.client.get(path).raise_for_status().json()
        approval,=view['tool_approvals']
        self.assertEqual(approval['status'],'pending')
        self.assertNotIn('arguments',approval)
        self.assertNotIn('approval_digest',approval)
        self.assertEqual(view['status'],'waiting')
        self.assertEqual(view['requests'],[])
        self.assertEqual(view['tool_invocations'],[])
        self.assertEqual(self.synthetic.calls,0)
        return path,approval

    def test_approved_write_waits_without_dispatch_and_resumes_once(self):
        path,approval=self.request_approval()
        detail=path+'/tool-approvals/'+approval['id']
        self.app.state.runner.recover()
        self.assertFalse(self.app.state.runner.step())
        pending=self.client.get(detail).raise_for_status().json()
        self.assertEqual(pending['arguments'],{'value':7})
        body=dict(decision='approve',approval_digest=pending['approval_digest'])
        self.assertEqual(self.client.post(detail+'/decision',json=dict(body,approval_digest='0'*64)).status_code,409)
        other=TestClient(self.app)
        try:
            user=create_user(self.s,other,'approval-member','fixture-password-1234')
            self.assertEqual(other.get(detail).status_code,404)
            with self.s.db.tx() as c:
                c.execute('INSERT INTO workspace_members VALUES(%s,%s)',(self.workspace,user))
            other.headers['X-Workspace-Id']=self.workspace
            self.assertEqual(other.get(detail).status_code,403)
            self.assertEqual(other.post(detail+'/decision',json=body).status_code,403)
        finally:
            other.close()
        self.assertEqual(self.client.post(detail+'/decision',json=body).raise_for_status().json()['status'],'approved')
        self.assertEqual(self.client.post(detail+'/decision',json=body).raise_for_status().json()['status'],'approved')
        self.assertEqual(self.synthetic.calls,0)
        self.app.state.runner.recover()
        self.assertTrue(self.app.state.runner.step())
        self.assertFalse(self.app.state.runner.step())
        view=self.client.get(path).raise_for_status().json()
        self.assertEqual(view['result'],{'value':7})
        self.assertEqual(self.synthetic.calls,1)
        call,=view['tool_invocations']
        approved,=view['tool_approvals']
        self.assertEqual(approved['status'],'used')
        self.assertEqual(approved['tool_call_id'],call['id'])
        self.assertEqual(len(view['requests']),1)

    def test_cancelled_or_rejected_write_cannot_dispatch(self):
        for decision in ('reject','cancel'):
            with self.subTest(decision=decision):
                # Each run uses a different idempotency key against the same installed revision.
                if decision=='reject':
                    path,approval=self.request_approval()
                else:
                    run=self.client.post('/api/v1/workflows/fixture/runs',json=dict(operation='generate',
                        input={'value':7}),headers={'Idempotency-Key':'cancel-write'}).raise_for_status().json()
                    self.assertTrue(self.app.state.runner.step())
                    path='/api/v1/runs/'+run['id']
                    approval,=self.client.get(path).raise_for_status().json()['tool_approvals']
                detail=path+'/tool-approvals/'+approval['id']
                url=detail+'/decision'
                body=dict(approval_digest=self.client.get(detail).raise_for_status().json()['approval_digest'])
                if decision=='cancel':
                    self.client.post(path+'/cancel').raise_for_status()
                else:
                    rejected=self.client.post(url,json=dict(body,decision='reject')).raise_for_status().json()
                    self.assertEqual(rejected['status'],'rejected')
                self.assertEqual(self.client.post(url,json=dict(body,decision='approve')).status_code,409)
                self.assertFalse(self.app.state.runner.step())
                self.assertEqual(self.synthetic.calls,0)
                self.assertEqual(self.client.get(path).raise_for_status().json()['requests'],[])

    def test_approval_cannot_cross_rework_or_replay_an_unknown_write(self):
        path,old=self.request_approval()
        previous=path+'/tool-approvals/'+old['id']
        old_digest=self.client.get(previous).raise_for_status().json()['approval_digest']
        with self.s.db.tx() as c:
            self.s.invalidate(c,old['project_id'],{old['task_id']},'synthetic upstream rework')
        self.assertTrue(self.app.state.runner.step())
        stale,pending=self.client.get(path).raise_for_status().json()['tool_approvals']
        self.assertEqual(stale['status'],'stale')
        self.assertEqual(pending['generation'],old['generation']+1)
        self.assertEqual(self.client.post(previous+'/decision',
            json=dict(decision='approve',approval_digest=old_digest)).status_code,409)
        detail=path+'/tool-approvals/'+pending['id']
        current_digest=self.client.get(detail).raise_for_status().json()['approval_digest']
        self.client.post(detail+'/decision',json=dict(decision='approve',approval_digest=current_digest)).raise_for_status()
        self.synthetic.error=ToolTransportError('timeout')
        self.assertTrue(self.app.state.runner.step())
        self.app.state.runner.recover()
        self.retry(old['task_id'],'approved-unknown-retry')
        self.assertTrue(self.app.state.runner.step())
        view=self.client.get(path).raise_for_status().json()
        self.assertEqual(self.synthetic.calls,1)
        self.assertEqual([a['status'] for a in view['tool_approvals']],['stale','used'])
        self.assertEqual([call['status'] for call in view['tool_invocations']],['unknown'])
        self.assertEqual(view['requests'][0]['accounting']['state'],'held')

    def test_pure_tool_workflow_needs_no_model_and_delivers_multiple_json_files(self):
        value=workflow([node(outputs={'result.json':SCHEMA,'audit.json':SCHEMA})])
        self.register(value)
        preview=self.client.post('/api/v1/apps/fixture/preview',json={'bindings':self.bindings(value)})
        self.assertEqual(preview.status_code,200,preview.text)
        response=self.client.post('/api/v1/apps/fixture/install',json={'bindings':self.bindings(value)})
        self.assertEqual(response.status_code,200,response.text)
        preview=self.client.post('/api/v1/apps/fixture/preview',json={'bindings':self.bindings(value)})
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(preview.json()['units'][0]['model_id'],'')
        run=self.invoke()
        self.assertTrue(self.app.state.runner.step())
        view=self.view(run)
        self.assertEqual(view['status'],'completed');self.assertEqual(view['result'],{'value':7})
        self.assertEqual(self.synthetic.calls,1);self.assertEqual(view['crew_runs'],[]);self.assertEqual(view['agent_steps'],[])
        self.assertEqual([request['kind'] for request in view['requests']],['tool'])
        with self.s.db.connect() as c:
            attempts=[row[0] for row in c.execute('SELECT model_id FROM attempts')]
            snapshots=[loads(row[0]) for row in c.execute('SELECT snapshot FROM tasks')]
            self.assertEqual(c.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0],2)
        self.assertEqual(set(attempts),{'deterministic'})
        tool=next(snapshot for snapshot in snapshots if snapshot['executor']['kind']=='tool')
        self.assertEqual(tool['profile']['id'],'deterministic');self.assertEqual(set(tool['tools']),{'renderer'})
        self.assertNotIn('tool-secret',str(view))
        exported=self.client.get('/api/v1/apps/fixture/definition')
        self.assertEqual(exported.status_code,200,exported.text)
        self.assertEqual(exported.json()['resources']['models'],{})
        self.assertNotIn('model',exported.json()['units'][0]);self.assertNotIn('model_id',exported.text)

    def test_artifact_json_mapping_and_group_aggregation(self):
        source=node('source','source','source.json')
        group=dict(key='bundle',kind='group',unit='entry',description='Aggregate',expected='source.json',exports=['source.json'])
        final=node('final','final','result.json',depends_on=['bundle'],arguments={
            'value':{'$ref':'json','node':'bundle','file':'source.json','path':'value'},
            'artifact_id':{'$ref':'artifact','node':'bundle','file':'source.json'}})
        # The source is a child of the explicit organization group.
        source['parent']='bundle'
        value=workflow([source,group,final]);self.install(value);run=self.invoke()
        self.assertTrue(self.app.state.runner.step());self.assertTrue(self.app.state.runner.step())
        view=self.view(run)
        self.assertEqual(view['status'],'completed');self.assertEqual(view['result'],{'value':7})
        self.assertEqual(self.synthetic.calls,2);self.assertNotIn('artifact_id',self.seen[0])
        self.assertIn('artifact_id',self.seen[1])
        artifact_ids={task['artifact_id'] for task in view['tasks'] if task['artifact_id']}
        self.assertIn(self.seen[1]['artifact_id'],artifact_ids)
        with self.s.db.connect() as c:
            snapshots=[loads(row[0]) for row in c.execute("SELECT snapshot FROM tasks WHERE kind='work'")]
        self.assertEqual({next(iter(snapshot['tools'])) for snapshot in snapshots},{'source','final'})

    def test_tool_success_does_not_bypass_delivery_contract(self):
        value=workflow(relaxed=True);self.install(value)
        self.synthetic.result={'files':{'result.json':{'value':'not-an-integer'}}}
        run=self.invoke();self.assertTrue(self.app.state.runner.step())
        view=self.view(run)
        self.assertEqual(view['status'],'partial');self.assertIsNone(view['result'])
        self.assertEqual(view['tool_invocations'][0]['status'],'completed')
        with self.s.db.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0],0)

    def test_plan_amendment_uses_frozen_tool_definitions(self):
        extra=node('extra','extra','extra.json',depends_on=['render']);extra.pop('unit')
        value=workflow([node(),extra]);value['operations']['generate']['execution_plan'].update(nodes=[node()],result_node='render')
        self.install(value);run=self.invoke()
        invalid=dict(key='bad-add',action='amend',revision=1,payload={'reason':'unknown tool','add_nodes':[node('bad','missing','bad.json')]})
        response=self.client.post('/api/v1/runs/'+run['id']+'/commands',json=invalid)
        self.assertEqual(response.status_code,409,response.text);self.assertEqual(self.view(run)['revision'],1)
        command=dict(key='add-tool',action='amend',revision=1,payload={'reason':'add deterministic check','add_nodes':[extra]})
        response=self.client.post('/api/v1/runs/'+run['id']+'/commands',json=command)
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['revision'],2)
        self.assertTrue(self.app.state.runner.step());self.assertTrue(self.app.state.runner.step())
        view=self.view(run);self.assertEqual(view['status'],'completed');self.assertEqual(self.synthetic.calls,2)
        added=next(task for task in view['tasks'] if task['node_key']=='extra')
        with self.s.db.connect() as c:snapshot=loads(c.execute('SELECT snapshot FROM tasks WHERE id=%s',(added['id'],)).fetchone()[0])
        self.assertEqual(set(snapshot['tools']),{'extra'});self.assertEqual(snapshot['executor']['kind'],'tool')

    def test_completed_result_replays_without_second_call_or_charge(self):
        value=workflow();self.install(value);run=self.invoke()
        with patch.object(self.s,'complete_work',side_effect=RuntimeError('synthetic post-tool interruption')):
            self.assertTrue(self.app.state.runner.step())
        self.assertEqual(self.synthetic.calls,1)
        task=next(task for task in self.view(run)['tasks'] if task['node_key']=='render')
        self.retry(task['id'],'completed-node-retry')
        self.assertTrue(self.app.state.runner.step())
        view=self.view(run)
        self.assertEqual(view['status'],'completed');self.assertEqual(self.synthetic.calls,1)
        self.assertEqual(len(view['tool_invocations']),1);self.assertEqual(len(view['requests']),1)
        self.assertEqual(view['usage']['tool_points'],2)

    def test_unknown_external_write_never_replays(self):
        value=workflow(effect='external_write');self.install(value)
        self.synthetic.error=ToolTransportError('timeout')
        run=self.invoke();self.assertTrue(self.app.state.runner.step())
        task=next(task for task in self.view(run)['tasks'] if task['node_key']=='render')
        self.app.state.runner.recover();self.retry(task['id'],'unknown-node-retry')
        self.assertTrue(self.app.state.runner.step())
        view=self.view(run)
        self.assertEqual(self.synthetic.calls,1);self.assertEqual(len(view['tool_invocations']),1)
        self.assertEqual(view['tool_invocations'][0]['status'],'unknown')
        self.assertEqual(view['requests'][0]['accounting']['state'],'held')
        self.assertIsNone(view['result'])

    def test_only_safe_read_failure_can_be_explicitly_retried(self):
        value=workflow(retry_policy='safe_only');self.install(value)
        self.synthetic.error=ToolTransportError('definite_failure',definite=True)
        run=self.invoke();self.assertTrue(self.app.state.runner.step())
        task=next(task for task in self.view(run)['tasks'] if task['node_key']=='render')
        self.synthetic.error=None;self.retry(task['id'],'safe-node-retry')
        self.assertTrue(self.app.state.runner.step())
        view=self.view(run)
        self.assertEqual(view['status'],'completed');self.assertEqual(self.synthetic.calls,2)
        self.assertEqual(len(view['tool_invocations']),2);self.assertEqual(view['usage']['tool_points'],4)

    def test_non_retryable_read_failure_does_not_gain_a_new_call(self):
        value=workflow();self.install(value)
        self.synthetic.error=ToolTransportError('definite_failure',definite=True)
        run=self.invoke();self.assertTrue(self.app.state.runner.step())
        task=next(task for task in self.view(run)['tasks'] if task['node_key']=='render')
        self.synthetic.error=None;self.retry(task['id'],'unsafe-node-retry')
        self.assertTrue(self.app.state.runner.step())
        view=self.view(run)
        self.assertNotEqual(view['status'],'completed');self.assertEqual(self.synthetic.calls,1)
        self.assertEqual(len(view['tool_invocations']),1);self.assertEqual(len(view['requests']),1)

    def test_downstream_waits_for_current_upstream_acceptance(self):
        second=node('second','second','result.json',depends_on=['render'])
        value=workflow([node(path='source.json'),second]);self.install(value)
        response=self.client.put('/api/v1/policies/platform',json={'revision':0,'policy':{'independent_review':True}})
        self.assertEqual(response.status_code,200,response.text)
        run=self.invoke();self.assertTrue(self.app.state.runner.step())
        self.assertIsNone(self.s.claim());self.assertEqual(self.synthetic.calls,1)
        first=next(task for task in self.view(run)['tasks'] if task['node_key']=='render')
        command={'key':'accept-source','action':'review','payload':{'artifact_id':first['artifact_id'],'sha256':self.artifact_digest(first['artifact_id']),'verdict':'accept','feedback':'independent fixture acceptance'}}
        response=self.client.post('/api/v1/runs/'+run['id']+'/commands',json=command)
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(self.app.state.runner.step());self.assertEqual(self.synthetic.calls,2)

    def artifact_digest(self,artifact_id):
        with self.s.db.connect() as c:return self.s.row(c,'artifacts',artifact_id)['sha256']


if __name__=='__main__':unittest.main()
