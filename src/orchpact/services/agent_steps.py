"""Platform-owned execution evidence; CrewAI's in-memory state is not a ledger."""
from ..adapters.database import ident, now, dumps, loads
from ..provider_http import write_receipt

LIMITS = dict(model_steps=12, tool_steps=8, wall_seconds=300, history_chars=128000)


class AgentSteps:
    def __init__(self, providers, ctx):
        self.providers, self.ctx = providers, ctx
        self.service = providers.service
        self.db = providers.db
        self.limits = dict(ctx['snapshot']['executor']['limits'])

    def check(self, c, capability='text'):
        if getattr(self.service, 'stop_event', None) and self.service.stop_event.is_set():
            raise RuntimeError('服务正在停止')
        if self.ctx.get('_crew_member_key') and capability not in loads(self.ctx['snapshot']['agent']['capabilities']):
            from ..kernel.errors import Forbidden
            raise Forbidden('本岗位未声明所需能力')
        task = self.service.current(c, self.ctx['task']['id'], self.ctx['task']['attempt_id'])
        project = self.service.row(c, 'projects', task['project_id'])
        self.service.policies.authorize(c, self.ctx['execution_context'], 'call', project, capability)
        started = c.execute('SELECT started FROM attempts WHERE id=%s', (task['attempt_id'],)).fetchone()[0]
        if now() - started >= self.limits['wall_seconds']:
            raise ValueError('Agent 执行时间达到上限，未提交交付')
        return task

    def begin(self, kind, name, capability='text'):
        with self.db.tx() as c:
            task = self.check(c, capability)
            count = c.execute('SELECT COUNT(*) FROM agent_steps WHERE attempt_id=%s AND kind=%s', (task['attempt_id'], kind)).fetchone()[0]
            if count >= self.limits[kind + '_steps']:
                raise ValueError('Agent ' + kind + ' 步骤达到上限，未提交交付')
            ordinal = c.execute('SELECT COUNT(*) FROM agent_steps WHERE attempt_id=%s', (task['attempt_id'],)).fetchone()[0] + 1
            key = ident()
            c.execute('INSERT INTO agent_steps(id,project_id,task_id,attempt_id,ordinal,kind,name,status,created,crew_run_id,member_key,runtime_agent_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                      (key, task['project_id'], task['id'], task['attempt_id'], ordinal, kind, name, 'started', now(),self.ctx.get('_crew_run_id'),self.ctx.get('_crew_member_key'),self.ctx.get('_runtime_agent_id')))
            self.db.event(c, task['project_id'], task['id'], 'agent.step_started', step_id=key, step_kind=kind, name=name)
        return key

    def finish(self, key, status, evidence):
        path = self.providers.settings.data / 'agent-steps' / self.ctx['task']['attempt_id'] / (key + '.json')
        # Actual arguments and observations stay in private files, never audit/events.
        write_receipt(path, dict(step_id=key, status=status, **evidence))
        with self.db.tx() as c:
            c.execute('UPDATE agent_steps SET status=%s,finished=%s WHERE id=%s', (status, now(), key))
            self.db.event(c, self.ctx['project']['id'], self.ctx['task']['id'], 'agent.step_finished', step_id=key, status=status)

    def fail(self, key, error):
        path = self.providers.settings.data / 'agent-steps' / self.ctx['task']['attempt_id'] / (key + '.json')
        # Do not erase an actual successful response when its database update failed.
        if not path.exists():
            self.finish(key, 'failed', dict(error_type=type(error).__name__))


def recover_steps(service):
    """Recover evidence only; never resume a CrewAI loop or invoke a tool here."""
    with service.db.tx() as c:
        rows = c.execute("SELECT id,attempt_id FROM agent_steps WHERE status='started'").fetchall()
        for row in rows:
            status = 'unknown'
            path = service.settings.data / 'agent-steps' / row['attempt_id'] / (row['id'] + '.json')
            try:
                value = loads(path.read_text())
                if isinstance(value, dict) and value.get('step_id') == row['id'] and value.get('status') in ('completed', 'failed'):
                    status = value['status']
            except (OSError, ValueError, TypeError):
                pass
            c.execute('UPDATE agent_steps SET status=%s,finished=%s WHERE id=%s', (status, now(), row['id']))

    # CrewAI execution objects are never restored or shared after interruption.
    with service.db.tx() as c:
        for row in c.execute("SELECT id,attempt_id FROM crew_runs WHERE status='started'").fetchall():
            status='unknown'
            path=service.settings.data/'crew-runs'/row['attempt_id']/(row['id']+'.json')
            try:
                value=loads(path.read_text())
                if isinstance(value,dict) and value.get('crew_run_id')==row['id'] and value.get('status')=='completed':status='completed'
            except (OSError,ValueError,TypeError):pass
            c.execute('UPDATE crew_runs SET status=%s,finished=%s WHERE id=%s',(status,now(),row['id']))
