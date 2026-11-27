"""Workspace workflow installations and direct execution, without consuming apps.

Legacy application foreign keys remain as private storage scope identifiers so
existing artifacts, receipts and accounting never need destructive migration.
"""
from contextlib import nullcontext
from hashlib import sha256
import json

from ..adapters.database import dumps, loads, now
from ..kernel.errors import DomainError, NotFound
from ..kernel.verification import canonical_digest


class WorkflowDelivery:
    def __init__(self, service):
        self.s=service;self.db=service.db

    def member(self,c,ctx,owner=False):
        self.s.access.member(c,ctx.workspace_id,self.s.actor(ctx),owner=owner and not ctx.admin)

    def installed(self,c,ctx,workflow):
        return c.execute('SELECT * FROM workflow_installations WHERE workspace_id=%s AND workflow_id=%s',
            (ctx.workspace_id,workflow)).fetchone()

    @staticmethod
    def scope(ctx,asset,bindings):
        return 'wf-'+canonical_digest(dict(workspace=ctx.workspace_id,workflow=asset['id'],revision=asset['revision'],bindings=bindings))

    def preview(self,ctx,workflow,revision,bindings):
        with self.db.connect() as c:
            self.member(c,ctx)
            asset=self.s.workflows.resolve(c,workflow,revision)
            old=self.installed(c,ctx,workflow)
            scope=self.scope(ctx,asset,bindings)
            binding=dict(id=scope,name=asset['definition']['name'],revision=1,manifest=asset['definition'],workflow={k:asset[k] for k in ('id','revision','version','sha256')})
            manifest=self.s.prepare_package(binding,bindings,ctx=ctx)
            return dict(workflow_id=workflow,workflow_revision=revision,sha256=asset['sha256'],
                installed_revision=old['revision'] if old else 0,
                action='unchanged' if old and old['scope_id']==scope else 'upgrade' if old else 'install',
                bindings_digest=canonical_digest(bindings),operations=list(manifest['operations']))

    def installation(self,ctx,workflow,c=None):
        with self.db.connect() if c is None else nullcontext(c) as c:
            self.member(c,ctx);row=self.installed(c,ctx,workflow)
            if not row:raise NotFound('工作流尚未安装到当前 Workspace')
            manifest=loads(c.execute('SELECT manifest FROM organization_packages WHERE workspace_id=%s AND application_id=%s',
                (ctx.workspace_id,row['scope_id'])).fetchone()[0])
            return dict(workflow_id=workflow,workflow_revision=row['workflow_revision'],revision=row['revision'],
                bindings=manifest['_workflow']['bindings'],bindings_digest=row['bindings_digest'])

    def install(self,ctx,workflow,revision,bindings,expected,*,c=None):
        with self.db.tx() if c is None else nullcontext(c) as c:
            self.member(c,ctx,owner=True)
            asset=self.s.workflows.resolve(c,workflow,revision)
            old=self.installed(c,ctx,workflow)
            if (old['revision'] if old else 0)!=expected:
                raise DomainError('revision_conflict','安装已变化，请重新预览')
            scope=self.scope(ctx,asset,bindings)
            if old and old['scope_id']==scope:return self.installation(ctx,workflow,c)
            next_revision=expected+1
            existing=c.execute('SELECT config FROM applications WHERE id=%s',(scope,)).fetchone()
            marker=dict(direct_workflow=workflow,workspace_id=ctx.workspace_id)
            if existing and loads(existing['config'])!=marker:raise DomainError('scope_conflict','执行存储范围冲突')
            if not existing:
                c.execute('INSERT INTO applications(id,name,config,revision,enabled,require_signature) VALUES(%s,%s,%s,1,1,0)',
                    (scope,asset['definition']['name'],dumps(marker)))
                c.execute('INSERT INTO application_bindings VALUES(%s,1,%s,%s,%s,%s)',(scope,workflow,revision,now(),ctx.user_id))
            manifest=self.s.install_package(ctx,scope,c,expected_revision=0 if not existing else None,bindings=bindings,binding_revision=1)
            # Public provenance is about the workflow and Workspace installation.
            manifest['_workflow'].pop('application_binding_revision',None)
            manifest['_workflow']['installation_revision']=next_revision
            c.execute('UPDATE organization_packages SET manifest=%s WHERE workspace_id=%s AND application_id=%s',
                (dumps(manifest),ctx.workspace_id,scope))
            c.execute('''INSERT INTO workflow_installations VALUES(%s,%s,%s,%s,%s,%s)
                ON CONFLICT(workspace_id,workflow_id) DO UPDATE SET workflow_revision=excluded.workflow_revision,
                scope_id=excluded.scope_id,revision=excluded.revision,bindings_digest=excluded.bindings_digest''',
                (ctx.workspace_id,workflow,revision,scope,next_revision,canonical_digest(bindings)))
            self.s.db.event(c,None,None,'workflow.installed',workflow_id=workflow,workflow_revision=revision,
                workspace_id=ctx.workspace_id,installation_revision=next_revision,actor=ctx.subject)
            return self.installation(ctx,workflow,c)

    @staticmethod
    def public(value):
        value=dict(value);value.pop('application_id',None)
        return value

    def invoke(self,ctx,workflow,data,key,expected_revision=None):
        if not isinstance(key,str) or not 1<=len(key)<=100:raise ValueError('需要 Idempotency-Key')
        request_key=sha256((ctx.workspace_id+':'+key).encode()).hexdigest()
        fingerprint=sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
        with self.db.tx() as c:
            self.member(c,ctx)
            # The stable workflow key survives explicit installation upgrades.
            old=c.execute('''SELECT i.* FROM invocations i JOIN applications a ON a.id=i.application_id
                JOIN projects p ON p.id=i.id WHERE p.workspace_id=%s AND i.request_key=%s
                AND a.config::jsonb->>'direct_workflow'=%s''',(ctx.workspace_id,request_key,workflow)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint:raise DomainError('idempotency_conflict','幂等键已用于不同输入')
                return self.public(self.s.invocation(ctx,old['application_id'],old['id'],c))
            installed=self.installed(c,ctx,workflow)
            if not installed:raise DomainError('workflow_not_installed','先预览并安装工作流资源')
            if expected_revision is not None and installed['revision'] != expected_revision:
                raise DomainError('revision_conflict','安装已变化，请重新加载运行表单')
            return self.public(self.s.invoke(ctx,installed['scope_id'],data,key,c=c))

    def run(self,ctx,key):
        with self.db.connect() as c:
            project=self.s.scope(c,ctx,self.s.row(c,'projects',key))
            return self.public(self.s.invocation(ctx,project['application_id'],key,c))

    def runs(self,ctx,workflow,limit=100):
        with self.db.connect() as c:
            self.member(c,ctx)
            keys=[row['id'] for row in c.execute('''SELECT p.id FROM projects p JOIN applications a ON a.id=p.application_id
                WHERE p.workspace_id=%s AND a.config::jsonb->>'direct_workflow'=%s ORDER BY p.created DESC,p.id DESC LIMIT %s''',
                (ctx.workspace_id,workflow,limit))]
        return [self.run(ctx,key) for key in keys]
