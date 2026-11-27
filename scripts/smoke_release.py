"""Fresh CLI and direct workflow HTTP acceptance, using only a local synthetic provider."""
import argparse
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time

import httpx
from orchpact.testing import database_url_for

ROOT = Path(__file__).resolve().parents[1]


class SyntheticProvider(BaseHTTPRequestHandler):
    calls = 0

    def log_message(self, *_args):
        pass

    def do_POST(self):
        size = int(self.headers.get('Content-Length', '0'))
        if self.path != '/v1/chat/completions' or not 0 < size <= 1_000_000:
            self.send_error(400)
            return
        context = json.loads(json.loads(self.rfile.read(size))['messages'][1]['content'])
        records = context['application']['input']['records']
        if not context['crew_member']['final']:
            files=None
            notes='Verified original record amounts and source identifiers.'
        elif context['stage_contract'].get('gate'):
            files = {'audit.json': dict(passed=True, issues=[], reviewed_artifact_ids=[d['artifact_id'] for d in context['deliveries']])}
        else:
            files = {'result.json': dict(total=sum(row['amount'] for row in records), source_ids=[row['id'] for row in records])}
        type(self).calls += 1
        data = json.dumps(dict(id='synthetic-local-response', choices=[dict(finish_reason='stop', message=dict(content=json.dumps(dict(files=files) if files is not None else dict(notes=notes))))], usage={'total_tokens': 1000})).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def smoke(output):
    if output.exists():
        raise ValueError('Output already exists')
    checks = []
    SyntheticProvider.calls = 0
    provider = ThreadingHTTPServer(('127.0.0.1', 0), SyntheticProvider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='orchpact-release-smoke-') as directory:
            root = Path(directory)
            # This environment never reads a business instance or inherited provider credentials.
            env = {k: v for k, v in os.environ.items() if not k.startswith(('ORCHPACT_', 'DEEPSEEK_', 'OPENAI_', 'ANTHROPIC_'))}
            cli = [str(Path(sys.executable).parent / 'orchpact'), '--root', str(root)]
            for command in ('init',):
                subprocess.run(cli + [command], env=env, check=True, capture_output=True, timeout=45)
            assert (root / '.env').stat().st_mode & 0o777 == 0o600
            assert 'ORCHPACT_API_TOKEN=' not in (root / '.env').read_text()
            env['ORCHPACT_DATABASE_URL'] = database_url_for(root / 'var')
            lines=(root / '.env').read_text().splitlines()
            (root / '.env').write_text('\n'.join('ORCHPACT_DATABASE_URL='+env['ORCHPACT_DATABASE_URL'] if line.startswith('ORCHPACT_DATABASE_URL=') else line for line in lines)+'\n')
            (root / 'config/models.toml').write_text('[models.fixture]\nlabel="Synthetic local provider"\nkind="text"\nmodel="synthetic"\nbase_url="http://127.0.0.1:' + str(provider.server_port) + '/v1"\njson_mode=true\nenabled=true\npriority=1\n')
            subprocess.run(cli + ['check'], env=env, check=True, capture_output=True, timeout=45)
            checks.append('fresh-cli-init-check-private-credentials')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            url = 'http://127.0.0.1:' + str(port)
            launcher = root / 'serve-fixture.py'
            launcher.write_text('''from contextlib import asynccontextmanager
from pathlib import Path
import sys
import uvicorn
from orchpact.api import create_app
from orchpact.config import Settings
from orchpact.testing import fixture_provider_credentials

settings = Settings.load(Path(sys.argv[1]),port=int(sys.argv[2]))
app = create_app(settings)
lifespan = app.router.lifespan_context

@asynccontextmanager
async def fixture(application):
    async with lifespan(application):
        fixture_provider_credentials(application.state.service,{'fixture':'synthetic-provider-key'})
        yield

app.router.lifespan_context = fixture
uvicorn.run(app,host=settings.host,port=settings.port)
''')
            process = None
            with (root / 'server.log').open('ab') as log:
                def stop():
                    nonlocal process
                    if process and process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=20)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                    process = None

                def start():
                    nonlocal process
                    process = subprocess.Popen([sys.executable,str(launcher),str(root),str(port)], env=env, stdout=log, stderr=log)
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise RuntimeError('Fresh HTTP service exited before readiness')
                        try:
                            if httpx.get(url + '/readyz', timeout=1, trust_env=False).status_code == 200:
                                return
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.1)
                    raise RuntimeError('HTTP readiness timeout')

                try:
                    start()
                    with httpx.Client(base_url=url, trust_env=False, timeout=15) as admin, httpx.Client(base_url=url, trust_env=False, timeout=15) as app:
                        def api(method, path, **kwargs):
                            response = admin.request(method, '/api/v1' + path, **kwargs)
                            response.raise_for_status()
                            return response.json()

                        credentials = dict(username='smoke-owner',password=secrets.token_urlsafe(48))
                        api('POST','/auth/setup',json=credentials)
                        me = api('GET', '/auth/me')
                        assert me['admin']
                        checks.append('first-account-is-instance-administrator')
                        api('POST', '/admin/users/' + me['user_id'] + '/credits', json=dict(delta=1000, reason='synthetic release smoke'), headers={'Idempotency-Key': 'smoke-budget'})
                        definition = json.loads((ROOT / 'examples/workflows/department-review.json').read_text())
                        asset = api('POST', '/workflows', json=definition)
                        install=dict(workflow_revision=1,expected_revision=0,bindings={'models':{'reasoning':'fixture'}})
                        preview=api('POST','/workflows/'+definition['id']+'/preview',json={k:v for k,v in install.items() if k!='expected_revision'})
                        assert preview['action']=='install'
                        api('POST','/workflows/'+definition['id']+'/install',json=install)
                        checks.append('direct-workflow-install-no-child-application')
                        payload=dict(operation='calculate',input={'records':[{'id':'a','amount':3},{'id':'b','amount':5}]},context={})
                        path='/workflows/'+definition['id']+'/runs'
                        run=api('POST',path,json=payload,headers={'Idempotency-Key':'same-business-key'})
                        deadline=time.monotonic()+45
                        while time.monotonic()<deadline:
                            run=api('GET','/runs/'+run['id'])
                            if run['status'] in ('completed','failed','partial','cancelled'):break
                            time.sleep(0.1)
                        assert run['status']=='completed',run['status']
                        assert run['result']==dict(total=8,source_ids=['a','b'])
                        assert run['workflow']['sha256']==asset['sha256']
                        assert 'application_id' not in run
                        assert len(run['requests'])==3 and all(r['accounting']['state']=='settled' for r in run['requests'])
                        assert api('POST',path,json=payload,headers={'Idempotency-Key':'same-business-key'})['id']==run['id']
                        assert admin.get('/api/v1/artifacts/'+run['artifact_id']+'/download').json()==run['result']
                        assert admin.post('/api/v1/apps/unused/keys',json={}).status_code==404
                        assert admin.post('/api/v1/apps/unused/binding',json={}).status_code==404
                        checks.append('direct-execution-review-download-idempotency-settlement')
                        updated = copy.deepcopy(definition)
                        updated['version'] = '1.1.0'
                        api('POST', '/workflows?revision=1', json=updated)
                        stop()
                        start()
                        assert api('GET','/workflows/'+definition['id']+'/installation')['workflow_revision']==1
                        assert api('GET','/runs/'+run['id'])['workflow']==run['workflow']
                        assert SyntheticProvider.calls==3
                        checks.append('restart-preserves-original-pins-and-snapshots-no-replay')
                finally:
                    stop()
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
    report = dict(format='orchpact-release-smoke', passed=True, storage='isolated-postgresql', provider_transport='synthetic-local-http', quality_evidence=False, provider_requests=SyntheticProvider.calls, checks=checks)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    smoke(parser.parse_args().output)
