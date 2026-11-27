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

    def test_display_title_survives_install_and_run_and_dynamic_operation_has_no_fixed_nodes(self):
        updated = deepcopy(self.value)
        op = updated['operations']['generate']
        node = op['execution_plan']['nodes'][0]
        node['title'] = '核对金额'
        updated['operations']['adhoc'] = {key:value for key,value in op.items() if key != 'execution_plan'}
        self.client.post('/api/v1/workflows?revision=1',json=updated).raise_for_status()
        self.install(revision=2).raise_for_status()
        graph = self.client.get('/api/v1/workbench/installations/shared-evidence/definition').json()
        self.assertEqual(graph['definition']['operations']['adhoc'],{})
        fixed = graph['definition']['operations']['generate']['execution_plan']
        self.assertEqual(fixed['nodes'][0]['title'],'核对金额')
        run = self.start().json()
        task = next(item for item in run['tasks'] if item['node_key'] == node['key'])
        self.assertEqual(task['title'],'核对金额')
        self.assertEqual(task['description'],node['description'])
        node['title'] = 'x' * 121
        self.assertGreaterEqual(self.client.post('/api/v1/workflows/validate',json=updated).status_code,400)

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

    def test_run_draft_preserves_actual_plan_and_portable_model_slots(self):
        self.install().raise_for_status()
        run=self.start().json()
        base='/api/v1/workbench/runs/'+run['id']+'/draft'
        writer=next(task for task in run['tasks'] if task['node_key']=='writer')
        branch=dict(key='inspect',kind='agent',description='Inspect independently',expected='inspect.json',
                    outputs={'inspect.json':{'type':'object'}},depends_on=['writer'],
                    staff=dict(name='Inspector',role='Check result',model_id='alternate',
                               reason='An additional check',capabilities=['text','json']))
        command='/api/v1/runs/'+run['id']+'/commands'
        self.client.post(command,json=dict(key='append',action='amend',revision=run['revision'],
            payload=dict(reason='Inspect final delivery',add_nodes=[branch],changes=[
                dict(task_id=writer['id'],description='Revised instructions')]))).raise_for_status()
        response=self.client.get(base+'?node=inspect&plan_revision=2')
        response.raise_for_status()
        draft=response.json()
        self.assertEqual((draft['run_id'],draft['revision'],draft['plan_revision'],draft['selected_node']),
                         (run['id'],1,2,'inspect'))
        definition=draft['definition']
        nodes={node['key']:node for node in definition['operations']['generate']['execution_plan']['nodes']}
        self.assertEqual(nodes['writer']['description'],'Revised instructions')
        self.assertEqual(nodes['writer']['staff']['model'],'reasoning')
        self.assertEqual(nodes['inspect']['depends_on'],['writer'])
        self.assertNotIn('model_id',nodes['inspect']['staff'])
        self.assertNotIn('unit_id',nodes['inspect'])
        self.assertEqual(nodes['inspect']['unit'],'entry')
        slot=nodes['inspect']['staff']['model']
        self.assertEqual(definition['resources']['models'][slot],{'capabilities':['text','json']})
        self.assertEqual(self.client.get(base+'?plan_revision=1').json()['code'],'revision_conflict')
        self.assertEqual(self.client.get(base+'?node=missing').status_code,404)
        self.assertEqual(self.client.get(base).json()['selected_node'],'writer')
        self.assertEqual(self.client.get('/api/v1/workflows/shared-evidence').json(),self.asset)
        self.assertEqual(self.client.get('/api/v1/workbench/runs/'+run['id']).json()['revision'],2)
        self.assertNotIn('context',draft)
        self.assertNotIn('input',draft)
        self.assertNotIn('result',draft)
        self.assertNotIn('bindings',draft)
        self.assertNotIn('artifact_id',draft)
        self.assertNotIn('sha256',draft)
        self.assertEqual(self.client.post('/api/v1/workflows/validate',json=definition).status_code,200)

        # An explicitly selected cancelled source is usable in a private draft.
        runtime_node=next(n for n in self.client.get('/api/v1/runs/'+run['id']).json()['tasks'] if n['node_key']=='inspect')
        self.client.post(command,json=dict(key='remove',action='amend',revision=2,
            payload=dict(reason='Stop additional check',changes=[dict(task_id=runtime_node['id'],cancel=True)]))).raise_for_status()
        normal=self.client.get(base).json()['definition']['operations']['generate']['execution_plan']
        self.assertEqual([node['key'] for node in normal['nodes']],['writer'])
        restored=self.client.get(base+'?node=inspect').json()
        self.assertEqual(restored['restored_nodes'],['inspect'])
        self.assertEqual(restored['plan_revision'],3)
        mixed=self.client.get(base+'?node=writer&select=writer&select=inspect&select=writer').json()
        self.assertEqual(mixed['selected_node'],'writer')
        self.assertEqual(mixed['restored_nodes'],['inspect'])
        self.assertEqual(self.client.get(base+'?select=missing').status_code,404)
        self.assertEqual(self.client.get('/api/v1/runs/'+run['id']).json()['revision'],3)

    def test_run_draft_requires_admin_and_selected_workspace(self):
        viewer=TestClient(self.app)
        try:
            create_user(self.s,viewer,'draft-viewer','fixture-password-1234')
            self.install(viewer).raise_for_status()
            theirs=self.start(viewer).json()
            path='/api/v1/workbench/runs/'+theirs['id']+'/draft'
            self.assertEqual(viewer.get(path).status_code,403)
            self.assertEqual(self.client.get(path).status_code,404)
            self.install().raise_for_status()
            mine=self.start().json()
            with self.s.db.tx() as c:
                c.execute('INSERT INTO workspace_members VALUES(%s,%s)',(self.workspace,theirs['payer_user_id']))
            viewer.headers['X-Workspace-Id']=self.workspace
            self.assertEqual(viewer.get('/api/v1/workbench/runs/'+mine['id']).status_code,200)
            self.assertEqual(viewer.get('/api/v1/workbench/runs/'+mine['id']+'/draft').status_code,403)
        finally:
            viewer.close()

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

    def test_workbench_lists_only_scoped_resources_with_role_appropriate_detail(self):
        owner, member = TestClient(self.app), TestClient(self.app)
        try:
            create_user(self.s, owner, 'workspace-owner', 'fixture-password-1234')
            member_id = create_user(self.s, member, 'workspace-member', 'fixture-password-1234')
            space = owner.get('/api/v1/auth/me').json()['workspace_id']
            self.install(owner).raise_for_status()
            run = self.start(owner).json()
            self.assertEqual(member.get('/api/v1/workbench/installations').json(), [])
            forbidden = member.get('/api/v1/workbench/runs/' + run['id'])
            self.assertEqual(forbidden.status_code, 404)

            with self.s.db.tx() as c:
                c.execute('INSERT INTO workspace_members VALUES(%s,%s)', (space, member_id))
            member.headers['X-Workspace-Id'] = space
            installed = member.get('/api/v1/workbench/installations').json()[0]
            self.assertEqual(installed['operations'][0]['key'], 'generate')
            self.assertEqual(installed['installation_revision'], 1)
            self.assertNotIn('definition', installed)
            binding_url = '/api/v1/workbench/binding-options/shared-evidence'
            self.assertEqual(member.get(binding_url).status_code, 403)
            self.assertEqual(member.get('/api/v1/admin/instance').status_code, 403)
            instance = self.client.get('/api/v1/admin/instance').json()
            self.assertEqual(instance['active']['queued'], 1)
            self.assertFalse(instance['worker_enabled'])
            model_options = owner.get(binding_url).json()['model_options']['reasoning']
            self.assertEqual({option['id'] for option in model_options}, {'text', 'alternate'})
            self.assertEqual(member.get('/api/v1/workflows/shared-evidence/revisions').status_code, 403)
            self.assertEqual(self.client.get('/api/v1/workflows/shared-evidence/revisions').json()[0]['revision'], 1)
            detail = member.get('/api/v1/workbench/runs/' + run['id']).json()
            self.assertEqual(detail['id'], run['id'])
            self.assertFalse(detail['can_decide_tool'])
            self.assertNotIn('application_id', detail)
            self.assertEqual([row['id'] for row in member.get('/api/v1/workbench/runs?q=shared-evidence').json()['items']], [run['id']])
            self.assertEqual(member.get('/api/v1/workbench/run-counts').json(), {'queued': 1})
            member.headers.pop('X-Workspace-Id')
            self.assertEqual(member.get('/api/v1/workbench/runs').json()['items'], [])
        finally:
            owner.close()
            member.close()

    def test_stale_workbench_admission_keeps_idempotent_results(self):
        self.install().raise_for_status()
        path = '/api/v1/workflows/shared-evidence/runs?installation_revision=1'
        value = dict(operation='generate', input={}, context={})
        first = self.client.post(path, json=value, headers={'Idempotency-Key': 'accepted'})
        first.raise_for_status()
        self.install(expected=1, model='alternate').raise_for_status()
        reused = self.client.post(path, json=value, headers={'Idempotency-Key': 'accepted'})
        self.assertEqual(reused.json()['id'], first.json()['id'])
        stale = self.client.post(path, json=value, headers={'Idempotency-Key': 'new-run'})
        self.assertEqual(stale.json()['code'], 'revision_conflict')

    def test_inbox_includes_earlier_work_and_has_validated_keyset_pagination(self):
        from orchpact.adapters.database import ident

        self.install().raise_for_status()
        old = self.start().json()['id']
        question_ids = [ident(), ident()]
        with self.s.db.tx() as c:
            c.execute('''INSERT INTO projects(id,name,goal,root_unit_id,created,workspace_id,created_by,application_id)
                SELECT 'workbench-fixture-'||n,name,goal,root_unit_id,created+n,workspace_id,created_by,application_id
                FROM projects CROSS JOIN generate_series(1,101) AS n WHERE id=%s''', (old,))
            c.execute('''INSERT INTO invocations SELECT 'workbench-fixture-'||n,application_id,operation,
                root_task_id,input,context,binding,'workbench-fixture-'||n,fingerprint,result,created+n
                FROM invocations CROSS JOIN generate_series(1,101) AS n WHERE id=%s''', (old,))
            for timestamp, key in enumerate(question_ids, 1):
                c.execute("INSERT INTO messages(id,project_id,kind,content,created) VALUES(%s,%s,'question','待答复',%s)", (key, old, timestamp))
        url = '/api/v1/workbench/inbox'
        first = self.client.get(url, params={'kind': 'question', 'limit': 1}).json()
        self.assertEqual(first['remaining'], 2)
        self.assertEqual(first['items'][0]['reference'], question_ids[1])
        second = self.client.get(url, params={'kind': 'question', 'limit': 1, 'cursor': first['next_cursor']}).json()
        self.assertEqual(second['items'][0]['reference'], question_ids[0])
        self.assertIsNone(second['next_cursor'])
        self.assertEqual(self.client.get(url, params={'cursor': '!'}).status_code, 409)

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

    def test_member_can_read_installed_revision_only(self):
        self.install().raise_for_status()
        member = TestClient(self.app)
        try:
            create_user(self.s,member,'canvas-reader','fixture-password-1234')
            path='/api/v1/workbench/installations/shared-evidence/definition'
            self.assertEqual(member.get(path).status_code,404)
            response=member.get(path,headers={'X-Workspace-Id':self.workspace})
            self.assertIn(response.status_code,(403,404),response.text)
            self.install(member).raise_for_status()
            response=member.get(path)
            response.raise_for_status()
            canvas=response.json()
            self.assertEqual(canvas['revision'],1)
            node=canvas['definition']['operations']['generate']['execution_plan']['nodes'][0]
            self.assertEqual(node['kind'],'agent')
            self.assertNotIn('staff',node)
        finally:
            member.close()
