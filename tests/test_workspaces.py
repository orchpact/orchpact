from orchpact.testing import database_url_for
from account_fixtures import create_user, register_package
"""Cross-account authorization, collaboration and public-mode regression tests."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from orchpact.api import create_app
from orchpact.config import Settings
from orchpact.access import password_hash, digest
from orchpact.db import now

TOKEN='admin-test-token-'*3
PASSWORD='test-password-1234'

class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.hashed=password_hash(PASSWORD)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        p=dict(id='text',label='Text',kind='text',model='test',base_url='https://example.invalid/v1',key_env='TEST_KEY',capabilities=['text','json'],priority=1,json_mode=True)
        self.settings=Settings(root,TOKEN,{'text':p},root/'var',public_origin='https://test.example',database_url=database_url_for(root/'var'))
        self.app=create_app(self.settings,run_worker=False)
        self.admin=TestClient(self.app,base_url='https://test.example').__enter__()
        self.admin.headers['Authorization']='Bearer '+TOKEN
        self.s=self.app.state.service
        register_package(self.admin)
        self.clients=[]
        self.alice=self.user('alice');self.bob=self.user('bob');self.cara=self.user('cara')

    def tearDown(self):
        for c in self.clients:c.close()
        self.admin.__exit__(None,None,None);self.tmp.cleanup()

    def user(self,name):
        client=TestClient(self.app,base_url='https://test.example');self.clients.append(client)
        create_user(self.s,client,name,PASSWORD,self.hashed)
        client.me=client.get('/api/v1/auth/me').json()
        return client

    def test_admin_member_space_counts_details_and_authorization(self):
        owned=self.workspace(self.alice,'Alice 协作')
        joined=self.workspace(self.bob,'Bob 协作')
        self.join(self.bob,self.alice,joined)
        users=self.admin.get('/api/v1/admin/users').json()
        alice=next(u for u in users if u['id']==self.alice.me['user_id'])
        self.assertEqual((alice['owned_spaces'],alice['joined_spaces']),(2,1))
        path='/api/v1/admin/users/'+alice['id']+'/workspaces'
        rows=self.admin.get(path).json()
        self.assertEqual(len(rows),3)
        self.assertEqual(next(w for w in rows if w['id']==owned)['role'],'owner')
        shared=next(w for w in rows if w['id']==joined)
        self.assertEqual((shared['role'],shared['owner_name'],shared['member_count']),('member','bob',2))
        self.assertNotEqual(self.alice.get(path).status_code,200)
        self.assertNotEqual(self.bob.get('/api/v1/admin/users').status_code,200)
        self.assertEqual(self.admin.get('/api/v1/admin/users/missing/workspaces').status_code,404)
        self.admin.delete('/api/v1/workspaces/'+joined+'/members/'+alice['id']).raise_for_status()
        alice=next(u for u in self.admin.get('/api/v1/admin/users').json() if u['id']==alice['id'])
        self.assertEqual(alice['joined_spaces'],0)
        self.assertEqual(len(self.admin.get(path).json()),2)

    def workspace(self,c,name='共享空间'):
        r=c.post('/api/v1/workspaces',json={'name':name});self.assertEqual(r.status_code,201,r.text)
        return r.json()['id']

    def join(self,owner,user,workspace):
        invite=owner.post('/api/v1/workspaces/'+workspace+'/invitations',json={})
        self.assertEqual(invite.status_code,200,invite.text)
        r=user.post('/api/v1/workspaces/join',json={'code':invite.json()['code']})
        self.assertEqual(r.status_code,200,r.text)
        return invite.json()['code']

    def invoke(self,c,key='same-key'):
        return c.post('/api/v1/apps/demo/runs',headers={'Idempotency-Key':key},json={'operation':'run','input':{}})

    def doc(self,c,value,revision=0):
        return c.put('/api/v1/apps/demo/documents/index',json={'revision':revision,'value':value})

    def test_private_defaults_and_multiple_workspaces(self):
        self.assertEqual(len(self.alice.me['workspaces']),1)
        self.assertTrue(self.alice.me['workspaces'][0]['personal'])
        self.assertNotEqual(self.alice.me['workspace_id'],self.bob.me['workspace_id'])
        self.assertEqual(self.doc(self.alice,{'secret':'alice'}).status_code,200)
        self.assertEqual(self.bob.get('/api/v1/apps/demo/documents/index').json(),{'revision':0,'value':None})
        first=self.workspace(self.alice,'第一空间');second=self.workspace(self.alice,'第二空间')
        self.assertNotEqual(first,second)
        self.assertEqual(len(self.alice.get('/api/v1/workspaces').json()),3)
        self.bob.headers['X-Workspace-Id']=first
        self.assertEqual(self.bob.get('/api/v1/apps/demo/documents/index').status_code,404)

    def test_shared_documents_invocations_and_revocation(self):
        workspace=self.workspace(self.alice)
        self.join(self.alice,self.bob,workspace)
        self.alice.headers['X-Workspace-Id']=workspace;self.bob.headers['X-Workspace-Id']=workspace
        self.doc(self.alice,{'shared':True})
        self.assertEqual(self.bob.get('/api/v1/apps/demo/documents/index').json()['value'],{'shared':True})
        self.assertEqual(self.doc(self.bob,{},0).status_code,409)
        job=self.invoke(self.alice).json()
        self.assertEqual(self.bob.get('/api/v1/apps/demo/runs/'+job['id']).status_code,200)
        self.assertEqual(len(self.bob.get('/api/v1/apps/demo/runs').json()),1)
        self.assertEqual(self.cara.get('/api/v1/apps/demo/runs/'+job['id']).status_code,404)
        self.alice.delete('/api/v1/workspaces/'+workspace+'/members/'+self.bob.me['user_id']).raise_for_status()
        self.assertEqual(self.bob.get('/api/v1/apps/demo/runs/'+job['id']).status_code,404)
        del self.bob.headers['X-Workspace-Id']
        self.assertEqual(self.bob.get('/api/v1/apps/demo/runs/'+job['id']).status_code,404)
        self.assertEqual(self.bob.get('/api/v1/apps/demo/documents/index').json()['revision'],0)

    def test_invocation_cursor_page_is_lightweight_and_complete(self):
        first=self.invoke(self.alice,'page-1').json();second=self.invoke(self.alice,'page-2').json()
        page=self.alice.get('/api/v1/apps/demo/runs-page?limit=1').json()
        self.assertEqual(len(page['items']),1);self.assertTrue(page['next_cursor'])
        self.assertNotIn('requests',page['items'][0]);self.assertNotIn('tasks',page['items'][0])
        following=self.alice.get('/api/v1/apps/demo/runs-page?limit=1&cursor='+page['next_cursor']).json()
        self.assertEqual({page['items'][0]['id'],following['items'][0]['id']},{first['id'],second['id']})
        self.assertIsNone(following['next_cursor'])

    def test_idempotency_and_documents_are_scoped_to_workspace(self):
        one=self.invoke(self.alice).json();two=self.invoke(self.bob).json()
        self.assertNotEqual(one['id'],two['id'])
        self.assertEqual(self.invoke(self.alice).json()['id'],one['id'])
        workspace=self.workspace(self.alice)
        self.alice.headers['X-Workspace-Id']=workspace
        self.assertNotEqual(self.invoke(self.alice).json()['id'],one['id'])
        self.assertEqual(self.alice.get('/api/v1/apps/demo/documents/index').json()['revision'],0)
        with self.s.db.connect() as c:
            rows=c.execute('SELECT workspace_id,created_by FROM projects').fetchall()
            self.assertEqual({r['workspace_id'] for r in rows},{self.alice.me['workspace_id'],self.bob.me['workspace_id'],workspace})

    def test_project_task_question_receipt_and_artifact_guards(self):
        job=self.invoke(self.alice).json();ctx=self.s.claim()
        # Create a valid artifact and question using the actual organization pipeline.
        self.s.decide(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],dict(action='wait',summary='提问',messages=[dict(to_task='user',kind='question',content='什么方向？')]))
        question=self.alice.get('/api/v1/apps/demo/runs/'+job['id']).json()['questions'][0]['id']
        self.assertEqual(self.bob.post('/api/v1/questions/'+question+'/answers',json={'content':'偷看'}).status_code,404)
        self.alice.post('/api/v1/questions/'+question+'/answers',json={'content':'继续'}).raise_for_status()
        ctx=self.s.claim()
        from orchpact.runtime import Providers
        key=Providers(self.s).reserve(ctx,'model','text')
        self.s.decide(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],dict(action='deliver',summary='完成',result='完成',files=[dict(path='result.json',content='{}')]))
        job=self.alice.get('/api/v1/apps/demo/runs/'+job['id']).json()
        paths=['/runs/'+job['id']+'/requests','/runs/'+job['id']+'/usage','/work-items/'+job['root_task_id']+'/attempts',
               '/artifacts/'+job['artifact_id']+'/download','/artifacts/'+job['artifact_id']+'/preview','/requests/'+key]
        for path in paths:
            self.assertEqual(self.bob.get('/api/v1'+path).status_code,404,path)
        self.assertEqual(self.alice.get('/api/v1/artifacts/'+job['artifact_id']+'/download').status_code,200)
        self.assertEqual(self.bob.post('/api/v1/runs/'+job['id']+'/cancel').status_code,404)
        self.assertEqual(self.bob.post('/api/v1/work-items/'+job['root_task_id']+'/retry').status_code,404)

    def test_admin_routes_are_not_consumer_capabilities(self):
        for method,path,body in [('GET','/state',None),

            ('PATCH','/admin/users/unknown',{'disabled':True}),('GET','/admin/users',None)]:
            r=self.alice.request(method,'/api/v1'+path,**({'json':body} if body is not None else {}))
            self.assertEqual(r.status_code,403,path)
        config=self.alice.get('/api/v1/apps/demo').json()
        self.assertEqual(list(config['operations']),['run'])
        self.assertNotIn('unit_id',str(config))

    def test_invitations_roles_and_leave(self):
        personal=self.alice.me['workspace_id']
        self.assertEqual(self.alice.post('/api/v1/workspaces/'+personal+'/invitations').status_code,409)
        workspace=self.workspace(self.alice)
        code=self.join(self.alice,self.bob,workspace)
        self.assertEqual(self.cara.post('/api/v1/workspaces/join',json={'code':code}).status_code,409)
        self.assertEqual(self.bob.post('/api/v1/workspaces/'+workspace+'/invitations').status_code,403)
        self.assertEqual(self.bob.delete('/api/v1/workspaces/'+workspace+'/members/'+self.alice.me['user_id']).status_code,409)
        self.assertEqual(self.bob.delete('/api/v1/workspaces/'+workspace+'/members/'+self.bob.me['user_id']).status_code,200)
        self.assertEqual(self.bob.get('/api/v1/workspaces/'+workspace+'/members').status_code,404)
        invite=self.alice.post('/api/v1/workspaces/'+workspace+'/invitations').json()
        self.alice.delete('/api/v1/workspaces/'+workspace+'/invitations/'+invite['id']).raise_for_status()
        self.assertEqual(self.cara.post('/api/v1/workspaces/join',json={'code':invite['code']}).status_code,409)

    def test_sessions_login_logout_disable_and_hash_storage(self):
        token=self.alice.cookies.get('orchpact_session')
        with self.s.db.connect() as c:
            self.assertIsNotNone(c.execute('SELECT 1 FROM sessions WHERE hash=%s',(digest(token),)).fetchone())
            self.assertNotIn(PASSWORD,str([dict(r) for r in c.execute('SELECT * FROM users')]))
        self.alice.delete('/api/v1/session').raise_for_status()
        self.alice.cookies.set('orchpact_session',token)
        self.assertEqual(self.alice.get('/api/v1/auth/me').status_code,401)
        self.alice.cookies.clear()
        self.assertEqual(self.alice.post('/api/v1/auth/login',json=dict(username='alice',password='wrong-password')).status_code,401)
        r=self.alice.post('/api/v1/auth/login',json=dict(username='ALICE',password=PASSWORD))
        self.assertEqual(r.status_code,200,r.text)
        self.assertIn('Secure',r.headers['set-cookie']);self.assertIn('HttpOnly',r.headers['set-cookie'])
        self.admin.patch('/api/v1/admin/users/'+self.alice.me['user_id'],json={'disabled':True}).raise_for_status()
        self.assertEqual(self.alice.get('/api/v1/auth/me').status_code,401)

    def test_unknown_invites_csrf_and_public_host(self):
        c=TestClient(self.app,base_url='https://test.example');self.clients.append(c)
        r=c.post('/api/v1/auth/register',json=dict(username='unknown',password=PASSWORD,invitation='x'*32))
        self.assertEqual(r.status_code,409)
        self.assertEqual(self.alice.post('/api/v1/workspaces',json={'name':'x'},headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(c.get('/account/').status_code,200)
        self.assertNotIn('set-cookie',c.get('/account/').headers)
        self.assertEqual(c.get('/healthz',headers={'Host':'evil.example'}).status_code,403)
        for origin in ('http://example.com','https://example.com/path','https://u:p@example.com'):
            with self.assertRaises(ValueError):replace(self.settings,public_origin=origin)

    def test_admin_documents_are_isolated_from_personal_workspace(self):
        path='/api/v1/apps/demo/documents/index'
        self.admin.put(path,json={'revision':0,'value':{'admin':True}}).raise_for_status()
        self.assertIsNone(self.alice.get(path).json()['value'])
        self.assertEqual(self.admin.get(path).json()['value'],{'admin':True})

    def test_quotas_apply_per_user_across_spaces_and_idempotent_replay(self):
        first=self.invoke(self.alice,'one').json()
        self.invoke(self.alice,'two').raise_for_status()
        space=self.workspace(self.alice);self.alice.headers['X-Workspace-Id']=space
        self.invoke(self.alice,'three').raise_for_status()
        self.assertEqual(self.invoke(self.alice,'four').status_code,201)
        self.bob.headers['X-Workspace-Id']=self.bob.me['workspace_id']
        self.assertEqual(self.invoke(self.bob,'four').status_code,201)
        del self.alice.headers['X-Workspace-Id']
        self.assertEqual(self.invoke(self.alice,'one').json()['id'],first['id'])

    def test_concurrent_invitation_redemption_and_revision_conflict(self):
        space=self.workspace(self.alice)
        invitation=self.alice.post('/api/v1/workspaces/'+space+'/invitations').json()['code']
        with ThreadPoolExecutor(2) as pool:
            statuses=list(pool.map(lambda c:c.post('/api/v1/workspaces/join',json={'code':invitation}).status_code,[self.bob,self.cara]))
        self.assertEqual(sorted(statuses),[200,409])
        self.join(self.alice,self.bob if statuses[0]!=200 else self.cara,space)
        for c in (self.bob,self.cara):c.headers['X-Workspace-Id']=space
        with ThreadPoolExecutor(2) as pool:statuses=list(pool.map(lambda c:self.doc(c,{'race':True}).status_code,[self.bob,self.cara]))
        self.assertEqual(sorted(statuses),[200,409])

    def test_auth_attempt_throttle(self):
        with patch('orchpact.access.password_hash',return_value=self.hashed):
            statuses=[self.alice.post('/api/v1/auth/login',json=dict(username='missing',password=PASSWORD)).status_code for _ in range(13)]
        self.assertEqual(statuses[-1],429)

    def test_reference_images_cannot_cross_workspace_even_for_shared_member(self):
        import base64
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ1sAAAAASUVORK5CYII=')
        self.settings.profiles['image']=dict(id='image',label='Image',kind='image',model='test',base_url='https://example.invalid',key_env='IMAGE_KEY',capabilities=['image','image_reference'],priority=1,provider='openai')
        self.invoke(self.alice).raise_for_status();ctx=self.s.claim()
        self.s.decide(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],dict(action='plan',summary='render fixture',recruits=[dict(name='renderer',role='image',capabilities=['image'],reason='fixture')],assignments=[dict(agent='renderer',description='render',expected='image')]))
        ctx=self.s.claim()
        self.s.complete_work(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],'图片',[dict(path='reference.png',data=png)])
        with self.s.db.connect() as c:artifact=self.s.row(c,'tasks',ctx['task']['id'])['artifact_id']
        payload=dict(operation='run',input={},image_request=dict(prompt='参考图片',model_id='image',reference_artifact_ids=[artifact]))
        response=self.bob.post('/api/v1/apps/demo/runs',json=payload,headers={'Idempotency-Key':'ref'})
        self.assertEqual(response.status_code,404)
        space=self.workspace(self.alice);self.alice.headers['X-Workspace-Id']=space
        response=self.alice.post('/api/v1/apps/demo/runs',json=payload,headers={'Idempotency-Key':'ref'})
        self.assertEqual(response.status_code,404,response.text)
        del self.alice.headers['X-Workspace-Id']
        self.assertEqual(self.alice.post('/api/v1/apps/demo/runs',json=payload,headers={'Idempotency-Key':'ref'}).status_code,201)

    def test_public_admin_pages_and_auth_context_isolation(self):
        login=self.alice.get('/admin/')
        self.assertEqual(login.status_code,200)
        self.assertIn('验证管理凭据',login.text)
        self.assertNotIn('id="projects"',login.text)
        for path in ('/api/docs','/api/v1/openapi.json'):
            self.assertEqual(self.alice.get(path).status_code,403,path)
            self.assertEqual(self.admin.get(path).status_code,200,path)
        self.doc(self.alice,{'owner':'alice'});self.doc(self.bob,{'owner':'bob'})
        with ThreadPoolExecutor(8) as pool:
            rows=list(pool.map(lambda c:(c.get('/api/v1/apps/demo/documents/index').json()['value'],c.me['username']),[self.alice,self.bob]*10))
        self.assertTrue(all(value['owner']==username for value,username in rows))
        # A finished web request must not constrain internal organization execution.
        from orchpact.access import principal
        self.assertIsNone(principal.get())

    def test_shared_file_access_stops_immediately_after_removal(self):
        space=self.workspace(self.alice);self.join(self.alice,self.bob,space)
        self.alice.headers['X-Workspace-Id']=space
        job=self.invoke(self.alice).json();ctx=self.s.claim()
        self.s.decide(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],dict(action='deliver',summary='完成',result='完成',files=[dict(path='result.json',content='{}')]))
        artifact=self.alice.get('/api/v1/apps/demo/runs/'+job['id']).json()['artifact_id']
        path='/api/v1/artifacts/'+artifact+'/download'
        self.bob.headers['X-Workspace-Id']=space
        self.assertEqual(self.bob.get(path).status_code,200)
        self.alice.delete('/api/v1/workspaces/'+space+'/members/'+self.bob.me['user_id']).raise_for_status()
        self.assertEqual(self.bob.get(path).status_code,404)

    def test_account_sessions_survive_app_restart_but_expire_and_invites_expire(self):
        token=self.alice.cookies.get('orchpact_session')
        from orchpact.access import Access
        self.assertEqual(Access(self.s.db).authenticate(token).user_id,self.alice.me['user_id'])
        workspace=self.workspace(self.bob)
        invite=self.bob.post('/api/v1/workspaces/'+workspace+'/invitations').json()['code']
        with self.s.db.tx() as c:
            c.execute('UPDATE sessions SET expires=%s WHERE hash=%s',(now()-1,digest(token)))
            c.execute('UPDATE invitations SET expires=%s WHERE hash=%s',(now()-1,digest(invite)))
        self.assertEqual(self.alice.get('/api/v1/auth/me').status_code,401)
        with patch('orchpact.access.password_hash',return_value=self.hashed):
            self.assertEqual(self.cara.post('/api/v1/workspaces/join',json={'code':invite}).status_code,409)
