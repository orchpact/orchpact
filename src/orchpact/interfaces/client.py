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
    parser = argparse.ArgumentParser(description='Register an Orchpact organization package')
    parser.add_argument('--url', default='http://127.0.0.1:8780')
    parser.add_argument('--update', action='store_true', help='Explicitly register a new package version')
    parser.add_argument('--workspace', help='Target Workspace for explicit installation')
    parser.add_argument('--install', action='store_true', help='Install package into selected Workspace')
    parser.add_argument('--bindings', type=Path, help='Local workflow bindings JSON; never included in the definition')
    parser.add_argument('--application', help='Bind an independent application to this workflow asset')
    parser.add_argument('--application-name', help='Display name for the consuming application')
    parser.add_argument('--gateway-key-dir', type=Path)
    parser.add_argument('--key-owner')
    parser.add_argument('--rotate-key', action='store_true')
    args = parser.parse_args(argv)
    token = os.environ.get('ORCHPACT_API_TOKEN', '')
    if not token:
        parser.error('从环境变量提供 ORCHPACT_API_TOKEN，禁止将凭据写入组织包')
    if args.gateway_key_dir and not args.key_owner:
        parser.error('--gateway-key-dir 需要 --key-owner')
    manifest = json.loads(Path(manifest_path).read_text())
    manifest.setdefault('version', '1.0.0')
    if not isinstance(manifest.get('id'),str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}',manifest['id']):
        parser.error('定义标识无效，未发送管理请求')
    portable=manifest.get('format') == 'orchpact-workflow'
    if not portable:manifest.setdefault('require_signature', True)
    bindings=json.loads(args.bindings.read_text()) if args.bindings else None
    if args.bindings and not args.install:parser.error('--bindings 需要 --install')
    client = Client(args.url, token, args.workspace)
    if args.application:
        if not portable:parser.error('--application 需要 orchpact-workflow 定义')
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}',args.application):parser.error('应用标识无效')
        asset=client.call('GET','workflows/'+manifest['id'])
        if asset is None or args.update:
            asset=client.call('POST','workflows?revision='+str(asset['revision'] if asset else 0),manifest)
        application=client.call('GET','apps/'+args.application)
        if application and not args.update and (not application.get('workflow') or application['workflow']['id']!=manifest['id']):
            parser.error('现有应用引用不同定义；使用 --update 明确更新绑定')
        if application is None or args.update:
            application=client.call('POST','apps/'+args.application+'/binding',dict(name=args.application_name or manifest['name'],
                workflow_id=asset['id'],workflow_revision=asset['revision'],expected_revision=application['revision'] if application else 0))
        if args.install:
            query='?expected_revision='+str(application.get('installed_revision') or 0)+'&binding_revision='+str(application['revision'])
            client.call('POST','apps/'+args.application+'/install'+query,{'bindings':bindings} if bindings is not None else {})
        if args.gateway_key_dir:
            from ..application_provision import provision_gateway
            provision_gateway(client.call,args.application,args.gateway_key_dir,args.key_owner,rotate=args.rotate_key)
        print('已绑定应用 '+args.application+'；工作流 '+application['workflow']['id']+' revision='+str(application['workflow']['revision']))
        return
    previous = client.call('GET', 'apps/' + manifest['id'])
    if previous is not None and args.update and not portable:
        manifest['enabled']=previous.get('enabled',manifest.get('enabled',True))
        manifest['require_signature']=previous.get('require_signature',manifest.get('require_signature',True))
    if previous is None or args.update:
        previous = client.call('POST', 'organization-packages?revision=' + str(previous['revision'] if previous else 0), manifest)
    if args.install:
        expected=previous.get('installed_revision')
        suffix='?expected_revision='+str(expected or 0) if args.update else ''
        if portable:suffix='?expected_revision='+str(expected or 0)+'&binding_revision='+str(previous['revision'])
        client.call('POST', 'apps/' + manifest['id'] + '/install'+suffix, {'bindings':bindings} if bindings is not None else {})
    if args.gateway_key_dir:
        from ..application_provision import provision_gateway
        provision_gateway(client.call, manifest['id'], args.gateway_key_dir, args.key_owner, rotate=args.rotate_key)
    print('已登记组织包 ' + manifest['id'] + '，revision=' + str(previous['revision']))
    print('已有 Workspace 的组织定义不会由普通代码发布改写。')
