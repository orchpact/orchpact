"""Provision app-owned signing files through admin APIs, never copy platform secrets."""
import json
import os
from pathlib import Path
import secrets
import time
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from .application_signing import SigningCredentials


def provision_gateway(call,app_id,directory,owner,rotate=False):
    directory=Path(directory)
    if directory.is_symlink():raise ValueError('密钥目录不能是符号链接')
    directory=directory.resolve();directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    if os.name=='posix' and directory.stat().st_mode&0o077:raise ValueError('密钥目录权限必须为700')
    bundle=directory/'credentials.json'
    keys=call('GET','apps/'+app_id+'/keys')
    if bundle.exists() and not rotate:
        saved=SigningCredentials.load(bundle,app_id)
        current=next((k for k in keys if k['id']==saved.key_id),None)
        if not current or current['revoked'] or current['expires']<=time.time() or not current['user_session']:raise ValueError('已有应用密钥已失效或模式不符；请显式使用 --rotate-key')
        expected=saved.private_key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        if expected!=current['public_key']:raise ValueError('本地私钥与已登记公钥不匹配')
    else:
        users=call('GET','admin/users');user=next((u for u in users if u['username']==owner and not u['disabled']),None)
        if not user:raise ValueError('密钥管理账号不存在或已停用，请先注册个人账号')
        spaces=call('GET','workspaces');space=next((w for w in spaces if w['owner_id']==user['id'] and w['personal']),None)
        if not space:raise ValueError('密钥管理账号没有私人空间')
        private=Ed25519PrivateKey.generate();name='key-'+secrets.token_hex(12)+'.pem';key_path=directory/name
        with open(key_path,'xb',opener=lambda path,flags:os.open(path,flags,0o600)) as file:
            file.write(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        public=private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        current=call('POST','apps/'+app_id+'/keys',dict(label=app_id+' 用户接入服务',public_key=public,user_id=user['id'],workspace_id=space['id'],expires_in_days=365,user_session=True))
        value=dict(application_id=app_id,key_id=current['id'],private_key_file=name)
        temp=directory/('credentials-'+secrets.token_hex(8)+'.tmp')
        with open(temp,'x',opener=lambda path,flags:os.open(path,flags,0o600)) as file:
            json.dump(value,file);file.flush();os.fsync(file.fileno())
        os.replace(temp,bundle)
    # Enforce signing only after credentials are durable. Preserve admin edits and CAS semantics.
    app=call('GET','apps/'+app_id)
    if not app.get('require_signature'):
        manifest=dict(app['manifest'],require_signature=True)
        call('POST','organization-packages?revision='+str(app['revision']),manifest)
    return current
