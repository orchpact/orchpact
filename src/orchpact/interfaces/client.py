"""Shared administrator client and explicit organization-package provisioning."""
import argparse
import json
import os
import re
from pathlib import Path
import urllib.error
import urllib.request


class Client:
    def __init__(self, origin, token, workspace=None):
        self.origin, self.token, self.workspace = origin.rstrip('/'), token, workspace

    def call(self, method, path, value=None):
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token}
        if self.workspace:
            headers['X-Workspace-Id'] = self.workspace
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode() if value is not None else None
        request = urllib.request.Request(self.origin + '/api/v1/' + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise RuntimeError('Orchpact 请求失败，HTTP ' + str(exc.code)) from None


def configure(manifest_path, argv=None):
    parser=argparse.ArgumentParser(description='Publish and optionally install a workflow directly')
    parser.add_argument('--url',default='http://127.0.0.1:8780')
    parser.add_argument('--update',action='store_true')
    parser.add_argument('--workspace')
    parser.add_argument('--install',action='store_true')
    parser.add_argument('--bindings',type=Path)
    args=parser.parse_args(argv)
    token=os.environ.get('ORCHPACT_API_TOKEN','')
    if not token:parser.error('从环境变量提供 ORCHPACT_API_TOKEN')
    from ..kernel.workflows import validate_definition
    try:manifest=validate_definition(json.loads(Path(manifest_path).read_text()))
    except ValueError as exc:parser.error(str(exc))
    if args.bindings and not args.install:parser.error('--bindings 需要 --install')
    if args.install and not args.bindings:parser.error('--install 需要显式 --bindings 文件')
    client=Client(args.url,token,args.workspace)
    asset=client.call('GET','workflows/'+manifest['id'])
    if asset is None or args.update:
        asset=client.call('POST','workflows?revision='+str(asset['revision'] if asset else 0),manifest)
    if args.install:
        value=dict(workflow_revision=asset['revision'],bindings=json.loads(args.bindings.read_text()))
        preview=client.call('POST','workflows/'+manifest['id']+'/preview',value)
        client.call('POST','workflows/'+manifest['id']+'/install',dict(value,expected_revision=preview['installed_revision']))
    print('工作流 '+manifest['id']+' revision='+str(asset['revision'])+(' 已安装到当前 Workspace' if args.install else ' 已登记；未发起执行'))
