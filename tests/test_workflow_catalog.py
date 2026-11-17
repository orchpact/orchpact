"""One immutable asset, separately signed applications, pins and migrations."""
import copy
from dataclasses import replace
from pathlib import Path
import json
import unittest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from orchpact.application_signing import SigningCredentials
from orchpact.adapters.providers import Providers
from orchpact.api import create_app
from orchpact.kernel.errors import Forbidden
from orchpact.interfaces.surface import product_path
from test_workflows import definition
from runtime_fixtures import KernelFixture

class CatalogTests(KernelFixture):
    def setUp(self):
        super().setUp()
        self.settings.profiles['alternate']=dict(self.settings.profiles['text'],id='alternate')
        self.value=definition();self.value['id']='shared-evidence'
        response=self.client.post('/api/v1/workflows',json=self.value)
        self.assertEqual(response.status_code,200,response.text);self.asset=response.json()

    def bind(self,app='consumer-a',workflow_revision=1,expected=0,**extra):
        return self.client.post('/api/v1/apps/'+app+'/binding',json=dict(name=app,workflow_id=self.value['id'],workflow_revision=workflow_revision,expected_revision=expected,**extra))

    def install(self,app,model='text',revision=1,expected=0):
        return self.client.post('/api/v1/apps/'+app+'/install?binding_revision='+str(revision)+'&expected_revision='+str(expected),json={'bindings':{'models':{'reasoning':model}}})

    def credentials(self,app):
        private=Ed25519PrivateKey.generate()
        public=private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        response=self.client.post('/api/v1/apps/'+app+'/keys',json=dict(label='isolated test',public_key=public,user_id=self.user,workspace_id=self.workspace))
        self.assertEqual(response.status_code,201,response.text)
        return SigningCredentials(app,response.json()['id'],private)

    def signed(self,key,method,path,value=None,idempotency=''):
        body=json.dumps(value).encode() if value is not None else b''
        headers=key.headers(method,path,'testserver',body,idempotency_key=idempotency)
        headers['Content-Type']='application/json'
        if idempotency:headers['Idempotency-Key']=idempotency
        return TestClient(self.app).request(method,path,content=body,headers=headers)

    def invoke_signed(self,key,name):
        response=self.signed(key,'POST','/api/v1/apps/'+key.app_id+'/runs',{'operation':'generate','input':{},'context':{}},name)
        self.assertEqual(response.status_code,201,response.text);return response.json()

    def test_publish_creates_no_application_and_versions_never_follow_latest(self):
        self.assertEqual(self.client.get('/api/v1/apps').json(),[])
        self.bind().raise_for_status()
        updated=copy.deepcopy(self.value);updated['version']='1.1.0';updated['units'][0]['purpose']='Updated purpose'
        self.client.post('/api/v1/workflows?revision=1',json=updated).raise_for_status()
        first=self.client.get('/api/v1/workflows/shared-evidence?revision=1').json()
        self.assertEqual(first,self.asset)
        app=self.client.get('/api/v1/apps/consumer-a').json()
        self.assertEqual(app['workflow']['revision'],1)
        self.assertEqual(app['manifest'],self.asset['definition'])
        self.assertEqual(len(self.client.get('/api/v1/workflows').json()),1)
        self.assertEqual(self.client.post('/api/v1/workflows?revision=1',json=updated).status_code,409)
        self.assertEqual(self.client.get('/api/v1/workflows/shared-evidence?revision=999').status_code,404)


    def test_concurrent_publication_and_binding_updates_have_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        updated=copy.deepcopy(self.value);updated['version']='1.1.0'
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda _:self.client.post('/api/v1/workflows?revision=1',json=updated).status_code,range(2)))
        self.assertEqual(sorted(responses),[200,409])
        self.bind().raise_for_status()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda _:self.bind(workflow_revision=2,expected=1).status_code,range(2)))
        self.assertEqual(sorted(responses),[200,409])
        with self.s.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM workflow_versions').fetchone()[0],2)
            self.assertEqual(c.execute('SELECT count(*) FROM application_bindings').fetchone()[0],2)

    def test_two_signed_apps_share_asset_but_isolate_runs_files_requests_and_charges(self):
        keys={}
        for app,model in [('consumer-a','text'),('consumer-b','alternate')]:
            self.bind(app).raise_for_status();self.install(app,model).raise_for_status();keys[app]=self.credentials(app)
        runs=[self.invoke_signed(keys[app],'same-business-key') for app in keys]
        self.assertNotEqual(runs[0]['id'],runs[1]['id'])
        for run in runs:
            self.assertEqual(run['workflow']['id'],self.value['id']);self.assertEqual(run['workflow']['sha256'],self.asset['sha256'])
        providers=Providers(self.s)
        for _ in runs:
            ctx=self.s.claim();request=providers.reserve(ctx,'model',ctx['snapshot']['profile']['id'],token_bound=1000)
            providers.record(request,'completed',{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150})
            self.finish(ctx)
        completed=[self.client.get('/api/v1/apps/'+app+'/runs/'+run['id']).json() for app,run in zip(keys,runs)]
        self.assertTrue(all(run['status']=='completed' and run['result']=={'value':1} for run in completed))
        self.assertEqual(completed[0]['workflow']['bindings']['models']['reasoning'],'text')
        self.assertEqual(completed[1]['workflow']['bindings']['models']['reasoning'],'alternate')
        key=keys['consumer-a'];other=completed[1]
        for path in ['/api/v1/apps/consumer-b/runs/'+other['id'],'/api/v1/runs/'+other['id']+'/usage','/api/v1/artifacts/'+other['artifact_id']+'/download','/api/v1/requests/'+other['requests'][0]['id']]:
            self.assertIn(self.signed(key,'GET',path).status_code,(403,404),path)
        self.assertEqual(self.signed(key,'POST','/api/v1/workflows',self.value).status_code,403)
        self.assertEqual(self.signed(key,'GET','/api/v1/workflows/shared-evidence').status_code,403)
        self.assertEqual(self.signed(key,'POST','/api/v1/apps/consumer-a/binding',dict(name='a',workflow_id='shared-evidence',workflow_revision=1,expected_revision=1)).status_code,403)
        self.assertEqual(self.invoke_signed(keys['consumer-a'],'same-business-key')['id'],runs[0]['id'])
        with self.s.db.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM credit_charges WHERE state='settled'").fetchone()[0],2)
            self.assertEqual(c.execute("SELECT count(*) FROM credit_ledger WHERE kind='settlement'").fetchone()[0],2)
        self.assertNotEqual(keys['consumer-a'].key_id,keys['consumer-b'].key_id)

    def test_rebinding_and_workspace_upgrade_are_separate_old_runs_stay_frozen(self):
        self.bind(require_signature=False).raise_for_status();self.install('consumer-a').raise_for_status()
        first=self.client.post('/api/v1/apps/consumer-a/runs',json={'operation':'generate','input':{},'context':{}},headers={'Idempotency-Key':'before'}).json()
        updated=copy.deepcopy(self.value);updated['version']='1.1.0'
        self.client.post('/api/v1/workflows?revision=1',json=updated).raise_for_status()
        self.bind(workflow_revision=2,expected=1).raise_for_status()
        before=self.client.post('/api/v1/apps/consumer-a/runs',json={'operation':'generate','input':{},'context':{}},headers={'Idempotency-Key':'still-installed'}).json()
        self.assertEqual(before['workflow']['revision'],1)
        self.assertEqual(self.install('consumer-a',revision=1,expected=1).status_code,409)
        self.install('consumer-a','alternate',revision=2,expected=1).raise_for_status()
        last=self.client.post('/api/v1/apps/consumer-a/runs',json={'operation':'generate','input':{},'context':{}},headers={'Idempotency-Key':'after'}).json()
        self.assertEqual(last['workflow']['revision'],2)
        self.assertEqual(self.client.get('/api/v1/apps/consumer-a/runs/'+first['id']).json()['workflow'],first['workflow'])
        self.assertEqual(self.bind(workflow_revision=1,expected=1).status_code,409)
        self.assertEqual(self.client.post('/api/v1/organization-packages?revision=2',json=dict(self.value,id='consumer-a')).status_code,409)
        with self.s.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM application_bindings WHERE application_id=%s',('consumer-a',)).fetchone()[0],2)

    def test_invalid_pins_and_unauthorized_catalog_mutation_leave_no_state(self):
        before=self.client.get('/api/v1/apps').json()
        self.assertEqual(self.bind(workflow_revision=99).status_code,404)
        self.assertEqual(self.client.get('/api/v1/apps').json(),before)
        with self.assertRaises(Forbidden):self.s.workflows.register(replace(self.ctx,admin=False),self.value)
        with self.assertRaises(Forbidden):self.s.workflows.bind(replace(self.ctx,admin=False),'a',{})
        self.bind(require_signature=False,enabled=False).raise_for_status()
        self.bind(expected=1).raise_for_status()
        app=self.client.get('/api/v1/apps/consumer-a').json()
        self.assertFalse(app['require_signature']);self.assertFalse(app['enabled'])
        with self.s.db.connect() as c:
            events=[json.loads(row['detail']) for row in c.execute("SELECT detail FROM events WHERE kind='application.bound' ORDER BY id")]
        self.assertEqual(len(events),2)
        self.assertEqual(events[-1]['actor'],self.ctx.subject)
        self.assertFalse(events[-1]['require_signature']);self.assertFalse(events[-1]['previous_require_signature'])
        self.assertFalse(events[-1]['enabled']);self.assertFalse(events[-1]['previous_enabled'])
        for path in ('/api/v1/workflows','/api/v1/workflows/shared-evidence','/api/v1/apps/consumer-a/binding','/api/v1/apps/consumer-a/preview','/api/v1/apps/consumer-a/definition'):
            for method in ('GET','POST'):self.assertFalse(product_path('consumer-a',path,method))

    def test_application_aliases_are_exact_even_when_ids_contain_sql_wildcards(self):
        for app in ('consumer_one','consumerXone'):
            self.bind(app).raise_for_status();self.install(app).raise_for_status()
        with self.s.db.connect() as c:
            before=[tuple(r.values()) for r in c.execute("SELECT id,alias FROM units WHERE workspace_id=%s AND split_part(alias,':',1)=%s ORDER BY id",(self.workspace,'consumerXone'))]
        self.bind('consumer_one',expected=1).raise_for_status()
        self.install('consumer_one','alternate',revision=2,expected=1).raise_for_status()
        with self.s.db.connect() as c:
            after=[tuple(r.values()) for r in c.execute("SELECT id,alias FROM units WHERE workspace_id=%s AND split_part(alias,':',1)=%s ORDER BY id",(self.workspace,'consumerXone'))]
        self.assertEqual(len(before),2);self.assertEqual(after,before)
