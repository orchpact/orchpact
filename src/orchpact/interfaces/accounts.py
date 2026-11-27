"""Generic invited accounts, sessions, workspaces and resource accounts."""
from fastapi import Depends, HTTPException, Request, Response, Header, Query
from pydantic import Field, model_validator
from typing import Literal
from ..kernel.models import Strict
from ..adapters.identity import USERNAME_PATTERN, principal


class Credentials(Strict):
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(min_length=12,max_length=128)

class Registration(Credentials):
    invitation: str = Field(min_length=20,max_length=200)



class PasswordSetup(Strict):
    password: str = Field(min_length=12,max_length=128)

class PasswordChange(Strict):
    current_password: str = Field(min_length=1,max_length=128)
    new_password: str = Field(min_length=12,max_length=128)


class Named(Strict):
    name: str = Field(min_length=1,max_length=80)

class Join(Strict):
    code: str = Field(min_length=20,max_length=200)

class Enabled(Strict):
    disabled: bool

class CreditAdjustment(Strict):
    delta: float = Field(ge=-1_000_000_000,le=1_000_000_000,allow_inf_nan=False)
    reason: str = Field(min_length=1,max_length=500)

class CreditSettlement(Strict):
    points: float = Field(ge=0,le=1_000_000_000,allow_inf_nan=False)
    reason: str = Field(min_length=1,max_length=500)


