from orchpact.testing import database_url_for
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import importlib.util
import json
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from fastapi.testclient import TestClient
from orchpact.api import create_app
from orchpact.application_keys import canonical
from orchpact.config import Settings
from account_fixtures import register_package

TOKEN='key-test-admin-token-'*4


class ApplicationKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.settings=Settings(root,TOKEN,{'text':dict(id='text',kind='text',model='test',key_env='TEST_KEY',capabilities=['text','json'],base_url='https://example.invalid')},root/'var',database_url=database_url_for(root/'var'))
        self.app=create_app(self.settings,run_worker=False)
        self.client=TestClient(self.app).__enter__();self.client.headers['Authorization']='Bearer '+TOKEN;self.s=self.app.state.service
        for app in ('first','second'):register_package(self.client,app)
        with self.s.db.tx() as c:
            for user in ('payer','other'):
                c.execute('INSERT INTO users(id,username,password,created) VALUES(%s,%s,%s,%s)',(user,user,'unused',time.time()))
                self.s.access.new_workspace(c,user,user+' space',True)
                self.s.access.wallets.create(c,user)
                self.s.access.wallets.entry(c,user,'adjustment',10000*1000000,reason='isolated test budget')
            self.workspace=c.execute("SELECT id FROM workspaces WHERE owner_id='payer'").fetchone()[0]
        self.private=Ed25519PrivateKey.generate();self.key=self.register().json()

    def tearDown(self):self.client.__exit__(None,None,None);self.tmp.cleanup()

    def register(self,app='first',private=None,**changes):
        public=(private or self.private).public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        return self.client.post('/api/v1/apps/'+app+'/keys',json=dict(label='production',public_key=public,user_id='payer',workspace_id=self.workspace,**changes))

    def signed(self,method,path,body=b'',app='first',key=None,private=None,nonce=None,timestamp=None,idempotency='',sign_path=None,extra=None,host='testserver'):
        key=key or self.key['id'];timestamp=str(timestamp or int(time.time()));nonce=nonce or secrets.token_urlsafe(24)
        signature=(private or self.private).sign(canonical(key,app,timestamp,nonce,method,sign_path or path,host,idempotency,body))
        headers={'Host':host,'X-Orchpact-Key-Id':key,'X-Orchpact-Application':app,'X-Orchpact-Timestamp':timestamp,'X-Orchpact-Nonce':nonce,'X-Orchpact-Signature':base64.b64encode(signature).decode()}
        if idempotency:headers['Idempotency-Key']=idempotency
        if body:headers['Content-Type']='application/json'
        headers.update(extra or {})
        # Separate client so no admin header/cookie can accidentally authenticate it.
        return TestClient(self.app).request(method,path,content=body,headers=headers)

    def invoke(self,idempotency='job-1'):
        return self.signed('POST','/api/v1/apps/first/runs',json.dumps({'operation':'run','input':{'prompt':'任务'}}).encode(),idempotency=idempotency)

    def test_signed_invocation_owner_wallet_idempotency_and_actual_charge(self):
        response=self.invoke();self.assertEqual(response.status_code,201,response.text);job=response.json()
        self.assertEqual(job['payer_user_id'],'payer')
        replay=self.invoke();self.assertEqual(replay.status_code,201,replay.text);self.assertEqual(replay.json()['id'],job['id'])
        self.assertEqual(self.signed('GET','/api/v1/apps/first/runs/'+job['id']).status_code,200)
        output=dict(action='deliver',summary='完成',result='完成',files=[dict(path='result.json',content='{}')])
        response=httpx.Response(200,request=httpx.Request('POST','https://example.invalid'),json={'usage':{'total_tokens':2000},'choices':[{'message':{'content':json.dumps(output)},'finish_reason':'stop'}]})
        with patch.dict('os.environ',{'TEST_KEY':'test-only'}),patch('orchpact.runtime.httpx.Client') as network:
            network.return_value.__enter__.return_value.post.return_value=response
            self.app.state.runner.step()
        with self.s.db.connect() as c:
            project=c.execute('SELECT * FROM projects WHERE id=%s',(job['id'],)).fetchone()
            self.assertEqual(project['workspace_id'],self.workspace)
            self.assertEqual(self.s.access.wallets.view(c,'payer')['available'],9998)
            self.assertEqual(self.s.access.wallets.view(c,'other')['available'],10000)

    def test_replay_expiry_tampering_and_wrong_private_key(self):
        path='/api/v1/apps/first';nonce=secrets.token_urlsafe(24)
        self.assertEqual(self.signed('GET',path,nonce=nonce).status_code,200)
        self.assertEqual(self.signed('GET',path,nonce=nonce).status_code,401)
        self.assertEqual(self.signed('GET',path,timestamp=int(time.time())-400).status_code,401)
        self.assertEqual(self.signed('GET',path,private=Ed25519PrivateKey.generate()).status_code,401)
        self.assertEqual(self.signed('GET',path+'?changed=yes',sign_path=path).status_code,401)
        self.assertEqual(self.signed('PUT',path+'/documents/a',b'{"revision":0,"value":1}',sign_path=path).status_code,401)
        self.assertEqual(self.signed('GET',path,extra={'X-Workspace-Id':self.workspace}).status_code,400)
        self.assertEqual(self.signed('GET',path,extra={'Authorization':'Bearer '+TOKEN}).status_code,400)

    def test_apps_and_management_resources_are_isolated(self):
        self.assertEqual(self.signed('GET','/api/v1/apps/second').status_code,403)
        for path in ('/api/v1/apps','/api/v1/admin/users','/api/v1/workspaces','/api/v1/auth/me','/api/v1/apps/first/keys'):
            self.assertEqual(self.signed('GET',path).status_code,403,path)
        me=self.client.get('/api/v1/auth/me').json()
        self.s.access.wallets.adjust(me['user_id'],10000,'fixture','admin-budget',me['user_id'])
        second=self.client.post('/api/v1/apps/second/runs',json={'operation':'run','input':{}},headers={'Idempotency-Key':'second'}).json()
        self.assertEqual(self.signed('GET','/api/v1/apps/first/runs/'+second['id']).status_code,404)
        self.assertEqual(self.signed('POST','/api/v1/work-items/'+second['root_task_id']+'/retry',b'{}').status_code,404)

    def test_each_app_has_independent_keys_and_rotation_revocation(self):
        self.assertEqual(self.register('second').status_code,409)
        private=Ed25519PrivateKey.generate();new=self.register(private=private)
        self.assertEqual(new.status_code,201,new.text)
        path='/api/v1/apps/first'
        self.assertEqual(self.signed('GET',path,key=new.json()['id'],private=private).status_code,200)
        self.assertEqual(self.client.delete(path+'/keys/'+self.key['id']).status_code,200)
        self.assertEqual(self.signed('GET',path).status_code,401)
        self.assertEqual(self.signed('GET',path,key=new.json()['id'],private=private).status_code,200)
        history=self.client.get(path+'/key-history').json()
        self.assertEqual([h['action'] for h in history],['revoked','created','created'])
        self.assertNotIn('PRIVATE KEY',self.client.get(path+'/keys').text)
        self.assertEqual(self.client.delete('/api/v1/apps/second/keys/'+new.json()['id']).status_code,404)

    def test_disabled_user_removed_membership_and_expired_key(self):
        path='/api/v1/apps/first'
        with self.s.db.tx() as c:c.execute("UPDATE users SET disabled=1 WHERE id='payer'")
        self.assertEqual(self.signed('GET',path).status_code,401)
        with self.s.db.tx() as c:
            c.execute("UPDATE users SET disabled=0 WHERE id='payer'")
            c.execute("DELETE FROM workspace_members WHERE user_id='payer'")
        self.assertEqual(self.signed('GET',path).status_code,404)
        with self.s.db.tx() as c:
            c.execute('INSERT INTO workspace_members VALUES(%s,%s)',(self.workspace,'payer'))
            c.execute('UPDATE application_keys SET expires=0 WHERE id=%s',(self.key['id'],))
        self.assertEqual(self.signed('GET',path).status_code,401)

    def test_invalid_public_key_and_non_member_payer_rejected(self):
        self.assertEqual(self.client.post('/api/v1/apps/first/keys',json=dict(label='bad',public_key='-----BEGIN PRIVATE KEY-----'+'a'*80,user_id='payer',workspace_id=self.workspace)).status_code,409)
        self.assertEqual(self.register(private=generate_private_key(public_exponent=65537,key_size=2048)).status_code,409)
        # Pick the known other workspace, irrespective of ordering.
        with self.s.db.connect() as c:other_workspace=c.execute("SELECT id FROM workspaces WHERE owner_id='other'").fetchone()[0]
        public=self.private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.assertEqual(self.client.post('/api/v1/apps/first/keys',json=dict(label='bad',public_key=public,user_id='payer',workspace_id=other_workspace)).status_code,404)

    def test_nonce_persists_after_restart_and_documents_scoped(self):
        path='/api/v1/apps/first/documents/example';nonce=secrets.token_urlsafe(24)
        self.assertEqual(self.signed('PUT',path,b'{"revision":0,"value":{"ok":true}}',nonce=nonce).status_code,200)
        self.restart()
        self.assertEqual(self.signed('PUT',path,b'{"revision":0,"value":{"ok":true}}',nonce=nonce).status_code,401)
        self.assertEqual(self.signed('GET',path).json()['value'],{'ok':True})

    def test_same_workspace_other_application_tasks_and_files_are_denied(self):
        other_private=Ed25519PrivateKey.generate();other=self.register('second',private=other_private).json()
        response=self.signed('POST','/api/v1/apps/second/runs',b'{"operation":"run","input":{}}',app='second',key=other['id'],private=other_private,idempotency='shared-space')
        self.assertEqual(response.status_code,201,response.text);job=response.json()
        self.assertEqual(self.signed('GET','/api/v1/apps/first/runs/'+job['id']).status_code,404)
        self.assertEqual(self.signed('GET','/api/v1/runs/'+job['id']+'/usage').status_code,404)
        self.assertEqual(self.signed('POST','/api/v1/work-items/'+job['root_task_id']+'/retry',b'{}').status_code,404)
        self.assertEqual(self.signed('POST','/api/v1/runs/'+job['id']+'/cancel',b'{}').status_code,404)

    def test_empty_balance_blocks_new_tasks_but_allows_recovery(self):
        job=self.invoke().json()
        with self.s.db.tx() as c:c.execute("UPDATE credit_wallets SET balance=0 WHERE user_id='payer'")
        self.assertEqual(self.invoke().json()['id'],job['id'])
        self.assertEqual(self.signed('GET','/api/v1/apps/first/runs/'+job['id']).status_code,200)
        self.assertEqual(self.invoke('new-job').status_code,409)

    def test_application_pause_and_signed_header_body_tampering(self):
        path='/api/v1/apps/first'
        timestamp=str(int(time.time()));nonce=secrets.token_urlsafe(24)
        signed_body=b'{"operation":"run","input":{}}'
        signature=self.private.sign(canonical(self.key['id'],'first',timestamp,nonce,'POST',path+'/runs','testserver','business-key',signed_body))
        headers={'X-Orchpact-Key-Id':self.key['id'],'X-Orchpact-Application':'first','X-Orchpact-Timestamp':timestamp,'X-Orchpact-Nonce':nonce,'X-Orchpact-Signature':base64.b64encode(signature).decode(),'Idempotency-Key':'business-key','Content-Type':'application/json'}
        client=TestClient(self.app)
        self.assertEqual(client.post(path+'/runs',content=b'{"operation":"run","input":{"tampered":true}}',headers=headers).status_code,401)
        headers['Idempotency-Key']='changed'
        self.assertEqual(client.post(path+'/runs',content=signed_body,headers=headers).status_code,401)
        current=self.client.get(path).json()
        data=current['manifest'];data['enabled']=False
        self.assertEqual(self.client.post('/api/v1/organization-packages?revision=1',json=data).status_code,200)
        self.assertEqual(self.signed('GET',path).status_code,401)
        self.assertEqual(self.client.post(path+'/runs',json={'operation':'run','input':{}},headers={'Idempotency-Key':'pause'}).status_code,409)
        data['enabled']=True
        self.assertEqual(self.client.post('/api/v1/organization-packages?revision=2',json=data).status_code,200)
        self.assertEqual(self.signed('GET',path).status_code,200)

    def test_concurrent_replay_is_admitted_once(self):
        nonce=secrets.token_urlsafe(24)
        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses=list(pool.map(lambda _:self.signed('GET','/api/v1/apps/first',nonce=nonce).status_code,range(4)))
        self.assertEqual(sorted(statuses),[200,401,401,401])

    def test_product_host_binding_and_signed_host_tampering(self):
        frontend=Path(self.tmp.name)/'frontend';frontend.mkdir();(frontend/'index.html').write_text('test product')
        frontends={app:{'route':'/'+app,'directory':str(frontend)} for app in ('first','second')}
        self.restart(replace(self.settings,public_origin='https://admin.example',products={'first':'https://first.example','second':'https://second.example'},frontends=frontends))
        self.assertEqual(self.signed('GET','/api/v1/apps/first',host='first.example').status_code,200)
        self.assertEqual(self.signed('GET','/api/v1/apps/first',host='second.example').status_code,404)
        self.assertEqual(self.signed('GET','/api/v1/apps/first',extra={'Host':'first.example'}).status_code,401)


    def restart(self,settings=None):
        self.client.__exit__(None,None,None)
        self.app=create_app(settings or self.settings,run_worker=False)
        self.client=TestClient(self.app).__enter__();self.client.headers['Authorization']='Bearer '+TOKEN
        self.s=self.app.state.service

    def test_signing_client_preserves_raw_query_bytes(self):
        from orchpact.application_signing import SigningCredentials
        credentials=SigningCredentials('first',self.key['id'],self.private)
        target='/api/v1/apps/first?x=%E4%B8%AD&x=2'
        headers=credentials.headers('GET',target,'testserver')
        self.assertEqual(TestClient(self.app).get(target,headers=headers).status_code,200)
        headers=credentials.headers('GET',target,'testserver')
        self.assertEqual(TestClient(self.app).get(target.replace('x=2','x=3'),headers=headers).status_code,401)


if __name__=='__main__':unittest.main()
