from orchpact.testing import database_url_for
from account_fixtures import create_user,register_package
"""Multi-domain hosting, session audience and product API boundaries."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from orchpact.config import Settings
from orchpact.api import create_app
from orchpact.access import password_hash

ADMIN='https://admin.example'
PRODUCTS={'consumer-a':'https://a.example','consumer-b':'https://b.example','consumer-c':'https://c.example'}
TOKEN='site-admin-test-token-'*3
PASSWORD='site-password-1234'

class SiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.hashed=password_hash(PASSWORD)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        from account_fixtures import synthetic_frontends
        frontends=synthetic_frontends(root/'products',PRODUCTS)
        profile=dict(id='text',label='Text',model='test',kind='text',capabilities=['text','json'],key_env='TEST_KEY',base_url='https://example.invalid',priority=1,json_mode=True)
        self.settings=Settings(root,TOKEN,{'text':profile},root/'var',public_origin=ADMIN,products=PRODUCTS,frontends=frontends,database_url=database_url_for(root/'var'))
        self.app=create_app(self.settings,run_worker=False)
        self.admin=TestClient(self.app,base_url=ADMIN).__enter__();self.admin.headers['Authorization']='Bearer '+TOKEN
        self.s=self.app.state.service
        for app in PRODUCTS:register_package(self.admin,app)
        self.clients=[]
        self.story=self.client(PRODUCTS['consumer-a'])
        create_user(self.s,self.story,'friend',PASSWORD,self.hashed,audience=PRODUCTS['consumer-a'])
        self.user=self.story.get('/api/v1/auth/me').json()['user_id']

    def tearDown(self):
        for c in self.clients:c.close()
        self.admin.__exit__(None,None,None);self.tmp.cleanup()

    def test_admin_console_available_only_on_management_surface(self):
        self.admin.post('/api/v1/session',json={'token':TOKEN}).raise_for_status()
        for path in ('/static/kernel/app.js','/static/kernel/style.css'):
            self.assertEqual(self.admin.get(path).status_code,200,path)
            self.assertEqual(self.story.get(path).status_code,404,path)
        self.assertIn('/static/kernel/app.js',self.admin.get('/admin/').text)
        self.assertNotIn('/static/kernel/app.js',self.story.get('/account/').text)

    def test_orchpact_showcase_is_public_and_separate_from_management_and_product_pages(self):
        visitor = self.client(ADMIN)
        home = visitor.get('/')
        self.assertEqual(home.status_code, 200)
        self.assertEqual(home.text, self.admin.get('/').text)
        self.assertEqual(visitor.head('/').status_code, 200)
        self.assertIn('href="/admin/"', home.text)
        self.assertIn('Orchpact', home.text)
        self.assertNotIn('data-gate-node', home.text)
        self.assertNotIn('data-mode="blocked"', home.text)
        self.assertNotIn('admin-page-title', home.text)
        self.assertNotIn('set-cookie', home.headers)
        response = visitor.get('/about', follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers['location'], '/about/')
        page = visitor.get('/about/')
        self.assertEqual(page.status_code, 200)
        self.assertIn('可复用的组织协作工作流', page.text)
        self.assertIn('授权限定行动', page.text)
        self.assertIn('PostgreSQL', page.text)
        self.assertNotIn('SQLite', page.text)
        self.assertNotIn('监察部', page.text)

        self.assertIn('Orchpact',page.text)
        self.assertIn('uv run orchpact',page.text)
        self.assertIn('修订稿', page.text)
        self.assertNotIn('set-cookie', page.headers)
        self.assertEqual(visitor.head('/about/').status_code, 200)
        for asset in ('style.css', 'app.js', 'mark.svg', 'orchpact-logo.svg'):
            self.assertIn('"/about/' + asset + '"', home.text)
            self.assertEqual(visitor.get('/about/' + asset).status_code, 200)
        client_asset=visitor.get('/static/orchpact-client.js')
        self.assertEqual(client_asset.status_code,200)
        self.assertIn('OrchpactClient',client_asset.text)
        self.assertEqual(client_asset.content,visitor.get('/static/orchpact-client.js').content)
        self.assertEqual(visitor.get('/api/v1/state').status_code, 401)
        self.assertIn('验证管理凭据', visitor.get('/admin').text)
        self.assertEqual(self.story.get('/about/').status_code, 404)


    def client(self,url):
        client=TestClient(self.app,base_url=url);self.clients.append(client);return client

    def signed(self,app):
        c=self.client(PRODUCTS[app]);token=self.app.state.access.session(self.user,audience=PRODUCTS[app])
        c.cookies.set('orchpact_session',token)
        return c

    def invoke(self,c,app,key='once'):
        return c.post('/api/v1/apps/'+app+'/runs',headers={'Idempotency-Key':key},json=dict(operation='run',input={}))

    def test_three_products_open_at_root_and_serve_own_assets(self):
        for app in PRODUCTS:
            title=app
            c=self.client(PRODUCTS[app]);r=c.get('/')
            self.assertEqual(r.status_code,200);self.assertIn(title,r.text);self.assertNotIn('ORCHPACT_API_TOKEN',r.text)
            self.assertEqual(c.get('/style.css').status_code,200)
            self.assertEqual(c.get('/app.js').status_code,200)
            self.assertEqual(c.get('/static/account/workspace.js').status_code,200)
            self.assertEqual(c.get('/static/account/credits.js').status_code,200)
            account=c.get('/account/');self.assertIn('data-product-root="true"',account.text)
            self.assertNotIn('管理员登录',account.text)
            self.assertIn('href="/favicon.svg"',account.text)
            self.assertIn('href="favicon.svg"',r.text)
            for path in ('/favicon.svg','/static/account/orchpact-logo.svg','/static/account/orchpact-logo.svg','/static/account/favicon.svg'):
                asset=c.get(path)
                self.assertEqual(asset.status_code,200,path)
                self.assertIn('image/svg+xml',asset.headers['content-type'])
            self.assertEqual(c.get('/not-mounted/').status_code,404)
            self.assertEqual(c.get('/integration.json').status_code,404)
            self.assertEqual(c.get('/static/account/index.html').status_code,404)
        self.assertIn('验证管理凭据',self.client(ADMIN).get('/admin/').text)
        self.assertEqual(self.admin.get('/not-mounted/').status_code,404)

    def test_admin_routes_blocked_on_products_even_with_admin_token(self):
        for path in ('/admin','/admin/','/admin/providers/','/api/docs','/api/openapi.json','/api/v1/state','/api/v1/admin/users','/static/index.html','/static/app.js','/readyz'):
            self.assertEqual(self.story.get(path,headers={'Authorization':'Bearer '+TOKEN}).status_code,404,path)
        self.assertEqual(self.story.post('/api/v1/session',json={'token':TOKEN}).status_code,404)
        self.assertEqual(self.story.get('/api/v1/auth/me',headers={'Authorization':'Bearer '+TOKEN}).status_code,403)
        self.assertEqual(self.admin.get('/api/docs').status_code,200)



    def test_session_cookie_replay_fails_on_another_product_and_management(self):
        token=self.story.cookies.get('orchpact_session')
        for url in (ADMIN,PRODUCTS['consumer-c']):
            other=self.client(url);other.cookies.set('orchpact_session',token)
            self.assertEqual(other.get('/api/v1/auth/me').status_code,401)
        c=self.client(ADMIN);c.post('/api/v1/session',json={'token':TOKEN}).raise_for_status()
        product=self.client(PRODUCTS['consumer-a']);product.cookies.set('orchpact_session',c.cookies.get('orchpact_session'))
        self.assertEqual(product.get('/api/v1/auth/me').status_code,401)

    def test_password_change_requires_current_password_on_every_host(self):
        research = self.signed('consumer-b')
        response = research.put('/api/v1/auth/password', json={
            'current_password': 'incorrect', 'new_password': 'changed-password-1234'})
        self.assertEqual(response.status_code, 401, response.text)
        for app in ('consumer-a', 'consumer-c'):
            client = self.signed(app)
            self.assertEqual(client.put('/api/v1/auth/password', json={
                'current_password': 'incorrect', 'new_password': 'changed-password-1234'}).status_code, 401)

    def test_session_audience_must_match_exactly(self):
        token=self.s.access.session(self.user,audience=PRODUCTS['consumer-a']+'#other-client')
        self.story.cookies.set('orchpact_session',token)
        self.assertEqual(self.story.get('/api/v1/auth/me').status_code,401)

    def test_same_account_logs_in_independently_and_sees_same_workspaces(self):
        other=self.client(PRODUCTS['consumer-c'])
        r=other.post('/api/v1/auth/login',json=dict(username='friend',password=PASSWORD),headers={'Origin':PRODUCTS['consumer-c']})
        self.assertEqual(r.status_code,200,r.text)
        self.assertIn('Secure',r.headers['set-cookie']);self.assertNotIn('Domain=',r.headers['set-cookie'])
        space=self.story.post('/api/v1/workspaces',json={'name':'一起创作'}).json()['id']
        self.assertIn(space,[w['id'] for w in other.get('/api/v1/workspaces').json()])
        self.assertEqual(self.story.get('/api/v1/auth/me').json()['workspace_id'],other.get('/api/v1/auth/me').json()['workspace_id'])

    def test_product_cannot_access_other_application_paths_or_its_resources(self):
        frame=self.signed('consumer-c');job=self.invoke(frame,'consumer-c').json()
        for path in ('/api/v1/apps/consumer-c','/api/v1/apps/consumer-c/documents/index','/api/v1/apps/consumer-c/runs'):
            self.assertEqual(self.story.get(path).status_code,404,path)
        self.assertEqual(self.story.get('/api/v1/runs/'+job['id']+'/requests').status_code,404)
        self.assertEqual(self.story.get('/api/v1/runs/'+job['id']+'/usage').status_code,404)
        self.assertEqual(self.story.post('/api/v1/runs/'+job['id']+'/cancel').status_code,404)
        self.assertEqual(self.story.post('/api/v1/work-items/'+job['root_task_id']+'/retry').status_code,404)
        ctx=self.s.claim()
        from orchpact.runtime import Providers
        request=Providers(self.s).reserve(ctx,'model','text')
        self.s.decide(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],dict(action='deliver',summary='完成',result='完成',files=[dict(path='result.json',content='{}')]))
        artifact=frame.get('/api/v1/apps/consumer-c/runs/'+job['id']).json()['artifact_id']
        self.assertEqual(frame.get('/api/v1/artifacts/'+artifact+'/download').status_code,200)
        self.assertEqual(self.story.get('/api/v1/artifacts/'+artifact+'/download').status_code,404)
        self.assertEqual(self.story.get('/api/v1/requests/'+request).status_code,404)

    def test_origin_and_host_not_taken_from_untrusted_forwarded_headers(self):
        r=self.story.post('/api/v1/workspaces',json={'name':'无效'},headers={'Origin':PRODUCTS['consumer-c']})
        self.assertEqual(r.status_code,403)
        self.assertEqual(self.story.get('/',headers={'Host':'evil.example','X-Forwarded-Host':'a.example'}).status_code,403)
        self.assertEqual(self.story.post('/api/v1/workspaces',json={'name':'有效'},headers={'Origin':PRODUCTS['consumer-a']}).status_code,201)

    def test_shared_workspace_documents_remain_separate_between_products(self):
        frame=self.signed('consumer-c')
        path='/api/v1/apps/consumer-a/documents/index'
        self.story.put(path,json={'revision':0,'value':{'story':'private'}}).raise_for_status()
        doc=frame.get('/api/v1/apps/consumer-c/documents/index').json()
        self.assertEqual(doc,dict(revision=0,value=None))

    def test_internal_admin_api_allows_initial_provisioning_without_public_port(self):
        c=self.client('http://127.0.0.1:8780');c.headers['Authorization']='Bearer '+TOKEN
        self.assertEqual(c.get('/api/v1/state').status_code,200)
        self.assertEqual(c.get('/readyz').status_code,200)

    def test_bad_host_configuration_rejected(self):
        for products in ({'unknown':'https://x.example'},{'consumer-a':ADMIN},{'consumer-a':'http://x.example'}, {'consumer-a':'https://x.example','consumer-c':'https://x.example'}):
            with self.assertRaises(ValueError):replace(self.settings,products=products)
