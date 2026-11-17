"""Workflow control plane. Startup owns the instance before any writable service."""
from contextlib import asynccontextmanager
from dataclasses import replace
import hmac
from pathlib import Path
import re
import secrets

from fastapi import FastAPI, Depends, Request, Response, HTTPException, Header, Query, Body
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from ..adapters.database import dumps, loads, now, ident
from ..adapters.identity import Principal, principal
from ..bootstrap import Instance
from .. import __version__
from ..kernel.errors import DomainError, Forbidden, NotFound
from ..kernel.models import Strict, UnitInput, AgentInput, ImageRequest, Command, MandateInput, WorkflowBinding
from ..application_keys import ApplicationKeys, PublicKeyInput
from ..sites import product, audience, account_page_html, frontend_file
from .surface import product_path
from .context import from_actor, ApplicationService

STATIC = Path(__file__).resolve().parents[1] / 'static'


class Login(Strict):
    token: str = Field(min_length=32, max_length=200)


class Invocation(Strict):
    operation: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,79}$')
    input: dict
    context: dict = Field(default_factory=dict)
    image_request: ImageRequest | None = None


class Document(Strict):
    revision: int = Field(ge=0)
    value: object


class Answer(Strict):
    content: str = Field(min_length=1, max_length=8000)

class WorkflowInstallation(Strict):
    bindings: dict | None = None


