"""Real CrewAI loops, with isolated PostgreSQL and synthetic HTTP providers."""
import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch
import httpx

from runtime_fixtures import KernelFixture, package
from orchpact.adapters.providers import Providers
from orchpact.services.executor import CrewAIExecutor
from orchpact.kernel.errors import Forbidden
from orchpact.services.agent_steps import AgentSteps, recover_steps
from orchpact.provider_http import write_receipt

HttpClient = httpx.Client


class SearchObservationTests(unittest.TestCase):
    def test_long_sources_keep_valid_json_all_identities_and_calls(self):
        from copy import deepcopy
        from orchpact.adapters.crewai import search_observation
        value=dict(sources=[dict(url='https://evidence.example/'+str(i), title='Source '+str(i),
            request_id='actual-request', excerpt='long evidence '*1000) for i in range(10)],
            calls=[dict(request_id='actual-request', status='completed', results=10)])
        original=deepcopy(value)
        observation=search_observation(value)
        content=json.loads(observation['content'])
        self.assertTrue(observation['truncated'])
        self.assertLessEqual(len(observation['content']),24000)
        self.assertEqual([s['url'] for s in content['sources']],[s['url'] for s in original['sources']])
        self.assertEqual(content['calls'],original['calls'])
        self.assertTrue(all(s['request_id']=='actual-request' and s['excerpt_truncated'] for s in content['sources']))
        self.assertEqual(value,original)

    def test_oversized_metadata_omits_whole_sources_explicitly(self):
        from orchpact.adapters.crewai import search_observation
        value=dict(sources=[dict(url='https://evidence.example/'+str(i)+'x'*3000,excerpt='text') for i in range(10)],
                   calls=[dict(request_id='actual-request',status='completed')])
        observation=search_observation(value)
        content=json.loads(observation['content'])
        self.assertTrue(observation['truncated'])
        self.assertLessEqual(len(observation['content']),24000)
        self.assertGreater(content['omitted_sources'],0)
        self.assertEqual(content['calls'],value['calls'])
        self.assertEqual(content['sources'][0]['url'],value['sources'][0]['url'])


