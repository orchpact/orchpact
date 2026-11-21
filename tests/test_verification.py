"""Trusted verification evidence and candidate acceptance recovery."""
from unittest.mock import patch

from orchpact.adapters.database import loads
from orchpact.adapters.tools import ToolTransportError
from orchpact.kernel.errors import DomainError
from verification_fixtures import VerificationFixture, verifier_result, workflow


class VerificationTests(VerificationFixture):
    def test_pass_binds_node_and_root_evidence_before_acceptance(self):
        value=workflow(node_verification=True,root_verification=True);self.install(value);run=self.invoke()
        self.assertTrue(self.app.state.runner.step());self.assertTrue(self.app.state.runner.step())
        view=self.view(run)
        self.assertEqual(view['status'],'completed');self.assertIsNone(view['result'])
        self.assertEqual(self.renderer.calls,1);self.assertEqual(self.verifier.calls,2)
        self.assertEqual(len(view['verification_evidence']),2)
        self.assertEqual({row['status'] for row in view['verification_evidence']},{'PASS'})
        self.assertEqual({row['artifact_id'] for row in view['verification_evidence']},{task['artifact_id'] for task in view['tasks'] if task['artifact_id']})
        for row in view['verification_evidence']:
            self.assertEqual(len(row['artifact_sha256']),64);self.assertEqual(len(row['contract_digest']),64)
            response=self.client.get('/api/v1/verification-evidence/'+row['id'])
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['bundle']['artifact_id'],row['artifact_id'])

    def test_fail_and_inconclusive_block_acceptance_and_waive_is_partial(self):
        for status in ('FAIL','INCONCLUSIVE'):
            value=workflow(node_verification=True);self.install(value);run=self.invoke('verification-'+status)
            self.verifier.result=verifier_result(status)
            self.assertTrue(self.app.state.runner.step())
            view=self.view(run);self.assertNotEqual(view['status'],'completed');self.assertIsNone(view['result'])
            evidence=view['verification_evidence'][0]
            self.assertEqual(evidence['status'],status)
            task=next(task for task in view['tasks'] if task['node_key']=='render')
            command={'key':'forced-accept','action':'review','payload':{'artifact_id':task['artifact_id'],'sha256':evidence['artifact_sha256'],'verdict':'accept','feedback':'forged acceptance'}}
            self.assertEqual(self.client.post('/api/v1/runs/'+run['id']+'/commands',json=command).status_code,409)
            if status=='FAIL':
                self.assertEqual(task['status'],'queued');self.assertEqual(task['generation'],2)
                self.assertEqual(view['execution_plan']['repairs'],1)
            else:
                command['key']='waive-verification';command['payload']['verdict']='waive'
                response=self.client.post('/api/v1/runs/'+run['id']+'/commands',json=command)
                self.assertEqual(response.status_code,200,response.text)
                current=next(item for item in self.view(run)['tasks'] if item['id']==task['id'])
                self.assertEqual(current['status'],'partial');self.assertEqual(current['review'],'waived')
            self.tearDown();self.setUp()

    def test_missing_or_unknown_checks_and_forged_pass_are_inconclusive(self):
        cases=[
            verifier_result('PASS',checks=[dict(check_id='other',status='PASS')]),
            verifier_result('PASS',checks=[]),
            verifier_result('FAIL',checks=[dict(check_id='structure',status='PASS')]),
        ]
        for raw in cases:
            value=workflow(node_verification=True);self.install(value);run=self.invoke('forged-'+str(len(raw['checks']))+raw['status'])
            self.verifier.result=raw;self.assertTrue(self.app.state.runner.step())
            view=self.view(run);self.assertNotEqual(view['status'],'completed')
            self.assertEqual(view['verification_evidence'][0]['status'],'INCONCLUSIVE')
            self.tearDown();self.setUp()

    def test_untrusted_or_incomplete_verifier_binding_is_rejected_at_install(self):
        value=workflow(node_verification=True)
        self.update_verifier(tool_verifier={'checks':['structure'],'media_types':['application/json'],'trust_level':'untrusted','environment':'synthetic'})
        self.register(value)
        response=self.client.post('/api/v1/apps/fixture/install',json={'bindings':self.bindings()})
        self.assertEqual(response.status_code,409,response.text)

    def test_stale_generation_or_missing_evidence_cannot_be_accepted(self):
        value=workflow(node_verification=True);self.install(value);run=self.invoke();self.assertTrue(self.app.state.runner.step())
        with self.s.db.tx() as c:
            task=self.s.row(c,'tasks',run['root_task_id'])
            render=next(row for row in c.execute('SELECT * FROM tasks WHERE project_id=%s',(run['id'],)) if row['node_key']=='render')
            artifact=self.s.row(c,'artifacts',render['artifact_id'])
            c.execute('UPDATE tasks SET generation=generation+1 WHERE id=%s',(render['id'],))
            render=self.s.row(c,'tasks',render['id'])
            with self.assertRaises(DomainError):self.s.verifications.assert_accepted(c,render,artifact)
            c.execute('DELETE FROM verification_evidence')
            c.execute('UPDATE tasks SET generation=generation-1 WHERE id=%s',(render['id'],))
            render=self.s.row(c,'tasks',render['id'])
            with self.assertRaises(DomainError):self.s.verifications.assert_accepted(c,render,artifact)

    def test_completed_verifier_receipt_recovers_without_second_call(self):
        value=workflow(node_verification=True);self.install(value);run=self.invoke()
        with patch.object(self.s.verifications,'record',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.app.state.runner.step()
        self.assertEqual(self.verifier.calls,1)
        self.app.state.runner.recover()
        view=self.view(run)
        self.assertEqual(view['status'],'completed');self.assertEqual(self.verifier.calls,1)
        self.assertEqual(len(view['verification_evidence']),1)

    def test_existing_evidence_recovers_before_interruption_without_replay(self):
        value=workflow(node_verification=True);self.install(value);run=self.invoke()
        with patch.object(self.s.verifications,'finalize',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.app.state.runner.step()
        self.assertEqual(self.verifier.calls,1)
        with self.s.db.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM verification_evidence').fetchone()[0],1)
        self.app.state.runner.recover()
        view=self.view(run);self.assertEqual(view['status'],'completed');self.assertEqual(self.verifier.calls,1)

    def test_unknown_verifier_is_not_replayed_with_a_new_artifact(self):
        value=workflow(node_verification=True);self.install(value);run=self.invoke()
        self.verifier.error=KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.app.state.runner.step()
        self.app.state.runner.recover()
        task=next(task for task in self.view(run)['tasks'] if task['node_key']=='render')
        self.retry(task['id'])
        self.assertTrue(self.app.state.runner.step())
        view=self.view(run)
        self.assertEqual(self.verifier.calls,1)
        unknown=[call for call in view['tool_invocations'] if call['logical_tool']=='verifier']
        self.assertEqual(len(unknown),1);self.assertEqual(unknown[0]['status'],'unknown')
        self.assertNotEqual(view['status'],'completed')

    def test_evidence_api_enforces_workspace_isolation(self):
        value=workflow(node_verification=True);self.install(value);run=self.invoke();self.assertTrue(self.app.state.runner.step())
        evidence=self.view(run)['verification_evidence'][0]
        from account_fixtures import create_user
        from fastapi.testclient import TestClient
        client=TestClient(self.app)
        try:
            create_user(self.s,client,'evidence-reader','fixture-password-1234')
            response=client.get('/api/v1/verification-evidence/'+evidence['id'])
            self.assertEqual(response.status_code,404,response.text)
        finally:client.close()

    def test_agent_candidate_uses_the_same_verification_gate(self):
        import contextlib
        import json
        import os
        import httpx
        from test_workflows import definition
        from verification_fixtures import VERIFICATION
        value=workflow();agent=definition()
        agent['resources']['tools']={'verifier':value['resources']['tools']['verifier']}
        agent['operations']['generate']['tool_limit']=2
        agent['operations']['generate']['execution_plan']['nodes'][0]['verification']=VERIFICATION
        self.settings.profiles['text']=self.text_profile
        self.register(agent)
        bindings=self.bindings();bindings['models']={'reasoning':'text'};bindings['tools']={'verifier':bindings['tools']['verifier']}
        response=self.client.post('/api/v1/apps/fixture/install',json={'bindings':bindings})
        self.assertEqual(response.status_code,200,response.text);run=self.invoke()
        def send(request):
            decision={'files':{'result.json':{'value':7}}}
            return httpx.Response(200,json={'id':'synthetic-agent','choices':[{'finish_reason':'stop','message':{'content':json.dumps(decision)}}],'usage':{'total_tokens':10}})
        stack=contextlib.ExitStack();http_client=httpx.Client
        stack.enter_context(patch.dict(os.environ,{'RUNTIME_FIXTURE_KEY':'test-only'}))
        stack.enter_context(patch('orchpact.adapters.providers.httpx.Client',side_effect=lambda **kw:http_client(transport=httpx.MockTransport(send),**kw)))
        with stack:self.assertTrue(self.app.state.runner.step())
        view=self.view(run)
        self.assertEqual(view['status'],'completed');self.assertEqual(view['verification_evidence'][0]['status'],'PASS')
        self.assertEqual(self.verifier.calls,1);self.assertEqual(len(view['crew_runs']),1)

    def test_recovery_does_not_start_a_new_verifier_call(self):
        value=workflow(node_verification=True);self.install(value);run=self.invoke()
        original=self.s.tools.invoke
        def interrupt(ctx,slot,*args,**kwargs):
            if slot=='verifier':raise KeyboardInterrupt()
            return original(ctx,slot,*args,**kwargs)
        with patch.object(self.s.tools,'invoke',side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):self.app.state.runner.step()
        self.assertEqual(self.verifier.calls,0)
        self.app.state.runner.recover()
        self.assertEqual(self.verifier.calls,0);self.assertNotEqual(self.view(run)['status'],'completed')

    def test_database_artifacts_receipts_and_evidence_restore_as_a_pair(self):
        import os
        import shutil
        import subprocess
        from pathlib import Path
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        from orchpact.adapters.database import Database
        from orchpact.testing import database_url_for
        value=workflow(node_verification=True);self.install(value);run=self.invoke();self.assertTrue(self.app.state.runner.step())
        evidence=self.view(run)['verification_evidence'][0]
        relative=Path('verification-evidence')/evidence['attempt_id']/(evidence['id']+'.json')
        expected=(self.settings.data/relative).read_text()
        backup=self.s.db.backup('verification-restore')
        restored_root=self.root/'restored-verification'
        shutil.copytree(self.settings.data,restored_root/'var')
        restored_url=database_url_for(restored_root/'var')
        options={option.keyword.decode():option.envvar.decode() for option in psycopg.pq.Conninfo.get_defaults() if option.envvar}
        env=dict(os.environ)
        for key,item in conninfo_to_dict(restored_url).items():env[options[key]]=item
        binary=Path(os.environ['ORCHPACT_PG_DUMP']).with_name('pg_restore')
        result=subprocess.run([str(binary),'--dbname',conninfo_to_dict(restored_url)['dbname'],'--no-owner','--no-acl',str(backup)],env=env,capture_output=True)
        self.assertEqual(result.returncode,0,'pg_restore failed')
        restored=Database(restored_url,restored_root/'var'/'backups');restored.verify_file_store()
        with restored.connect() as c:
            row=c.execute('SELECT status,artifact_sha256 FROM verification_evidence WHERE id=%s',(evidence['id'],)).fetchone()
            self.assertEqual(row['status'],'PASS');self.assertEqual(row['artifact_sha256'],evidence['artifact_sha256'])
        self.assertEqual((restored_root/'var'/relative).read_text(),expected)

    def test_named_migration_is_repeatable_and_backed_up(self):
        from orchpact.adapters.schema import MIGRATIONS
        with self.s.db.tx() as c:
            c.execute('DROP TABLE verification_evidence')
            c.execute("DELETE FROM schema_migrations WHERE name='0003_verification_evidence'")
        statements=dict(MIGRATIONS)['0003_verification_evidence']
        self.assertTrue(self.s.db.migrate('0003_verification_evidence',statements))
        self.assertFalse(self.s.db.migrate('0003_verification_evidence',statements))
        self.assertTrue((self.s.db.backup_directory/'0003_verification_evidence.dump').exists())


if __name__=='__main__':unittest.main()
