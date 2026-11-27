"""Workspace-scoped read models for the browser workbench."""
import base64
import binascii
import math

from ..adapters.database import dumps, loads
from ..kernel.errors import Forbidden, NotFound
from ..kernel.plan import node_kind


def cursor_value(cursor):
    try:
        timestamp, key = loads(base64.b64decode(cursor, altchars=b'-_', validate=True))
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or not isinstance(key, str):
            raise ValueError()
        return timestamp, key
    except (binascii.Error, UnicodeError, TypeError, ValueError, OverflowError):
        raise ValueError('无效分页游标') from None


def page(rows, limit, key):
    items = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = base64.urlsafe_b64encode(dumps([last['created'], last[key]]).encode()).decode()
    return dict(items=items, next_cursor=next_cursor)


def operations(definition):
    return [dict(key=key, input_schema=value.get('input_schema', {}), output_file=value.get('output_file'),
                 fixed_plan=bool(value.get('execution_plan')),
                 limits={limit: value[limit] for limit in ('request_limit', 'search_limit', 'tool_limit') if limit in value})
            for key, value in definition['operations'].items()]


class Workbench:
    def __init__(self, platform):
        self.s = platform

    def installations(self, ctx):
        with self.s.db.connect() as c:
            self.s.delivery.member(c, ctx)
            rows = c.execute('''SELECT i.workflow_id, i.workflow_revision, i.revision AS installation_revision,
                    v.version, v.definition, w.revision AS latest_revision
                FROM workflow_installations i JOIN workflow_versions v
                ON v.workflow_id=i.workflow_id AND v.revision=i.workflow_revision
                JOIN workflows w ON w.id=i.workflow_id
                WHERE i.workspace_id=%s ORDER BY i.workflow_id''', (ctx.workspace_id,))
            result = []
            for row in rows:
                item = dict(row)
                definition = loads(item.pop('definition'))
                item.update(name=definition['name'], operations=operations(definition))
                result.append(item)
            return result

    def installed_definition(self, ctx, workflow):
        with self.s.db.connect() as c:
            self.s.delivery.member(c, ctx)
            installed = self.s.delivery.installed(c, ctx, workflow)
            if not installed:
                raise NotFound('工作流尚未安装到当前 Workspace')
            asset = self.s.workflows.resolve(c, workflow, installed['workflow_revision'])
            # This read model exposes only the graph data the canvas needs.
            definition = asset['definition']
            graphs = {}
            for key, operation in definition['operations'].items():
                plan = operation.get('execution_plan')
                if not plan:
                    graphs[key] = {}
                    continue
                nodes = [
                    {field: node[field] for field in ('key','title','description','expected','parent','depends_on') if field in node}
                    | dict(kind=node_kind(node)) for node in plan['nodes']
                ]
                graphs[key] = dict(execution_plan=dict(result_node=plan['result_node'], nodes=nodes))
            return dict(revision=asset['revision'],
                        definition=dict(id=asset['id'], name=definition['name'], operations=graphs))

    def binding_options(self, ctx, workflow, revision):
        with self.s.db.connect() as c:
            self.s.delivery.member(c, ctx, owner=True)
            asset = self.s.workflows.resolve(c, workflow, revision)
            _, configuration = self.s.configuration.read(c)
        resources = asset['definition']['resources']
        models = self.s.settings.catalog()
        model_options = {key: [dict(id=model['id'], label=model['label']) for model in models
                               if set(requirement['capabilities']) <= set(model['capabilities'])]
                         for key, requirement in resources['models'].items()}
        tool_options = {}
        for key, requirement in resources.get('tools', {}).items():
            tool_options[key] = [
                dict(endpoint_ref=provider['id'], label=provider['label'],
                     implementation_version=provider['implementation_version'])
                for provider in configuration['providers']
                if provider['category'] == 'tool' and provider['adapter'] != 'mcp'
                and provider['tool_interface'] == requirement['interface']
                and provider['tool_interface_version'] == requirement['version']
                and (not provider['tool_policy']['workspace_ids'] or ctx.workspace_id in provider['tool_policy']['workspace_ids'])
                and (not provider['tool_policy'].get('workflow_ids') or workflow in provider['tool_policy']['workflow_ids'])
                and not provider['tool_policy']['application_ids']
                and self.s.configuration.ready(provider)]
        requirements = dict(models={key: ' / '.join(value['capabilities']) for key, value in resources['models'].items()},
                            tools={key: value['interface'] + ' ' + value['version']
                                   for key, value in resources.get('tools', {}).items()})
        return dict(workflow_id=workflow, name=asset['definition']['name'], workflow_revision=asset['revision'],
                    version=asset['version'], model_options=model_options, tool_options=tool_options, requirements=requirements)

    def orgs(self, ctx):
        with self.s.db.connect() as c:
            self.s.delivery.member(c, ctx, owner=True)
            units = [dict(row, crew=loads(row['crew']) if row['crew'] else None) for row in
                     c.execute('SELECT * FROM units WHERE workspace_id=%s', (ctx.workspace_id,))]
            agents = [dict(row) for row in c.execute('''SELECT a.* FROM agents a
                JOIN units u ON u.id=a.unit_id WHERE u.workspace_id=%s''', (ctx.workspace_id,))]
        return dict(units=units, agents=agents, models=self.s.settings.catalog())

    def run_counts(self, ctx):
        with self.s.db.connect() as c:
            self.s.delivery.member(c, ctx)
            return dict(c.execute('''SELECT p.status,count(*) FROM projects p
                JOIN invocations i ON i.id=p.id JOIN applications a ON a.id=p.application_id
                WHERE p.workspace_id=%s AND (a.require_signature=0 OR %s)
                GROUP BY p.status''', (ctx.workspace_id, ctx.admin)).fetchall())

    def runs(self, ctx, limit, cursor, status, query):
        with self.s.db.connect() as c:
            self.s.delivery.member(c, ctx)
            clauses = ['p.workspace_id=%s', '(a.require_signature=0 OR %s)']
            args = [ctx.workspace_id, ctx.admin]
            if cursor:
                clauses.append('(p.created,p.id)<(%s,%s)')
                args.extend(cursor_value(cursor))
            if status:
                clauses.append('p.status=%s')
                args.append(status)
            if query:
                clauses.append('position(lower(%s) in lower(concat_ws(\' \',p.id,i.operation,i.binding::jsonb->\'workflow\'->>\'id\')))>0')
                args.append(query)
            where = ' AND '.join(clauses)
            rows = [dict(row) for row in c.execute(f'''SELECT p.id,p.created,p.status,p.revision,
                    i.operation,i.binding::jsonb -> 'workflow' ->> 'id' AS workflow_id,
                    i.binding::jsonb -> 'workflow' ->> 'revision' AS workflow_revision,
                    p.created_by AS payer_user_id,u.username AS payer,
                    (SELECT max(e.created) FROM events e WHERE e.project_id=p.id) AS last_event
                FROM projects p JOIN invocations i ON i.id=p.id JOIN applications a ON a.id=p.application_id
                LEFT JOIN users u ON u.id=p.created_by
                WHERE {where} ORDER BY p.created DESC,p.id DESC LIMIT %s''', (*args, limit+1))]
        return page(rows, limit, 'id')

    def run(self, ctx, run):
        with self.s.db.connect() as c:
            workspace = self.s.access.member(c, ctx.workspace_id, self.s.actor(ctx))
            project = self.s.scope(c, ctx, self.s.row(c, 'projects', run))
            if project['workspace_id'] != ctx.workspace_id:
                raise NotFound()
            detail = self.s.delivery.public(self.s.invocation(ctx, project['application_id'], run, c))
            payer = c.execute('SELECT username FROM users WHERE id=%s', (project['created_by'],)).fetchone()
            workflow = detail.get('workflow')
            title = c.execute("SELECT definition::jsonb ->> 'name' FROM workflow_versions WHERE workflow_id=%s AND revision=%s",
                              (workflow['id'], workflow['revision'])).fetchone() if workflow else None
            dependencies = {}
            for row in c.execute('''SELECT d.task_id,d.depends_on FROM dependencies d
                    JOIN tasks t ON t.id=d.task_id WHERE t.project_id=%s ORDER BY d.depends_on''', (run,)):
                dependencies.setdefault(row['task_id'], []).append(row['depends_on'])
            actions = []
            for action in ('ask', 'cancel', 'retry', 'review', 'amend', 'recruit', 'delegate'):
                try:
                    self.s.policies.authorize(c, ctx, action, project)
                except Forbidden:
                    continue
                actions.append(action)
            return dict(detail, created=project['created'], payer=payer['username'] if payer else None,
                        workflow_name=title[0] if title else None, dependencies=dependencies, actions=actions,
                        can_decide_tool=ctx.admin or workspace['owner_id']==ctx.user_id,
                        limits={key: project[key] for key in ('request_limit', 'search_limit', 'tool_limit')})

    def inbox(self, ctx, limit, cursor, kind):
        with self.s.db.connect() as c:
            self.s.delivery.member(c, ctx)
            args = [ctx.workspace_id, ctx.admin]
            filters = []
            if cursor:
                filters.append('(created,key)<(%s,%s)')
                args.extend(cursor_value(cursor))
            if kind:
                filters.append('kind=%s')
                args.append(kind)
            where = ' WHERE ' + ' AND '.join(filters) if filters else ''
            # Snapshot review status and approval generation must match the current
            # task. Older runs are eligible, regardless of the run list's page size.
            rows = [dict(row) for row in c.execute(f'''WITH scoped AS (
                SELECT p.id,p.status,p.created,i.operation,i.binding::jsonb->'workflow'->>'id' AS workflow_id
                FROM projects p JOIN invocations i ON i.id=p.id JOIN applications a ON a.id=p.application_id
                WHERE p.workspace_id=%s AND (a.require_signature=0 OR %s)
            ), pending AS (
                SELECT p.id AS project_id,r.id AS reference,r.created,
                    CASE WHEN r.status='unknown' THEN 'receipt' ELSE 'accounting' END AS kind
                FROM scoped p JOIN requests r ON r.project_id=p.id
                LEFT JOIN credit_charges ch ON ch.request_id=r.id
                WHERE r.status='unknown' OR (r.status<>'started' AND ch.state='held')
                UNION ALL
                SELECT p.id,a.id,a.created,'approval' FROM scoped p JOIN tool_approvals a ON a.project_id=p.id
                JOIN tasks t ON t.id=a.task_id
                WHERE a.decision IS NULL AND a.tool_call_id IS NULL AND a.generation=t.generation
                    AND t.status='awaiting_approval' AND p.status<>'cancelled'
                UNION ALL
                SELECT p.id,t.id,t.created,'review' FROM scoped p JOIN tasks t ON t.project_id=p.id
                WHERE p.status<>'cancelled' AND t.review='pending' AND t.artifact_id IS NOT NULL
                    AND t.status IN ('completed','partial')
                    AND t.definition::jsonb->'active' IS DISTINCT FROM 'false'::jsonb
                    AND EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(t.snapshot::jsonb->'policies','[]'::jsonb)) AS rule
                                WHERE rule->'policy'->'independent_review'='true'::jsonb)
                UNION ALL
                SELECT p.id,m.id,m.created,'question' FROM scoped p JOIN messages m ON m.project_id=p.id
                WHERE p.status<>'cancelled' AND m.to_task IS NULL AND m.kind='question'
                    AND NOT EXISTS (SELECT 1 FROM messages reply WHERE reply.reply_to=m.id)
                UNION ALL
                SELECT p.id,p.id,p.created,'execution' FROM scoped p
                WHERE p.status IN ('failed','interrupted','partial','rejected')
            ), entries AS (
                SELECT pending.*, p.operation, p.workflow_id, pending.kind || ':' || pending.reference AS key
                FROM pending JOIN scoped p ON p.id=pending.project_id
            ) SELECT *,count(*) OVER () AS remaining FROM entries{where}
            ORDER BY created DESC,key DESC LIMIT %s''', (*args, limit+1))]
        remaining = rows[0]['remaining'] if rows else 0
        for row in rows:
            row.pop('remaining')
        return dict(page(rows, limit, 'key'), remaining=remaining)