def install(app,access,auth,admin,cookie,origin,local_setup,audience=lambda request: "",credit_policy=None,provider_prices=lambda:[]):
    def guard(request, username=''):
        origin(request)
        access.throttle('ip:'+(request.client.host if request.client else 'unknown'),limit=30)
        if username:access.throttle('user:'+username.lower(),limit=12)

    @app.get('/api/v1/auth/setup')
    def setup_status(request: Request):
        state = access.setup_state()
        return dict(required=state != 'complete', available=state == 'first' and local_setup(request))

    @app.post('/api/v1/auth/setup',status_code=201)
    def setup(data: Credentials, request: Request, response: Response):
        if not local_setup(request):
            raise HTTPException(403, '请在实例本机创建首个账号')
        guard(request,data.username)
        actor = access.setup_admin(data.username,data.password)
        cookie(response,request,access.session(actor.user_id,audience(request)))
        return {'ok':True}

    @app.post('/api/v1/auth/login')
    def login(data:Credentials,request:Request,response:Response):
        guard(request,data.username)
        cookie(response,request,access.login(data.username,data.password,audience(request)))
        return {'ok':True}

    @app.post('/api/v1/auth/register',status_code=201)
    def register(data:Registration,request:Request,response:Response):
        guard(request,data.username)
        cookie(response,request,access.register(data.username,data.password,data.invitation,audience(request)))
        return {'ok':True}




    @app.get('/api/v1/auth/me',dependencies=[Depends(auth)])
    def me(request:Request):
        actor=principal.get()
        with access.db.connect() as c:
            row=c.execute('SELECT username,password_set FROM users WHERE id=%s',(actor.user_id,)).fetchone()
            credits=access.wallets.view(c,actor.user_id)
        return dict(user_id=actor.user_id,username=row['username'],password_set=bool(row['password_set']),admin=actor.admin,workspace_id=actor.workspace_id,workspaces=access.workspaces(actor),credits=credits,credit_policy=credit_policy() if callable(credit_policy) else credit_policy,provider_prices=provider_prices())


    @app.put('/api/v1/auth/password',dependencies=[Depends(auth)])
    def change_password(data:PasswordChange,request:Request,response:Response):
        actor=principal.get()
        if not actor.user_id:raise HTTPException(403,'请使用个人账号修改密码')
        origin(request)
        access.throttle('password-change:'+actor.user_id,limit=10)
        token=request.cookies.get('orchpact_session','')
        authorization=request.headers.get('authorization','')
        if authorization.startswith('Bearer '):token=authorization[7:]
        fresh=access.change_password(actor.user_id,data.current_password,data.new_password,token,actor,request.state.request_id)
        cookie(response,request,fresh)
        return {'ok':True}

    @app.post('/api/v1/admin/users/{user}/password',dependencies=[Depends(admin)])
    def reset_password(user:str,data:PasswordSetup,request:Request):
        origin(request)
        access.reset_password(user,data.password,principal.get(),request.state.request_id)
        return {'ok':True}




    def credit_account(user):
        with access.db.connect() as c:
            return dict(credits=access.wallets.view(c,user),ledger=access.wallets.ledger(c,user),
                        pending=[dict(r,reserved=r['reserved']/1_000_000) for r in c.execute("SELECT h.request_id,h.reserved,r.status FROM credit_charges h JOIN requests r ON r.id=h.request_id WHERE h.user_id=%s AND h.state='held' ORDER BY r.created DESC",(user,))])

    @app.get('/api/v1/auth/credits',dependencies=[Depends(auth)])
    def own_credits():
        user=principal.get().user_id
        if not user:raise HTTPException(403,'管理员平台任务不使用个人积分')
        return credit_account(user)

    @app.get('/api/v1/admin/users/{user}/credits',dependencies=[Depends(admin)])
    def user_credits(user:str):return credit_account(user)

    @app.post('/api/v1/admin/users/{user}/credits',dependencies=[Depends(admin)])
    def adjust_credits(user:str,data:CreditAdjustment,idempotency_key:str=Header(min_length=1,max_length=120)):
        if not idempotency_key.strip():raise ValueError('需要幂等键')
        return access.wallets.adjust(user,data.delta,data.reason,idempotency_key,principal.get().user_id or 'platform_admin')

    @app.post('/api/v1/admin/credit-requests/{key}/settle',dependencies=[Depends(admin)])
    def settle_credits(key:str,data:CreditSettlement):
        return access.wallets.reconcile(key,data.points,data.reason,principal.get().user_id or 'platform_admin')

    @app.get('/api/v1/workspaces',dependencies=[Depends(auth)])
    def spaces():return access.workspaces(principal.get())

    @app.post('/api/v1/workspaces',status_code=201,dependencies=[Depends(auth)])
    def create(data:Named,request:Request):
        actor=principal.get()
        if not actor.user_id:raise HTTPException(403,'请使用个人账号创建空间')
        if not data.name.strip():raise HTTPException(422,'空间名称不能为空')
        with access.db.tx() as c:
            key=access.new_workspace(c,actor.user_id,data.name.strip())
            access.audit.record(c,actor,'workspace.created','workspace',key,
                after={'owner_id':actor.user_id},workspace_id=key,request_id=request.state.request_id)
        return dict(id=key)

    @app.post('/api/v1/workspaces/join',dependencies=[Depends(auth)])
    def join(data:Join):return access.join(data.code)

    @app.patch('/api/v1/workspaces/{workspace}',dependencies=[Depends(auth)])
    def rename(workspace:str,data:Named,request:Request):
        if not data.name.strip():raise HTTPException(422,'空间名称不能为空')
        with access.db.tx() as c:
            before=access.member(c,workspace,owner=True)
            c.execute('UPDATE workspaces SET name=%s WHERE id=%s',(data.name.strip(),workspace))
            access.audit.record(c,principal.get(),'workspace.renamed','workspace',workspace,
                before={'renamed':False},after={'renamed':True},workspace_id=workspace,request_id=request.state.request_id)
        return {'ok':True}

    @app.get('/api/v1/workspaces/{workspace}/members',dependencies=[Depends(auth)])
    def members(workspace:str):
        with access.db.connect() as c:
            space=access.member(c,workspace)
            return [dict(r,role='owner' if r['id']==space['owner_id'] else 'member') for r in c.execute('SELECT u.id,u.username FROM users u JOIN workspace_members m ON m.user_id=u.id WHERE m.workspace_id=%s',(workspace,))]

    @app.delete('/api/v1/workspaces/{workspace}/members/{user}',dependencies=[Depends(auth)])
    def remove_member(workspace:str,user:str,request:Request):
        with access.db.tx() as c:
            actor=principal.get();space=access.member(c,workspace)
            if user==space['owner_id']:raise HTTPException(409,'空间所有者不能退出或被移除')
            if actor.user_id!=user:access.member(c,workspace,owner=True)
            removed=c.execute('DELETE FROM workspace_members WHERE workspace_id=%s AND user_id=%s',(workspace,user))
            if not removed.rowcount:raise HTTPException(404,'空间成员不存在')
            access.audit.record(c,actor,'workspace.member_removed','workspace',workspace,
                before={'user_id':user},after={'member':False},workspace_id=workspace,request_id=request.state.request_id)
        return {'ok':True}

    @app.post('/api/v1/workspaces/{workspace}/invitations',dependencies=[Depends(auth)])
    def invite_member(workspace:str):return access.invite(workspace)

    @app.get('/api/v1/workspaces/{workspace}/invitations',dependencies=[Depends(auth)])
    def invitations(workspace:str):
        with access.db.connect() as c:
            access.member(c,workspace,owner=True)
            return [dict(r) for r in c.execute('SELECT id,expires,used_by,revoked FROM invitations WHERE workspace_id=%s ORDER BY expires DESC',(workspace,))]

    @app.delete('/api/v1/workspaces/{workspace}/invitations/{key}',dependencies=[Depends(auth)])
    def revoke(workspace:str,key:str):
        with access.db.tx() as c:
            access.member(c,workspace,owner=True)
            c.execute('UPDATE invitations SET revoked=1 WHERE id=%s AND workspace_id=%s',(key,workspace))
        return {'ok':True}

    @app.post('/api/v1/admin/invitations',dependencies=[Depends(admin)])
    def invite_account(request:Request):
        origin(request)
        return access.invite()

    @app.get('/api/v1/admin/users',dependencies=[Depends(admin)])
    def users():
        with access.db.connect() as c:
            return [dict(r,credits=access.wallets.view(c,r['id'])) for r in c.execute('''
                SELECT u.id,u.username,u.disabled,u.created,u.password_set,
                  u.id=(SELECT value FROM instance_metadata WHERE key='admin_user') AS admin,
                  (SELECT COUNT(*) FROM workspaces w WHERE w.owner_id=u.id) AS owned_spaces,
                  (SELECT COUNT(*) FROM workspace_members m JOIN workspaces w ON w.id=m.workspace_id
                    WHERE m.user_id=u.id AND w.owner_id!=u.id) AS joined_spaces
                FROM users u ORDER BY u.created DESC''')]

    @app.get('/api/v1/admin/users/{user}/workspaces',dependencies=[Depends(admin)])
    def user_workspaces(user:str):
        with access.db.connect() as c:
            if not c.execute('SELECT 1 FROM users WHERE id=%s',(user,)).fetchone():raise HTTPException(404,'账号不存在')
            return [dict(r,role='owner' if r['owner_id']==user else 'member') for r in c.execute('''
                SELECT w.*,u.username AS owner_name,
                  (SELECT COUNT(*) FROM workspace_members m WHERE m.workspace_id=w.id) AS member_count
                FROM workspaces w JOIN users u ON u.id=w.owner_id
                WHERE w.owner_id=%s OR EXISTS(SELECT 1 FROM workspace_members m WHERE m.workspace_id=w.id AND m.user_id=%s)
                ORDER BY w.personal DESC,w.created DESC''',(user,user))]


    @app.get('/api/v1/admin/audit',dependencies=[Depends(admin)])
    def audit_page(limit:int=Query(default=50,ge=1,le=100),cursor:str|None=None,action:str|None=None,
                   workspace_id:str|None=None,actor_id:str|None=None):
        return access.audit.page(limit,cursor,action,workspace_id,actor_id)

    @app.patch('/api/v1/admin/users/{user}',dependencies=[Depends(admin)])
    def disable(user:str,data:Enabled,request:Request):
        with access.db.tx() as c:
            if data.disabled and user == principal.get().user_id:
                raise HTTPException(409,'不能停用实例管理员')
            old=c.execute('SELECT disabled FROM users WHERE id=%s',(user,)).fetchone()
            if not old:raise HTTPException(404,'账号不存在')
            c.execute('UPDATE users SET disabled=%s WHERE id=%s',(int(data.disabled),user))
            revoked=c.execute('DELETE FROM sessions WHERE user_id=%s',(user,)).rowcount
            access.audit.record(c,principal.get(),'account.status_changed','user',user,
                before={'disabled':bool(old['disabled'])},after={'disabled':data.disabled,'sessions_revoked':revoked},request_id=request.state.request_id)
        return {'ok':True}
