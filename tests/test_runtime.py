"""Real call admission, durable receipts, settlement, recovery and executor regressions."""
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch
import httpx

from orchpact.adapters.database import ident, now, dumps, loads
from orchpact.adapters.providers import Providers
from orchpact.services.executor import CrewAIExecutor
from orchpact.kernel.models import WorkOutput
from orchpact.kernel.errors import Forbidden
from orchpact.provider_http import write_receipt
from runtime_fixtures import KernelFixture, package


class RuntimeTests(KernelFixture):
    def response(self, value=7, usage=None, raw=None):
        return httpx.Response(200,request=httpx.Request('POST','https://example.invalid/v1/chat/completions'),json=dict(id='provider-fixture',choices=[dict(finish_reason='stop',message=dict(content=raw or dumps(dict(files={'result.json':{'value':value}},summary='fixture'))))],usage={'total_tokens':1000} if usage is None else usage))

    def worker(self):
        run=self.invoke();return run,self.s.claim(),Providers(self.s)

    def test_real_adapter_settles_once_and_delivers(self):
        run,ctx,p=self.worker()
        with patch.dict(os.environ,{'RUNTIME_FIXTURE_KEY':'fixture-not-real'}),patch('orchpact.adapters.providers.httpx.Client') as network:
            network.return_value.__enter__.return_value.post.return_value=self.response()
            CrewAIExecutor(self.s,p).execute(ctx)
            self.assertEqual(network.return_value.__enter__.return_value.post.call_count,1)
        result=self.view(run);self.assertEqual(result['status'],'completed');self.assertEqual(result['usage']['points'],1)
        with self.s.db.connect() as c:
            before=self.s.access.wallets.view(c,self.user)
            r=dict(c.execute('SELECT * FROM requests').fetchone())
        self.assertEqual(before['held'],0);self.assertEqual(before['balance'],9999)
        p.record(r['id'],'completed',{'total_tokens':99999},'late-response')
        with self.s.db.connect() as c:
            self.assertEqual(self.s.access.wallets.view(c,self.user),before)
            self.assertEqual(loads(c.execute('SELECT usage FROM requests WHERE id=%s',(r['id'],)).fetchone()[0])['total_tokens'],1000)

    def test_missing_usage_remains_held_and_manual_settlement_does_not_invent_tokens(self):
        run,ctx,p=self.worker()
        with patch.dict(os.environ,{'RUNTIME_FIXTURE_KEY':'fixture-not-real'}),patch('orchpact.adapters.providers.httpx.Client') as network:
            network.return_value.__enter__.return_value.post.return_value=self.response(usage={})
            CrewAIExecutor(self.s,p).execute(ctx)
        result=self.view(run);self.assertFalse(result['usage']['complete']);r=result['requests'][0]
        self.assertEqual(r['accounting']['state'],'held')
        with self.s.db.connect() as c:self.assertGreater(self.s.access.wallets.view(c,self.user)['held'],0)
        self.s.access.wallets.reconcile(r['id'],2,'verified supplier record',self.user)
        self.s.access.wallets.reconcile(r['id'],2,'idempotent repeat',self.user)
        final=self.view(run)['requests'][0]
        self.assertEqual(final['accounting']['state'],'settled')
        self.assertEqual(final['accounting']['settled_points'],2)
        self.assertEqual(final['credits']['status'],'missing_usage')
        with self.s.db.connect() as c:
            self.assertEqual(self.s.access.wallets.view(c,self.user)['balance'],9998)
            self.assertEqual(loads(c.execute('SELECT usage FROM requests WHERE id=%s',(r['id'],)).fetchone()[0]),{})

    def test_receipt_recovers_settlement_after_interruption(self):
        run,ctx,p=self.worker();request=p.reserve(ctx,'model','text',token_bound=2000)
        write_receipt(self.settings.data/'receipts'/ctx['task']['attempt_id']/(request+'.json'),dict(usage={'total_tokens':500},response_id='paid-response'))
        self.assertEqual(p.recover_receipts(),1);self.assertEqual(p.recover_receipts(),0)
        with self.s.db.connect() as c:
            self.assertEqual(self.s.access.wallets.view(c,self.user)['balance'],9999.5)
            self.assertEqual(c.execute('SELECT status FROM requests WHERE id=%s',(request,)).fetchone()[0],'completed')

    def test_transport_failure_never_automatically_replays(self):
        run,ctx,p=self.worker()
        with patch.dict(os.environ,{'RUNTIME_FIXTURE_KEY':'fixture-not-real'}),patch('orchpact.adapters.providers.httpx.Client') as network:
            network.return_value.__enter__.return_value.post.side_effect=httpx.ReadTimeout('fixture')
            with self.assertRaises(RuntimeError):CrewAIExecutor(self.s,p).execute(ctx)
            self.assertEqual(network.return_value.__enter__.return_value.post.call_count,1)
        with self.s.db.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM requests').fetchone()[0],1)
            self.assertGreater(self.s.access.wallets.view(c,self.user)['held'],0)

    def test_json_repair_is_bounded_and_each_request_is_charged(self):
        run,ctx,p=self.worker()
        with patch.dict(os.environ,{'RUNTIME_FIXTURE_KEY':'fixture-not-real'}),patch('orchpact.adapters.providers.httpx.Client') as network:
            network.return_value.__enter__.return_value.post.return_value=self.response(raw='{"files":')
            with self.assertRaises(ValueError):CrewAIExecutor(self.s,p).execute(ctx)
            self.assertEqual(network.return_value.__enter__.return_value.post.call_count,2)
        with self.s.db.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM requests').fetchone()[0],2)
            self.assertEqual(self.s.access.wallets.view(c,self.user)['balance'],9998)

    def test_policy_budget_blocks_before_transport(self):
        self.client.put('/api/v1/policies/platform',json=dict(revision=0,policy=dict(budget_points=1)))
        run,ctx,p=self.worker()
        with patch('orchpact.adapters.providers.httpx.Client') as network:
            with self.assertRaises(ValueError):CrewAIExecutor(self.s,p).execute(ctx)
            network.assert_not_called()
        with self.s.db.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM requests').fetchone()[0],0)

    def test_call_capability_is_bound_to_role_not_model_superset(self):
        self.settings.profiles['text']['capabilities'].append('search')
        run,ctx,p=self.worker()
        with self.assertRaises(Forbidden):p.reserve(ctx,'search','search:tavily:search')

    def test_revoked_authorization_prevents_transport(self):
        run,ctx,p=self.worker()
        with self.s.db.tx() as c:c.execute('UPDATE mandates SET revoked=1 WHERE subject=%s',('task:'+ctx['task']['id'],))
        with patch('orchpact.adapters.providers.httpx.Client') as network:
            with self.assertRaises(Forbidden):p.json(ctx,'fixture',{},WorkOutput)
            network.assert_not_called()

    def test_mandate_budget_includes_prior_attempts(self):
        run,ctx,p=self.worker()
        with self.s.db.tx() as c:c.execute('UPDATE mandates SET budget=1 WHERE subject=%s',('task:'+ctx['task']['id'],))
        request=p.reserve(ctx,'model','text',token_bound=500)
        p.record(request,'completed',{'total_tokens':900})
        with self.assertRaises(ValueError):p.reserve(ctx,'model','text',token_bound=500)

    def test_restart_marks_unknown_and_keeps_payer_reservation(self):
        run,ctx,p=self.worker();r=p.reserve(ctx,'model','text',token_bound=2000)
        self.app.state.runner.recover()
        with self.s.db.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM requests WHERE id=%s',(r,)).fetchone()[0],'unknown')
            self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=%s',(ctx['task']['id'],)).fetchone()[0],'interrupted')
            self.assertEqual(self.s.access.wallets.view(c,self.user)['held'],2)
        with patch('orchpact.adapters.providers.httpx.Client') as network:
            self.assertFalse(self.app.state.runner.step());network.assert_not_called()

    def test_cancelled_attempt_records_paid_receipt_without_publication(self):
        run,ctx,p=self.worker()
        def response(*args,**kwargs):
            self.s.command(self.ctx,run['id'],dict(key='cancel-during-call',action='cancel'))
            return self.response()
        with patch.dict(os.environ,{'RUNTIME_FIXTURE_KEY':'fixture-not-real'}),patch('orchpact.adapters.providers.httpx.Client') as network:
            network.return_value.__enter__.return_value.post.side_effect=response
            with self.assertRaises(ValueError):CrewAIExecutor(self.s,p).execute(ctx)
        self.assertEqual(self.view(run)['status'],'cancelled')
        with self.s.db.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0],0)
            self.assertEqual(self.s.access.wallets.view(c,self.user)['balance'],9999)

    def test_retry_preserves_payer_and_durable_artifacts(self):
        run,ctx,p=self.worker();self.s.fail(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],'fixture failure')
        response=self.client.post('/api/v1/work-items/'+ctx['task']['id']+'/retry',headers={'Idempotency-Key':'retry-one'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.post('/api/v1/work-items/'+ctx['task']['id']+'/retry',headers={'Idempotency-Key':'retry-one'}).status_code,200)
        newctx=self.s.claim();self.assertNotEqual(newctx['task']['attempt_id'],ctx['task']['attempt_id']);self.finish(newctx)
        self.assertEqual(self.view(run)['payer_user_id'],self.user)
