"""Workflow definition portability, binding isolation, preview and frozen provenance."""
import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from orchpact.kernel.workflows import validate_definition, export_definition, definition_digest
from orchpact.interfaces.surface import product_path
from orchpact.cli import main
from runtime_fixtures import KernelFixture, package


def definition():
    value=copy.deepcopy(package());value.pop('require_signature')
    value.update(format='orchpact-workflow',version='1.0.0',resources={'models':{'reasoning':{'capabilities':['text','json']}}})
    for unit in value['units']:unit['model']='reasoning'
    value['operations']['generate']['execution_plan']['nodes'][0]['staff']['model']='reasoning'
    return value


class DefinitionTests(unittest.TestCase):

    def test_portable_fields_exclude_runtime_identities_models_and_secrets(self):
        for target,field in [('top','workspace_id'),('top','bindings'),('unit','model_id'),('staff','model_id'),('top','api_key'),('operation','payer_user_id')]:
            with self.subTest(target=target,field=field):
                value=definition();site={'top':value,'unit':value['units'][0],'staff':value['operations']['generate']['execution_plan']['nodes'][0]['staff'],'operation':value['operations']['generate']}[target]
                site[field]='fixture-only'
                with self.assertRaises(ValueError):validate_definition(value)
        value=definition();value['operations']['generate']['output_schema']['properties']['model_id']={'type':'string'}
        value['operations']['generate']['execution_plan']['nodes'][0]['outputs']['result.json']=value['operations']['generate']['output_schema']
        validate_definition(value)  # Business schema field names are not runtime bindings.

    def test_slot_references_capabilities_and_malformed_structures(self):
        for mutate in [lambda v:v['units'][0].update(model='missing'),lambda v:v['resources']['models']['reasoning'].update(capabilities=['text']),lambda v:v.update(units=None),lambda v:v['operations']['generate'].update(execution_plan=None),lambda v:v['units'][0].update(model=None)]:
            value=definition();mutate(value)
            with self.assertRaises(ValueError):validate_definition(value)

    def test_package_export_is_whitelisted_and_replaces_local_model_ids(self):
        value=package();value.update(workspace_id='private-space',api_key='fixture-only-secret')
        value['units'][0]['model_id']='private-provider'
        value['operations']['generate']['execution_plan']['nodes'][0]['staff']['model_id']='private-provider'
        before=copy.deepcopy(value);exported=export_definition(value);encoded=json.dumps(exported)
        self.assertEqual(value,before)
        for forbidden in ('private-space','fixture-only-secret','private-provider','require_signature'):
            self.assertNotIn(forbidden,encoded)
        self.assertEqual(exported['units'][0]['model'],'unit_entry')
        self.assertEqual(definition_digest(exported),definition_digest(validate_definition(exported)))

    def test_offline_cli_never_loads_instance_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'workflow.json';file.write_text(json.dumps(definition()))
            with patch('sys.argv',['orchpact','--root',folder,'workflow-check',str(file)]),patch('orchpact.cli.Settings.load',side_effect=AssertionError('No instance configuration')),patch('sys.stdout',new_callable=io.StringIO) as output:
                main()
            self.assertIn('SHA-256',output.getvalue())
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()),['workflow.json'])


