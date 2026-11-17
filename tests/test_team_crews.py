"""Reusable departments compile to fresh CrewAI teams, scoped to each run."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient

from runtime_fixtures import KernelFixture,package
from account_fixtures import create_user
from orchpact.adapters.providers import Providers
from orchpact.services.executor import CrewAIExecutor
from orchpact.adapters.database import loads
from orchpact.kernel.workflows import export_definition,validate_definition,bind_definition
from orchpact.kernel.packages import validate_package
from orchpact.kernel.errors import Forbidden

HttpClient=httpx.Client


def team_package():
    value=package()
    value['units'][0]['crew']=dict(process='sequential',members=[
        dict(key='analyst',name='Analyst',role='Analyze the supplied facts',instruction='Write factual notes',capabilities=['text','json']),
        dict(key='writer',name='Writer',role='Write the contracted result',instruction='Use the analyst notes and submit result.json',capabilities=['text','json'])])
    node=value['operations']['generate']['execution_plan']['nodes'][0]
    node.pop('staff');node['participants']=['analyst','writer']
    return value


class TeamCrewTests(KernelFixture):
    def transport(self):
        self.inputs=[]
        def send(request):
            body=json.loads(request.content)
            inputs=json.loads(body['messages'][1]['content']);self.inputs.append(inputs)
            marker=inputs.get('application',{}).get('input',{}).get('marker','SYNTHETIC')
            if not inputs['crew_member']['final']:result={'notes':marker+' verified facts'}
            elif inputs.get('stage_contract',{}).get('gate'):
                result={'files':{'audit.json':dict(passed=True,issues=[],reviewed_artifact_ids=[d['artifact_id'] for d in inputs['deliveries']])}}
            elif inputs.get('stage_contract'):result={'files':{'result.json':{'value':7}}}
            else:result=dict(summary='team result',files=[dict(path='result.json',content='{"value":7}')])
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}],'usage':{'total_tokens':1000}})
        stack=ExitStack();stack.enter_context(patch.dict(os.environ,{'RUNTIME_FIXTURE_KEY':'synthetic-only'}))
        stack.enter_context(patch('orchpact.adapters.providers.httpx.Client',side_effect=lambda **kw:HttpClient(transport=httpx.MockTransport(send),**kw)))
        return stack

    def test_department_runs_real_crew_with_two_agents_and_task_context(self):
        run=self.invoke(team_package());ctx=self.s.claim()
        with self.transport():CrewAIExecutor(self.s).execute(ctx)
        view=self.view(run);self.assertEqual(view['status'],'completed')
        self.assertEqual(len(view['crew_runs']),1)
        crew=view['crew_runs'][0];self.assertEqual(crew['status'],'completed')
        self.assertEqual([m['key'] for m in crew['roster']],['analyst','writer'])
        self.assertEqual(len({m['agent_id'] for m in crew['roster']}),2)
        self.assertEqual([s['member_key'] for s in view['agent_steps']],['analyst','writer'])
        self.assertEqual({s['crew_run_id'] for s in view['agent_steps']},{crew['id']})
        self.assertIn('SYNTHETIC verified facts',json.dumps(self.inputs[-1]['agent_history']))
        self.assertEqual(view['usage']['points'],2)
        self.assertFalse(list(self.settings.data.rglob('*.db')))

    def test_same_department_concurrent_runs_have_fresh_crews_agents_and_payers(self):
        self.register(team_package());clients=[];runs=[];users=[]
        try:
            for index in range(2):
                client=TestClient(self.app);clients.append(client)
                users.append(create_user(self.s,client,'member-'+str(index),'fixture-password-1234'))
                response=client.post('/api/v1/apps/fixture/runs',json=dict(operation='generate',input={'marker':'PRIVATE_'+str(index)}),headers={'Idempotency-Key':'parallel'})
                self.assertEqual(response.status_code,201,response.text);runs.append(response.json())
            contexts=[self.s.claim(),self.s.claim()]
            self.assertTrue(all(contexts))
            with self.transport(),ThreadPoolExecutor(2) as pool:
                list(pool.map(CrewAIExecutor(self.s).execute,contexts))
            views=[client.get('/api/v1/apps/fixture/runs/'+run['id']).json() for client,run in zip(clients,runs)]
            self.assertTrue(all(v['status']=='completed' for v in views))
            self.assertNotEqual(views[0]['crew_runs'][0]['id'],views[1]['crew_runs'][0]['id'])
            ids=[{r['agent_id'] for r in v['crew_runs'][0]['roster']} for v in views]
            self.assertFalse(ids[0]&ids[1])
            for inputs in self.inputs:
                marker=inputs['application']['input']['marker'];other='PRIVATE_'+str(1-int(marker[-1]))
                self.assertNotIn(other,json.dumps(inputs['agent_history']))
            for user in users:
                with self.s.db.connect() as c:self.assertEqual(self.s.access.wallets.view(c,user)['balance'],9998)
            self.assertEqual(clients[0].get('/api/v1/apps/fixture/runs/'+runs[1]['id']).status_code,404)
        finally:
            for client in clients:client.close()

    def test_declaration_changes_do_not_rewrite_accepted_team_roster(self):
        self.invoke(team_package());ctx=self.s.claim();unit=ctx['snapshot']['unit']
        updated=deepcopy(unit['crew']);updated['members'][0]['instruction']='Changed instruction'
        self.s.unit(self.ctx,dict(name=unit['name'],purpose=unit['purpose'],model_id=unit['model_id'],crew=updated),key=unit['id'])
        with self.s.db.connect() as c:
            frozen=loads(c.execute('SELECT snapshot FROM tasks WHERE id=%s',(ctx['task']['id'],)).fetchone()[0])
        self.assertEqual(frozen['crew']['members'][0]['instruction'],'Write factual notes')
        self.assertNotEqual(frozen['crew']['members'][0]['instruction'],updated['members'][0]['instruction'])
        self.s.unit(self.ctx,dict(name='Renamed',purpose=unit['purpose'],model_id=unit['model_id']),key=unit['id'])
        with self.s.db.connect() as c:self.assertEqual(self.s.row(c,'units',unit['id'])['crew'],updated)

    def test_member_cannot_use_another_members_search_grant(self):
        value=team_package();self.settings.profiles['text']['capabilities'].append('search')
        value['units'][0]['crew']['members'][0]['capabilities'].append('search')
        self.invoke(value);ctx=self.s.claim()
        from orchpact.services.agent_steps import AgentSteps
        writer=ctx['snapshot']['crew']['members'][1]
        role_ctx=dict(ctx,snapshot=dict(ctx['snapshot'],agent=writer['agent'],profile=writer['profile']),_crew_member_key='writer')
        with self.assertRaises(Forbidden):Providers(self.s).reserve(role_ctx,'search','search:tavily:search')
        with self.assertRaises(Forbidden):AgentSteps(Providers(self.s),role_ctx).begin('tool','search_evidence','search')
        with self.s.db.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM requests').fetchone()[0],0)

    def test_participant_subset_uses_only_declared_selected_roles(self):
        value=team_package();value['operations']['generate']['execution_plan']['nodes'][0]['participants']=['writer']
        self.invoke(value);ctx=self.s.claim()
        self.assertEqual([r['key'] for r in ctx['snapshot']['crew']['members']],['writer'])

    def test_portable_team_model_slots_round_trip_and_validation(self):
        value=team_package();value['units'][0]['model_id']='local-profile'
        for member in value['units'][0]['crew']['members']:member['model_id']='local-profile'
        definition=export_definition(value);validate_definition(definition)
        self.assertNotIn('local-profile',json.dumps(definition))
        slots={slot:'bound-profile' for slot in definition['resources']['models']}
        installed=bind_definition(definition,{'models':slots},lambda *args:None)
        self.assertTrue(all(m['model_id']=='bound-profile' for m in installed['units'][0]['crew']['members']))
        for invalid in ('foreign-role',):
            bad=team_package();bad['operations']['generate']['execution_plan']['nodes'][0]['participants']=[invalid]
            with self.assertRaises(ValueError):validate_package(bad)
        for selected in ([{'key':'analyst'}],'analyst',[2]):
            bad=team_package();bad['operations']['generate']['execution_plan']['nodes'][0]['participants']=selected
            with self.assertRaises(ValueError):validate_package(bad)
        bad=team_package();bad['units'][0]['crew']['members'][1]['key']='analyst'
        with self.assertRaises(ValueError):validate_package(bad)

    def test_team_operation_without_explicit_plan_runs_crew_directly(self):
        value=team_package();value['operations']['generate'].pop('execution_plan')
        run=self.invoke(value);ctx=self.s.claim()
        self.assertEqual(ctx['task']['kind'],'work')
        with self.transport():CrewAIExecutor(self.s).execute(ctx)
        view=self.view(run);self.assertEqual(view['status'],'completed')
        self.assertEqual(len(view['crew_runs'][0]['roster']),2)

    def test_two_departments_handoff_through_accepted_immutable_artifacts(self):
        value=team_package();review=package(review=True)['operations']['generate']['execution_plan']['nodes'][1]
        value['units'][1]['crew']=dict(members=[dict(key='checker',name='Checker',role='Independent reviewer',instruction='Verify actual analysis artifacts',capabilities=['text','json'])])
        review.pop('staff');review['participants']=['checker']
        value['operations']['generate']['execution_plan']['nodes'].append(review)
        run=self.invoke(value)
        with self.transport():
            CrewAIExecutor(self.s).execute(self.s.claim())
            CrewAIExecutor(self.s).execute(self.s.claim())
        view=self.view(run);self.assertEqual(view['status'],'completed')
        self.assertEqual(len(view['crew_runs']),2)
        self.assertNotEqual(view['crew_runs'][0]['unit_id'],view['crew_runs'][1]['unit_id'])
        self.assertEqual([len(c['roster']) for c in view['crew_runs']],[2,1])

    def test_each_member_uses_its_own_frozen_model_binding(self):
        value=team_package()
        self.settings.profiles['alternate']=dict(self.settings.profiles['text'],id='alternate',model='alternate-model')
        value['units'][0]['model_id']='text'
        value['units'][0]['crew']['members'][0]['model_id']='alternate'
        run=self.invoke(value);ctx=self.s.claim()
        with self.transport():CrewAIExecutor(self.s).execute(ctx)
        with self.s.db.connect() as c:
            models=[r[0] for r in c.execute('SELECT model_id FROM requests ORDER BY created,id')]
        self.assertEqual(models,['alternate','text'])
        state=self.client.get('/api/v1/state').json()
        self.assertTrue(any(isinstance(u.get('crew'),dict) for u in state['units']))

    def test_same_installed_department_has_distinct_concurrent_execution_objects(self):
        self.register(team_package());runs=[]
        for index in range(2):
            response=self.client.post('/api/v1/apps/fixture/runs',json=dict(operation='generate',input={'marker':'JOB_'+str(index)}),headers={'Idempotency-Key':'same-department-'+str(index)})
            self.assertEqual(response.status_code,201,response.text);runs.append(response.json())
        contexts=[self.s.claim(),self.s.claim()]
        self.assertEqual(contexts[0]['task']['unit_id'],contexts[1]['task']['unit_id'])
        with self.transport(),ThreadPoolExecutor(2) as pool:list(pool.map(CrewAIExecutor(self.s).execute,contexts))
        crews=[self.view(run)['crew_runs'][0] for run in runs]
        self.assertNotEqual(crews[0]['id'],crews[1]['id'])
        self.assertFalse({m['agent_id'] for m in crews[0]['roster']}&{m['agent_id'] for m in crews[1]['roster']})
        self.assertFalse(list(self.settings.data.rglob('*.db')))

    def test_model_step_budget_is_shared_by_all_members(self):
        run=self.invoke(team_package());ctx=self.s.claim()
        ctx['snapshot']['executor']['limits']['model_steps']=1
        with self.transport():
            with self.assertRaisesRegex(ValueError,'步骤达到上限'):CrewAIExecutor(self.s).execute(ctx)
        self.assertEqual(len(self.inputs),1)
        view=self.view(run);self.assertIsNone(view['artifact_id'])
        self.assertEqual(view['crew_runs'][0]['status'],'failed')

    def test_cancel_between_members_prevents_second_model_request(self):
        run=self.invoke(team_package());ctx=self.s.claim();providers=Providers(self.s);original=providers.json
        def response(*args,**kwargs):
            value=original(*args,**kwargs)
            self.s.command(self.ctx,run['id'],dict(key='cancel-between-members',action='cancel'))
            return value
        with self.transport(),patch.object(providers,'json',side_effect=response):
            with self.assertRaises(ValueError):CrewAIExecutor(self.s,providers).execute(ctx)
        view=self.view(run);self.assertEqual(view['status'],'cancelled')
        self.assertEqual(len(self.inputs),1);self.assertIsNone(view['artifact_id'])
        self.assertEqual(view['usage']['points'],1)
