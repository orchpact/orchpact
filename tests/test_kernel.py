"""New domain and graph invariants, independent of the retired v1 schema behavior."""
import copy
from dataclasses import replace
from pathlib import Path
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from orchpact.adapters.database import loads, dumps, now
from orchpact.kernel.errors import Forbidden, DomainError
from orchpact.kernel.models import ExecutionContext
from runtime_fixtures import KernelFixture, package, SCHEMA


class KernelTests(KernelFixture):
    def test_fresh_instance_has_no_fixed_department(self):
        with self.s.db.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM units').fetchone()[0],0)
            self.assertFalse(c.execute("SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename IN ('supervision_policies','planned_nodes')").fetchone())

    def test_native_plan_delivers_and_records_versioned_review(self):
        run=self.invoke();ctx=self.finish();result=self.view(run)
        self.assertEqual(result['status'],'completed');self.assertEqual(result['result'],{'value':1})
        with self.s.db.connect() as c:
            reviews=[dict(r) for r in c.execute('SELECT * FROM reviews WHERE project_id=%s',(run['id'],))]
            self.assertEqual(len(reviews),2)
            for r in reviews:self.assertEqual(r['sha256'],self.s.row(c,'artifacts',r['artifact_id'])['sha256'])
        self.assertEqual(self.client.get('/api/v1/artifacts/'+result['artifact_id']+'/download').status_code,200)

    def test_idempotent_run_and_conflicting_input(self):
        run=self.invoke()
        again=self.client.post('/api/v1/apps/fixture/runs',json=dict(operation='generate',input={},context={}),headers={'Idempotency-Key':'fixture-run'})
        self.assertEqual(again.json()['id'],run['id'])
        bad=self.client.post('/api/v1/apps/fixture/runs',json=dict(operation='generate',input={'different':True}),headers={'Idempotency-Key':'fixture-run'})
        self.assertEqual(bad.status_code,409)

    def test_invalid_package_rolls_back_without_organizations(self):
        value=package();value['operations']['generate']['execution_plan']['nodes'][0]['depends_on']=['writer']
        self.assertEqual(self.client.post('/api/v1/organization-packages',json=value).status_code,409)
        with self.s.db.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM applications').fetchone()[0],0)

    def test_missing_capability_leaves_no_partial_installation(self):
        value=package();value['operations']['generate']['execution_plan']['nodes'][0]['staff']['capabilities']=['text','json','search']
        self.register(value)
        response=self.client.post('/api/v1/apps/fixture/runs',json=dict(operation='generate',input={}),headers={'Idempotency-Key':'missing'})
        self.assertEqual(response.status_code,409)
        with self.s.db.connect() as c:
            for table in ['projects','units','agents','organization_packages']:self.assertEqual(c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0,table)

    def test_all_file_entries_reported_when_context_budget_exactly_exhausted(self):
        value=package();node=value['operations']['generate']['execution_plan']['nodes'][0]
        node['outputs']['second.json']={'type':'object'}
        run=self.invoke(value);ctx=self.s.claim()
        first=dumps({'value':1})
        first=first+' '*(9000-len(first))
        self.s.complete_work(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],'two files',[dict(path='result.json',content=first),dict(path='second.json',content='{}')])
        with self.s.db.connect() as c:a=self.s.row(c,'artifacts',self.s.row(c,'tasks',ctx['task']['id'])['artifact_id'])
        excerpt=self.s.artifacts.excerpts(a,9000)
        self.assertEqual([f['path'] for f in excerpt['files']],['result.json','second.json'])
        self.assertTrue(excerpt['files'][1]['omitted']);self.assertFalse(excerpt['complete'])

    def test_independent_review_invalidates_only_affected_branch(self):
        run=self.invoke(package(review=True));first=self.finish();gate=self.s.claim()
        report=dict(passed=False,issues=[dict(node_key='writer',reason='wrong value')],reviewed_artifact_ids=[a['artifact_id'] for a in gate['deliveries']])
        self.s.complete_work(gate['execution_context'],gate['task']['id'],gate['task']['attempt_id'],'review',[dict(path='audit.json',content=dumps(report))])
        next_ctx=self.s.claim();self.assertEqual(next_ctx['task']['id'],first['task']['id'])
        self.finish(next_ctx,2);gate=self.s.claim()
        report.update(passed=True,issues=[],reviewed_artifact_ids=[a['artifact_id'] for a in gate['deliveries']])
        self.s.complete_work(gate['execution_context'],gate['task']['id'],gate['task']['attempt_id'],'review',[dict(path='audit.json',content=dumps(report))])
        self.assertEqual(self.view(run)['result'],{'value':2})
        with self.s.db.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM artifacts WHERE task_id=%s',(first['task']['id'],)).fetchone()[0],2)
            current=self.s.row(c,'tasks',first['task']['id']);self.assertEqual(current['generation'],2)

    def test_stale_artifact_review_rejected_after_rework(self):
        run=self.invoke();first=self.finish()
        with self.s.db.tx() as c:
            old=self.s.row(c,'tasks',first['task']['id']);artifact=self.s.row(c,'artifacts',old['artifact_id'])
            self.s.invalidate(c,run['id'],{old['id']},'new version')
        self.finish(value=2)
        response=self.client.post('/api/v1/runs/'+run['id']+'/commands',json=dict(key='stale',action='review',payload=dict(artifact_id=artifact['id'],sha256=artifact['sha256'],verdict='accept',feedback='old')))
        self.assertEqual(response.status_code,409)

    def test_cancel_refuses_late_publication_but_keeps_attempt(self):
        run=self.invoke();ctx=self.s.claim()
        self.assertEqual(self.client.post('/api/v1/runs/'+run['id']+'/cancel').status_code,200)
        with self.assertRaises(DomainError):self.finish(ctx)
        self.assertEqual(self.view(run)['status'],'cancelled')

    def test_concurrent_claim_does_not_duplicate_attempt(self):
        self.invoke()
        with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(lambda _:self.s.claim(),range(6)))
        self.assertEqual(sum(r is not None for r in results),1)
        with self.s.db.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM attempts WHERE model_id!='deterministic'").fetchone()[0],1)

    def test_document_revision_conflict_retains_original(self):
        self.register();path='/api/v1/apps/fixture/documents/draft'
        self.assertEqual(self.client.put(path,json=dict(revision=0,value={'text':'original'})).status_code,200)
        self.assertEqual(self.client.put(path,json=dict(revision=0,value={'text':'stale'})).status_code,409)
        self.assertEqual(self.client.get(path).json()['value'],{'text':'original'})

    def test_pending_plan_amendment_is_versioned_and_cycle_safe(self):
        value=package();node=value['operations']['generate']['execution_plan']['nodes'][0]
        second=copy.deepcopy(node);second.update(key='second',description='second',depends_on=['writer']);value['operations']['generate']['execution_plan']['nodes'].append(second)
        run=self.invoke(value);tasks={t['node_key']:t for t in run['tasks']}
        path='/api/v1/runs/'+run['id']+'/commands'
        command=dict(key='amend',action='amend',revision=1,payload=dict(reason='clarify',changes=[dict(task_id=tasks['second']['id'],description='new requirement')]))
        result=self.client.post(path,json=command);self.assertEqual(result.status_code,200,result.text);self.assertEqual(result.json()['revision'],2)
        self.assertEqual(self.client.post(path,json=command).json()['revision'],2)
        command['key']='stale';self.assertEqual(self.client.post(path,json=command).status_code,409)
        cycle=dict(key='cycle',action='amend',revision=2,payload=dict(reason='invalid cycle',changes=[dict(task_id=tasks['writer']['id'],depends_on=[tasks['second']['id']])]))
        self.assertEqual(self.client.post(path,json=cycle).status_code,409)
        self.assertEqual(self.view(run)['revision'],2)

    def test_execution_context_cannot_be_missing(self):
        run=self.invoke();ctx=self.s.claim()
        with self.assertRaises(Forbidden):self.s.complete_work(None,ctx['task']['id'],ctx['task']['attempt_id'],'bad',[dict(path='result.json',content='{"value":1}')])

    def test_authorization_revocation_cascades_to_delegated_workers(self):
        run=self.invoke();ctx=self.s.claim()
        with self.s.db.tx() as c:
            root=c.execute('SELECT id FROM mandates WHERE project_id=%s AND parent_id IS NULL',(run['id'],)).fetchone()[0]
            c.execute('UPDATE mandates SET revoked=1 WHERE id=%s',(root,))
        with self.assertRaises(Forbidden):self.finish(ctx)

    def test_reparenting_organization_does_not_grant_permissions(self):
        run=self.invoke();ctx=self.s.claim()
        other=replace(self.ctx,subject='ungranted-agent',subject_type='agent',project_id=run['id'])
        with self.s.db.tx() as c:
            unit=self.s.row(c,'units',ctx['task']['unit_id'])
            c.execute('UPDATE units SET parent_id=NULL WHERE id=%s',(unit['id'],))
            with self.assertRaises(Forbidden):self.s.policies.authorize(c,other,'review',self.s.row(c,'projects',run['id']))

    def test_delegation_cannot_expand_actions_or_expiry(self):
        run=self.invoke();ctx=self.s.claim()
        with self.s.db.tx() as c:
            project=self.s.row(c,'projects',run['id'])
            with self.assertRaises(Forbidden):self.s.policies.grant(c,ctx['execution_context'],project,'other',['delegate'])
            with self.assertRaises(Forbidden):self.s.policies.grant(c,self.ctx,project,'other',['call'],['text'],now()+86400*60)

    def test_governance_requires_independent_human_review(self):
        self.client.put('/api/v1/policies/platform',json=dict(revision=0,policy=dict(independent_review=True)))
        run=self.invoke();ctx=self.finish();current=self.view(run)
        self.assertNotEqual(current['status'],'completed')
        worker=next(t for t in current['tasks'] if t['kind']=='work')
        self.assertTrue(worker['requires_independent_review'])
        self.client.put('/api/v1/policies/platform',json=dict(revision=1,policy=dict(independent_review=False))).raise_for_status()
        self.assertTrue(next(t for t in self.view(run)['tasks'] if t['id']==worker['id'])['requires_independent_review'])
        with self.s.db.connect() as c:a=self.s.row(c,'artifacts',worker['artifact_id'])
        command=dict(key='review-worker',action='review',payload=dict(artifact_id=a['id'],sha256=a['sha256'],verdict='accept',feedback='verified'))
        self.assertEqual(self.client.post('/api/v1/runs/'+run['id']+'/commands',json=command).status_code,200)
        root=next(t for t in self.view(run)['tasks'] if t['parent_id'] is None)
        with self.s.db.connect() as c:a=self.s.row(c,'artifacts',root['artifact_id'])
        command.update(key='review-root',payload=dict(artifact_id=a['id'],sha256=a['sha256'],verdict='accept',feedback='approved delivery'))
        self.assertEqual(self.client.post('/api/v1/runs/'+run['id']+'/commands',json=command).status_code,200)
        self.assertEqual(self.view(run)['status'],'completed')

    def test_policy_revision_conflict(self):
        path='/api/v1/policies/platform'
        self.assertEqual(self.client.put(path,json=dict(revision=0,policy=dict(budget_points=1))).status_code,200)
        self.assertEqual(self.client.put(path,json=dict(revision=0,policy={})).status_code,409)

    def test_package_upgrade_requires_explicit_revision_and_preserves_running_tasks(self):
        run=self.invoke();claimed=self.s.claim()
        updated=package();updated['units'][0]['prompt']='new responsibility'
        response=self.client.post('/api/v1/organization-packages?revision=1',json=updated);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['installed_revision'],1)
        self.client.post('/api/v1/apps/fixture/install').raise_for_status()
        self.assertEqual(self.client.get('/api/v1/apps/fixture').json()['installed_revision'],1)
        self.assertEqual(self.client.post('/api/v1/apps/fixture/install?expected_revision=0').status_code,409)
        self.client.post('/api/v1/apps/fixture/install?expected_revision=1').raise_for_status()
        self.assertEqual(self.client.get('/api/v1/apps/fixture').json()['installed_revision'],2)
        self.finish(claimed,3);self.assertEqual(self.view(run)['result'],{'value':3})
        with self.s.db.connect() as c:self.assertEqual(self.s.row(c,'units',claimed['task']['unit_id'])['prompt'],'')

    def test_plan_addition_is_validated_and_versioned(self):
        run=self.invoke();node=copy.deepcopy(package()['operations']['generate']['execution_plan']['nodes'][0]);node.pop('unit');node.update(key='extra',outputs={'extra.json':SCHEMA},depends_on=['writer'])
        response=self.client.post('/api/v1/runs/'+run['id']+'/commands',json=dict(key='add',action='amend',revision=1,payload=dict(reason='independent extra check',add_nodes=[node])))
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['revision'],2)
        self.finish();ctx=self.s.claim();self.assertEqual(ctx['planned_node']['key'],'extra')
        self.s.complete_work(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],'extra',[dict(path='extra.json',content='{"value":1}')])
        self.assertEqual(self.view(run)['status'],'completed')

    def test_plan_cannot_cancel_the_final_result_node(self):
        run=self.invoke();writer=next(t for t in run['tasks'] if t['node_key']=='writer')
        response=self.client.post('/api/v1/runs/'+run['id']+'/commands',json=dict(key='cancel-final',action='amend',revision=1,payload=dict(reason='invalid cancellation',changes=[dict(task_id=writer['id'],cancel=True)])))
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.view(run)['revision'],1)

    def test_aggregation_storage_failure_preserves_successful_worker(self):
        from unittest.mock import patch
        run=self.invoke();original=self.s.artifacts.publish
        def store(c,task,attempt,files):
            if task['kind']=='group':raise OSError('fixture storage unavailable')
            return original(c,task,attempt,files)
        with patch.object(self.s.artifacts,'publish',side_effect=store):ctx=self.finish()
        current=self.view(run);worker=next(t for t in current['tasks'] if t['id']==ctx['task']['id'])
        self.assertEqual(worker['status'],'completed');self.assertIsNotNone(worker['artifact_id'])
        self.client.post('/api/v1/work-items/'+run['root_task_id']+'/retry').raise_for_status()
        self.assertIsNone(self.s.claim());self.assertEqual(self.view(run)['status'],'completed')
