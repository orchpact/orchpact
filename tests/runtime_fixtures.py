from orchpact.testing import database_url_for
"""Isolated runtime fixtures. No credential files, real providers, or runtime directories."""
import copy
from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from orchpact.api import create_app
from orchpact.config import Settings
from orchpact.interfaces.context import from_actor
from orchpact.adapters.identity import Principal
from orchpact.adapters.database import dumps

TOKEN = 'runtime-fixture-administrator-' * 3
SCHEMA = {'type':'object','additionalProperties':False,'required':['value'],'properties':{'value':{'type':'integer'}}}


def package(planned=True, review=False):
    operation = dict(unit='entry',instruction='Return the verified integer.',input_schema={},output_schema=SCHEMA,output_file='result.json',request_limit=32,search_limit=3)
    if planned:
        nodes = [dict(key='writer',unit='entry',description='Write a result',expected='result.json',staff=dict(name='Writer',role='Writer',prompt='Use provided facts',capabilities=['text','json'],reason='Write'),outputs={'result.json':SCHEMA})]
        if review:
            nodes.append(dict(key='reviewer',unit='review',description='Review current result',expected='audit.json',staff=dict(name='Reviewer',role='Reviewer',prompt='Independently check',capabilities=['text','json'],reason='Review'),outputs={'audit.json':{'type':'object','required':['passed','reviewed_artifact_ids'],'properties':{'passed':{'type':'boolean'},'reviewed_artifact_ids':{'type':'array','items':{'type':'string'}},'issues':{'type':'array'}}}},depends_on=['writer'],gate=True,repair_from=['writer'],rules=[dict(kind='current_inputs',file='audit.json',path='reviewed_artifact_ids.*')]))
        operation['execution_plan'] = dict(nodes=nodes,result_node='writer',max_repairs=2)
    return dict(id='fixture',name='Fixture',version='1.0.0',require_signature=False,units=[dict(key='entry',parent=None,name='Entry',purpose='Write facts',prompt=''),dict(key='review',parent=None,name='Review',purpose='Review facts',prompt='')],operations={'generate':operation})


class KernelFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        text = dict(id='text',label='Fixture',kind='text',model='fixture',base_url='https://example.invalid/v1',key_env='RUNTIME_FIXTURE_KEY',capabilities=['text','json'],priority=1,json_mode=True)
        self.settings = Settings(self.root,TOKEN,{'text':text},self.root/'var',workers=2,database_url=database_url_for(self.root/'var'))
        self.app = create_app(self.settings,run_worker=False)
        self.client = TestClient(self.app).__enter__()
        self.client.headers['Authorization'] = 'Bearer '+TOKEN
        self.s = self.app.state.service
        me = self.client.get('/api/v1/auth/me').json()
        self.user,self.workspace = me['user_id'],me['workspace_id']
        self.ctx = from_actor(Principal(self.user,True,self.workspace))
        self.s.access.wallets.adjust(self.user,10000,'isolated fixture','fixture-budget',self.user)

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.tmp.cleanup()

    def register(self, manifest=None):
        value=copy.deepcopy(manifest or package())
        response=self.client.post('/api/v1/organization-packages',json=value)
        self.assertEqual(response.status_code,200,response.text)
        return value

    def invoke(self, manifest=None, key='fixture-run'):
        if manifest is not False:self.register(manifest)
        response=self.client.post('/api/v1/apps/fixture/runs',json=dict(operation='generate',input={},context={}),headers={'Idempotency-Key':key})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def finish(self, ctx=None, value=1):
        ctx=ctx or self.s.claim()
        self.s.complete_work(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],'fixture result',[dict(path='result.json',content=dumps(dict(value=value)))])
        return ctx

    def view(self, run):
        response=self.client.get('/api/v1/apps/fixture/runs/'+run['id'])
        self.assertEqual(response.status_code,200,response.text)
        return response.json()
