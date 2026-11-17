from orchpact.testing import database_url_for
from account_fixtures import create_user, register_package
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from concurrent.futures import ThreadPoolExecutor
import copy

import httpx
from fastapi.testclient import TestClient
from orchpact.api import create_app
from orchpact.config import Settings
from orchpact.runtime import Providers, CrewAIExecutor
from orchpact.search import SearchQuotaExceeded
from orchpact.search import configuration, canonical_url, merge, normalize

HttpClient = httpx.Client


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.config = dict(mode='aggregate', providers=[dict(id=n, key_env='SEARCH_TEST_'+n.upper()) for n in ('deepseek', 'you', 'tavily')], max_results=5, fetch_top=0)
        profile = dict(id='research', kind='text', model='test', key_env='TEST_KEY', base_url='https://example.invalid', capabilities=['text','json','search'], search_config=self.config)
        self.settings = Settings(self.root, 'test-search-admin-'*3, {'research':profile}, self.root/'var',database_url=database_url_for(self.root/'var'))
        self.app = create_app(self.settings, run_worker=False)
        self.admin = TestClient(self.app).__enter__(); self.s = self.app.state.service; self.admin.headers['Authorization'] = 'Bearer '+self.settings.token
        register_package(self.admin)
        self.user = TestClient(self.app)
        create_user(self.s,self.user,'search-user','test-password-1234')
        self.uid = self.user.get('/api/v1/auth/me').json()['user_id']
        self.project = self.user.post('/api/v1/apps/demo/runs', headers={'Idempotency-Key':'search'}, json={'operation':'run','input':{}}).json()['id']
        self.ctx = self.s.claim(); self.provider = Providers(self.s)
        self.config = self.ctx['snapshot']['profile']['search_config']
        self.env = patch.dict(os.environ, {p['key_env']:'fake-'+p['id'] for p in self.config['providers']}); self.env.start()
        self.requests = []

    def tearDown(self):
        self.env.stop(); self.user.close(); self.admin.__exit__(None,None,None); self.tmp.cleanup()

    def transport(self, handler):
        def send(request):
            self.requests.append(request)
            return handler(request)
        return patch('orchpact.runtime.httpx.Client', side_effect=lambda **kw: HttpClient(transport=httpx.MockTransport(send), **kw))

    def success(self, request):
        if request.url.path.endswith('/fetch'):
            return httpx.Response(200,json={'title':'抓取原文','content':'# 经抓取的网页正文'})
        if request.url.host == 'api.deepseek.com':
            return httpx.Response(200,json={'results':[{'url':'https://original.example/report?utm_source=deepseek','title':'原始统计','summary':'统计摘要','date':'2026-10-01T00:00:00Z','authority':4}]})
        if request.url.host == 'ydc-index.io':
            return httpx.Response(200,json={'results':{'web':[{'url':'https://original.example/report','title':'同一统计','contents':{'highlights':['另一段摘要']},'page_age':'2026-10-02'}], 'news':[{'url':'https://independent.example/news','title':'另一发布者','description':'独立报道'}]}})
        return httpx.Response(200,json={'results':[{'url':'https://third.example/data','title':'第三个来源','raw_content':'实际正文','published_date':'2026-09-01'}],'usage':{'credits':1}})

    def rows(self):
        with self.s.db.connect() as c:return [dict(r) for r in c.execute('SELECT * FROM requests ORDER BY created')]

    def balance(self):return self.user.get('/api/v1/auth/credits').json()['credits']

    def limit(self, count):
        with self.s.db.tx() as c:c.execute('UPDATE projects SET search_limit=%s WHERE id=%s',(count,self.project))

    def test_aggregate_protocols_dedup_provenance_receipts_and_wallet(self):
        with self.transport(self.success):result = self.provider.search(self.ctx,'目标行业公开数据')
        self.assertEqual(len(self.requests),3); self.assertEqual(len(result['sources']),3)
        first = result['sources'][0]
        self.assertEqual(first['providers'],['deepseek','you']); self.assertEqual(len(first['retrievals']),2)
        self.assertEqual(first['url'],'https://original.example/report?utm_source=deepseek')
        self.assertEqual(first['retrievals'][1]['published_date'],'2026-10-02')
        self.assertEqual(first['authority'],4); self.assertIn('不是独立证据',result['verification_note'])
        bodies = [json.loads(r.content) for r in self.requests]
        self.assertEqual(bodies[0],dict(query='目标行业公开数据',count=5,need_content=True))
        self.assertEqual(bodies[1]['extraction'],{'extraction_mode':'highlights'})
        self.assertEqual(self.requests[1].headers['X-API-Key'],'fake-you')
        self.assertEqual(self.requests[0].headers['Authorization'],'Bearer fake-deepseek')
        self.assertEqual(self.balance(),dict(balance=9970,held=0,available=9970))
        self.assertEqual([r['model_id'] for r in self.rows()],['search:deepseek:search','search:you:search','search:tavily:search'])
        for row in self.rows():
            receipt=json.loads((self.settings.data/'receipts'/self.ctx['task']['attempt_id']/(row['id']+'.json')).read_text())
            self.assertIn(receipt['provider'],('deepseek','you','tavily'))
            self.assertNotIn('fake-',json.dumps(receipt))
        usage=self.user.get('/api/v1/apps/demo/runs/'+self.project).json()['usage']
        self.assertEqual((usage['points'],usage['search_requests']),(30,3))

    def test_fallback_after_http_failure_stops_at_first_useful_result(self):
        self.config['mode']='fallback'
        def handler(r):return httpx.Response(429,json={'error':'secret body fake-deepseek'}) if r.url.host=='api.deepseek.com' else self.success(r)
        with self.transport(handler):result=self.provider.search(self.ctx,'核验')
        self.assertEqual(len(self.requests),2); self.assertTrue(result['limited'])
        self.assertEqual([c['status'] for c in result['calls']],['failed','completed'])
        self.assertEqual(self.balance()['balance'],9980)
        self.assertNotIn('fake-deepseek',json.dumps(result))

    def test_fallback_on_empty_and_malformed_results(self):
        self.config['mode']='fallback'
        def handler(r):
            if r.url.host=='api.deepseek.com':return httpx.Response(200,json={'results':[]})
            if r.url.host=='ydc-index.io':return httpx.Response(200,json={'results':[]})
            return self.success(r)
        with self.transport(handler):result=self.provider.search(self.ctx,'核验')
        self.assertEqual([c['status'] for c in result['calls']],['completed','failed','completed'])
        self.assertEqual(self.balance()['balance'],9970)

    def test_all_fail_has_no_fake_evidence_and_charges_actual_attempts(self):
        with self.transport(lambda r:httpx.Response(401,json={'message':'private body'})),self.assertRaisesRegex(RuntimeError,'未获得可用证据'):
            self.provider.search(self.ctx,'核验')
        self.assertEqual(len(self.rows()),3); self.assertTrue(all(r['status']=='failed' for r in self.rows()))
        self.assertEqual(self.balance()['balance'],9970)

    def test_source_limit_skips_only_exhausted_provider_without_charge_or_network(self):
        self.limit(2)
        with self.transport(self.success):
            for _ in range(2):self.provider.search_call(self.ctx,self.config['providers'][0],'旧查询',5)
            result=self.provider.search(self.ctx,'核验')
        self.assertEqual(len(self.requests),4); self.assertEqual(len(self.rows()),4)
        self.assertEqual([c['status'] for c in result['calls']],['skipped','completed','completed'])
        self.assertEqual(self.balance()['balance'],9960)
        with self.transport(self.success):self.provider.search(self.ctx,'另一次核验')
        with self.transport(self.success),self.assertRaisesRegex(RuntimeError,'未获得可用证据'):self.provider.search(self.ctx,'再次核验')
        self.assertEqual(len(self.requests),6)
        self.assertEqual(self.provider.search_remaining(self.ctx),0)

    def test_agent_receives_per_source_remaining_budget(self):
        self.limit(1)
        with self.transport(self.success):self.provider.search_call(self.ctx,self.config['providers'][0],'旧查询',5)
        ctx=copy.deepcopy(self.ctx);ctx['task']['kind']='work'
        ctx['snapshot']['agent']=dict(name='Search role',role='Find evidence',prompt='',capabilities=json.dumps(['text','json','search']))
        class PlannerReached(Exception):pass
        self.provider.json=Mock(side_effect=PlannerReached)
        with self.assertRaises(PlannerReached):CrewAIExecutor(self.s,self.provider).execute(ctx)
        args=self.provider.json.call_args.args
        self.assertEqual(args[2]['search_budget'],dict(deepseek=0,you=1,tavily=1))
        self.assertIn('search_evidence',[tool['name'] for tool in args[2]['agent_tools']])

    def test_exhausted_sources_are_not_exposed_as_tools(self):
        self.limit(1)
        with self.transport(self.success):self.provider.search(self.ctx,'原查询')
        ctx=copy.deepcopy(self.ctx);ctx['task']['kind']='work'
        ctx['snapshot']['agent']=dict(name='Search role',role='Find evidence',prompt='',capabilities=json.dumps(['text','json','search']))
        class AgentReached(Exception):pass
        self.provider.json=Mock(side_effect=AgentReached)
        with self.assertRaises(AgentReached):CrewAIExecutor(self.s,self.provider).execute(ctx)
        inputs=self.provider.json.call_args.args[2]
        self.assertEqual(inputs['agent_tools'],[])
        self.assertEqual(inputs['search_budget'],dict(deepseek=0,you=0,tavily=0))

    def test_supplier_quota_error_does_not_block_other_sources_in_aggregate_mode(self):
        for status in (402,429,432):
            with self.subTest(status=status):
                def handler(r):
                    return httpx.Response(status,json={'error':'quota exceeded'}) if r.url.host=='api.deepseek.com' else self.success(r)
                with self.transport(handler):result=self.provider.search(self.ctx,'核验')
                self.assertEqual([c['status'] for c in result['calls']],['failed','completed','completed'])
                self.assertTrue(result['sources']);self.assertTrue(result['limited'])

    def test_source_limit_reservation_race_does_not_block_other_sources(self):
        reserve=self.provider.reserve
        def race(ctx,kind,model,**kwargs):
            if model=='search:deepseek:search':raise SearchQuotaExceeded('另一个员工已占最后名额')
            return reserve(ctx,kind,model,**kwargs)
        with patch.object(self.provider,'reserve',side_effect=race),self.transport(self.success):
            result=self.provider.search(self.ctx,'核验')
        self.assertEqual([c['status'] for c in result['calls']],['skipped','completed','completed'])
        self.assertEqual(len(self.requests),2)

    def test_concurrent_reservations_are_atomic_per_source(self):
        self.limit(1)
        def reserve(source):
            try:return self.provider.reserve(self.ctx,'search','search:'+source+':search')
            except SearchQuotaExceeded:return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            results=list(pool.map(reserve,['deepseek']*8+['you']*8+['tavily']*8))
        self.assertEqual(sum(r is not None for r in results),3)
        self.assertEqual({r['model_id'] for r in self.rows()},{'search:'+n+':search' for n in ('deepseek','you','tavily')})


    def test_insufficient_balance_stops_before_next_provider_network(self):
        self.s.access.wallets.adjust(self.uid,-9985,'测试余额','balance','platform_admin')
        with self.transport(self.success),self.assertRaisesRegex(ValueError,'积分不足'):
            self.provider.search(self.ctx,'核验')
        self.assertEqual(len(self.requests),1); self.assertEqual(len(self.rows()),1)
        self.assertEqual(self.balance(),dict(balance=5,held=0,available=5))

    def test_fetch_enriches_real_result_and_is_separately_metered(self):
        self.config['fetch_top']=1
        with self.transport(self.success):result=self.provider.search(self.ctx,'核验')
        self.assertEqual(len(self.requests),4)
        self.assertEqual(json.loads(self.requests[-1].content),{'url':'https://original.example/report?utm_source=deepseek'})
        self.assertEqual(result['sources'][0]['fetched_content'],'# 经抓取的网页正文')
        self.assertEqual(self.rows()[-1]['model_id'],'search:deepseek:fetch')
        self.assertEqual(self.balance()['balance'],9960)

    def test_fetch_failure_preserves_search_results_and_exposes_limitation(self):
        self.config['fetch_top']=1
        def handler(r):return httpx.Response(404) if r.url.path.endswith('/fetch') else self.success(r)
        with self.transport(handler):result=self.provider.search(self.ctx,'核验')
        self.assertTrue(result['sources']); self.assertTrue(result['limited'])
        self.assertEqual(result['calls'][-1]['status'],'failed'); self.assertNotIn('fetched_content',result['sources'][0])

    def test_fetch_respects_remaining_limit(self):
        self.config['fetch_top']=2; self.limit(1)
        with self.transport(self.success):result=self.provider.search(self.ctx,'核验')
        self.assertEqual(len(self.requests),3); self.assertEqual(result['calls'][-1]['operation'],'fetch')
        self.assertEqual(result['calls'][-1]['status'],'skipped')

    def test_fetch_and_search_share_only_deepseek_budget(self):
        self.limit(2);self.config['fetch_top']=1
        with self.transport(self.success):
            first=self.provider.search(self.ctx,'首次核验')
            second=self.provider.search(self.ctx,'继续核验')
        self.assertEqual(first['calls'][-1]['status'],'completed')
        self.assertEqual([c['status'] for c in second['calls']],['skipped','completed','completed','skipped'])
        self.assertEqual(len(self.requests),6)

    def test_long_query_and_cancelled_attempt_do_not_send_requests(self):
        with self.transport(self.success),self.assertRaisesRegex(ValueError,'128'):self.provider.search(self.ctx,'长'*129)
        self.s.fail(self.ctx['execution_context'],self.ctx['task']['id'],self.ctx['task']['attempt_id'],'终止')
        with self.transport(self.success),self.assertRaises(ValueError):self.provider.search(self.ctx,'核验')
        self.assertEqual(self.requests,[]); self.assertEqual(self.rows(),[])

    def test_old_snapshot_keeps_tavily_adapter(self):
        self.ctx['snapshot']['profile'].pop('search_config'); self.ctx['snapshot']['profile']['search_key_env']='SEARCH_TEST_TAVILY'
        with self.transport(self.success):result=self.provider.search(self.ctx,'旧任务')
        self.assertEqual([r.url.host for r in self.requests],['api.tavily.com'])
        self.assertEqual(result['sources'][0]['providers'],['tavily'])


