"""Shared wire format for app services; does not read platform settings or secrets."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def canonical(key_id,app_id,timestamp,nonce,method,target,host,idempotency_key,body):
    return '\n'.join(('ORCHPACT-ED25519-V1',key_id,app_id,timestamp,nonce,
                       method.upper(),target,host.lower(),idempotency_key,
                       hashlib.sha256(body).hexdigest())).encode('utf-8')


def delegated_canonical(key_id,app_id,timestamp,nonce,method,target,host,idempotency_key,body,session,workspace):
    base=canonical(key_id,app_id,timestamp,nonce,method,target,host,idempotency_key,body)
    return base.replace(b'ORCHPACT-ED25519-V1\n',b'ORCHPACT-ED25519-USER-V1\n',1)+b'\n'+hashlib.sha256(session.encode()).hexdigest().encode()+b'\n'+workspace.encode()


class SigningCredentials:
    def __init__(self,app_id,key_id,private_key):
        if not isinstance(private_key,Ed25519PrivateKey):raise ValueError('需要Ed25519私钥')
        self.app_id=app_id;self.key_id=key_id;self.private_key=private_key

    @classmethod
    def load(cls,bundle,app_id):
        bundle=Path(bundle)
        data=json.loads(bundle.read_text())
        if data['application_id']!=app_id:raise ValueError('签名配置属于其他应用')
        filename=data['private_key_file']
        if not isinstance(filename,str) or Path(filename).name!=filename or filename in ('.','..'):raise ValueError('私钥必须位于当前应用的密钥目录')
        path=bundle.parent/filename
        if path.is_symlink():raise ValueError('私钥不能使用符号链接')
        if os.name=='posix' and path.stat().st_mode&0o077:raise ValueError('私钥权限必须为600或400')
        key=serialization.load_pem_private_key(path.read_bytes(),password=None)
        return cls(app_id,data['key_id'],key)

    def headers(self,method,target,host,body=b'',idempotency_key='',session=None,workspace=''):
        timestamp=str(int(time.time()));nonce=secrets.token_urlsafe(24)
        args=(self.key_id,self.app_id,timestamp,nonce,method,target,host,idempotency_key,body)
        message=canonical(*args) if session is None else delegated_canonical(*args,session,workspace)
        headers={'X-Orchpact-Key-Id':self.key_id,'X-Orchpact-Application':self.app_id,
                 'X-Orchpact-Timestamp':timestamp,'X-Orchpact-Nonce':nonce,
                 'X-Orchpact-Signature':base64.b64encode(self.private_key.sign(message)).decode()}
        if session is not None:
            headers['X-Orchpact-User-Session']=session
            if workspace:headers['X-Orchpact-User-Workspace']=workspace
        return headers
