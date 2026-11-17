"""HTTP, tenancy and signed application regressions for the fresh control plane."""
from dataclasses import replace
import json
import time
from pathlib import Path
import tempfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from filelock import FileLock
from fastapi.testclient import TestClient

from orchpact.adapters.database import ident, now, dumps
from orchpact.adapters.identity import Principal, principal
from orchpact.application_signing import SigningCredentials
from orchpact.bootstrap import Instance
from orchpact.kernel.errors import Forbidden
from runtime_fixtures import KernelFixture, package, TOKEN


class SecurityTests(KernelFixture):
    def new_user(self, name='reader'):
        with self.s.db.tx() as c:
            user=ident();c.execute('INSERT INTO users(id,username,password,created) VALUES(%s,%s,%s,%s)',(user,name,'',now()))
            workspace=self.s.access.new_workspace(c,user,name,True)
            c.execute('INSERT INTO credit_wallets(user_id,balance) VALUES(%s,%s)',(user,10000*1_000_000))
        return user,workspace,self.s.access.session(user)

    def test_core_requires_auth(self):
        self.client.headers.pop('Authorization')
        self.assertEqual(self.client.get('/api/v1/state').status_code,401)
        self.assertEqual(self.client.get('/api/v1/openapi.json').status_code,401)
        self.assertEqual(self.client.get('/healthz').status_code,200)

    def test_cookie_cross_site_write_rejected(self):
        self.client.headers.pop('Authorization')
        self.assertEqual(self.client.post('/api/v1/session',json=dict(token=TOKEN)).status_code,200)
        self.assertEqual(self.client.post('/api/v1/workspaces',json=dict(name='bad'),headers={'Origin':'https://attacker.invalid'}).status_code,403)
        self.assertEqual(self.client.post('/api/v1/workspaces',json=dict(name='bad'),headers={'Sec-Fetch-Site':'cross-site'}).status_code,403)
        cookie=self.client.cookies.get('orchpact_session');self.assertTrue(cookie)

    def test_body_limit_including_missing_content_length(self):
        self.assertEqual(self.client.post('/api/v1/organization-packages',content=b'x'*2_000_001,headers={'Content-Type':'application/json'}).status_code,413)

    def test_two_instances_cannot_initialize_same_directory(self):
        with self.s.db.connect() as c:before=c.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0]
        second=Instance(self.settings)
        with self.assertRaises(RuntimeError):second.open()
        with self.s.db.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0],before)


    def test_transaction_failure_rolls_back_all_changes(self):
        with self.assertRaises(RuntimeError):
            with self.s.db.tx() as c:
                c.execute("INSERT INTO instance_metadata VALUES('rollback-probe','temporary')")
                raise RuntimeError('injected failure')
        with self.s.db.connect() as c:self.assertIsNone(c.execute("SELECT 1 FROM instance_metadata WHERE key='rollback-probe'").fetchone())

    def test_named_migration_failure_keeps_backup_and_no_ledger(self):
        with self.assertRaises(Exception):self.s.db.migrate('0002_failure',['CREATE TABLE temporary_probe(id TEXT)','INVALID SQL'])
        with self.s.db.connect() as c:
            self.assertIsNone(c.execute("SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='temporary_probe'").fetchone())
            self.assertIsNone(c.execute("SELECT 1 FROM schema_migrations WHERE name='0002_failure'").fetchone())
        self.assertTrue((self.s.db.backup_directory/'0002_failure.dump').exists())

    def test_workspace_and_application_resource_isolation(self):
        run=self.invoke();self.finish();artifact=self.view(run)['artifact_id']
        user,workspace,token=self.new_user()
        self.client.headers['Authorization']='Bearer '+token
        self.assertEqual(self.client.get('/api/v1/apps/fixture/runs/'+run['id']).status_code,404)
        self.assertEqual(self.client.get('/api/v1/artifacts/'+artifact+'/download').status_code,404)
        self.assertEqual(self.client.get('/api/v1/apps/fixture/documents/index',headers={'X-Workspace-Id':self.workspace}).status_code,404)

    def test_membership_removal_revokes_existing_session_resource_access(self):
        run=self.invoke();user,workspace,token=self.new_user()
        with self.s.db.tx() as c:
            c.execute('INSERT INTO workspace_members VALUES(%s,%s)',(self.workspace,user))
        self.client.headers['Authorization']='Bearer '+token
        self.assertEqual(self.client.get('/api/v1/apps/fixture/runs/'+run['id'],headers={'X-Workspace-Id':self.workspace}).status_code,200)
        with self.s.db.tx() as c:c.execute('DELETE FROM workspace_members WHERE workspace_id=%s AND user_id=%s',(self.workspace,user))
        self.assertEqual(self.client.get('/api/v1/apps/fixture/runs/'+run['id'],headers={'X-Workspace-Id':self.workspace}).status_code,404)

    def signed_setup(self):
        value=package();value['require_signature']=True;self.register(value)
        user,workspace,token=self.new_user()
        private=Ed25519PrivateKey.generate()
        public=private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        response=self.client.post('/api/v1/apps/fixture/keys',json=dict(label='fixture key',public_key=public,user_id=user,workspace_id=workspace,user_session=True))
        self.assertEqual(response.status_code,201,response.text)
        return user,workspace,token,SigningCredentials('fixture',response.json()['id'],private)

    def signed(self, credentials, token, workspace, path='/api/v1/apps/fixture/runs', body=None):
        raw=dumps(body or dict(operation='generate',input={})).encode()
        headers=credentials.headers('POST',path,'testserver',raw,'signed-fixture',session=token,workspace=workspace)
        headers.update({'Content-Type':'application/json','Idempotency-Key':'signed-fixture'})
        self.client.headers.pop('Authorization',None)
        return raw,headers

    def test_signed_delegation_binds_personal_payer(self):
        user,workspace,token,key=self.signed_setup();raw,headers=self.signed(key,token,workspace)
        response=self.client.post('/api/v1/apps/fixture/runs',content=raw,headers=headers)
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(response.json()['payer_user_id'],user)
        self.assertEqual(self.client.post('/api/v1/apps/fixture/runs',content=raw,headers=headers).status_code,401)
        raw,headers=self.signed(key,token,workspace)
        replay=self.client.post('/api/v1/apps/fixture/runs',content=raw,headers=headers)
        self.assertEqual(replay.json()['id'],response.json()['id'])

    def test_signed_request_tampering_or_revocation_rejected(self):
        user,workspace,token,key=self.signed_setup();raw,headers=self.signed(key,token,workspace)
        self.assertEqual(self.client.post('/api/v1/apps/fixture/runs',content=raw+b' ',headers=headers).status_code,401)
        with self.s.db.tx() as c:c.execute('UPDATE application_keys SET revoked=%s WHERE id=%s',(now(),key.key_id))
        raw,headers=self.signed(key,token,workspace)
        self.assertEqual(self.client.post('/api/v1/apps/fixture/runs',content=raw,headers=headers).status_code,401)

    def test_signed_identity_cannot_override_workspace_or_bearer(self):
        user,workspace,token,key=self.signed_setup();raw,headers=self.signed(key,token,workspace)
        headers['X-Workspace-Id']=self.workspace
        self.assertEqual(self.client.post('/api/v1/apps/fixture/runs',content=raw,headers=headers).status_code,400)
        raw,headers=self.signed(key,token,workspace);headers['Authorization']='Bearer '+TOKEN
        self.assertEqual(self.client.post('/api/v1/apps/fixture/runs',content=raw,headers=headers).status_code,400)

    def test_unsigned_user_cannot_bypass_required_signature(self):
        user,workspace,token,key=self.signed_setup()
        self.client.headers['Authorization']='Bearer '+token
        self.assertEqual(self.client.post('/api/v1/apps/fixture/runs',json=dict(operation='generate',input={}),headers={'Idempotency-Key':'unsigned'}).status_code,403)

    def test_organization_package_import_does_not_execute_code_or_remote_schema(self):
        value=package();value['operations']['generate']['input_schema']={'$ref':'https://example.invalid/schema'}
        self.assertEqual(self.client.post('/api/v1/organization-packages',json=value).status_code,409)
        value=package();value['operations']['generate']['execution_plan']['nodes'][0]['unit_id']='external-id'
        self.assertEqual(self.client.post('/api/v1/organization-packages',json=value).status_code,409)

    def test_registration_uses_invitation_and_never_grants_operator_privileges(self):
        response=self.client.post('/api/v1/admin/invitations',json={});self.assertEqual(response.status_code,200)
        invitation=response.json()['code'];self.client.headers.pop('Authorization')
        response=self.client.post('/api/v1/auth/register',json=dict(username='invited-user',password='long-fixture-password',invitation=invitation))
        self.assertEqual(response.status_code,201,response.text)
        me=self.client.get('/api/v1/auth/me').json();self.assertFalse(me['admin']);self.assertEqual(me['credits']['balance'],0)
        self.assertEqual(self.client.post('/api/v1/organization-packages',json=package()).status_code,403)
        self.assertEqual(self.client.post('/api/v1/auth/register',json=dict(username='another-user',password='long-fixture-password',invitation=invitation)).status_code,409)

    def test_database_owner_lock_applies_across_different_data_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            second=Instance(replace(self.settings,data=Path(directory)/'var'))
            with self.assertRaisesRegex(RuntimeError,'PostgreSQL'):
                second.open()
        with self.s.db.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM schema_migrations WHERE name='0001_initial_schema'").fetchone()[0],1)

    def test_postgresql_database_refuses_an_unrelated_nonempty_database(self):
        from orchpact.testing import database_url_for
        from orchpact.adapters.database import Database
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);url=database_url_for(path/'var')
            db=Database(url)
            with db.connect() as c:c.execute('CREATE TABLE foreign_application(id TEXT)')
            with self.assertRaisesRegex(ValueError,'独立空库'):
                Instance(replace(self.settings,root=path,data=path/'var',database_url=url)).open()
            with db.connect() as c:self.assertFalse(db.has_table(c,'instance_metadata'))

    def test_database_and_file_store_must_be_restored_together(self):
        self.app.state.instance.close()
        with tempfile.TemporaryDirectory() as directory:
            second=Instance(replace(self.settings,data=Path(directory)/'var'))
            with self.assertRaisesRegex(ValueError,'数据目录不匹配'):
                second.open()

    def test_sqlite_is_not_a_storage_fallback(self):
        from orchpact.adapters.database import Database
        for value in ('sqlite:///temporary.db','/tmp/temporary.db',''):
            with self.assertRaises(ValueError):Database(value)

    def test_lost_database_ownership_blocks_late_writes(self):
        with self.s.db.connect() as c:c.execute('SELECT pg_terminate_backend(%s)',(self.s.db.owner_pid,))
        self.assertFalse(self.s.db.healthy())
        with self.assertRaisesRegex(RuntimeError,'所有权已失效'):
            self.s.access.wallets.adjust(self.user,1,'blocked after lost ownership','lost-owner',self.user)
