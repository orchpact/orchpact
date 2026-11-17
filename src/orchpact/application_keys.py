"""App-scoped Ed25519 signatures. Private keys never enter the platform.

Canonical bytes are specified in docs/APPLICATIONS.md and static/admin/application-client.py.
Replay admission and revocation serialize in PostgreSQL transactions, across restarts.
"""
import base64
import hashlib
import re
import psycopg
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import HTTPException
from pydantic import Field
from .access import Principal, principal
from .db import ident, now
from .schemas import Strict
from .sites import product_path
from .application_signing import canonical, delegated_canonical
from dataclasses import replace


class PublicKeyInput(Strict):
    label: str = Field(min_length=1,max_length=100)
    public_key: str = Field(min_length=40,max_length=2000)
    user_id: str = Field(min_length=1,max_length=120)
    workspace_id: str = Field(min_length=1,max_length=120)
    expires_in_days: int = Field(default=90,ge=1,le=365)
    user_session: bool = False



class ApplicationKeys:
    def __init__(self, service):
        self.s = service

    def audit(self,c,app,key,action):
        actor=principal.get()
        c.execute('INSERT INTO application_key_audit VALUES(%s,%s,%s,%s,%s,%s)',
                  (ident(),app,key,action,(actor.user_id or 'platform_admin') if actor else 'internal',now()))

    def list(self,app):
        with self.s.db.connect() as c:
            self.s.applications.application(app,c)
            return [dict(r) for r in c.execute('SELECT k.*,u.username,w.name AS workspace_name FROM application_keys k JOIN users u ON u.id=k.user_id JOIN workspaces w ON w.id=k.workspace_id WHERE application_id=%s ORDER BY created DESC',(app,))]

    def create(self,app,data):
        try:
            key=serialization.load_pem_public_key(data.public_key.encode())
            if not isinstance(key,Ed25519PublicKey):raise ValueError()
        except (ValueError,TypeError,UnsupportedAlgorithm):raise ValueError('需要Ed25519的SPKI PEM公钥') from None
        public=key.public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        fingerprint=hashlib.sha256(key.public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).hexdigest()
        with self.s.db.tx() as c:
            self.s.applications.application(app,c)
            user=c.execute('SELECT * FROM users WHERE id=%s AND disabled=0',(data.user_id,)).fetchone()
            if not user:raise ValueError('扣费账号不存在或已停用')
            self.s.access.member(c,data.workspace_id,Principal(data.user_id))
            # Reusing a public key across apps defeats independent credentials.
            if c.execute('SELECT 1 FROM application_keys WHERE fingerprint=%s AND application_id<>%s',(fingerprint,app)).fetchone():raise ValueError('每个应用必须使用独立的密钥对')
            if c.execute('SELECT count(*) FROM application_keys WHERE application_id=%s AND revoked IS NULL AND expires>%s',(app,now())).fetchone()[0]>=10:raise ValueError('每个应用最多保留10个有效公钥，请先撤销旧密钥')
            key_id=ident();created=now()
            c.execute('INSERT INTO application_keys(id,application_id,label,public_key,fingerprint,user_id,workspace_id,created,expires,user_session) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                      (key_id,app,data.label,public,fingerprint,data.user_id,data.workspace_id,created,created+data.expires_in_days*86400,int(data.user_session)))
            self.audit(c,app,key_id,'created')
        return next(k for k in self.list(app) if k['id']==key_id)

    def revoke(self,app,key):
        with self.s.db.tx() as c:
            row=c.execute('SELECT * FROM application_keys WHERE id=%s AND application_id=%s',(key,app)).fetchone()
            if not row:raise KeyError(key)
            if row['revoked'] is None:
                c.execute('UPDATE application_keys SET revoked=%s WHERE id=%s',(now(),key));self.audit(c,app,key,'revoked')
        return {'ok':True}

    async def authenticate(self,request,host_app=None):
        def fail():raise HTTPException(401,'应用签名无效、已过期或密钥不可用',headers={'X-Orchpact-Auth-Failure':'application-key'})
        headers=request.headers
        names=('x-orchpact-key-id','x-orchpact-application','x-orchpact-timestamp','x-orchpact-nonce','x-orchpact-signature')
        if any(len(headers.getlist(n))!=1 for n in names):fail()
        key_id,app_id,timestamp,nonce,signature=(headers[n] for n in names)
        if not re.fullmatch(r'[0-9]{1,12}',timestamp) or abs(now()-int(timestamp))>300:fail()
        if not re.fullmatch(r'[a-zA-Z0-9_-]{16,100}',nonce):fail()
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,120}',key_id) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}',app_id):fail()
        if host_app and host_app!=app_id:raise HTTPException(403,'应用与入口不匹配')
        if headers.get('authorization') or headers.get('x-workspace-id'):raise HTTPException(400,'签名请求不能混用Bearer或空间覆盖')
        path=request.url.path
        # Reuse consumer resource allowlist, but never expose account/workspace APIs.
        allowed=product_path(app_id,path,request.method)
        if path.startswith(('/api/v1/auth/','/api/v1/workspaces','/api/v1/session')):allowed=False
        if not allowed:raise HTTPException(403,'此应用密钥无权访问该接口')
        if len(headers.getlist('idempotency-key'))>1:fail()
        session=headers.get('x-orchpact-user-session')
        workspace=headers.get('x-orchpact-user-workspace','')
        if any(len(headers.getlist(n))>1 for n in ('x-orchpact-user-session','x-orchpact-user-workspace')):fail()
        if session is not None and (not session or len(session)>200):fail()
        if workspace and (session is None or not re.fullmatch(r'[a-zA-Z0-9_-]{1,120}',workspace)):fail()
        target=request.scope['raw_path'].decode('ascii')
        query=request.scope.get('query_string',b'').decode('ascii')
        if query:target+='?'+query
        args=(key_id,app_id,timestamp,nonce,request.method,target,headers.get('host',''),headers.get('idempotency-key',''),await request.body())
        message=canonical(*args) if session is None else delegated_canonical(*args,session,workspace)
        if len(signature)!=88:fail()
        try:decoded=base64.b64decode(signature,validate=True)
        except (ValueError,TypeError):fail()
        with self.s.db.tx() as c:
            row=c.execute('SELECT k.* FROM application_keys k JOIN users u ON u.id=k.user_id WHERE k.id=%s AND k.application_id=%s AND k.revoked IS NULL AND k.expires>%s AND (k.user_session=1 OR u.disabled=0)',(key_id,app_id,now())).fetchone()
            if not row or not c.execute('SELECT enabled FROM applications WHERE id=%s',(app_id,)).fetchone()[0]:fail()
            try:serialization.load_pem_public_key(row['public_key'].encode()).verify(decoded,message)
            except (InvalidSignature,ValueError):fail()
            if bool(row['user_session'])!=(session is not None):raise HTTPException(403,'密钥接入模式不匹配',headers={'X-Orchpact-Auth-Failure':'application-key'})
            if session is not None:
                expected=self.s.settings.products.get(app_id,'')
                actor=self.s.access.authenticate(session,expected)
                if actor.admin:raise HTTPException(403,'应用网关不接受管理员会话')
                actor=replace(self.s.access.scope(actor,workspace or None),application_id=app_id)
            else:
                actor=Principal(row['user_id'],False,row['workspace_id'],app_id)
                self.s.access.member(c,actor.workspace_id,actor)
            c.execute('DELETE FROM application_nonces WHERE expires<%s',(now(),))
            try:c.execute('INSERT INTO application_nonces VALUES(%s,%s,%s)',(key_id,nonce,int(timestamp)+301))
            except psycopg.IntegrityError:raise HTTPException(401,'请求随机数已使用，请重新签名；任务重试继续使用原幂等键') from None
            c.execute('UPDATE application_keys SET last_used=%s WHERE id=%s',(now(),key_id))
            return replace(actor,application_key_id=key_id)