def create_app(settings, run_worker=True, executor=None):
    instance = Instance(settings, executor)

    @asynccontextmanager
    async def lifespan(app):
        instance.open()
        app.state.service = instance.service
        app.state.runner = instance.runner
        app.state.access = instance.service.access
        app.state.instance = instance
        app.state.keys = ApplicationKeys(ApplicationService(instance.service))
        try:
            if run_worker:
                instance.runner.start()
            yield
        finally:
            instance.close()

    app = FastAPI(title='Orchpact workflow engine', version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    bearer = HTTPBearer(auto_error=False)

    def service():
        return app.state.service

    def session_audience(request):
        return audience(settings, request)

    def trusted_origin(request):
        expected = settings.origin_for_host(request.url.hostname) or settings.public_origin or str(request.base_url).rstrip('/')
        if request.headers.get('origin') not in (None, expected) or request.headers.get('sec-fetch-site') == 'cross-site':
            raise HTTPException(403, '不允许跨站请求')

    async def auth(request: Request, credential: HTTPAuthorizationCredentials | None = Depends(bearer)):
        app_id = product(settings, request)
        signed = any(k.startswith('x-orchpact-') for k in request.headers)
        if signed:
            actor = await app.state.keys.authenticate(request, app_id)
        else:
            token = credential.credentials if credential else request.cookies.get('orchpact_session', '')
            if credential and hmac.compare_digest(token, settings.token):
                actor = Principal(instance.admin_user, True, instance.admin_workspace)
            else:
                actor = service().access.authenticate(token, audience(settings, request) if settings.products else None)
                if not credential:
                    trusted_origin(request)
            if app_id and actor.admin:
                raise Forbidden('产品入口不接受管理身份')
            workspace=request.headers.get('X-Workspace-Id')
            if re.fullmatch(r'/api/v1/(artifacts/[^/]+/(download|preview)|requests/[^/]+|apps/[^/]+/definition)',request.url.path):
                workspace=workspace or request.query_params.get('workspace')
            actor = service().access.scope(actor, workspace)
            if actor.admin and not actor.workspace_id:
                actor = replace(actor, workspace_id=instance.admin_workspace)
            actor = replace(actor, application_id=app_id)
            match = re.match(r'/api/v1/apps/([a-zA-Z0-9_-]+)', request.url.path)
            binding = match.group(1) if match else app_id
            if binding and not actor.admin and not request.url.path.startswith(('/api/v1/auth/', '/api/v1/workspaces', '/api/v1/session')):
                with service().db.connect() as c:
                    row = c.execute('SELECT require_signature FROM applications WHERE id=%s', (binding,)).fetchone()
                if row and row['require_signature']:
                    raise Forbidden('此应用需要签名网关')
        marker = principal.set(actor)
        try:
            yield actor
        finally:
            principal.reset(marker)

    def context(actor=Depends(auth)):
        return from_actor(actor)

    def admin(actor=Depends(auth)):
        if not actor.admin:
            raise Forbidden('仅限管理员')
        return from_actor(actor)

    def cookie(response, request, token):
        response.set_cookie('orchpact_session', token, httponly=True, samesite='strict',
                            secure=bool(settings.public_origin) or request.url.scheme == 'https', max_age=43200)

    @app.middleware('http')
    async def protect(request, call_next):
        request_id=request.headers.get('x-request-id','')
        request.state.request_id = request_id if re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}',request_id) else secrets.token_hex(16)
        host = request.url.hostname
        if settings.public_origin and not settings.origin_for_host(host) and host not in ('127.0.0.1', '::1'):
            return JSONResponse(status_code=403, content={'detail': 'Host 不受信任'})
        if settings.local_access and host not in ('localhost', '127.0.0.1', '::1', 'testserver'):
            return JSONResponse(status_code=403, content={'detail': 'Host 不受信任'})
        app_id = product(settings, request)
        path = request.url.path
        if path=='/static/account/index.html' or path.endswith('/integration.json'):
            return JSONResponse(status_code=404,content={'detail':'资源不存在'})
        if settings.products and any(path==v['route'] or path.startswith(v['route'].rstrip('/')+'/') for v in settings.frontends.values()):
            return JSONResponse(status_code=404,content={'detail':'资源不存在'})
        if app_id:
            if any(path==v['route'] or path.startswith(v['route'].rstrip('/')+'/') for v in settings.frontends.values()):
                return JSONResponse(status_code=404,content={'detail':'资源不存在'})
            if path.startswith('/api/') and not product_path(app_id, path, request.method):
                return JSONResponse(status_code=404, content={'detail': '接口不存在'})
            if path.startswith(('/admin', '/about', '/api-assets')) or path in ('/healthz', '/readyz'):
                return JSONResponse(status_code=404, content={'detail': '资源不存在'})
            if path.startswith('/static/') and not path.startswith('/static/account/') and path not in ('/static/orchpact-client.js',):
                return JSONResponse(status_code=404, content={'detail': '资源不存在'})
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            size, chunks = 0, []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 2_000_000:
                    return JSONResponse(status_code=413, content={'detail': '请求超过 2MB'})
                chunks.append(chunk)
            request._body = b''.join(chunks)
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy', 'no-referrer')
        if path.startswith(('/api/', '/admin', '/account')):
            response.headers['Cache-Control'] = 'no-store'
        if response.headers.get('content-type', '').startswith('text/html'):
            response.headers.setdefault('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(status_code=exc.status, content={'code': exc.code, 'detail': str(exc)})

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse(status_code=409, content={'code': 'invalid_operation', 'detail': str(exc)[:2000]})

    @app.exception_handler(KeyError)
    async def key_error(request, exc):
        return JSONResponse(status_code=404, content={'code': 'not_found', 'detail': '资源不存在'})

    # Core account endpoints are installed without product imports or schema writes.
    from .accounts import install as install_accounts
    class AccessProxy:
        def __getattr__(self, name):
            return getattr(service().access, name)
    install_accounts(app, AccessProxy(), auth, admin, cookie, trusted_origin, session_audience,
                     credit_policy=lambda: service().configuration.defaults(),
                     provider_prices=lambda: [dict(id=p['id'], category=p['category'], label=p['label'], pricing=p['pricing']) for p in service().configuration.read()[1]['providers'] if service().configuration.ready(p)])

    @app.get('/healthz')
    def health():
        return dict(status='ok', execution='single-host', format='orchpact-runtime')

    @app.get('/readyz')
    def ready(response: Response):
        healthy = not run_worker or app.state.runner.healthy()
        response.status_code = 200 if healthy else 503
        return dict(status='ready' if healthy else 'unavailable')

    @app.post('/api/v1/session')
    def login(data: Login, request: Request, response: Response):
        trusted_origin(request)
        if product(settings, request):
            raise Forbidden()
        service().access.throttle('admin:' + (request.client.host if request.client else 'unknown'))
        if not hmac.compare_digest(data.token, settings.token):
            raise HTTPException(401, '管理凭据无效')
        cookie(response, request, service().access.session(instance.admin_user, admin=True, audience=audience(settings, request)))
        return dict(ok=True)

    @app.delete('/api/v1/session', dependencies=[Depends(auth)])
    def logout(request: Request, response: Response):
        service().access.revoke_session(request.cookies.get('orchpact_session', ''))
        response.delete_cookie('orchpact_session', secure=bool(settings.public_origin), httponly=True, samesite='strict')
        return dict(ok=True)

    @app.get('/api/v1/models', dependencies=[Depends(auth)])
    def models():
        return service().settings.catalog()

    @app.get('/api/v1/image-models', dependencies=[Depends(auth)])
    def image_models():
        return service().settings.image_catalog()

    @app.get('/api/v1/admin/configuration', dependencies=[Depends(admin)])
    def configuration():
        return service().configuration.public()

    @app.put('/api/v1/admin/configuration')
    def configure(data: dict, ctx=Depends(admin)):
        return service().configuration.save(data['revision'], data['value'], ctx.subject)

    @app.get('/api/v1/admin/configuration/history', dependencies=[Depends(admin)])
    def configuration_history():
        return service().configuration.history()

    @app.post('/api/v1/organization-packages')
    def package(data: dict, revision: int = Query(default=0, ge=0), ctx=Depends(admin)):
        return service().register_package(ctx, data, revision)

    @app.post('/api/v1/apps/{application}/install')
    def install_package(application: str, data: WorkflowInstallation = Body(default=WorkflowInstallation()), expected_revision: int | None = Query(default=None,ge=0), binding_revision: int | None = Query(default=None,ge=1), ctx=Depends(context)):
        return service().install_package(ctx, application, expected_revision=expected_revision,bindings=data.bindings,binding_revision=binding_revision)

    @app.post('/api/v1/workflows/validate')
    def validate_workflow(data: dict, ctx=Depends(context)):
        from ..kernel.workflows import validate_definition,definition_digest
        value=validate_definition(data)
        return dict(definition=value,sha256=definition_digest(value))

    @app.post('/api/v1/workflows')
    def register_workflow(data: dict, revision: int = Query(default=0,ge=0), ctx=Depends(admin)):
        return service().workflows.register(ctx,data,revision)

    @app.get('/api/v1/workflows')
    def workflows(ctx=Depends(admin)):
        return service().workflows.list(ctx)

    @app.get('/api/v1/workflows/{workflow}')
    def workflow_asset(workflow: str, revision: int | None = Query(default=None,ge=1), ctx=Depends(admin)):
        return service().workflows.read(ctx,workflow,revision)

    @app.get('/api/v1/workflows/{workflow}/definition')
    def export_workflow(workflow: str, revision: int | None = Query(default=None,ge=1), ctx=Depends(admin)):
        result=service().workflows.read(ctx,workflow,revision)
        return JSONResponse(result['definition'],headers={'Content-Disposition':'attachment; filename="'+result['id']+'.json"','X-Workflow-SHA256':result['sha256']})

    @app.post('/api/v1/apps/{application}/binding')
    def bind_workflow(application: str, data: WorkflowBinding, ctx=Depends(admin)):
        return service().workflows.bind(ctx,application,data.model_dump())

    @app.get('/api/v1/apps/{application}/definition')
    def application_definition(application: str, ctx=Depends(context)):
        result=service().workflow_definition(ctx,application)
        return JSONResponse(result['definition'],headers={'Content-Disposition':'attachment; filename="'+result['definition']['id']+'.json"','X-Workflow-SHA256':result['sha256']})

    @app.post('/api/v1/apps/{application}/preview')
    def preview_workflow(application: str, data: WorkflowInstallation, ctx=Depends(context)):
        return service().preview_package(ctx,application,data.bindings)

    @app.get('/api/v1/apps/{application}')
    def application(application: str, ctx=Depends(context)):
        return service().application(ctx, application)

    @app.get('/api/v1/apps')
    def applications(ctx=Depends(admin)):
        with service().db.connect() as c:
            return [service().application(ctx, row['id'], c) for row in c.execute('SELECT id FROM applications ORDER BY name')]

    @app.get('/api/v1/apps/{application}/keys')
    def keys(application: str, ctx=Depends(admin)):
        return app.state.keys.list(application)

    @app.get('/api/v1/apps/{application}/key-history')
    def key_history(application: str, ctx=Depends(admin)):
        with service().db.connect() as c:
            service().application(ctx,application,c)
            return [dict(r) for r in c.execute('SELECT * FROM application_key_audit WHERE application_id=%s ORDER BY created DESC',(application,))]

    @app.post('/api/v1/apps/{application}/keys',status_code=201)
    def add_key(application: str, data: PublicKeyInput, ctx=Depends(admin)):
        return app.state.keys.create(application, data)

    @app.delete('/api/v1/apps/{application}/keys/{key}')
    def revoke_key(application: str, key: str, ctx=Depends(admin)):
        return app.state.keys.revoke(application, key)

    @app.get('/api/v1/apps/{application}/documents/{key}')
    def document(application: str, key: str, ctx=Depends(context)):
        return service().document(ctx, application, key)

    @app.put('/api/v1/apps/{application}/documents/{key}')
    def save_document(application: str, key: str, data: Document, ctx=Depends(context)):
        return service().document(ctx, application, key, data.model_dump())

    @app.post('/api/v1/apps/{application}/runs', status_code=201)
    def invoke(application: str, data: Invocation, idempotency_key: str = Header(min_length=1, max_length=100), ctx=Depends(context)):
        return service().invoke(ctx, application, data.model_dump(exclude_none=True), idempotency_key)

    @app.get('/api/v1/apps/{application}/runs')
    def runs(application: str, limit: int = Query(default=100, ge=1, le=100), ctx=Depends(context)):
        return service().list_invocations(ctx, application, limit)

    @app.get('/api/v1/apps/{application}/runs-page')
    def runs_page(application: str, limit: int = Query(default=25,ge=1,le=100), cursor: str | None = Query(default=None,max_length=500),ctx=Depends(context)):
        return service().page_invocations(ctx,application,limit,cursor)

    @app.get('/api/v1/apps/{application}/runs/{run}')
    def run(application: str, run: str, ctx=Depends(context)):
        return service().invocation(ctx, application, run)

    @app.post('/api/v1/runs/{run}/commands')
    def command(run: str, data: Command, ctx=Depends(context)):
        return service().command(ctx, run, data)

    @app.post('/api/v1/runs/{run}/cancel')
    def cancel(run: str, request: Request, ctx=Depends(context)):
        return service().command(ctx, run, dict(key=request.headers.get('Idempotency-Key') or 'cancel', action='cancel'))

    @app.get('/api/v1/work-items/{task}/attempts')
    def attempts(task: str, ctx=Depends(context)):
        with service().db.connect() as c:
            t=service().row(c,'tasks',task)
            service().scope(c,ctx,service().row(c,'projects',t['project_id']))
            return [dict(row) for row in c.execute('SELECT * FROM attempts WHERE task_id=%s ORDER BY started',(task,))]

    @app.post('/api/v1/work-items/{task}/retry')
    def retry(task: str, request: Request, ctx=Depends(context)):
        with service().db.connect() as c:
            t = service().row(c, 'tasks', task)
        return service().command(ctx, t['project_id'], dict(key=request.headers.get('Idempotency-Key') or ident(), action='retry', payload=dict(task_id=task)))

    @app.post('/api/v1/questions/{question}/answers')
    def reply(question: str, data: Answer, ctx=Depends(context)):
        with service().db.connect() as c:
            q = service().row(c, 'messages', question)
        return service().command(ctx, q['project_id'], dict(key='answer:' + question, action='reply', payload=dict(question_id=question, content=data.content)))

    @app.get('/api/v1/runs/{run}/events')
    def events(run: str, cursor: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100), ctx=Depends(context)):
        with service().db.connect() as c:
            service().scope(c, ctx, service().row(c, 'projects', run))
            rows = [dict(r) for r in c.execute('SELECT * FROM events WHERE project_id=%s AND id>%s ORDER BY id LIMIT %s', (run, cursor, limit))]
            return dict(items=[dict(r, detail=loads(r['detail'])) for r in rows], cursor=rows[-1]['id'] if rows else cursor)

    @app.get('/api/v1/runs/{run}/requests')
    @app.get('/api/v1/runs/{run}/usage')
    def usage(run: str, request: Request, ctx=Depends(context)):
        with service().db.connect() as c:
            p = service().scope(c, ctx, service().row(c, 'projects', run))
        result = service().invocation(ctx, p['application_id'], run)
        return result['requests'] if request.url.path.endswith('/requests') else result['usage']

    @app.get('/api/v1/requests/{request_id}')
    def receipt(request_id: str, ctx=Depends(context)):
        with service().db.connect() as c:
            r = service().row(c, 'requests', request_id)
            service().scope(c, ctx, service().row(c, 'projects', r['project_id']))
        path = service().settings.data / 'receipts' / r['attempt_id'] / (r['id'] + '.json')
        return dict(request=r, receipt=loads(path.read_text()) if path.is_file() else None)

    def artifact(ctx, key):
        with service().db.connect() as c:
            row = service().row(c, 'artifacts', key)
            service().scope(c, ctx, service().row(c, 'projects', row['project_id']))
        return row

    @app.get('/api/v1/artifacts/{key}')
    def artifact_metadata(key: str, ctx=Depends(context)):
        row=artifact(ctx,key)
        return {k:row[k] for k in ('id','sha256','name','media_type','bytes','manifest') if k in row}

    @app.get('/api/v1/artifacts/{key}/download')
    def download(key: str, ctx=Depends(context)):
        row = artifact(ctx, key)
        return FileResponse(service().artifacts.path(row), filename=row['name'], media_type=row['media_type'])

    @app.get('/api/v1/artifacts/{key}/preview')
    def preview(key: str, ctx=Depends(context)):
        row = artifact(ctx, key)
        if row['media_type'] in ('image/png', 'image/jpeg', 'image/webp'):
            return FileResponse(service().artifacts.path(row), media_type=row['media_type'])
        return service().artifacts.excerpts(row)

    @app.post('/api/v1/organizations', status_code=201)
    def unit(data: UnitInput, ctx=Depends(context)):
        return service().unit(ctx, data.model_dump())

    @app.put('/api/v1/organizations/{key}')
    def edit_unit(key: str, data: UnitInput, ctx=Depends(context)):
        return service().unit(ctx, data.model_dump(), key)

    @app.post('/api/v1/organizations/{key}/roles', status_code=201)
    def agent(key: str, data: AgentInput, ctx=Depends(context)):
        return service().agent(ctx, key, data.model_dump())

    @app.get('/api/v1/policies/{scope:path}')
    def policy(scope: str, ctx=Depends(admin)):
        with service().db.connect() as c:
            return service().policies.read(c, scope)

    @app.put('/api/v1/policies/{scope:path}')
    def save_policy(scope: str, data: dict, ctx=Depends(admin)):
        with service().db.tx() as c:
            return service().policies.save(c, scope, data['revision'], data['policy'])

    @app.post('/api/v1/runs/{run}/mandates')
    def grant(run: str, data: MandateInput, ctx=Depends(context)):
        with service().db.tx() as c:
            p = service().scope(c, ctx, service().row(c, 'projects', run))
            return dict(id=service().policies.grant(c, ctx, p, data.subject, data.actions, data.capabilities, data.expires, data.parent_id, data.budget_points))

    @app.delete('/api/v1/runs/{run}/mandates/{grant}')
    def revoke(run: str, grant: str, ctx=Depends(context)):
        with service().db.tx() as c:
            p = service().scope(c, ctx, service().row(c, 'projects', run))
            service().policies.authorize(c, ctx, 'delegate', p)
            row = c.execute('SELECT * FROM mandates WHERE id=%s AND project_id=%s', (grant, run)).fetchone()
            if not row or (not ctx.admin and row['issuer'] != ctx.subject):
                raise Forbidden()
            c.execute('UPDATE mandates SET revoked=1 WHERE id=%s', (grant,))
            service().db.event(c, run, None, 'mandate.revoked', mandate_id=grant, subject=ctx.subject)
            return dict(ok=True)

    @app.get('/api/v1/runs/{run}/mandates')
    def mandates(run: str, ctx=Depends(context)):
        with service().db.connect() as c:
            service().scope(c,ctx,service().row(c,'projects',run))
            return [dict(row,actions=loads(row['actions']),capabilities=loads(row['capabilities'])) for row in c.execute('SELECT * FROM mandates WHERE project_id=%s ORDER BY created LIMIT 256',(run,))]


    @app.get('/api/v1/state')
    def state(ctx=Depends(admin)):
        with service().db.connect() as c:
            units = [dict(r,crew=loads(r['crew']) if r['crew'] else None) for r in c.execute('SELECT * FROM units WHERE workspace_id=%s', (ctx.workspace_id,))]
            agents = [dict(r) for r in c.execute('SELECT a.* FROM agents a JOIN units u ON u.id=a.unit_id WHERE u.workspace_id=%s', (ctx.workspace_id,))]
            projects = [dict(r) for r in c.execute('SELECT * FROM projects WHERE workspace_id=%s ORDER BY created DESC LIMIT 100', (ctx.workspace_id,))]
            views = [service().invocation(ctx, p['application_id'], p['id'], c) for p in projects]
            return dict(units=units, agents=agents, projects=views, models=service().settings.catalog())

    @app.get('/static/orchpact-client.js')
    def browser_client():
        return FileResponse(Path(__file__).resolve().parents[1]/'static/orchpact-client.js',media_type='text/javascript')

    @app.get('/api/v1/openapi.json', dependencies=[Depends(admin)])
    def openapi():
        return app.openapi()

    @app.get('/api/docs', dependencies=[Depends(admin)])
    def docs():
        from fastapi.openapi.docs import get_swagger_ui_html
        return get_swagger_ui_html(openapi_url='/api/v1/openapi.json', title='Orchpact API', swagger_js_url='/api-assets/swagger-ui-bundle.js', swagger_css_url='/api-assets/swagger-ui.css')

    @app.get('/account/', include_in_schema=False)
    def account(request: Request):
        app_id = product(settings, request)
        directory = (settings.root / settings.frontends[app_id]['directory']) if app_id else None
        return HTMLResponse(account_page_html(product_directory=directory))

    @app.get('/admin/', include_in_schema=False)
    def console(request: Request):
        try:
            actor = service().access.authenticate(request.cookies.get('orchpact_session', ''), audience(settings, request) if settings.products else None)
            if actor.admin:
                return FileResponse(STATIC / 'kernel/index.html')
        except HTTPException:
            pass
        return FileResponse(STATIC / 'admin-login.html')

    @app.get('/admin', include_in_schema=False)
    def admin_redirect():
        return RedirectResponse('/admin/')

    @app.api_route('/', methods=['GET','HEAD'], include_in_schema=False)
    def home(request: Request):
        app_id = product(settings, request)
        if app_id:
            return FileResponse(frontend_file(settings, app_id, 'index.html'))
        return FileResponse(STATIC / 'showcase/index.html')

    for name, frontend in settings.frontends.items():
        if not frontend.get('enabled', True):
            continue
        directory = (settings.root / frontend['directory']).resolve()
        route = frontend['route'].rstrip('/')
        if not re.fullmatch(r'/[a-zA-Z0-9_-]+', route) or route in ('/api', '/admin', '/account', '/static', '/api-assets'):
            raise ValueError('前端路径无效')
        if not (directory / 'index.html').is_file():
            raise ValueError('显式启用的前端目录不存在')
        app.mount(route, StaticFiles(directory=directory, html=True), name='frontend-' + name)
    @app.get('/about',include_in_schema=False)
    def about_redirect():return RedirectResponse('/about/')

    app.mount('/about',StaticFiles(directory=STATIC/'showcase',html=True),name='design')
    app.mount('/api-assets', StaticFiles(directory=STATIC / 'vendor'), name='api-assets')
    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    @app.get('/{asset:path}', include_in_schema=False)
    def product_asset(asset: str, request: Request):
        app_id = product(settings, request)
        if not app_id:
            raise NotFound()
        return FileResponse(frontend_file(settings, app_id, asset))

    return app
