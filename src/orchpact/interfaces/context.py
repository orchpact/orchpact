"""Application identity context; transport cannot bypass domain authorization."""
from ..adapters.identity import principal
from ..kernel.models import ExecutionContext
from ..kernel.errors import Forbidden


def from_actor(actor):
    if not actor or not actor.user_id or not actor.workspace_id:
        raise Forbidden('需要有效账号与 Workspace')
    return ExecutionContext('user:' + actor.user_id, actor.workspace_id, actor.application_id,
                            actor.user_id, admin=actor.admin, application_key_id=actor.application_key_id, client=actor.client)