class WorkflowTests(KernelFixture):
    def setUp(self):
        super().setUp()
        self.settings.profiles['alternate']=dict(self.settings.profiles['text'],id='alternate',label='Alternate')
        self.value=definition();self.register(self.value)

    def counts(self):
        with self.s.db.connect() as c:
            return {table:c.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('units','agents','organization_packages','projects','requests','events')}

    def test_definition_update_preserves_security_and_private_catalog_access(self):
        from fastapi.testclient import TestClient
        from account_fixtures import create_user
        client=TestClient(self.app)
        try:
            create_user(self.s,client,'reader','fixture-password-1234')
            for path,method,payload in [('/api/v1/apps/fixture/definition','get',None),('/api/v1/apps/fixture/preview','post',{'bindings':{'models':{'reasoning':'text'}}})]:
                response=getattr(client,method)(path,**({'json':payload} if payload else {}))
                self.assertEqual(response.status_code,403,response.text)
            self.assertEqual(client.post('/api/v1/workflows/validate',json=self.value).status_code,200)
            with self.s.db.tx() as c:c.execute('UPDATE applications SET enabled=0 WHERE id=%s',('fixture',))
            updated=copy.deepcopy(self.value);updated['version']='1.1.0'
            response=self.client.post('/api/v1/organization-packages?revision=1',json=updated)
            self.assertEqual(response.status_code,200,response.text)
            self.assertFalse(response.json()['enabled']);self.assertTrue(response.json()['require_signature'])
            bad=copy.deepcopy(updated);bad['require_signature']=False
            self.assertEqual(self.client.post('/api/v1/organization-packages?revision=2',json=bad).status_code,409)
        finally:client.close()

    def install(self,models=None,workspace=None,revision=None):
        return self.client.post('/api/v1/apps/fixture/install'+('' if revision is None else '?expected_revision='+str(revision)),json={'bindings':{'models':models or {'reasoning':'text'}}},headers={'X-Workspace-Id':workspace or self.workspace})

    def test_preview_and_validation_are_read_only(self):
        before=self.counts()
        response=self.client.post('/api/v1/apps/fixture/preview',json={'bindings':{'models':{'reasoning':'alternate'}}})
        self.assertEqual(response.status_code,200,response.text)
        value=response.json();self.assertEqual(value['action'],'install')
        self.assertEqual(value['workflow']['sha256'],definition_digest(validate_definition(self.value)))
        self.assertTrue(all(u['model_id']=='alternate' for u in value['units']))
        self.assertEqual(self.client.post('/api/v1/workflows/validate',json=self.value).status_code,200)
        self.assertEqual(self.counts(),before)

    def test_one_definition_runs_with_independent_workspace_bindings(self):
        other=self.client.post('/api/v1/workspaces',json={'name':'Independent consumer'}).json()['id']
        self.install().raise_for_status();self.install({'reasoning':'alternate'},other).raise_for_status()
        runs=[]
        for workspace in (self.workspace,other):
            response=self.client.post('/api/v1/apps/fixture/runs',json={'operation':'generate','input':{},'context':{}},headers={'X-Workspace-Id':workspace,'Idempotency-Key':'same-business-key'})
            self.assertEqual(response.status_code,201,response.text);runs.append(response.json())
        self.assertNotEqual(runs[0]['id'],runs[1]['id'])
        self.assertEqual(runs[0]['workflow']['sha256'],runs[1]['workflow']['sha256'])
        self.assertEqual(runs[0]['workflow']['bindings']['models']['reasoning'],'text')
        self.assertEqual(runs[1]['workflow']['bindings']['models']['reasoning'],'alternate')
        with self.s.db.connect() as c:
            for run,model in zip(runs,('text','alternate')):
                snapshots=[json.loads(r['snapshot']) for r in c.execute('SELECT snapshot FROM tasks WHERE project_id=%s',(run['id'],))]
                self.assertTrue(all(s['profile']['id']==model for s in snapshots))
        self.assertTrue(set(t['unit_id'] for t in runs[0]['tasks']).isdisjoint(t['unit_id'] for t in runs[1]['tasks']))

    def test_missing_invalid_or_incompatible_bindings_do_not_install(self):
        before=self.counts()
        for bindings in ({},{'models':{}},{'models':{'reasoning':'missing'}},{'models':{'reasoning':'text','extra':'text'}},{'models':{'reasoning':'text'},'user_id':'other'}):
            response=self.client.post('/api/v1/apps/fixture/install',json={'bindings':bindings})
            self.assertEqual(response.status_code,409,response.text)
            self.assertEqual(self.counts(),before)
        self.settings.profiles['incompatible']=dict(self.settings.profiles['text'],id='incompatible',capabilities=['image'],kind='image')
        self.assertEqual(self.install({'reasoning':'incompatible'}).status_code,409)
        self.assertEqual(self.counts(),before)

    def test_explicit_upgrade_preserves_existing_run_and_old_model_snapshot(self):
        self.install().raise_for_status();run=self.invoke(manifest=False)
        updated=copy.deepcopy(self.value);updated['version']='1.1.0'
        response=self.client.post('/api/v1/organization-packages?revision=1',json=updated);self.assertEqual(response.status_code,200,response.text)
        before=self.counts()
        stale=self.client.post('/api/v1/apps/fixture/install?binding_revision=1&expected_revision=1',json={'bindings':{'models':{'reasoning':'alternate'}}})
        self.assertEqual(stale.status_code,409,stale.text);self.assertEqual(self.counts(),before)
        unchanged=self.client.post('/api/v1/apps/fixture/install',json={}).json()
        self.assertEqual(unchanged['_workflow']['revision'],1)
        self.install({'reasoning':'alternate'},revision=1).raise_for_status()
        old=self.view(run);self.assertEqual(old['workflow'],run['workflow'])
        self.assertEqual(old['workflow']['version'],'1.0.0')
        self.assertEqual(self.install({'reasoning':'text'},revision=1).status_code,409)
        self.assertEqual(self.install({'reasoning':'text'}).status_code,409)
        with self.s.db.connect() as c:
            self.assertTrue(all(json.loads(r['snapshot'])['profile']['id']=='text' for r in c.execute('SELECT snapshot FROM tasks WHERE project_id=%s',(run['id'],))))

    def test_export_returns_catalog_definition_without_installation_bindings(self):
        self.install().raise_for_status();response=self.client.get('/api/v1/apps/fixture/definition')
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('bindings',response.json());self.assertNotIn('model_id',response.text)
        self.assertEqual(response.headers['X-Workflow-SHA256'],definition_digest(response.json()))
        self.assertIn('attachment',response.headers['content-disposition'])
        actor=replace(self.ctx,application_id='other')
        from orchpact.kernel.errors import NotFound
        with self.assertRaises(NotFound):self.s.workflow_definition(actor,'fixture')
        with self.assertRaises(NotFound):self.s.preview_package(actor,'fixture',{'models':{'reasoning':'text'}})
        for path in ('/api/v1/workflows','/api/v1/workflows/validate','/api/v1/apps/fixture/preview','/api/v1/apps/fixture/definition'):
            self.assertFalse(product_path('fixture',path,'POST'));self.assertFalse(product_path('fixture',path,'GET'))

    def test_independent_people_install_and_execute_without_catalog_admin_rights(self):
        from fastapi.testclient import TestClient
        from account_fixtures import create_user
        with self.s.db.tx() as c:c.execute('UPDATE applications SET require_signature=0 WHERE id=%s',('fixture',))
        runs=[]
        for name,model in [('alice','text'),('bob','alternate')]:
            client=TestClient(self.app)
            try:
                user=create_user(self.s,client,name,'fixture-password-1234')
                preview=client.post('/api/v1/apps/fixture/preview',json={'bindings':{'models':{'reasoning':model}}})
                self.assertEqual(preview.status_code,200,preview.text)
                installed=client.post('/api/v1/apps/fixture/install?expected_revision=0&binding_revision=1',json={'bindings':{'models':{'reasoning':model}}})
                self.assertEqual(installed.status_code,200,installed.text)
                run=client.post('/api/v1/apps/fixture/runs',json={'operation':'generate','input':{},'context':{}},headers={'Idempotency-Key':'independent-input'})
                self.assertEqual(run.status_code,201,run.text);runs.append(run.json())
                self.assertEqual(run.json()['payer_user_id'],user)
                self.assertEqual(client.post('/api/v1/workflows',json=self.value).status_code,403)
                if len(runs)>1:self.assertEqual(client.get('/api/v1/apps/fixture/runs/'+runs[0]['id']).status_code,404)
            finally:client.close()
        self.assertEqual(runs[0]['workflow']['sha256'],runs[1]['workflow']['sha256'])
        self.assertNotEqual(runs[0]['payer_user_id'],runs[1]['payer_user_id'])
        self.assertNotEqual(runs[0]['workflow']['bindings'],runs[1]['workflow']['bindings'])
        self.finish();self.finish()
        for run in runs:
            completed=self.view(run)
            self.assertEqual(completed['status'],'completed')
            self.assertEqual(completed['result'],{'value':1})
