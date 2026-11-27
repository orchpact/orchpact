"""Shared administrator client and explicit organization-package provisioning."""
import argparse
from http.cookiejar import CookieJar
from getpass import getpass
import json
import os
import re
from pathlib import Path
import urllib.error
import urllib.request


class Client:
    def __init__(self, origin, username, password, workspace=None):
        self.origin, self.workspace = origin.rstrip('/'), workspace
        self._credentials = dict(username=username,password=password)
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def _request(self, method, path, value, workspace=None):
        headers = {'Content-Type': 'application/json'}
        if workspace:
            headers['X-Workspace-Id'] = workspace
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode() if value is not None else None
        request = urllib.request.Request(self.origin+'/api/v1/'+path,data=data,method=method,headers=headers)
        try:
            with self._opener.open(request,timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise RuntimeError('Orchpact 请求失败，HTTP ' + str(exc.code)) from None

    def call(self, method, path, value=None):
        if self._credentials:
            if self._request('POST','auth/login',self._credentials) is None:
                raise RuntimeError('Orchpact 登录接口不存在')
            self._credentials = None
        return self._request(method,path,value,self.workspace)


def configure(manifest_path, argv=None):
    parser=argparse.ArgumentParser(description='Publish and optionally install a workflow directly')
    parser.add_argument('--url',default='http://127.0.0.1:8780')
    parser.add_argument('--update',action='store_true')
    parser.add_argument('--workspace')
    parser.add_argument('--username', default=os.environ.get('ORCHPACT_USERNAME'))
    parser.add_argument('--install',action='store_true')
    parser.add_argument('--bindings',type=Path)
    args=parser.parse_args(argv)
    if not args.username:parser.error('提供 --username 或 ORCHPACT_USERNAME')
    from ..kernel.workflows import validate_definition
    try:manifest=validate_definition(json.loads(Path(manifest_path).read_text()))
    except ValueError as exc:parser.error(str(exc))
    if args.bindings and not args.install:parser.error('--bindings 需要 --install')
    if args.install and not args.bindings:parser.error('--install 需要显式 --bindings 文件')
    password = getpass("账号密码：")
    client=Client(args.url,args.username,password,args.workspace)
    asset=client.call('GET','workflows/'+manifest['id'])
    if asset is None or args.update:
        asset=client.call('POST','workflows?revision='+str(asset['revision'] if asset else 0),manifest)
    if args.install:
        value=dict(workflow_revision=asset['revision'],bindings=json.loads(args.bindings.read_text()))
        preview=client.call('POST','workflows/'+manifest['id']+'/preview',value)
        client.call('POST','workflows/'+manifest['id']+'/install',dict(value,expected_revision=preview['installed_revision']))
    print('工作流 '+manifest['id']+' revision='+str(asset['revision'])+(' 已安装到当前 Workspace' if args.install else ' 已登记；未发起执行'))
