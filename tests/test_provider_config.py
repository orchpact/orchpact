from orchpact.testing import database_url_for, fixture_provider_credentials
from account_fixtures import bootstrap_admin, create_user, register_package
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from orchpact.api import create_app
from orchpact.config import Settings
from orchpact.credits import measure, summarize
from orchpact.runtime import Providers




class ProviderConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'CONFIG_TEST_KEY':'fake-text','SEARCH_TEST_KEY':'fake-search'});self.env.start()
        p=dict(id='text',label='测试文本',kind='text',model='test',base_url='https://example.invalid',key_env='CONFIG_TEST_KEY',capabilities=['text','json','search'])
        self.settings=Settings(self.root,{'text':p},self.root/'var',database_url=database_url_for(self.root/'var'))
        self.app=create_app(self.settings,run_worker=False)
        self.admin=TestClient(self.app).__enter__();self.s=self.app.state.service;self.admin_actor=bootstrap_admin(self.s,self.admin)
        fixture_provider_credentials(self.s,default_model='fixture-text-key')
        register_package(self.admin)
        self.user=TestClient(self.app)
        self.register(self.user,'config-user')
        self.uid=self.user.get('/api/v1/auth/me').json()['user_id']

    def tearDown(self):
        self.user.close();self.admin.__exit__(None,None,None);self.env.stop();self.tmp.cleanup()

    def register(self,client,name):
        create_user(self.s,client,name,'config-test-password')

    def config(self):return self.admin.get('/api/v1/admin/configuration').json()
    def save(self,body):return self.admin.put('/api/v1/admin/configuration',json=body)
    def wallet(self):return self.user.get('/api/v1/auth/credits').json()['credits']
    def start(self,key='one'):
        job=self.user.post('/api/v1/apps/demo/runs',headers={'Idempotency-Key':key},json={'operation':'run','input':{}});job.raise_for_status()
        return job.json(),self.s.claim()
    def rate(self,body,key,unit,points,size=1):
        p=next(p for p in body['value']['providers'] if p['id']==key)
        p['pricing']={'unit':unit,'points':points,'unit_size':size};return p

    def test_admin_only_no_secret_echo_and_revision_conflict(self):
        self.assertEqual(self.user.get('/api/v1/admin/configuration').status_code,403)
        self.assertEqual(self.user.put('/api/v1/admin/configuration',json={}).status_code,403)
        self.assertEqual(self.user.get('/admin/',follow_redirects=False).headers['location'],'/workspace/')
        self.assertEqual(self.admin.get('/admin/').status_code,200)
        self.assertEqual(self.admin.get('/static/kernel/app.js').status_code,200)
        body=self.config();p=body['value']['providers'][0];p['secret']='new-secret-configured';body.pop('restart_required')
        result=self.save(body);self.assertEqual(result.status_code,200,result.text)
        self.assertNotIn('new-secret-configured',result.text)
        self.assertNotIn('new-secret-configured',self.admin.get('/api/v1/admin/configuration/history').text)
        self.assertEqual(self.save(body).status_code,409)
        self.assertTrue(result.json()['value']['providers'][0]['secret_configured'])
        stored=self.s.configuration.record(p['id'])
        path=self.settings.data/'provider-secrets'/stored['_secret_ref']
        self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_text_search_and_fetch_rates_snapshot_actual_settlement(self):
        body=self.config();body.pop('restart_required');self.rate(body,'text','tokens',2,1000)
        self.rate(body,'search:you','request',3);self.rate(body,'search:deepseek:fetch','request',7)
        next(p for p in body['value']['providers'] if p['id']=='text')['search']=True
        next(p for p in body['value']['providers'] if p['id']=='search:you')['secret']='fixture-search-key'
        self.save(body).raise_for_status();_,ctx=self.start();provider=Providers(self.s)
        a=provider.reserve(ctx,'model','text',token_bound=5000);provider.record(a,'completed',{'total_tokens':1500})
        b=provider.reserve(ctx,'search','search:you:search');provider.record(b,'failed')
        c=provider.reserve(ctx,'search','search:deepseek:fetch');provider.record(c,'completed')
        self.assertEqual(self.wallet()['balance'],9987);self.assertEqual(self.wallet()['held'],0)
        body=self.config();body.pop('restart_required');self.rate(body,'text','tokens',9,1000);self.save(body).raise_for_status()
        with self.s.db.connect() as conn:row=dict(conn.execute('SELECT * FROM requests WHERE id=%s',(a,)).fetchone())
        self.assertEqual(measure(row)['points'],3)
        key=provider.reserve(ctx,'model','text',token_bound=2000);provider.record(key,'completed',{'total_tokens':1000})
        self.assertEqual(self.wallet()['balance'],9978)

    def test_image_flat_rate_without_tokens_is_settled_and_totals_complete(self):
        body=self.config();body.pop('restart_required');image=dict(body['value']['providers'][0],id='image-test',category='image',adapter='openai',label='图片测试',image_options={},pricing={'unit':'request','unit_size':1,'points':100})
        body['value']['providers'].append(image);self.save(body).raise_for_status()
        _,ctx=self.start();provider=Providers(self.s);key=provider.reserve(ctx,'model','image-test');provider.record(key,'completed')
        self.assertEqual(self.wallet(),{'balance':9900,'available':9900,'held':0})
        with self.s.db.connect() as c:row=dict(c.execute('SELECT * FROM requests WHERE id=%s',(key,)).fetchone())
        summary=summarize([row]);self.assertEqual(summary['points'],100);self.assertEqual(summary['model_tokens'],0);self.assertTrue(summary['complete'])

    def test_live_model_catalog_and_secret_rotation_preserve_old_snapshot(self):
        body=self.config();body.pop('restart_required');p=body['value']['providers'][0];p['secret']='first-key';self.save(body).raise_for_status()
        _,ctx=self.start();old=ctx['snapshot']['profile'];self.assertEqual(Providers(self.s).credential(old),'first-key')
        body=self.config();body.pop('restart_required');p=body['value']['providers'][0];p['model']='new-model';p['secret']='second-key';self.save(body).raise_for_status()
        self.assertEqual(Providers(self.s).credential(old),'first-key')
        self.assertEqual(self.settings.catalog()[0]['model'],'new-model')
        self.assertEqual(Providers(self.s).credential(self.settings.choose(['text'])),'second-key')
        self.assertNotIn('second-key',json.dumps(self.admin.get('/api/v1/state').json()))

    def test_legacy_provider_fields_and_environment_never_supply_credentials(self):
        public = self.config()
        self.assertNotIn('key_env', public['value']['providers'][0])
        with patch.dict(os.environ, {'CONFIG_TEST_KEY':'must-not-be-read'}):
            self.assertEqual(self.s.configuration.secret({'key_env':'CONFIG_TEST_KEY'}),'')
            with self.assertRaisesRegex(ValueError,'后台凭据'):
                Providers(self.s).credential({'key_env':'CONFIG_TEST_KEY'})
            with self.assertRaisesRegex(ValueError,'后台凭据'):
                Providers(self.s).credential({'credential':{'key_env':'CONFIG_TEST_KEY'}})
            public.pop('restart_required')
            public['value']['providers'][0]['key_env']='CONFIG_TEST_KEY'
            self.assertEqual(self.save(public).status_code,409)

    def test_registration_subsidies_are_not_core_configuration(self):
        body=self.config();body.pop('restart_required');body['value']['registration_points']=1234
        self.assertEqual(self.save(body).status_code,409)
        body=self.config();body.pop('restart_required');body['value']['execution']['workers']=12
        self.save(body).raise_for_status();self.assertTrue(self.config()['restart_required'])
        self.assertEqual(self.wallet()['balance'],10000)

    def test_invalid_prices_rejected_and_last_text_provider_can_be_disabled(self):
        for points in (-1,0.0000001,True):
            body=self.config();body.pop('restart_required');self.rate(body,'text','tokens',points,1000)
            self.assertEqual(self.save(body).status_code,409)
        body=self.config();body.pop('restart_required');body['value']['providers'][0]['enabled']=False
        self.save(body).raise_for_status()
        self.assertFalse(self.config()['value']['providers'][0]['available'])
        body=self.config();body.pop('restart_required');body['value']['providers'][0]['base_url']='https://user:secret@example.com'
        self.assertEqual(self.save(body).status_code,409)
        self.assertEqual(self.config()['revision'],1)



    def test_saved_configuration_survives_new_application_instance(self):
        body=self.config();body.pop('restart_required');self.rate(body,'text','tokens',4,1000);body['value']['execution']['workers']=11;self.save(body).raise_for_status()
        self.restart(self.settings)
        self.assertEqual(self.s.settings.workers,11)
        self.assertEqual(self.s.configuration.public()['value']['providers'][0]['pricing']['points'],4)

    def test_bootstrap_uses_database_without_model_file_or_original_environment(self):
        body=self.config();body.pop('restart_required');body['value']['providers'][0]['secret']='persisted-test-key'
        self.save(body).raise_for_status()
        with patch.dict(os.environ,{},clear=True):
            settings=Settings.load(self.root)
            self.assertEqual(settings.choose(['text'])['id'],'text')
            self.restart(settings)
            profile=self.s.settings.choose(['text'])
            self.assertEqual(Providers(self.s).credential(profile),'persisted-test-key')

    def test_disabling_search_rejects_stale_agent_instead_of_silently_running_offline(self):
        body=self.config();body.pop('restart_required');body['value']['providers'][0]['search']=True
        source=next(p for p in body['value']['providers'] if p['id']=='search:you');source['secret']='fixture-search-key'
        self.save(body).raise_for_status()
        job,ctx=self.start();unit=ctx['task']['unit_id']
        from orchpact.interfaces.context import from_actor
        actor=from_actor(self.s.access.scope(self.s.access.authenticate(self.user.cookies.get('orchpact_session')),None))
        agent=self.s.agent(actor,unit,dict(name='检索员',role='核验',capabilities=['text','search'],model_id='text'))['id']
        body=self.config();body.pop('restart_required')
        for p in body['value']['providers']:
            if p['category']=='search':p['enabled']=False
        self.save(body).raise_for_status()
        with self.s.db.tx() as c,self.assertRaisesRegex(ValueError,'没有可用模型'):
            self.s.new_task(c,ctx['execution_context'],self.s.row(c,'projects',job['id']),unit,'检索','成果',parent=ctx['task']['id'],agent=agent)

    def test_custom_search_key_endpoint_and_pricing_reach_provider_adapter(self):
        import httpx
        body=self.config();body.pop('restart_required');body['value']['providers'][0]['search']=True
        for p in body['value']['providers']:
            if p['category']=='search':p['enabled']=p['adapter']=='you'
        source=self.rate(body,'search:you','request',12.5);source['secret']='managed-you-key';source['base_url']='https://search.example/v1/search'
        self.save(body).raise_for_status();_,ctx=self.start()
        response=httpx.Response(200,request=httpx.Request('POST',source['base_url']),json={'results':{'web':[{'url':'https://example.com/report','title':'公开报告','contents':{'highlights':['可核验正文']}}]}})
        with patch('orchpact.runtime.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value=response
            evidence=Providers(self.s).search(ctx,'公开报告')
            call=client.return_value.__enter__.return_value.post.call_args
            self.assertEqual(call.args[0],source['base_url']);self.assertEqual(call.kwargs['headers']['X-API-Key'],'managed-you-key')
        self.assertEqual(evidence['sources'][0]['providers'],['you']);self.assertEqual(self.wallet()['balance'],9987.5)

    def restart(self,settings):
        self.admin.__exit__(None,None,None)
        self.app=create_app(settings,run_worker=False)
        self.admin=TestClient(self.app).__enter__()
        self.s=self.app.state.service
        self.admin.cookies.set('orchpact_session',self.s.access.session(self.admin_actor.user_id))
        self.user.close();self.user=TestClient(self.app)
