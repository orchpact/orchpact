"""Application identity context; transport cannot bypass domain authorization."""
from ..adapters.identity import principal
from ..kernel.models import ExecutionContext
from ..kernel.errors import Forbidden


def from_actor(actor):
    if not actor or not actor.user_id or not actor.workspace_id:
        raise Forbidden('需要有效账号与 Workspace')
    return ExecutionContext('user:' + actor.user_id, actor.workspace_id, actor.application_id,
                            actor.user_id, admin=actor.admin, application_key_id=actor.application_key_id, client=actor.client)


class ApplicationService:
    def __init__(self, service):
        self.core = service
        self.db, self.settings, self.access = service.db, service.settings, service.access
        self.configuration = service.configuration
        self.applications = self

    def application(self, app, c=None):
        return self.core.application(from_actor(principal.get()), app, c)

    def invocation(self, app, key, c=None):
        return self.core.invocation(from_actor(principal.get()), app, key, c)

    def get(self, c, table, key):
        row = self.core.row(c, table, key)
        project = row if table == 'projects' else self.core.row(c, 'projects', row['project_id'] if 'project_id' in row else row['id'])
        self.core.scope(c, from_actor(principal.get()), project)
        return row
