from orchpact.testing import database_url_for
from account_fixtures import create_user, register_package
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from orchpact.api import create_app
from orchpact.access import Access, password_hash
from orchpact.config import Settings
from orchpact.db import ident, now
from orchpact.runtime import Providers, Runner
from orchpact.schemas import WorkOutput


class WalletTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.hashed=password_hash('test-password-1234')

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        profile=dict(id='text',kind='text',model='test',key_env='TEST_KEY',base_url='https://example.invalid',capabilities=['text','json','search'])
        self.settings=Settings(root,'wallet-admin-test-'*3,{'text':profile},root/'var',database_url=database_url_for(root/'var'))
        self.app=create_app(self.settings,run_worker=False)
        self.admin=TestClient(self.app).__enter__();self.s=self.app.state.service;self.admin.headers['Authorization']='Bearer '+self.settings.token
        register_package(self.admin)
        self.clients=[];self.alice=self.user('alice');self.bob=self.user('bob')

    def tearDown(self):
        for client in self.clients:client.close()
        self.admin.__exit__(None,None,None);self.tmp.cleanup()

    def user(self,name):
        client=TestClient(self.app);self.clients.append(client)
        client.uid=create_user(self.s,client,name,'test-password-1234',self.hashed)
        return client

    def account(self,client=None):return (client or self.alice).get('/api/v1/auth/credits').json()
    def adjust(self,delta,user=None,key=None):
        return self.admin.post('/api/v1/admin/users/'+(user or self.alice.uid)+'/credits',headers={'Idempotency-Key':key or ident()},json={'delta':delta,'reason':'测试调整'})
    def invoke(self,client=None,key='one'):
        return (client or self.alice).post('/api/v1/apps/demo/runs',headers={'Idempotency-Key':key},json={'operation':'run','input':{}})
    def start(self):
        response=self.invoke();self.assertEqual(response.status_code,201,response.text)
        return response.json(),self.s.claim()
    def reserve(self,ctx,kind='model'):return Providers(self.s).reserve(ctx,kind,'search:tavily:text' if kind=='search' else 'text')

    def test_allocated_budget_survives_identity_restart(self):
        self.assertEqual(self.account()['credits'],{'balance':10000,'held':0,'available':10000})
        Access(self.s.db);Access(self.s.db)
        self.assertEqual(self.account()['credits']['balance'],10000)
        self.assertEqual(len([r for r in self.account()['ledger'] if r['reason']=='isolated test budget']),1)
        self.assertTrue(any(u['credits']['balance']==10000 for u in self.admin.get('/api/v1/admin/users').json()))
        self.assertEqual(self.alice.get('/api/v1/auth/me').json()['credit_policy'],{'tokens_per_point':1000,'search_points':10})

    def test_model_reservation_actual_settlement_and_duplicate_record(self):
        _,ctx=self.start();key=self.reserve(ctx)
        self.assertEqual(self.account()['credits'],{'balance':10000,'held':100,'available':9900})
        provider=Providers(self.s);provider.record(key,'completed',{'total_tokens':1500})
        self.assertEqual(self.account()['credits'],{'balance':9998.5,'held':0,'available':9998.5})
        provider.record(key,'completed',{'total_tokens':9000});provider.record(key,'failed')
        self.assertEqual(self.account()['credits']['balance'],9998.5)
        with self.s.db.connect() as c:
            self.assertEqual(json.loads(c.execute('SELECT usage FROM requests WHERE id=%s',(key,)).fetchone()[0])['total_tokens'],1500)
        self.assertEqual(len([r for r in self.account()['ledger'] if r['kind']=='settlement']),1)

    def test_search_charges_ten_even_on_failure(self):
        _,ctx=self.start();key=self.reserve(ctx,'search');Providers(self.s).record(key,'failed')
        self.assertEqual(self.account()['credits'],{'balance':9990,'held':0,'available':9990})

    def test_search_reserved_before_restart_is_settled_once(self):
        _,ctx=self.start();self.reserve(ctx,'search')
        Runner(self.s).recover();Runner(self.s).recover()
        self.assertEqual(self.account()['credits'],{'balance':9990,'held':0,'available':9990})

    def test_confirmed_zero_tokens_releases_entire_hold(self):
        _,ctx=self.start();key=self.reserve(ctx)
        Providers(self.s).record(key,'completed',{'total_tokens':0})
        self.assertEqual(self.account()['credits'],{'balance':10000,'held':0,'available':10000})

    def test_real_provider_path_settles_and_insufficient_balance_prevents_network(self):
        _,ctx=self.start();output={'summary':'完成','files':[{'path':'result.json','content':'{}'}]}
        response=httpx.Response(200,request=httpx.Request('POST','https://example.invalid'),json={'choices':[{'message':{'content':json.dumps(output)},'finish_reason':'stop'}],'usage':{'total_tokens':2500}})
        with patch.dict(os.environ,{'TEST_KEY':'fake'}),patch('httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value=response
            Providers(self.s).json(ctx,'测试',{'task':'测试'},WorkOutput)
        self.assertEqual(self.account()['credits']['balance'],9997.5)
        self.adjust(-9997.5).raise_for_status()
        with patch('httpx.Client') as client,self.assertRaisesRegex(ValueError,'积分不足'):
            Providers(self.s).json(ctx,'测试',{},WorkOutput)
        client.assert_not_called()
        with self.s.db.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM requests').fetchone()[0],1)

    def test_concurrent_searches_cannot_reserve_same_balance(self):
        _,ctx=self.start();self.adjust(-9990).raise_for_status()
        def reserve(_):
            try:return self.reserve(ctx,'search')
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=12) as pool:results=list(pool.map(reserve,range(12)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(self.account()['credits']['available'],0)
        Providers(self.s).record(next(r for r in results if r),'completed')
        self.assertEqual(self.account()['credits'],{'balance':0,'held':0,'available':0})

    def test_adjustment_idempotency_precision_reason_and_reserved_funds(self):
        self.adjust(0.123456,key='same').raise_for_status();self.adjust(0.123456,key='same').raise_for_status()
        self.assertEqual(self.account()['credits']['balance'],10000.123456)
        self.assertEqual(self.adjust(1,key='same').status_code,409)
        for delta in (0,0.0000001):self.assertIn(self.adjust(delta).status_code,(409,422))
        response=self.admin.post('/api/v1/admin/users/'+self.alice.uid+'/credits',headers={'Idempotency-Key':'blank'},json={'delta':1,'reason':' '})
        self.assertEqual(response.status_code,409)
        _,ctx=self.start();self.reserve(ctx)
        self.assertEqual(self.adjust(-9950).status_code,409)
        self.assertEqual(self.account()['credits']['held'],100)

    def test_concurrent_admin_replay_is_one_adjustment(self):
        def adjust(_):return self.s.access.wallets.adjust(self.alice.uid,50,'并发操作','same-operation','platform_admin')
        with ThreadPoolExecutor(max_workers=10) as pool:list(pool.map(adjust,range(10)))
        self.assertEqual(self.account()['credits']['balance'],10050)
        self.assertEqual(len([r for r in self.account()['ledger'] if r['reason']=='并发操作']),1)

    def test_zero_balance_blocks_new_invocation_but_idempotent_read_survives(self):
        first=self.invoke();self.assertEqual(first.status_code,201)
        self.adjust(-10000).raise_for_status()
        self.assertEqual(self.invoke().json()['id'],first.json()['id'])
        self.assertEqual(self.invoke(key='new').status_code,409)
        self.adjust(100).raise_for_status();self.assertEqual(self.invoke(key='new').status_code,201)

    def test_zero_balance_retry_does_not_consume_attempts(self):
        _,ctx=self.start();self.s.fail(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],'积分不足测试')
        self.adjust(-10000).raise_for_status()
        path='/api/v1/work-items/'+ctx['task']['id']+'/retry'
        self.assertEqual(self.alice.post(path,json={}).status_code,409)
        with self.s.db.connect() as c:
            row=c.execute('SELECT status,turns FROM tasks WHERE id=%s',(ctx['task']['id'],)).fetchone()
        self.assertEqual(tuple(row),('failed',1))
        self.adjust(100).raise_for_status();self.alice.post(path,json={}).raise_for_status()
        self.assertIsNotNone(self.s.claim())

    def test_collaboration_retry_charges_original_initiator(self):
        space=self.alice.post('/api/v1/workspaces',json={'name':'协作空间'}).json()['id']
        code=self.alice.post('/api/v1/workspaces/'+space+'/invitations',json={}).json()['code']
        self.bob.post('/api/v1/workspaces/join',json={'code':code}).raise_for_status()
        self.alice.headers['X-Workspace-Id']=space;self.bob.headers['X-Workspace-Id']=space
        job,ctx=self.start();key=self.reserve(ctx,'search');Providers(self.s).record(key,'completed')
        self.assertEqual(job['payer_user_id'],self.alice.uid)
        self.s.fail(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],'重试')
        self.bob.post('/api/v1/work-items/'+ctx['task']['id']+'/retry',json={}).raise_for_status()
        resumed=self.s.claim();key=self.reserve(resumed);Providers(self.s).record(key,'completed',{'total_tokens':1000})
        self.assertEqual(self.account()['credits']['balance'],9989)
        self.assertEqual(self.account(self.bob)['credits']['balance'],10000)

    def test_cancellation_and_restart_do_not_release_unknown_provider_cost(self):
        job,ctx=self.start();key=self.reserve(ctx)
        self.alice.post('/api/v1/runs/'+job['id']+'/cancel',json={}).raise_for_status()
        Runner(self.s).recover();Access(self.s.db)
        self.assertEqual(self.account()['credits']['held'],100)
        Providers(self.s).record(key,'completed',{'total_tokens':2000})
        self.assertEqual(self.account()['credits']['balance'],9998)
        self.assertEqual(self.account()['credits']['held'],0)
        with self.assertRaises(ValueError):self.s.complete_work(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],'迟到',[{'path':'x','content':'x'}])

    def test_missing_usage_admin_reconciliation_is_final_and_audited(self):
        _,ctx=self.start();key=self.reserve(ctx)
        endpoint='/api/v1/admin/credit-requests/'+key+'/settle'
        self.assertEqual(self.admin.post(endpoint,json={'points':2.5,'reason':'核对'}).status_code,409)
        Providers(self.s).record(key,'unknown')
        self.assertEqual(self.account()['credits']['held'],100)
        self.admin.post(endpoint,json={'points':2.5,'reason':'核对供应商'}).raise_for_status()
        self.admin.post(endpoint,json={'points':2.5,'reason':'重复提交'}).raise_for_status()
        self.assertEqual(self.admin.post(endpoint,json={'points':3,'reason':'不同核定'}).status_code,409)
        self.assertEqual(self.account()['credits'],{'balance':9997.5,'held':0,'available':9997.5})
        self.assertIn('人工核定',self.account()['ledger'][0]['reason'])
        Providers(self.s).record(key,'completed',{'total_tokens':9000})
        self.assertEqual(self.account()['credits']['balance'],9997.5)

    def test_private_wallet_and_administration_permissions(self):
        for method,path,body in [('GET','/api/v1/admin/users/'+self.bob.uid+'/credits',None),('POST','/api/v1/admin/users/'+self.bob.uid+'/credits',{'delta':1,'reason':'越权'}),('POST','/api/v1/admin/credit-requests/missing/settle',{'points':0,'reason':'越权'})]:
            response=self.alice.request(method,path,json=body,headers={'Idempotency-Key':'bad'})
            self.assertEqual(response.status_code,403)
        self.assertEqual(self.account()['credits']['balance'],10000)
        self.assertEqual(self.account(self.bob)['credits']['balance'],10000)

    def test_wallet_creation_starts_at_zero(self):
        user=ident()
        with self.s.db.tx() as c:
            c.execute('INSERT INTO users(id,username,password,created) VALUES(%s,%s,%s,%s)',(user,'wallet-user',self.hashed,now()))
            self.s.access.wallets.create(c,user)
        Access(self.s.db);Access(self.s.db)
        response=self.admin.get('/api/v1/admin/users/'+user+'/credits')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['credits'],dict(balance=0,held=0,available=0))
        self.assertEqual(response.json()['ledger'],[])

    def test_provider_overrun_records_debt_and_blocks_new_calls(self):
        _,ctx=self.start();self.adjust(-9900).raise_for_status();key=self.reserve(ctx)
        Providers(self.s).record(key,'completed',{'total_tokens':200000})
        self.assertEqual(self.account()['credits']['balance'],-100)
        with self.assertRaisesRegex(ValueError,'积分不足'):self.reserve(ctx,'search')


if __name__=='__main__':unittest.main()