class SearchConfigTests(unittest.TestCase):
    def test_normalize_filters_nonpublic_urls_and_preserves_distinct_documents(self):
        for url in ('file:///tmp/key','http://127.0.0.1/x','http://user:pass@example.com','http://localhost','https://example.local','http://[::1]/'):
            self.assertIsNone(canonical_url(url))
        self.assertEqual(canonical_url('https://EXAMPLE.com:443/x?utm_source=a&id=2#top'),'https://example.com/x?id=2')
        rows=normalize('deepseek',{'results':[{'url':'http://127.0.0.1/x'},{'url':'https://example.com/x?id=1'},{'url':'https://example.com/x?id=2'}]},5)
        self.assertEqual(len(merge([rows])),2)

    def test_auto_sources_missing_explicit_key_and_invalid_configuration(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(configuration({})['providers'],[])
            with self.assertRaisesRegex(ValueError,'YDC_API_KEY'):configuration({'providers':{'you':{'enabled':True}}})
        for options in ({'mode':'random'},{'sources':['you','you']},{'sources':['unknown']},{'max_results':True},{'fetch_top':4},{'fetch_top':-1},{'typo':1}):
            with self.subTest(options=options),self.assertRaises(ValueError):configuration({'search':options})
        with self.assertRaises(ValueError):configuration({'providers':{'unknown':{}}})

    def test_settings_can_use_deepseek_without_tavily_and_freezes_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); database_url_for(root/'var'); (root/'config').mkdir()
            (root/'config/models.toml').write_text('[models.research]\nkind="text"\nmodel="test"\nbase_url="https://example.invalid"\nkey_env="DEEPSEEK_API_KEY"\njson_mode=true\nsearch=true\n')
            (root/'config/search.toml').write_text('[search]\nsources=["deepseek","you","tavily"]\n')
            with patch.dict(os.environ,{'ORCHPACT_API_TOKEN':'test-'*8,'DEEPSEEK_API_KEY':'fake'},clear=True):
                s=Settings.load(root)
                self.assertEqual(s.profiles['research']['search_config']['providers'],[{'id':'deepseek','key_env':'DEEPSEEK_API_KEY'}])
                self.assertIn('search',s.catalog()[0]['capabilities'])
                self.assertNotIn('search_config',s.catalog()[0])
            with patch.dict(os.environ,{'ORCHPACT_API_TOKEN':'test-'*8,'DEEPSEEK_API_KEY':'fake','YDC_API_KEY':'fake-you'},clear=True):
                self.assertEqual(len(Settings.load(root).profiles['research']['search_config']['providers']),2)
            self.assertEqual(len(s.profiles['research']['search_config']['providers']),1)
