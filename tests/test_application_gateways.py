from orchpact.testing import database_url_for
"""Independent consumers through application-owned signing gateways and the public API."""
from contextlib import ExitStack
from dataclasses import replace
import importlib.util
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from orchpact.api import create_app
from orchpact.access import password_hash
from orchpact.db import now
from orchpact.application_gateway import GatewaySettings,create_gateway
from orchpact.application_provision import provision_gateway
from orchpact.application_signing import SigningCredentials
from orchpact.config import Settings

APPS=('consumer-a','consumer-b','consumer-c')
ORIGINS={app:'https://'+app+'.example' for app in APPS}
TOKEN='gateway-admin-test-'*4
PASSWORD='gateway-test-password-123'
ROOT=Path(__file__).resolve().parents[2]


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self,app):self.upstream=httpx.ASGITransport(app=app);self.records=[]
    async def handle_async_request(self,request):
        self.records.append((request.method,request.url.path,dict(request.headers),request.content))
        return await self.upstream.handle_async_request(request)


class GatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.password=password_hash(PASSWORD)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.stack=ExitStack();root=Path(self.tmp.name)
        # This fixture exercises the engine independently of a product's release
        # qualification gate. Library/policy tests explicitly retain the real gate.
        profile=dict(id='text',kind='text',model='test',key_env='TEST_KEY',capabilities=['text','json'],base_url='https://example.invalid')
        from account_fixtures import synthetic_frontends
        self.products=root/'products';frontends=synthetic_frontends(self.products,APPS)
        self.settings=Settings(root,TOKEN,{'text':profile},root/'var',public_origin='https://admin.example',products=ORIGINS,frontends=frontends,database_url=database_url_for(root/'var'))
        self.platform=create_app(self.settings,run_worker=False)
        self.admin=self.stack.enter_context(TestClient(self.platform,base_url='https://admin.example'));self.admin.headers['Authorization']='Bearer '+TOKEN;self.s=self.platform.state.service
        with self.s.db.tx() as c:
            for user in ('alice','bob'):
                c.execute('INSERT INTO users(id,username,password,created) VALUES(%s,%s,%s,0)',(user,user,self.password))
                self.s.access.new_workspace(c,user,user+' space',True)
                self.s.access.wallets.create(c,user)
                self.s.access.wallets.entry(c,user,'adjustment',10000*1000000,reason='isolated test budget')
        self.manifests={};self.gateways={};self.transports={};self.bundles={}
        for app in APPS:
            manifest=dict(id=app,name=app,operations={'run':dict(instruction='Return the input as JSON',output_schema={'type':'object'})});self.manifests[app]=manifest
            # Gateway/auth tests deliberately use one generic test unit. The real
            # specialist graphs and recruitment are covered by application tests.
            operations={k:dict({field:value for field,value in op.items() if field not in ('unit','execution_plan')},unit='entry') for k,op in manifest['operations'].items()}
            from account_fixtures import register_package
            register_package(self.admin,app,operations,manifest['name'])
            directory=root/app;provision_gateway(self.call,app,directory,'alice')
            self.bundles[app]=directory/'credentials.json'
            transport=RecordingTransport(self.platform);self.transports[app]=transport
            gateway=create_gateway(GatewaySettings(app,self.products/app/'dist','http://platform',ORIGINS[app],self.bundles[app]),transport)
            self.gateways[app]=self.stack.enter_context(TestClient(gateway,base_url=ORIGINS[app]))

    def tearDown(self):self.stack.close();self.tmp.cleanup()

    def call(self,method,path,data=None):
        response=self.admin.request(method,'/api/v1/'+path,json=data)
        response.raise_for_status();return response.json()

    def login(self,app,user='bob'):
        response=self.gateways[app].post('/api/v1/auth/login',json=dict(username=user,password=PASSWORD),headers={'Origin':ORIGINS[app]})
        self.assertEqual(response.status_code,200,response.text)
        return self.gateways[app]


    def payload(self,app):
        return dict(operation='run',input={'value':7})

    def invoke(self,app,key='business-1'):
        response=self.gateways[app].post('/api/v1/apps/'+app+'/runs',json=self.payload(app),headers={'Idempotency-Key':key})
        self.assertEqual(response.status_code,201,response.text);return response.json()

    def test_gateway_serves_generic_account_assets(self):
        for app,gateway in self.gateways.items():
            for target in ('/account/','/account/?return=%2Fresearch%2F'):
                with self.subTest(app=app,target=target):
                    response=gateway.get(target)
                    self.assertEqual(response.status_code,200)
                    self.assertIn('id="login-form"',response.text)
                    self.assertIn('受邀注册',response.text)
                    self.assertIn('href="/favicon.svg"',response.text)
                    self.assertNotIn('管理员登录',response.text)
            for path in ('/favicon.svg','/static/account/orchpact-logo.svg','/static/account/favicon.svg'):
                asset=gateway.get(path)
                self.assertEqual(asset.status_code,200,path)
                self.assertIn('image/svg+xml',asset.headers['content-type'])
            self.assertEqual(self.transports[app].records,[])

    def test_invited_website_signup_across_gateways(self):
        story=self.gateways['consumer-a']
        research=self.gateways['consumer-b']
        self.assertEqual(story.post('/api/v1/admin/invitations',json={}).status_code,404)
        self.assertEqual(self.admin.post('/api/v1/admin/invitations',headers={'Origin':ORIGINS['consumer-a']}).status_code,403)
        invite=self.call('POST','admin/invitations',{})
        self.assertEqual(invite['expires_in'],7*86400)
        def redeem(client,username,code=invite['code'],headers=None):
            return client.post('/api/v1/auth/register',json=dict(username=username,password=PASSWORD,invitation=code),headers=headers)
        self.assertEqual(redeem(story,'alice').status_code,409)
        self.assertEqual(redeem(story,'13800138001').status_code,422)
        self.assertEqual(redeem(story,'denied',headers={'Origin':ORIGINS['consumer-c']}).status_code,403)
        expired=self.call('POST','admin/invitations',{})
        revoked=self.call('POST','admin/invitations',{})
        with self.s.db.tx() as c:
            c.execute('UPDATE invitations SET expires=%s WHERE id=%s',(now()-1,expired['id']))
            c.execute('UPDATE invitations SET revoked=1 WHERE id=%s',(revoked['id'],))
        for unusable in (expired,revoked):
            self.assertEqual(redeem(story,'denied',unusable['code']).status_code,409)
        with ThreadPoolExecutor(2) as pool:
            results=[pool.submit(redeem,client,username) for client,username in ((story,'Invited-Story'),(research,'Invited-Research'))]
            self.assertEqual(sorted(result.result().status_code for result in results),[201,409])
        winner,other=(story,research) if results[0].result().status_code==201 else (research,story)
        me=winner.get('/api/v1/auth/me').json()
        self.assertTrue(me['password_set'])
        self.assertTrue(me['workspaces'][0]['personal'])
        self.assertEqual(me['credits']['available'],0)
        self.assertEqual(other.get('/api/v1/auth/me').status_code,401)
        space=winner.post('/api/v1/workspaces',json={'name':'邀请测试'}).json()
        space_code=winner.post('/api/v1/workspaces/'+space['id']+'/invitations').json()['code']
        self.assertEqual(redeem(other,'denied',space_code).status_code,409)
        other.post('/api/v1/auth/login',json=dict(username=me['username'],password=PASSWORD)).raise_for_status()
        self.assertEqual(other.get('/api/v1/auth/me').json()['user_id'],me['user_id'])
        self.assertEqual(other.post('/api/v1/workspaces/join',json={'code':invite['code']}).status_code,409)

    def test_all_three_login_documents_submit_query_idempotency_and_user_billing(self):
        fingerprints=set()
        for app in APPS:
            with self.subTest(app=app):
                gateway=self.login(app);base='/api/v1/apps/'+app
                self.assertIn('data-product-root="true"',gateway.get('/account/').text)
                self.assertNotIn('管理员登录',gateway.get('/account/').text)
                self.assertEqual(gateway.get('/').status_code,200)
                self.assertEqual(gateway.get('/static/account/workspace.js').status_code,200)
                self.assertEqual(gateway.get('/api/v1/auth/me').json()['user_id'],'bob')
                saved=gateway.put(base+'/documents/gateway-test',json={'revision':0,'value':{'app':app}})
                self.assertEqual(saved.status_code,200,saved.text)
                self.assertEqual(gateway.get(base+'/documents/gateway-test').json()['value'],{'app':app})
                job=self.invoke(app);self.assertEqual(job['payer_user_id'],'bob')
                self.assertEqual(self.invoke(app)['id'],job['id'])
                self.assertEqual(gateway.get(base+'/runs/'+job['id']).status_code,200)
                # Real accounting through a mocked external HTTP response, never a mocked wallet.
                result={'action':'wait','summary':'等待进一步工作'}
                response=httpx.Response(200,request=httpx.Request('POST','https://example.invalid'),json={'usage':{'total_tokens':1000},'choices':[{'message':{'content':json.dumps(result)},'finish_reason':'stop'}]})
                with patch.dict(os.environ,{'TEST_KEY':'test-only'}),patch('orchpact.runtime.httpx.Client') as network:
                    network.return_value.__enter__.return_value.post.return_value=response
                    self.platform.state.runner.step()
                with self.s.db.connect() as c:
                    charges=c.execute('SELECT DISTINCT user_id FROM credit_charges').fetchall()
                    self.assertTrue(charges);self.assertEqual({r['user_id'] for r in charges},{'bob'})
                    self.assertEqual(self.s.access.wallets.view(c,'alice')['balance'],10000)
                latest=gateway.get(base+'/runs/'+job['id']).json()
                self.assertTrue(latest['requests']);self.assertEqual(gateway.get('/api/v1/requests/'+latest['requests'][0]['id']).status_code,200)
                signed=[r for r in self.transports[app].records if '/runs' in r[1] or '/documents/' in r[1]]
                self.assertTrue(all('x-orchpact-signature' in r[2] and 'x-orchpact-user-session' in r[2] for r in signed))
                self.assertEqual(len({r[2]['x-orchpact-nonce'] for r in signed}),len(signed))
                fingerprints.add(self.call('GET','apps/'+app+'/keys')[0]['fingerprint'])
                gateway.post('/api/v1/runs/'+job['id']+'/cancel',json={}).raise_for_status()
        self.assertEqual(len(fingerprints),3)

    def test_cross_user_app_and_unsigned_bypass_are_denied(self):
        app=APPS[0];bob=self.login(app);job=self.invoke(app);base='/api/v1/apps/'+app
        self.login(app,'alice')
        self.assertEqual(bob.get(base+'/runs/'+job['id']).status_code,404)
        self.assertEqual(bob.post('/api/v1/work-items/'+job['root_task_id']+'/retry',json={}).status_code,404)
        self.assertEqual(bob.get('/api/v1/apps/'+APPS[1]).status_code,404)
        session=self.s.access.session('bob',audience=ORIGINS[app])
        direct=TestClient(self.platform,base_url=ORIGINS[app]);direct.cookies.set('orchpact_session',session)
        self.assertEqual(direct.get(base).status_code,403)
        self.assertEqual(direct.get('/api/v1/runs/'+job['id']+'/usage').status_code,403)
        self.assertEqual(direct.get('/api/v1/auth/me').status_code,200)
        self.assertEqual(bob.get(base,headers={'X-Orchpact-User-Session':session}).status_code,400)
        self.assertEqual(bob.get(base,headers={'Authorization':'Bearer '+TOKEN}).status_code,400)
        self.assertEqual(bob.post(base+'/runs',json=self.payload(app),headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(bob.get('/api/v1/admin/users').status_code,404)
        self.assertEqual(bob.get('/integration.json').status_code,404)
        self.assertEqual(bob.get('/server.py').status_code,404)

    def test_workspace_switch_permissions_and_no_default_owner_fallback(self):
        app=APPS[1];gateway=self.login(app)
        with self.s.db.tx() as c:
            shared=self.s.access.new_workspace(c,'alice','shared')
            c.execute('INSERT INTO workspace_members VALUES(%s,%s)',(shared,'bob'))
        base='/api/v1/apps/'+app
        response=gateway.post(base+'/runs',json=self.payload(app),headers={'Idempotency-Key':'shared','X-Workspace-Id':shared})
        self.assertEqual(response.status_code,201,response.text);job=response.json();self.assertEqual(job['payer_user_id'],'bob')
        self.assertEqual(gateway.get(base+'/runs/'+job['id'],headers={'X-Workspace-Id':shared}).status_code,200)
        with self.s.db.connect() as c:self.assertEqual(c.execute('SELECT workspace_id FROM projects WHERE id=%s',(job['id'],)).fetchone()[0],shared)
        self.assertEqual(gateway.get(base+'/runs/'+job['id'],headers={'X-Workspace-Id':shared}).status_code,200)
        with self.s.db.tx() as c:c.execute('DELETE FROM workspace_members WHERE workspace_id=%s AND user_id=%s',(shared,'bob'))
        self.assertEqual(gateway.get(base+'/runs/'+job['id'],headers={'X-Workspace-Id':shared}).status_code,404)
        gateway.cookies.clear()
        self.assertEqual(gateway.post(base+'/runs',json=self.payload(app),headers={'Idempotency-Key':'anonymous'}).status_code,401)

    def test_key_rotation_reload_revocation_and_no_secrets_in_assets(self):
        app=APPS[2];gateway=self.login(app);base='/api/v1/apps/'+app
        old=json.loads(self.bundles[app].read_text())['key_id']
        provision_gateway(self.call,app,self.bundles[app].parent,'alice')
        self.assertEqual(json.loads(self.bundles[app].read_text())['key_id'],old)
        new=provision_gateway(self.call,app,self.bundles[app].parent,'alice',rotate=True)
        self.assertNotEqual(old,new['id'])
        self.call('DELETE','apps/'+app+'/keys/'+old)
        self.assertEqual(gateway.get(base).status_code,200)
        self.call('DELETE','apps/'+app+'/keys/'+new['id'])
        self.assertEqual(gateway.get(base).status_code,503)
        for path in ('/','/app.js','/client.js','/static/account/workspace.js'):
            response=gateway.get(path)
            self.assertNotIn('PRIVATE KEY',response.text);self.assertNotIn(TOKEN,response.text)

    def test_session_audience_and_signed_delegation_tampering(self):
        app=APPS[0];gateway=self.login(app);session=gateway.cookies.get('orchpact_session')
        self.gateways[APPS[1]].cookies.set('orchpact_session',session)
        self.assertEqual(self.gateways[APPS[1]].get('/api/v1/apps/'+APPS[1]).status_code,401)
        signing=SigningCredentials.load(self.bundles[app],app);path='/api/v1/apps/'+app;host=app+'.example'
        headers=signing.headers('GET',path,host,session=session)
        headers['X-Orchpact-User-Session']=self.s.access.session('alice',audience=ORIGINS[app])
        self.assertEqual(TestClient(self.platform,base_url=ORIGINS[app]).get(path,headers=headers).status_code,401)
        headers=signing.headers('GET',path,host,session=session);headers['X-Orchpact-User-Workspace']='tampered'
        self.assertEqual(TestClient(self.platform,base_url=ORIGINS[app]).get(path,headers=headers).status_code,401)

    def step(self,output):
        with patch('orchpact.runtime.Providers.json',return_value=output):self.assertTrue(self.platform.state.runner.step())

    def test_all_three_failure_retry_questions_delivery_and_download(self):
        for app in APPS:
            with self.subTest(app=app):
                gateway=self.login(app);job=self.invoke(app);base='/api/v1/apps/'+app
                with patch('orchpact.runtime.Providers.json',side_effect=RuntimeError('test failure')):self.platform.state.runner.step()
                self.assertEqual(gateway.get(base+'/runs/'+job['id']).json()['status'],'failed')
                self.assertEqual(gateway.post('/api/v1/work-items/'+job['root_task_id']+'/retry',json={}).status_code,200)
                self.step(dict(action='wait',summary='澄清',messages=[dict(to_task='user',kind='question',content='需要补充什么？')]))
                question=gateway.get(base+'/runs/'+job['id']).json()['questions'][0]
                self.assertEqual(gateway.post('/api/v1/questions/'+question['id']+'/answers',json={'content':'按原要求交付'}).status_code,200)
                self.step(dict(action='plan',summary='委派',recruits=[dict(name='执行员',role='交付',reason='执行')],assignments=[dict(agent='执行员',description='交付JSON',expected='结构完整')]))
                output=dict(value=7)
                self.step(dict(summary='实际文件',files=[dict(path='result.json',content=json.dumps(output,ensure_ascii=False))]))
                current=gateway.get(base+'/runs/'+job['id']).json();worker=next(t for t in current['tasks'] if t['kind']=='work')
                self.step(dict(action='deliver',summary='验收',result='完整交付',source_artifact_id=worker['artifact_id'],reviews=[dict(task_id=worker['id'],verdict='accept',feedback='内容符合委托')]))
                current=gateway.get(base+'/runs/'+job['id']).json();self.assertEqual(current['status'],'completed',current);self.assertEqual(current['result'],output)
                artifact=current['artifact_id'];download=gateway.get('/api/v1/artifacts/'+artifact+'/download')
                self.assertEqual(download.status_code,200,download.text[:100]);self.assertTrue(download.content);self.assertIn('attachment',download.headers['content-disposition'])
                self.assertEqual(gateway.get('/api/v1/work-items/'+job['root_task_id']+'/attempts').status_code,200)
                other=self.gateways[next(other for other in APPS if other!=app)]
                self.login(next(other for other in APPS if other!=app))
                self.assertEqual(other.get('/api/v1/artifacts/'+artifact+'/download').status_code,404)


    def test_missing_keys_fail_closed_but_login_still_works(self):
        app=APPS[0];self.bundles[app].unlink();gateway=self.login(app)
        self.assertEqual(gateway.get('/healthz').status_code,503)
        self.assertEqual(gateway.get('/api/v1/apps/'+app).status_code,503)
        self.assertEqual(gateway.get('/api/v1/auth/me').status_code,200)


if __name__=='__main__':unittest.main()
