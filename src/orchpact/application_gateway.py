"""Independent app BFF: keep each private key on its app server, sign user-scoped calls.

No provider/admin credentials are loaded here. Client identity is the existing
HttpOnly user session, bound into the app signature and checked by Orchpact.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
import httpx
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import FileResponse,JSONResponse,StreamingResponse,Response
from .application_signing import SigningCredentials
from .sites import account_page_html, product_path


@dataclass(frozen=True)
class GatewaySettings:
    application_id: str
    directory: Path
    upstream: str
    public_origin: str
    key_bundle: Path

    def __post_init__(self):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}',self.application_id):raise ValueError('应用ID无效')
        for value in (self.upstream,self.public_origin):
            parsed=urlsplit(value)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:raise ValueError('接入地址必须是无路径的HTTP(S) Origin')
        if not (self.directory/'index.html').is_file():raise ValueError('应用静态目录缺少index.html')

    @classmethod
    def environment(cls,app_id,directory):
        return cls(app_id,Path(directory).resolve(),os.environ.get('ORCHPACT_UPSTREAM','http://127.0.0.1:8780'),
                   os.environ['APPLICATION_PUBLIC_ORIGIN'],Path(os.environ.get('APPLICATION_KEY_BUNDLE','/run/application-keys/credentials.json')))


def account_path(path):
    return path.startswith(('/api/v1/auth/','/api/v1/workspaces')) or path=='/api/v1/session'


def create_gateway(settings,transport=None):
    host=urlsplit(settings.public_origin).netloc.lower()
    @asynccontextmanager
    async def lifespan(app):
        yield
        await client.aclose()
    client=httpx.AsyncClient(base_url=settings.upstream,timeout=httpx.Timeout(90,connect=10),follow_redirects=False,transport=transport)
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)

    @app.middleware('http')
    async def boundary(request,call_next):
        if request.headers.get('host','').lower()!=host:
            return JSONResponse(status_code=403,content={'detail':'Host不匹配'})
        response=await call_next(request)
        response.headers.setdefault('X-Content-Type-Options','nosniff')
        response.headers.setdefault('Referrer-Policy','no-referrer')
        response.headers.setdefault('Cache-Control','no-store' if request.url.path.startswith('/api/') else 'no-cache')
        response.headers.setdefault('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.get('/healthz')
    def health():
        try:SigningCredentials.load(settings.key_bundle,settings.application_id)
        except (OSError,ValueError,TypeError,KeyError):return JSONResponse(status_code=503,content={'status':'waiting_for_application_key'})
        return {'status':'ok','application_id':settings.application_id}

    async def proxy(request):
        path=request.url.path
        if path.startswith('/api/') and not product_path(settings.application_id,path,request.method):raise HTTPException(404,'接口不存在')
        # Signing material is always generated here; browsers cannot supply proof/identity headers.
        if any(name.startswith('x-orchpact-') for name in request.headers) or request.headers.get('authorization'):
            raise HTTPException(400,'此入口仅接受个人会话')
        if request.method not in ('GET','HEAD'):
            if request.headers.get('origin') not in (None,settings.public_origin):raise HTTPException(403,'请求来源不匹配')
            if request.headers.get('sec-fetch-site')=='cross-site':raise HTTPException(403,'不允许跨站访问')
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>2_000_000:raise HTTPException(413,'请求超过2MB')
        headers={'Host':host}
        for name in ('content-type','accept','origin','sec-fetch-site','user-agent','idempotency-key','cookie','x-orchpact-client','referer'):
            if name in request.headers:headers[name]=request.headers[name]
        # Cookie header is used for account routes; signed business routes explicitly bind its token.
        if path.startswith('/api/') and not account_path(path):
            session=request.cookies.get('orchpact_session')
            if not session:raise HTTPException(401,'请登录后继续')
            try:credentials=SigningCredentials.load(settings.key_bundle,settings.application_id)
            except (OSError,ValueError,TypeError,KeyError):raise HTTPException(503,'应用接入尚未配置，请联系管理员') from None
            target=request.scope['raw_path'].decode('ascii')
            query=request.scope.get('query_string',b'')
            if query:target+='?'+query.decode('ascii')
            headers.update(credentials.headers(request.method,target,host,bytes(body),request.headers.get('idempotency-key',''),
                                               session=session,workspace=request.headers.get('x-workspace-id','')))
            headers.pop('cookie',None)
        elif 'x-workspace-id' in request.headers:headers['X-Workspace-Id']=request.headers['x-workspace-id']
        target=request.scope['raw_path'].decode('ascii')
        if request.scope.get('query_string'):target+='?'+request.scope['query_string'].decode('ascii')
        # Never use request.url as an upstream URL: //paths must not change destination.
        upstream_url=settings.upstream+target
        try:
            response=await client.send(client.build_request(request.method,upstream_url,content=bytes(body),headers=headers),stream=True)
        except httpx.HTTPError:raise HTTPException(502,'执行服务暂时无法连接，请保留输入后重试') from None
        if response.headers.get('x-orchpact-auth-failure')=='application-key':
            await response.aclose()
            return JSONResponse(status_code=503,content={'detail':'应用接入凭据不可用，请联系管理员检查公钥、有效期或服务时钟；已提交任务不会重新创建'})
        async def content():
            try:
                async for chunk in response.aiter_raw():yield chunk
            finally:await response.aclose()
        result=StreamingResponse(content(),status_code=response.status_code)
        # Preserve downloads, cookies and policy headers, but never forward hop-by-hop headers.
        blocked={'connection','keep-alive','transfer-encoding','upgrade','proxy-authenticate','proxy-authorization','trailer','te'}
        result.raw_headers=[(name,value) for name,value in response.headers.raw if name.decode().lower() not in blocked]
        return result

    @app.api_route('/api/{path:path}',methods=['GET','POST','PUT','PATCH','DELETE','HEAD'])
    async def api(request:Request,path:str):return await proxy(request)

    @app.get('/account/')
    async def account():
        html=account_page_html(product_directory=settings.directory)
        return Response(html,media_type='text/html')

    @app.get('/static/orchpact-client.js')
    @app.get('/static/orchpact-client.js')
    async def shared_client():
        return FileResponse(Path(__file__).parent/'static/orchpact-client.js')

    @app.get('/static/account/{path:path}')
    async def shared(request:Request,path:str):
        if path not in ('workspace.js','workspace.css','app.js','style.css','credits.js','orchpact-logo.svg','orchpact-logo.svg','favicon.svg'):raise HTTPException(404,'资源不存在')
        return FileResponse(Path(__file__).parent/'static/account'/path)

    @app.get('/{path:path}')
    def frontend(path:str):
        if path=='integration.json' or path.startswith(('.','api/','static/')):raise HTTPException(404,'资源不存在')
        root=settings.directory.resolve();file=(root/(path or 'index.html')).resolve()
        if not file.is_relative_to(root) or not file.is_file():raise HTTPException(404,'页面不存在')
        return FileResponse(file)
    return app


def serve(app_id,directory):
    import uvicorn
    settings=GatewaySettings.environment(app_id,directory)
    uvicorn.run(create_gateway(settings),host=os.environ.get('APPLICATION_LISTEN_HOST','127.0.0.1'),port=int(os.environ.get('APPLICATION_PORT','8790')),proxy_headers=False)
