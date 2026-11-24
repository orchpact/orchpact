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
from ..kernel.models import Strict, UnitInput, AgentInput, ImageRequest, Command, MandateInput, TemplatePreviewInput
from .context import from_actor

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

class DirectInstallation(Strict):
    workflow_revision: int = Field(ge=1)
    bindings: dict

class InstallWorkflow(DirectInstallation):
    expected_revision: int = Field(ge=0)


def create_app(settings, run_worker=True, executor=None):
    instance = Instance(settings, executor)

    @asynccontextmanager
    async def lifespan(app):
        instance.open()
        app.state.service = instance.service
        app.state.runner = instance.runner
        app.state.access = instance.service.access
        app.state.instance = instance
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
        # Preserve existing platform sessions. Former product audiences are never
        # accepted on this single platform surface.
        return ''

    def trusted_origin(request):
        expected = settings.origin_for_host(request.url.hostname) or settings.public_origin or str(request.base_url).rstrip('/')
        if request.headers.get('origin') not in (None, expected) or request.headers.get('sec-fetch-site') == 'cross-site':
            raise HTTPException(403, '不允许跨站请求')

    async def auth(request: Request, credential: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if any(key.startswith('x-orchpact-') for key in request.headers):
            raise Forbidden('子应用签名接入已移除，请使用个人会话或管理员 API')
        token = credential.credentials if credential else request.cookies.get('orchpact_session', '')
        if credential and hmac.compare_digest(token, settings.token):
            actor = Principal(instance.admin_user, True, instance.admin_workspace)
        else:
            actor = service().access.authenticate(token, session_audience(request))
            if not credential:trusted_origin(request)
        workspace=request.headers.get('X-Workspace-Id')
        if re.fullmatch(r'/api/v1/(artifacts/[^/]+/(download|preview)|requests/[^/]+|apps/[^/]+/definition)',request.url.path):
            workspace=workspace or request.query_params.get('workspace')
        actor=service().access.scope(actor,workspace)
        if actor.admin and not actor.workspace_id:actor=replace(actor,workspace_id=instance.admin_workspace)
        legacy=re.match(r'/api/v1/apps/([a-zA-Z0-9_-]+)',request.url.path)
        if legacy and not actor.admin:
            with service().db.connect() as c:
                row=c.execute('SELECT require_signature FROM applications WHERE id=%s',(legacy.group(1),)).fetchone()
            if row and row['require_signature']:raise Forbidden('旧签名范围仅供管理员查看或导出；请迁移为直接工作流安装')
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
        path = request.url.path
        if path=='/static/account/index.html' or path.endswith('/integration.json'):
            return JSONResponse(status_code=404,content={'detail':'资源不存在'})
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
        service().access.throttle('admin:' + (request.client.host if request.client else 'unknown'))
        if not hmac.compare_digest(data.token, settings.token):
            raise HTTPException(401, '管理凭据无效')
        cookie(response, request, service().access.session(instance.admin_user, admin=True, audience=session_audience(request)))
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

    @app.post('/api/v1/organization-packages', deprecated=True)
    def package(data: dict, revision: int = Query(default=0, ge=0), ctx=Depends(admin)):
        return service().register_package(ctx, data, revision)

    @app.post('/api/v1/apps/{application}/install', include_in_schema=False)
    def install_package(application: str, data: WorkflowInstallation = Body(default=WorkflowInstallation()), expected_revision: int | None = Query(default=None,ge=0), binding_revision: int | None = Query(default=None,ge=1), ctx=Depends(context)):
        return service().install_package(ctx, application, expected_revision=expected_revision,bindings=data.bindings,binding_revision=binding_revision)

    @app.get('/api/v1/workflow-templates')
    def workflow_templates(ctx=Depends(admin)):
        from ..kernel.templates import list_templates
        return list_templates()

    @app.post('/api/v1/workflow-templates/{template_id}/preview')
    def workflow_template_preview(template_id: str, data: TemplatePreviewInput, ctx=Depends(admin)):
        from ..kernel.templates import preview_template
        return preview_template(template_id, data.sha256, data.parameters)

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

    @app.post('/api/v1/workflows/{workflow}/preview')
    def preview_direct(workflow: str,data: DirectInstallation,ctx=Depends(context)):
        return service().delivery.preview(ctx,workflow,data.workflow_revision,data.bindings)

    @app.get('/api/v1/workflows/{workflow}/installation')
    def installed_direct(workflow: str,ctx=Depends(context)):
        return service().delivery.installation(ctx,workflow)

    @app.post('/api/v1/workflows/{workflow}/install')
    def install_direct(workflow: str,data: InstallWorkflow,ctx=Depends(context)):
        return service().delivery.install(ctx,workflow,data.workflow_revision,data.bindings,data.expected_revision)

    @app.post('/api/v1/workflows/{workflow}/runs',status_code=201)
    def invoke_direct(workflow: str,data: Invocation,idempotency_key: str=Header(min_length=1,max_length=100),ctx=Depends(context)):
        return service().delivery.invoke(ctx,workflow,data.model_dump(exclude_none=True),idempotency_key)

    @app.get('/api/v1/workflows/{workflow}/runs')
    def direct_runs(workflow: str,limit: int=Query(default=100,ge=1,le=100),ctx=Depends(context)):
        return service().delivery.runs(ctx,workflow,limit)

    @app.get('/api/v1/runs/{run}')
    def direct_run(run: str,ctx=Depends(context)):
        return service().delivery.run(ctx,run)

    @app.get('/api/v1/apps/{application}/definition', include_in_schema=False)
    def application_definition(application: str, ctx=Depends(context)):
        result=service().workflow_definition(ctx,application)
        return JSONResponse(result['definition'],headers={'Content-Disposition':'attachment; filename="'+result['definition']['id']+'.json"','X-Workflow-SHA256':result['sha256']})

    @app.post('/api/v1/apps/{application}/preview', include_in_schema=False)
    def preview_workflow(application: str, data: WorkflowInstallation, ctx=Depends(context)):
        return service().preview_package(ctx,application,data.bindings)

    @app.get('/api/v1/apps/{application}', include_in_schema=False)
    def application(application: str, ctx=Depends(context)):
        return service().application(ctx, application)

    @app.get('/api/v1/apps/{application}/documents/{key}', include_in_schema=False)
    def document(application: str, key: str, ctx=Depends(context)):
        return service().document(ctx, application, key)

    @app.put('/api/v1/apps/{application}/documents/{key}', include_in_schema=False)
    def save_document(application: str, key: str, data: Document, ctx=Depends(context)):
        return service().document(ctx, application, key, data.model_dump())

    @app.post('/api/v1/apps/{application}/runs', status_code=201, include_in_schema=False)
    def invoke(application: str, data: Invocation, idempotency_key: str = Header(min_length=1, max_length=100), ctx=Depends(context)):
        return service().invoke(ctx, application, data.model_dump(exclude_none=True), idempotency_key)

    @app.get('/api/v1/apps/{application}/runs', include_in_schema=False)
    def runs(application: str, limit: int = Query(default=100, ge=1, le=100), ctx=Depends(context)):
        return service().list_invocations(ctx, application, limit)

    @app.get('/api/v1/apps/{application}/runs-page', include_in_schema=False)
    def runs_page(application: str, limit: int = Query(default=25,ge=1,le=100), cursor: str | None = Query(default=None,max_length=500),ctx=Depends(context)):
        return service().page_invocations(ctx,application,limit,cursor)

    @app.get('/api/v1/apps/{application}/runs/{run}', include_in_schema=False)
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

    @app.post('/api/v1/tool-invocations/{tool_call_id}/reconcile')
    def reconcile_tool(tool_call_id: str, ctx=Depends(context)):
        from ..services.tools import ToolCallError
        try:
            return service().tools.lookup(ctx,tool_call_id)
        except ToolCallError as exc:
            from ..kernel.errors import DomainError
            raise DomainError(exc.code,str(exc)) from None

    @app.get('/api/v1/verification-evidence/{key}')
    def verification_evidence(key: str, ctx=Depends(context)):
        with service().db.connect() as c:
            row=service().row(c,'verification_evidence',key)
            service().scope(c,ctx,service().row(c,'projects',row['project_id']))
        path=service().settings.data/'verification-evidence'/row['attempt_id']/(row['id']+'.json')
        return dict(evidence=service().verifications.public(row),bundle=loads(path.read_text()) if path.is_file() else None)

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
    def account():
        return FileResponse(STATIC / 'account/index.html')

    @app.get('/admin/', include_in_schema=False)
    def console(request: Request):
        try:
            actor = service().access.authenticate(request.cookies.get('orchpact_session', ''), session_audience(request))
            if actor.admin:
                return FileResponse(STATIC / 'kernel/index.html')
        except HTTPException:
            pass
        return FileResponse(STATIC / 'admin-login.html')

    @app.get('/admin', include_in_schema=False)
    def admin_redirect():
        return RedirectResponse('/admin/')

    @app.api_route('/', methods=['GET','HEAD'], include_in_schema=False)
    def home():
        return FileResponse(STATIC / 'showcase/index.html')

    @app.get('/about',include_in_schema=False)
    def about_redirect():return RedirectResponse('/about/')

    app.mount('/about',StaticFiles(directory=STATIC/'showcase',html=True),name='design')
    app.mount('/api-assets', StaticFiles(directory=STATIC / 'vendor'), name='api-assets')
    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    return app
