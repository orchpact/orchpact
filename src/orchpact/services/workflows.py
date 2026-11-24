"""Immutable workflow assets; Workspace installation and execution live in delivery."""
from ..adapters.database import dumps,loads,now
from ..kernel.errors import Forbidden,NotFound,DomainError
from ..kernel.workflows import validate_definition,definition_digest


class WorkflowCatalog:
    def __init__(self,service):self.s=service;self.db=service.db

    @staticmethod
    def admin(ctx):
        if not ctx.admin:raise Forbidden('工作流目录发布仅限管理员')

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
