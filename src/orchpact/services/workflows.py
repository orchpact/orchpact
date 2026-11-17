"""Immutable workflow assets and explicitly pinned consuming applications."""
import re
from ..adapters.database import dumps,loads,now
from ..kernel.errors import Forbidden,NotFound,DomainError
from ..kernel.workflows import validate_definition,definition_digest
from ..kernel.models import WorkflowBinding


class WorkflowCatalog:
    def __init__(self,service):self.s=service;self.db=service.db

    @staticmethod
    def admin(ctx):
        if not ctx.admin:raise Forbidden('工作流目录与应用绑定仅限管理员')

    def resolve(self,c,workflow,revision=None):
        if revision is None:
            head=c.execute('SELECT revision FROM workflows WHERE id=%s',(workflow,)).fetchone()
            if not head:raise NotFound('工作流尚未登记')
            revision=head['revision']
        row=c.execute('SELECT * FROM workflow_versions WHERE workflow_id=%s AND revision=%s',(workflow,revision)).fetchone()
        if not row:raise NotFound('工作流修订不存在')
        return dict(id=workflow,revision=row['revision'],version=row['version'],sha256=row['sha256'],definition=loads(row['definition']))

    def read(self,ctx,workflow,revision=None):
        self.admin(ctx)
        with self.db.connect() as c:return self.resolve(c,workflow,revision)

    def list(self,ctx):
        self.admin(ctx)
        with self.db.connect() as c:
            return [self.resolve(c,row['id'],row['revision']) for row in c.execute('SELECT id,revision FROM workflows ORDER BY id')]

    def register(self,ctx,value,revision=0):
        self.admin(ctx);value=validate_definition(value)
        with self.db.tx() as c:
            old=c.execute('SELECT revision FROM workflows WHERE id=%s',(value['id'],)).fetchone()
            if (old['revision'] if old else 0)!=revision:raise DomainError('revision_conflict','工作流目录修订已变化')
            next_revision=revision+1
            c.execute('INSERT INTO workflows VALUES(%s,%s) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision',(value['id'],next_revision))
            c.execute('INSERT INTO workflow_versions VALUES(%s,%s,%s,%s,%s,%s,%s)',
                (value['id'],next_revision,value['version'],definition_digest(value),dumps(value),now(),ctx.user_id))
            self.db.event(c,None,None,'workflow.registered',workflow_id=value['id'],revision=next_revision,sha256=definition_digest(value),actor=ctx.subject)
            return self.resolve(c,value['id'],next_revision)

    def bind(self,ctx,application,value):
        self.admin(ctx);value=WorkflowBinding.model_validate(value).model_dump()
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}',application):raise ValueError('应用标识无效')
        with self.db.tx() as c:
            workflow=self.resolve(c,value['workflow_id'],value['workflow_revision'])
            old=c.execute('SELECT * FROM applications WHERE id=%s',(application,)).fetchone()
            if (old['revision'] if old else 0)!=value['expected_revision']:
                raise DomainError('revision_conflict','应用绑定修订已变化')
            revision=value['expected_revision']+1
            enabled=value.get('enabled');signature=value.get('require_signature')
            if enabled is None:enabled=bool(old['enabled']) if old else True
            if signature is None:signature=bool(old['require_signature']) if old else True
            c.execute('''INSERT INTO applications(id,name,config,revision,enabled,require_signature) VALUES(%s,%s,%s,%s,%s,%s)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,config=excluded.config,revision=excluded.revision,
                enabled=excluded.enabled,require_signature=excluded.require_signature''',
                (application,value['name'],'{}',revision,int(enabled),int(signature)))
            c.execute('INSERT INTO application_bindings VALUES(%s,%s,%s,%s,%s,%s)',
                (application,revision,workflow['id'],workflow['revision'],now(),ctx.user_id))
            self.db.event(c,None,None,'application.bound',application_id=application,revision=revision,
                workflow_id=workflow['id'],workflow_revision=workflow['revision'],sha256=workflow['sha256'],actor=ctx.subject,
                enabled=bool(enabled),require_signature=bool(signature),previous_revision=value['expected_revision'],
                previous_enabled=bool(old['enabled']) if old else None,previous_require_signature=bool(old['require_signature']) if old else None)
            return self.s.application(ctx,application,c)
