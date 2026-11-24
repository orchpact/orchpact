"""Direct workflow installation/execution and retained legacy storage boundaries."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from fastapi.testclient import TestClient

from account_fixtures import create_user
from runtime_fixtures import KernelFixture
from test_workflows import definition
from orchpact.kernel.errors import Forbidden
from orchpact.adapters.database import loads


class CatalogTests(KernelFixture):
    def setUp(self):
        super().setUp()
        self.value=definition();self.value['id']='shared-evidence'
        self.settings.profiles['alternate']=dict(self.settings.profiles['text'],id='alternate')
        response=self.client.post('/api/v1/workflows',json=self.value)
        self.assertEqual(response.status_code,200,response.text);self.asset=response.json()

    def install(self,client=None,revision=1,expected=0,model='text'):
        return (client or self.client).post('/api/v1/workflows/shared-evidence/install',json=dict(workflow_revision=revision,expected_revision=expected,bindings={'models':{'reasoning':model}}))

    def start(self,client=None,key='same-key'):
        return (client or self.client).post('/api/v1/workflows/shared-evidence/runs',json=dict(operation='generate',input={},context={}),headers={'Idempotency-Key':key})

    def test_publish_preview_install_and_run_need_no_application(self):
        with self.s.db.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM applications').fetchone()[0],0)
        body=dict(workflow_revision=1,bindings={'models':{'reasoning':'text'}})
        preview=self.client.post('/api/v1/workflows/shared-evidence/preview',json=body)
        self.assertEqual(preview.status_code,200,preview.text)
        with self.s.db.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM applications').fetchone()[0],0)
        self.assertEqual(self.start().json()['code'],'workflow_not_installed')
        self.install().raise_for_status();run=self.start().json();self.finish()
        view=self.client.get('/api/v1/runs/'+run['id']).json()
        self.assertEqual(view['result'],{'value':1});self.assertNotIn('application_id',view)
        self.assertEqual(view['workflow']['revision'],1)
        self.assertEqual(view['workflow']['installation_revision'],1)
        self.assertNotIn('application_binding_revision',view['workflow'])
        self.assertEqual(self.start().json()['id'],run['id'])

    def test_upgrade_pins_history_and_idempotency_across_installations(self):
        self.install().raise_for_status();first=self.start().json()
        updated=deepcopy(self.value);updated['version']='1.1.0'
        self.client.post('/api/v1/workflows?revision=1',json=updated).raise_for_status()
        self.assertEqual(self.client.get('/api/v1/workflows/shared-evidence/installation').json()['workflow_revision'],1)
        self.install(revision=2,expected=1,model='alternate').raise_for_status()
        self.assertEqual(self.start().json()['id'],first['id'])
        other=self.start(key='new-key').json()
        self.assertEqual(other['workflow']['revision'],2)
        self.assertEqual(other['workflow']['bindings']['models']['reasoning'],'alternate')
        self.assertEqual(self.client.get('/api/v1/runs/'+first['id']).json()['workflow'],first['workflow'])
        changed=self.client.post('/api/v1/workflows/shared-evidence/runs',json=dict(operation='generate',input={'changed':True}),headers={'Idempotency-Key':'same-key'})
        self.assertEqual(changed.status_code,409)
        self.assertEqual(self.install(revision=2,expected=1).status_code,409)

    def test_direct_admission_requires_fixed_plan_unless_experiment_is_enabled(self):
        updated=deepcopy(self.value)
        updated['version']='1.1.0'
        updated['operations']['generate'].pop('execution_plan')
        self.client.post('/api/v1/workflows?revision=1',json=updated).raise_for_status()
        self.install(revision=2).raise_for_status()
        denied=self.start()
        self.assertEqual(denied.status_code,409)
        self.assertEqual(denied.json()['code'],'fixed_plan_required')
        with self.s.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM invocations').fetchone()[0],0)
            scope=c.execute('SELECT scope_id FROM workflow_installations').fetchone()[0]
        legacy_alias=self.client.post('/api/v1/apps/'+scope+'/runs',json=dict(operation='generate',input={}),
            headers={'Idempotency-Key':'legacy-alias'})
        self.assertEqual(legacy_alias.json()['code'],'fixed_plan_required')
        with patch.object(self.s,'settings',replace(self.settings,allow_experimental_planning=True)):
            started=self.start()
            started.raise_for_status()
        self.assertEqual(self.start().json()['id'],started.json()['id'])

    def test_two_workspaces_keep_runs_artifacts_and_payers_isolated(self):
        clients=[TestClient(self.app),TestClient(self.app)]
        try:
            users=[create_user(self.s,client,'direct-'+str(i),'fixture-password-1234') for i,client in enumerate(clients)]
            runs=[]
            for client in clients:
                self.install(client).raise_for_status();response=self.start(client);response.raise_for_status();runs.append(response.json())
            self.assertNotEqual(runs[0]['id'],runs[1]['id'])
            for _ in clients:self.finish()
            for index,(client,run) in enumerate(zip(clients,runs)):
                view=client.get('/api/v1/runs/'+run['id']).json()
                self.assertEqual(view['payer_user_id'],users[index]);self.assertEqual(view['status'],'completed')
                other=clients[1-index]
                for path in ('/api/v1/runs/'+run['id'],'/api/v1/artifacts/'+view['artifact_id']+'/download','/api/v1/runs/'+run['id']+'/events'):
                    self.assertIn(other.get(path).status_code,(403,404),path)
        finally:
            for client in clients:client.close()

    def test_concurrent_install_and_admission_have_one_result(self):
        with ThreadPoolExecutor(2) as pool:responses=list(pool.map(lambda _:self.install().status_code,range(2)))
        self.assertEqual(sorted(responses),[200,409])
        with ThreadPoolExecutor(2) as pool:responses=list(pool.map(lambda _:self.start().json(),range(2)))
        self.assertEqual(responses[0]['id'],responses[1]['id'])
        with self.s.db.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM invocations').fetchone()[0],1)

    def test_failed_install_rolls_back_and_stale_preview_is_rejected(self):
        self.assertEqual(self.install(revision=99).status_code,404)
        self.assertEqual(self.install(model='missing').status_code,409)
        with self.s.db.connect() as c:
            for table in ('applications','workflow_installations','organization_packages','units'):
                self.assertEqual(c.execute('SELECT count(*) FROM '+table).fetchone()[0],0,table)
        self.install().raise_for_status()
        self.assertEqual(self.install(expected=0).status_code,409)
        self.assertEqual(self.install(expected=1).json()['revision'],1)

    def test_members_cannot_upgrade_without_workspace_ownership(self):
        client=TestClient(self.app)
        try:
            user=create_user(self.s,client,'member-only','fixture-password-1234')
            with self.s.db.tx() as c:c.execute('INSERT INTO workspace_members VALUES(%s,%s)',(self.workspace,user))
            client.headers['X-Workspace-Id']=self.workspace
            self.assertEqual(self.install(client).status_code,403)
            self.install().raise_for_status();run=self.start(client).json()
            self.assertEqual(run['payer_user_id'],user)
        finally:client.close()

    def test_child_application_management_and_signed_headers_are_removed(self):
        for method,path in [('post','/api/v1/apps/unused/binding'),('post','/api/v1/apps/unused/keys'),('get','/api/v1/apps'),('get','/api/v1/apps/unused/keys')]:
            self.assertEqual(getattr(self.client,method)(path,**({'json':{}} if method=='post' else {})).status_code,404)
        self.assertEqual(self.client.get('/api/v1/workflows',headers={'X-Orchpact-App':'old-app'}).status_code,403)
        with self.assertRaisesRegex(ValueError,'已移除'):
            replace(self.settings,frontends={'old':{}},products={'old':'https://old.invalid'},public_origin='https://admin.invalid')

    def test_named_migration_preserves_legacy_records(self):
        self.register();self.install().raise_for_status()
        with self.s.db.connect() as c:
            self.assertTrue(c.execute("SELECT 1 FROM schema_migrations WHERE name='0004_direct_workflows'").fetchone())
            legacy=dict(c.execute("SELECT * FROM applications WHERE id='fixture'").fetchone())
        from orchpact.adapters.schema import DIRECT_WORKFLOW_SCHEMA
        self.assertFalse(self.s.db.migrate('0004_direct_workflows',DIRECT_WORKFLOW_SCHEMA))
        # Exercise an upgrade from a pre-direct-workflow schema without replacing
        # the database or losing its existing application/ledger records.
        with self.s.db.tx() as c:
            c.execute('DROP TABLE workflow_installations')
            c.execute("DELETE FROM schema_migrations WHERE name='0004_direct_workflows'")
        self.assertTrue(self.s.db.migrate('0004_direct_workflows',DIRECT_WORKFLOW_SCHEMA))
        self.assertTrue((self.s.db.backup_directory/'0004_direct_workflows.dump').is_file())
        with self.s.db.connect() as c:self.assertEqual(dict(c.execute("SELECT * FROM applications WHERE id='fixture'").fetchone()),legacy)
