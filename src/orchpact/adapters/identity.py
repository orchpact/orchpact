"""Personal identities, sessions, and workspace ownership."""
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import hashlib
import hmac
import re
import secrets
import psycopg
import threading
from fastapi import HTTPException
from .database import ident, now

@dataclass(frozen=True)
class Principal:
    user_id: str | None = None
    admin: bool = False
    workspace_id: str | None = None
    application_id: str | None = None
    application_key_id: str | None = None
    client: str = "web"

USERNAME_PATTERN = r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,63}$'

principal = ContextVar('web_principal', default=None)
_hash_slots = threading.BoundedSemaphore(2)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    with _hash_slots:
        value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**17,
                               r=8, p=1, maxmem=256*1024*1024).hex()
    return salt + ':' + value


class Access:
    def __init__(self, db):
        self.db = db
        from ..services.wallets import Wallets
        self.wallets = Wallets(db)

    def throttle(self, key, limit=10, seconds=900):
            limited = False
            with self.db.tx() as c:
                c.execute('DELETE FROM auth_throttle WHERE reset<%s', (now(),))
                c.execute('INSERT INTO auth_throttle VALUES(%s,1,%s) ON CONFLICT(key) DO UPDATE SET count=auth_throttle.count+1', (key,now()+seconds))
                limited = c.execute('SELECT count FROM auth_throttle WHERE key=%s',(key,)).fetchone()[0] > limit
            if limited: raise HTTPException(429,'尝试过于频繁，请稍后再试')

    def session(self, user_id, audience=""):
        with self.db.tx() as c:
            return self._create_session(c, user_id, audience)

    def _create_session(self, c, user_id, audience):
        token = secrets.token_urlsafe(48)
        c.execute('DELETE FROM sessions WHERE expires<%s', (now(),))
        c.execute('INSERT INTO sessions(hash,user_id,expires,audience) VALUES(%s,%s,%s,%s)',
                  (digest(token), user_id, now()+43200, audience))
        return token

    def authenticate(self, token, audience=None):
        with self.db.connect() as c:
            row = c.execute("""
                SELECT s.user_id,s.audience,
                    s.user_id = (SELECT value FROM instance_metadata WHERE key='admin_user') AS admin,
                    (SELECT value FROM instance_metadata WHERE key='admin_workspace') AS admin_workspace
                FROM sessions s JOIN users u ON u.id=s.user_id
                WHERE s.hash=%s AND s.expires>%s AND u.disabled=0
                """, (digest(token),now())).fetchone()
        if not row or (audience is not None and row['audience'] != audience):
            raise HTTPException(401,'请登录后继续')
        admin = bool(row['admin'])
        return Principal(row['user_id'], admin, row['admin_workspace'] if admin else None)

    def revoke_session(self, token):
            with self.db.tx() as c:c.execute('DELETE FROM sessions WHERE hash=%s',(digest(token),))

    def password_user(self, username, password):
            with self.db.connect() as c:row=c.execute('SELECT * FROM users WHERE username=%s',(username.lower(),)).fetchone()
            salt=row['password'].split(':')[0] if row and row['password'] else '00'*16
            candidate=password_hash(password,salt)
            if not row or not row['password_set'] or not hmac.compare_digest(candidate,row['password']) or row['disabled']:
                raise HTTPException(401,'账号或密码不正确')
            return row['id']

    def login(self, username, password, audience=""):
            return self.session(self.password_user(username,password),audience=audience)

    def member(self,c,workspace,user=None,owner=False):
            actor=user or principal.get()
            row=c.execute('SELECT * FROM workspaces WHERE id=%s',(workspace,)).fetchone()
            if not row:raise HTTPException(404,'空间不存在或无权访问')
            if actor and actor.admin:return dict(row)
            if not actor or not c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=%s AND user_id=%s',(workspace,actor.user_id)).fetchone():
                raise HTTPException(404,'空间不存在或无权访问')
            if owner and row['owner_id']!=actor.user_id:raise HTTPException(403,'只有空间所有者可以管理成员')
            return dict(row)

    def scope(self,actor,requested=None):
            if actor.admin:
                if requested:
                    with self.db.connect() as c:self.member(c,requested,actor)
                return replace(actor,workspace_id=requested or actor.workspace_id)
            with self.db.connect() as c:
                if not requested:
                    row=c.execute('SELECT id FROM workspaces WHERE owner_id=%s AND personal=1',(actor.user_id,)).fetchone()
                    requested=row['id'] if row else None
                self.member(c,requested,actor)
            return replace(actor,workspace_id=requested)

    def project(self,c,project):
            actor=principal.get()
            if actor is None:raise HTTPException(401,'缺少执行身份')
            if actor.admin:return
            row=c.execute('SELECT workspace_id,application_id FROM projects WHERE id=%s',(project,)).fetchone()
            if not row or not row['workspace_id']:raise HTTPException(404,'项目不存在或无权访问')
            self.member(c,row['workspace_id'],actor)
            if actor.application_id and row['application_id']!=actor.application_id:
                raise HTTPException(404,'项目不存在或无权访问')
            if row['application_id'] and not actor.application_key_id:
                binding=c.execute('SELECT require_signature FROM applications WHERE id=%s',(row['application_id'],)).fetchone()
                if binding and binding['require_signature']:raise HTTPException(403,'此应用需要通过签名接入服务访问')

    def resource(self,c,table,row):
            if table=='projects':self.project(c,row['id'])
            elif table in ('tasks','messages','artifacts','requests','agents') and row.get('project_id'):
                self.project(c,row['project_id'])
            elif table=='invocations':self.project(c,row['id'])

    def new_workspace(self,c,user_id,name,personal=False):
            count=c.execute('SELECT COUNT(*) FROM workspaces WHERE owner_id=%s',(user_id,)).fetchone()[0]
            if count>=20:raise HTTPException(409,'每人最多创建20个空间')
            workspace=ident()
            c.execute('INSERT INTO workspaces VALUES(%s,%s,%s,%s,%s)',(workspace,name,user_id,int(personal),now()))
            c.execute('INSERT INTO workspace_members VALUES(%s,%s)',(workspace,user_id))
            return workspace

    @staticmethod
    def _primary(c):
        return c.execute("""
            SELECT m.value AS identity,u.id,u.username,u.password_set,
                u.password<>'' AS has_password,
                (SELECT value FROM instance_metadata WHERE key='admin_workspace') AS workspace_id
            FROM instance_metadata m LEFT JOIN users u ON u.id=m.value
            WHERE m.key='admin_user'
            """).fetchone()

    def _setup_state(self, c):
        primary = self._primary(c)
        if primary:
            if not primary['id'] or not primary['workspace_id']:
                return 'unavailable', primary
            if primary['has_password'] and primary['password_set']:
                return 'complete', primary
            if primary['username'] == 'platform-admin' and not primary['has_password']:
                others = c.execute('SELECT 1 FROM users WHERE id<>%s LIMIT 1', (primary['id'],)).fetchone()
                return ('legacy' if others else 'first'), primary
            return 'unavailable', primary
        if c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            return 'unavailable', None
        return 'first', None

    def setup_state(self):
        with self.db.connect() as c:
            return self._setup_state(c)[0]

    def setup_admin(self, username, password, *, offline=False):
        if not re.fullmatch(USERNAME_PATTERN, username) or re.fullmatch(r'1[3-9][0-9]{9}', username):
            raise HTTPException(422, '请使用 3–64 个字符的有效非手机号账号名')
        if not 12 <= len(password) <= 128:
            raise HTTPException(422, '密码须为 12–128 个字符')
        state = self.setup_state()
        if state not in ('first','legacy') or state == 'legacy' and not offline:
            raise HTTPException(409, '管理员已设置或需要在实例本机初始化')
        hashed = password_hash(password)
        username = username.lower()
        with self.db.tx() as c:
            # The write lock serializes competing initializations and registration.
            state, primary = self._setup_state(c)
            if state not in ('first','legacy') or state == 'legacy' and not offline:
                raise HTTPException(409, '管理员已设置或需要在实例本机初始化')
            if primary:
                user, workspace = primary['id'], primary['workspace_id']
                if c.execute('SELECT 1 FROM users WHERE username=%s AND id<>%s', (username,user)).fetchone():
                    raise HTTPException(409, '账号名已被使用')
                c.execute('UPDATE users SET username=%s,password=%s,password_set=1,disabled=0 WHERE id=%s',
                          (username,hashed,user))
                c.execute('DELETE FROM sessions WHERE user_id=%s', (user,))
            else:
                user = ident()
                c.execute('INSERT INTO users(id,username,password,created) VALUES(%s,%s,%s,%s)',
                          (user,username,hashed,now()))
                workspace = self.new_workspace(c,user,'平台工作空间',True)
                self.wallets.create(c,user)
                c.execute("INSERT INTO instance_metadata VALUES('admin_user',%s),('admin_workspace',%s)",
                          (user,workspace))
            actor = Principal(user,True,workspace)
            if getattr(self,'audit',None):
                self.audit.record(c,actor,'account.administrator_initialized','user',user,
                                  workspace_id=workspace,after={'converted':primary is not None})
            return actor

    def register(self,username,password,invite,audience=""):
            if re.fullmatch(r'1[3-9][0-9]{9}',username):
                raise HTTPException(422,'请使用非手机号的账号名')
            hashed=password_hash(password)
            with self.db.tx() as c:
                if self._setup_state(c)[0] != 'complete':
                    raise HTTPException(409,'请先创建首个账号')
                invitation=c.execute('SELECT id FROM invitations WHERE hash=%s AND workspace_id IS NULL AND revoked=0 AND used_by IS NULL AND expires>%s',(digest(invite),now())).fetchone()
                if not invitation:raise HTTPException(409,'注册邀请无效、已使用或已过期')
                user=ident()
                try:
                    c.execute("INSERT INTO users(id,username,password,created,password_set) VALUES(%s,%s,%s,%s,1)",(user,username.lower(),hashed,now()))
                except psycopg.IntegrityError:
                    raise HTTPException(409,'账号名已被使用') from None
                self.new_workspace(c,user,'私人空间',True)
                self.wallets.create(c,user)
                c.execute('UPDATE invitations SET used_by=%s WHERE id=%s',(user,invitation['id']))
                return self._create_session(c,user,audience)

    def invite(self,workspace=None):
            actor=principal.get()
            with self.db.tx() as c:
                if workspace is not None:
                    row=self.member(c,workspace,owner=True)
                    if row['personal']:raise HTTPException(409,'私人空间不能邀请成员，请新建协作空间')
                elif not actor or not actor.admin:
                    raise HTTPException(403,'只有管理员可以发放注册邀请')
                token=secrets.token_urlsafe(32);key=ident()
                c.execute('INSERT INTO invitations(id,hash,workspace_id,created_by,expires) VALUES(%s,%s,%s,%s,%s)',
                          (key,digest(token),workspace,actor.user_id,now()+7*86400))
                if getattr(self,'audit',None):self.audit.record(c,actor,'workspace.invitation_created' if workspace else 'account.invitation_created',
                    'workspace' if workspace else 'invitation',workspace or key,after={'invitation_id':key},workspace_id=workspace)
                return dict(id=key,code=token,expires_in=7*86400)

    def join(self,code):
            actor=principal.get()
            if not actor.user_id:raise HTTPException(403,'请先使用个人账号登录')
            with self.db.tx() as c:
                row=c.execute('SELECT * FROM invitations WHERE hash=%s AND workspace_id IS NOT NULL AND revoked=0 AND used_by IS NULL AND expires>%s',(digest(code),now())).fetchone()
                if not row:raise HTTPException(409,'空间邀请无效、已使用或已过期')
                workspace=row['workspace_id']
                if c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=%s AND user_id=%s',(workspace,actor.user_id)).fetchone():raise HTTPException(409,'你已是该空间成员')
                c.execute('INSERT INTO workspace_members VALUES(%s,%s)',(workspace,actor.user_id))
                c.execute('UPDATE invitations SET used_by=%s WHERE id=%s',(actor.user_id,row['id']))
                if getattr(self,'audit',None):self.audit.record(c,actor,'workspace.member_joined','workspace',workspace,
                    after={'user_id':actor.user_id,'invitation_id':row['id']},workspace_id=workspace)
                return dict(workspace_id=workspace)

    def workspaces(self,actor):
            with self.db.connect() as c:
                if actor.admin:rows=c.execute('SELECT * FROM workspaces ORDER BY created').fetchall()
                else:rows=c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=%s ORDER BY w.created',(actor.user_id,)).fetchall()
                return [dict(r,role='owner' if r['owner_id']==actor.user_id else 'member') for r in rows]

    def set_password(self,user,password):
            hashed=password_hash(password)
            with self.db.tx() as c:
                row=c.execute('SELECT password_set FROM users WHERE id=%s',(user,)).fetchone()
                if not row:raise HTTPException(404,'账号不存在')
                if row['password_set']:raise HTTPException(409,'已设置网站登录密码，无需重复设置')
                c.execute('UPDATE users SET password=%s,password_set=1 WHERE id=%s',(hashed,user))

    def change_password(self,user,current_password,new_password,current_token,actor,request_id=None):
            """Verify the old secret outside the write lock, then rotate every session."""
            with self.db.connect() as c:
                row=c.execute('SELECT password,password_set,disabled FROM users WHERE id=%s',(user,)).fetchone()
            if not row or row['disabled'] or not row['password_set']:raise HTTPException(403,'当前账号不能修改密码')
            salt=row['password'].split(':')[0]
            if not hmac.compare_digest(password_hash(current_password,salt),row['password']):
                raise HTTPException(401,'当前密码不正确')
            hashed=password_hash(new_password)
            with self.db.tx() as c:
                old=c.execute('SELECT audience FROM sessions WHERE hash=%s AND user_id=%s',(digest(current_token),user)).fetchone()
                if not old:raise HTTPException(401,'当前会话已失效，请重新登录')
                c.execute('UPDATE users SET password=%s,password_set=1 WHERE id=%s',(hashed,user))
                c.execute('DELETE FROM sessions WHERE user_id=%s',(user,))
                token=self._create_session(c,user,old['audience'])
                if getattr(self,'audit',None):self.audit.record(c,actor,'account.password_changed','user',user,
                    before={'password_set':True},after={'password_set':True,'sessions_revoked':True},request_id=request_id)
            return token

    def reset_password(self,user,password,actor,request_id=None):
            hashed=password_hash(password)
            with self.db.tx() as c:
                if not c.execute('SELECT 1 FROM users WHERE id=%s',(user,)).fetchone():raise HTTPException(404,'账号不存在')
                c.execute('UPDATE users SET password=%s,password_set=1 WHERE id=%s',(hashed,user))
                revoked=c.execute('DELETE FROM sessions WHERE user_id=%s',(user,)).rowcount
                if getattr(self,'audit',None):self.audit.record(c,actor,'account.password_reset','user',user,
                    after={'password_set':True,'sessions_revoked':revoked},request_id=request_id)