class CrewAIExecutionTests(KernelFixture):
    def worker(self, search=True, review=False):
        manifest=package(review=review)
        if search:
            self.settings.profiles['text']['capabilities'].append('search')
            self.settings.profiles['text']['search_config']=dict(mode='aggregate',providers=[dict(id='tavily',key_env='SEARCH_FIXTURE')],max_results=3,fetch_top=0)
            manifest['operations']['generate']['execution_plan']['nodes'][0]['staff']['capabilities'].append('search')
        run=self.invoke(manifest)
        return run,self.s.claim(),Providers(self.s)

    def transport(self, decisions, after_search=None):
        self.calls=[];answers=iter(decisions)
        def send(request):
            self.calls.append(request.url.host)
            if request.url.host=='api.tavily.com':
                if after_search:after_search()
                return httpx.Response(200,json={'results':[dict(url='https://evidence.example/source',title='Synthetic evidence',content='value=7')]})
            value=next(answers)
            if isinstance(value,Exception):raise value
            return httpx.Response(200,json=dict(id='synthetic-response',choices=[dict(finish_reason='stop',message={'content':json.dumps(value)})],usage={'total_tokens':1000}))
        stack=contextlib.ExitStack()
        stack.enter_context(patch.dict(os.environ,{'RUNTIME_FIXTURE_KEY':'test-only','SEARCH_FIXTURE':'test-only'}))
        stack.enter_context(patch('orchpact.adapters.providers.httpx.Client',side_effect=lambda **kw:HttpClient(transport=httpx.MockTransport(send),**kw)))
        return stack

    def final(self):return {'files':{'result.json':{'value':7}}}
    def search(self,query='verify value'):return dict(tool='search_evidence',arguments=dict(query=query))

    def test_real_crewai_selects_tools_twice_and_observes_results(self):
        run,ctx,p=self.worker()
        output=io.StringIO()
        with self.transport([self.search(),self.search('check another source'),self.final()]),contextlib.redirect_stdout(output):
            CrewAIExecutor(self.s,p).execute(ctx)
        self.assertEqual(output.getvalue(),'')
        view=self.view(run);self.assertEqual(view['status'],'completed')
        self.assertEqual([s['kind'] for s in view['agent_steps']],['model','tool','model','tool','model'])
        self.assertTrue(all(s['status']=='completed' for s in view['agent_steps']))
        self.assertEqual(view['usage']['points'],23)
        self.assertEqual(self.calls.count('example.invalid'),3)
        self.assertEqual(self.calls.count('api.tavily.com'),2)
        receipts=list((self.settings.data/'receipts'/ctx['task']['attempt_id']).glob('*.json'))
        models=[json.loads(path.read_text()) for path in receipts if json.loads(path.read_text()).get('model_id')]
        self.assertTrue(any('Synthetic evidence' in json.dumps(m['messages']) for m in models))
        self.assertNotIn('test-only',json.dumps(view))
        with self.s.db.connect() as c:self.assertEqual(self.s.access.wallets.view(c,self.user)['balance'],9977)

    def test_revocation_after_tool_blocks_next_model_without_replay(self):
        run,ctx,p=self.worker()
        def revoke():
            with self.s.db.tx() as c:c.execute('UPDATE mandates SET revoked=1 WHERE subject=%s',('task:'+ctx['task']['id'],))
        with self.transport([self.search(),self.final()],revoke):
            with self.assertRaises(Forbidden):CrewAIExecutor(self.s,p).execute(ctx)
        self.assertEqual(self.calls,['example.invalid','api.tavily.com'])
        self.assertEqual(self.view(run)['artifact_id'],None)

    def test_cancel_during_tool_preserves_cost_and_stops_next_action(self):
        run,ctx,p=self.worker()
        def cancel():self.s.command(self.ctx,run['id'],dict(key='cancel-tool',action='cancel'))
        with self.transport([self.search(),self.final()],cancel):
            with self.assertRaises(ValueError):CrewAIExecutor(self.s,p).execute(ctx)
        view=self.view(run);self.assertEqual(view['status'],'cancelled')
        self.assertEqual(view['usage']['points'],11)
        self.assertEqual(self.calls,['example.invalid','api.tavily.com'])
        self.assertIsNone(view['artifact_id'])

    def test_model_step_limit_does_not_force_success(self):
        run,ctx,p=self.worker();ctx['snapshot']['executor']['limits']['model_steps']=1
        with self.transport([self.search(),self.final()]):
            with self.assertRaisesRegex(ValueError,'步骤达到上限'):CrewAIExecutor(self.s,p).execute(ctx)
        self.assertEqual(self.calls,['example.invalid','api.tavily.com'])
        self.assertIsNone(self.view(run)['artifact_id'])

    def test_tool_step_limit_does_not_repeat_search(self):
        run,ctx,p=self.worker();ctx['snapshot']['executor']['limits']['tool_steps']=1
        with self.transport([self.search(),self.search('different query')]):
            with self.assertRaisesRegex(ValueError,'步骤达到上限'):CrewAIExecutor(self.s,p).execute(ctx)
        self.assertEqual(self.calls.count('api.tavily.com'),1)

    def test_invalid_tool_arguments_stop_before_tool_transport(self):
        run,ctx,p=self.worker()
        with self.transport([self.search('x'*129)]):
            with self.assertRaises(ValueError):CrewAIExecutor(self.s,p).execute(ctx)
        self.assertEqual(self.calls,['example.invalid'])

    def test_budget_exhaustion_between_decisions_stops_before_next_request(self):
        run,ctx,p=self.worker()
        def exhaust():
            with self.s.db.tx() as c:
                wallet=self.s.access.wallets.view(c,self.user)
            self.s.access.wallets.adjust(self.user,-wallet['available'],'test budget exhaustion','exhaust',self.user)
        with self.transport([self.search(),self.final()],exhaust):
            with self.assertRaisesRegex(ValueError,'积分不足'):CrewAIExecutor(self.s,p).execute(ctx)
        self.assertEqual(self.calls,['example.invalid','api.tavily.com'])

    def test_unlisted_artifact_is_denied(self):
        run,writer,p=self.worker(search=False,review=True);self.finish(writer)
        ctx=self.s.claim()
        with self.transport([dict(tool='read_artifact',arguments=dict(artifact_id='outside-current-dependencies'))]):
            with self.assertRaises(Forbidden):CrewAIExecutor(self.s,p).execute(ctx)
        self.assertEqual(self.calls,['example.invalid'])

    def test_authorized_artifact_read_returns_to_crewai_then_review(self):
        run,writer,p=self.worker(search=False,review=True);self.finish(writer)
        ctx=self.s.claim();artifact=ctx['deliveries'][0]['artifact_id']
        final={'files':{'audit.json':dict(passed=True,issues=[],reviewed_artifact_ids=[artifact])}}
        with self.transport([dict(tool='read_artifact',arguments=dict(artifact_id=artifact)),final]):
            CrewAIExecutor(self.s,p).execute(ctx)
        self.assertEqual(self.view(run)['status'],'completed')
        self.assertIn('read_artifact',[step['name'] for step in self.view(run)['agent_steps']])

    def test_step_recovery_uses_evidence_and_never_invokes_model(self):
        run,ctx,p=self.worker(search=False);steps=AgentSteps(p,ctx)
        confirmed=steps.begin('model','crewai');unknown=steps.begin('model','crewai')
        write_receipt(self.settings.data/'agent-steps'/ctx['task']['attempt_id']/(confirmed+'.json'),dict(step_id=confirmed,status='completed',output=self.final()))
        with patch('orchpact.adapters.providers.httpx.Client') as network:
            self.app.state.runner.recover();recover_steps(self.s);network.assert_not_called()
        with self.s.db.connect() as c:
            states={r['id']:r['status'] for r in c.execute('SELECT * FROM agent_steps')}
        self.assertEqual(states[confirmed],'completed');self.assertEqual(states[unknown],'unknown')
        self.assertEqual(next(t for t in self.view(run)['tasks'] if t['id']==ctx['task']['id'])['status'],'interrupted')

    def test_durable_success_is_not_overwritten_by_database_failure(self):
        run,ctx,p=self.worker(search=False);steps=AgentSteps(p,ctx);key=steps.begin('model','crewai')
        path=self.settings.data/'agent-steps'/ctx['task']['attempt_id']/(key+'.json')
        write_receipt(path,dict(step_id=key,status='completed',output=self.final()))
        steps.fail(key,RuntimeError('local write failed'));recover_steps(self.s)
        self.assertEqual(json.loads(path.read_text())['status'],'completed')
        with self.s.db.connect() as c:self.assertEqual(c.execute('SELECT status FROM agent_steps WHERE id=%s',(key,)).fetchone()[0],'completed')

    def test_framework_version_mismatch_blocks_before_transport(self):
        run,ctx,p=self.worker(search=False)
        ctx['snapshot']['executor']['version']='mismatched-runtime'
        with patch('orchpact.adapters.providers.httpx.Client') as network:
            with self.assertRaisesRegex(ValueError,'version differs'):CrewAIExecutor(self.s,p).execute(ctx)
            network.assert_not_called()
