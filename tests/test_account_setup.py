"""The instance has one permanent administrator identity using account sessions."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from orchpact.adapters.database import ident, now
from orchpact.adapters.identity import digest
from orchpact.adapters.schema import MIGRATIONS
from orchpact.api import create_app
from orchpact.config import Settings
from orchpact.testing import database_url_for


PASSWORD = 'isolated-account-setup-password'
BASE = 'http://localhost'
LOOPBACK = ('127.0.0.1',50100)


class AccountSetupTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        root = Path(folder)
        app = create_app(Settings(root,{},root/'var',database_url=database_url_for(root/'var')),run_worker=False)
        self.app = app
        self.client = self.enterContext(TestClient(app,base_url=BASE,client=LOOPBACK))
        self.s = app.state.service

    def test_first_account_setup_is_atomic_and_later_registration_cannot_grant_admin(self):
        self.assertEqual(self.client.get('/api/v1/auth/setup').json(),dict(required=True,available=True))
        def request(index):
            client=TestClient(self.app,base_url=BASE,client=LOOPBACK)
            try:
                return client.post('/api/v1/auth/setup',json=dict(username=f'founder-{index}',password=PASSWORD)).status_code
            finally:
                client.close()
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(request,range(2))),[201,409])
        self.assertEqual(self.client.get('/api/v1/auth/setup').json(),dict(required=False,available=False))
        with self.s.db.connect() as c:
            primary=c.execute('SELECT id,username FROM users').fetchone()
            self.assertEqual(c.execute('SELECT count(*) FROM users').fetchone()[0],1)
        self.client.post('/api/v1/auth/login',json=dict(username=primary['username'],password=PASSWORD)).raise_for_status()
        self.assertTrue(self.client.get('/api/v1/auth/me').json()['admin'])
        self.assertEqual(self.client.patch('/api/v1/admin/users/'+primary['id'],json={'disabled':True}).status_code,409)
        self.assertEqual(self.client.post('/api/v1/session',json=dict(token='obsolete-token')).status_code,405)
        invitation=self.client.post('/api/v1/admin/invitations').json()['code']
        member=TestClient(self.app,base_url=BASE,client=LOOPBACK)
        try:
            payload=dict(username='ordinary-member',password=PASSWORD,invitation=invitation)
            wrong=member.post('/api/v1/auth/register',json=dict(payload,admin=True))
            self.assertEqual(wrong.status_code,422)
            self.assertNotIn(PASSWORD,wrong.text)
            member.post('/api/v1/auth/register',json=payload).raise_for_status()
            self.assertFalse(member.get('/api/v1/auth/me').json()['admin'])
            self.assertEqual(member.get('/api/v1/admin/users').status_code,403)
        finally:
            member.close()

    def test_web_setup_behind_proxy_or_from_nonlocal_peer_is_closed(self):
        payload=dict(username='blocked',password=PASSWORD)
        self.assertEqual(self.client.post('/api/v1/auth/setup',json=payload,
                            headers={'X-Forwarded-For':'203.0.113.10'}).status_code,403)
        self.assertEqual(self.client.post('/api/v1/auth/setup',json=payload,
                            headers={'Origin':'https://untrusted.invalid'}).status_code,403)
        client=TestClient(self.app,base_url=BASE,client=('203.0.113.10',50100))
        try:
            self.assertEqual(client.get('/api/v1/auth/setup').json(),dict(required=True,available=False))
            self.assertEqual(client.post('/api/v1/auth/setup',json=payload).status_code,403)
        finally:
            client.close()
        with self.s.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM users').fetchone()[0],0)

    def test_legacy_admin_conversion_preserves_identity_and_revokes_old_token_sessions(self):
        old_user, member = ident(), ident()
        with self.s.db.tx() as c:
            c.execute("INSERT INTO users(id,username,password,created) VALUES(%s,'platform-admin','',%s),(%s,'member','',%s)",
                      (old_user,now(),member,now()))
            workspace=self.s.access.new_workspace(c,old_user,'旧平台空间',True)
            self.s.access.wallets.create(c,old_user)
            c.execute("INSERT INTO instance_metadata VALUES('admin_user',%s),('admin_workspace',%s)",(old_user,workspace))
            c.execute("ALTER TABLE sessions ALTER COLUMN user_id DROP NOT NULL, ADD COLUMN admin BIGINT NOT NULL DEFAULT 0")
            c.execute("INSERT INTO sessions(hash,user_id,admin,expires) VALUES(%s,%s,1,%s),(%s,%s,0,%s)",
                      (digest('old-root-session'),old_user,now()+3600,digest('regular-session'),member,now()+3600))
            c.execute("DELETE FROM schema_migrations WHERE name='0006_account_sessions'")
        name, sql = MIGRATIONS[-1]
        self.s.db.migrate(name,sql)
        self.assertEqual(self.client.get('/api/v1/auth/setup').json(),dict(required=True,available=False))
        self.assertEqual(self.client.post('/api/v1/auth/setup',json=dict(username='blocked',password=PASSWORD)).status_code,409)
        self.assertEqual(self.client.get('/api/v1/auth/me',headers={'Authorization':'Bearer old-root-session'}).status_code,401)
        actor=self.s.access.setup_admin('restored-owner',PASSWORD,offline=True)
        self.assertEqual((actor.user_id,actor.workspace_id),(old_user,workspace))
        self.assertFalse(self.s.access.authenticate('regular-session').admin)
        self.client.post('/api/v1/auth/login',json=dict(username='restored-owner',password=PASSWORD)).raise_for_status()
        self.assertTrue(self.client.get('/api/v1/auth/me').json()['admin'])
