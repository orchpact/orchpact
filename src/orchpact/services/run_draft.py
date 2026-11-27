"""Build an unpublished, portable definition from a recorded run plan."""
from ..adapters.database import loads
from ..kernel.errors import DomainError, NotFound
from ..kernel.models import model_capabilities
from ..kernel.workflows import validate_definition


def read_run_draft(service, ctx, run, source=None, revision=None, selection=None):
    service.workflows.admin(ctx)
    with service.db.connect() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        service.delivery.member(c, ctx)
        row = c.execute("""SELECT p.revision, p.application_id, i.operation,
                        i.binding::jsonb -> 'workflow' AS origin, plan.definition
                    FROM projects p JOIN invocations i ON i.id=p.id
                    LEFT JOIN execution_plans plan ON plan.project_id=p.id
                    WHERE p.id=%s AND p.workspace_id=%s""", (run, ctx.workspace_id)).fetchone()
        if not row:
            raise NotFound()
        if revision is not None and row['revision'] != revision:
            raise DomainError('revision_conflict', '此运行的计划已更新，请重新打开运行画布')
        origin = row['origin']
        if not origin or not row['definition']:
            raise DomainError('no_workflow_plan', '此运行没有可另存的工作流定义与执行计划')
        asset = service.workflows.resolve(c, origin['id'], origin['revision'])
        # Only keyed plan tasks carry reusable instructions. The synthetic root
        # description embeds the invocation input and must never enter a draft.
        tasks = [dict(task) for task in c.execute("""SELECT t.node_key, t.description, t.expected, t.definition,
                    t.snapshot::jsonb #>> '{unit,alias}' AS unit_alias, parent.node_key AS parent_key,
                    ARRAY(SELECT pre.node_key FROM dependencies d JOIN tasks pre ON pre.id=d.depends_on
                          WHERE d.task_id=t.id ORDER BY pre.node_key) AS upstream
                FROM tasks t LEFT JOIN tasks parent ON parent.id=t.parent_id
                WHERE t.project_id=%s AND t.node_key IS NOT NULL ORDER BY t.created,t.id""", (run,))]
    plan = loads(row['definition'])
    selected = source or plan['result_node']
    restore = list(dict.fromkeys([selected, *(selection or [])]))
    restored = include_cancelled_sources(plan, tasks, restore)
    definition = asset['definition']
    operation = definition['operations'][row['operation']]
    original = {node['key']: node for node in operation.get('execution_plan', {}).get('nodes', [])}
    known_units = {unit['key'] for unit in definition['units']}
    tasks_by_key = {task['node_key']: task for task in tasks}
    model_slots = definition['resources']['models']
    bindings = dict(origin.get('bindings', {}).get('models', {}))
    for node in plan['nodes']:
        task = tasks_by_key[node['key']]
        scope, _, unit = (task['unit_alias'] or '').partition(':')
        installed_scope, archived, number = scope.rpartition('@')
        if archived and number.isdecimal():
            scope = installed_scope
        if scope != row['application_id'] or unit not in known_units:
            raise DomainError('nonportable_unit', '环节分派给了工作流定义之外的组织，无法复用其组织声明')
        node.pop('unit_id', None)
        node['unit'] = unit
        staff = node.get('staff')
        if staff:
            model = staff.pop('model_id', None)
            declared = (original.get(node['key'], {}).get('staff') or {}).get('model')
            if declared:
                staff['model'] = declared
            elif model:
                staff['model'] = draft_model_slot(model_slots, bindings, model, staff['capabilities'])
    operation['execution_plan'] = plan
    return dict(id=asset['id'], revision=asset['revision'], run_id=run, plan_revision=row['revision'],
                operation=row['operation'], selected_node=selected, restored_nodes=restored,
                definition=validate_definition(definition))


def include_cancelled_sources(plan, tasks, selection):
    """Restore selected cancelled branches with their dependencies only in the draft."""
    included = {node['key'] for node in plan['nodes']}
    if all(key in included for key in selection):
        return []
    by_key = {task['node_key']: task for task in tasks}
    if any(key not in by_key for key in selection):
        raise NotFound('运行中没有这个环节')
    restored = []
    pending = list(reversed(selection))
    while pending:
        key = pending.pop()
        if key in included:
            continue
        task = by_key[key]
        node = loads(task['definition'])
        node.pop('active', None)
        node.update(parent=task['parent_key'], depends_on=task['upstream'],
                    description=task['description'], expected=task['expected'])
        included.add(key)
        restored.append(key)
        plan['nodes'].append(node)
        pending.extend(node['depends_on'] + node.get('repair_from', []))
        if node['parent']:
            pending.append(node['parent'])
        # A restored group needs its children to gather the same deliverables.
        pending.extend(t['node_key'] for t in tasks if t['parent_key'] == key)
    return restored


def draft_model_slot(slots, bindings, model, capabilities):
    required = model_capabilities(capabilities)
    for slot, bound in bindings.items():
        if bound == model and set(required) <= set(slots[slot]['capabilities']):
            return slot
    # A locally selected model becomes a capability requirement. Installation
    # of the newly published workflow explicitly binds it in its own Workspace.
    index = 1
    while (slot := f'branch_model_{index}') in slots:
        index += 1
    slots[slot] = dict(capabilities=required)
    bindings[slot] = model
    return slot
