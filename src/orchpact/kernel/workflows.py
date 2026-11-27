"""Portable workflow definitions; concrete identities and providers belong to bindings."""
from copy import deepcopy
from hashlib import sha256
import json
import re

from .models import model_capabilities

FORMAT = 'orchpact-workflow'
TOP = {'format','id','name','version','description','resources','units','agents','operations'}
UNIT = {'key','parent','name','purpose','prompt','model','crew'}
AGENT = {'unit','name','role','prompt','capabilities','model'}
STAFF = (AGENT - {'unit'}) | {'reason'}
CREW_MEMBER = (AGENT - {'unit'}) | {'key','instruction'}
OPERATION = {'unit','instruction','input_schema','output_schema','output_kind','artifact_output','verification','output_file','request_limit','search_limit','tool_limit','delivery_context_limit','execution_plan'}
CAPABILITIES = {'text','json','search','image','image_reference'}


def fields(value, allowed, label):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(label + ' 包含不支持的字段或运行时配置')


def model_sites(value):
    for unit in value.get('units', []):
        yield unit, ['text','json']
        for member in (unit.get('crew') or {}).get('members',[]):
            yield member, member.get('capabilities',['text'])
    for agent in value.get('agents', []):
        yield agent, agent.get('capabilities', ['text'])
    for operation in value['operations'].values():
        for node in operation.get('execution_plan', {}).get('nodes', []):
            if node.get('staff'):
                yield node['staff'], node['staff'].get('capabilities', ['text'])


def materialize(value, models):
    """Translate one definition to the existing package/graph implementation."""
    package = deepcopy(value)
    tools = package.get('resources', {}).get('tools')
    if tools is not None:
        package['_tool_definitions'] = deepcopy(tools)
    package.pop('format', None);package.pop('resources', None)
    for site, _ in model_sites(package):
        if 'model' in site:
            site['model_id'] = models[site.pop('model')]
    return package


def validate_definition(value):
    # Reject runtime fields structurally, without inspecting prompt/schema text.
    if not isinstance(value, dict) or len(json.dumps(value, ensure_ascii=False, allow_nan=False)) > 1_500_000:
        raise ValueError('工作流定义须为有界 JSON 对象')
    fields(value, TOP, '工作流定义')
    if value.get('format') != FORMAT:
        raise ValueError('工作流 format 必须为 ' + FORMAT)
    value = deepcopy(value)
    for key, low, high in (('units',1,48),('agents',0,100)):
        if key in value and (not isinstance(value[key],list) or not low <= len(value[key]) <= high):
            raise ValueError(key+' 必须是有界列表')
    if not isinstance(value.get('version'), str) or not 1 <= len(value['version']) <= 80:
        raise ValueError('工作流必须声明作者版本 version')
    if 'description' in value and (not isinstance(value['description'], str) or len(value['description']) > 12000):
        raise ValueError('工作流描述无效')
    resources = value.setdefault('resources', {'models': {}})
    fields(resources, {'models','tools'}, '资源需求')
    models = resources.setdefault('models', {})
    if not isinstance(models, dict) or len(models) > 64:
        raise ValueError('模型能力槽位数量无效')
    for key, requirement in models.items():
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', key):
            raise ValueError('模型能力槽位名称无效')
        fields(requirement, {'capabilities'}, '模型能力槽位')
        caps = requirement.get('capabilities')
        if not isinstance(caps, list) or not caps or any(not isinstance(cap,str) for cap in caps) or not set(caps) <= CAPABILITIES or len(caps) != len(set(caps)):
            raise ValueError('模型能力槽位须声明有效能力')
    if 'tools' in resources:
        from .tools import validate_tool_definitions
        resources['tools'] = validate_tool_definitions(resources['tools'])
    for unit in value.get('units', []):
        fields(unit, UNIT, '职责定义')
        if unit.get('crew') is not None:
            fields(unit['crew'], {'process','members'}, '团队声明')
            if not isinstance(unit['crew'].get('members'),list):raise ValueError('团队须声明 members')
            for member in unit['crew']['members']:fields(member, CREW_MEMBER, '团队岗位')
    for agent in value.get('agents', []):fields(agent, AGENT, '角色定义')
    operations = value.get('operations')
    if not isinstance(operations, dict):raise ValueError('工作流须声明 operations')
    for operation in operations.values():
        fields(operation, OPERATION, '工作流操作')
        plan = operation.get('execution_plan')
        if 'execution_plan' in operation:
            fields(plan, {'nodes','result_node','max_repairs','staffing'}, '工作流计划')
            if not isinstance(plan.get('nodes'), list):raise ValueError('计划须声明 nodes')
            for node in plan['nodes']:
                fields(node, {'key','title','kind','parent','unit','staff','participants','tool_ref','arguments','artifact_outputs','verification','description','expected','depends_on','outputs','rules','exports','gate','repair_from'}, '工作流节点')
                if node.get('staff') is not None:fields(node['staff'], STAFF, '节点角色')
    declared_tools = set(resources.get('tools', {}))
    approved_tools = {key for key, tool in resources.get('tools', {}).items() if tool.get('requires_approval')}
    for site, capabilities in model_sites(value):
        if not isinstance(capabilities,list) or any(not isinstance(cap,str) for cap in capabilities):raise ValueError('角色能力必须为列表')
        granted = {cap[5:] for cap in capabilities if cap.startswith('tool:')}
        if granted & approved_tools:
            raise ValueError('需要事前审批的工具只允许确定性工具节点调用')
        if any(not re.fullmatch(r'tool:[A-Za-z][A-Za-z0-9_-]{0,63}',cap) for cap in capabilities if cap.startswith('tool:')) or not granted <= declared_tools:
            raise ValueError('角色引用了未声明或无效的工具槽位')
        required = model_capabilities(capabilities)
        slot = site.get('model')
        if 'model' in site and (not isinstance(slot,str) or slot not in models):
            raise ValueError('引用了未声明的模型能力槽位')
        if slot is not None and not set(required) <= set(models[slot]['capabilities']):
            raise ValueError('模型能力槽位未覆盖使用方所需能力')
    from .packages import validate_package
    checked = validate_package(materialize(value, {key:key for key in models}))
    value.setdefault('units', checked['units'])
    return value


def definition_digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',',':')).encode()).hexdigest()


def bind_definition(value, bindings, choose, choose_tool=None):
    value = validate_definition(value)
    fields(bindings, {'models','tools'}, '工作流运行时绑定')
    models = bindings.get('models', {})
    requirements = value['resources']['models']
    if not isinstance(models, dict) or set(models) != set(requirements):
        raise ValueError('模型绑定须恰好覆盖工作流声明的能力槽位')
    for slot, model in models.items():
        if not isinstance(model, str) or not model:raise ValueError('模型绑定须为本实例模型标识')
        choose(requirements[slot]['capabilities'], model)
    from .tools import bind_tool_definitions
    tool_requirements = value['resources'].get('tools', {})
    if tool_requirements and choose_tool is None:
        raise ValueError('当前安装器未配置工具绑定解析')
    tools = bind_tool_definitions(bindings.get('tools'), tool_requirements, choose_tool or (lambda *_: None))
    from .packages import validate_package
    package = validate_package(materialize(value, models))
    if 'tools' in value['resources']:
        package['_tool_bindings'] = tools
    return package


def export_definition(package):
    """Export an authoring definition, never an installed manifest or run snapshot."""
    if package.get('format') == FORMAT:return validate_definition(package)
    value = {key:deepcopy(package[key]) for key in ('id','name','version','description','units','agents','operations') if key in package}
    value.update(format=FORMAT, version=str(value.get('version','1.0.0')), resources={'models':{}})
    # Keep only supported definition fields; local model identifiers become slots.
    for unit_index,unit in enumerate(value.get('units', [])):
        for member_index,member in enumerate((unit.get('crew') or {}).get('members',[])):
            local_member=member.get('model_id')
            for field in set(member)-CREW_MEMBER:member.pop(field)
            if local_member:
                slot=f'crew_{unit_index}_{member_index}'
                member['model']=slot;value['resources']['models'][slot]={'capabilities':model_capabilities(member.get('capabilities',['text']))}
        local = unit.get('model_id')
        for key in set(unit)-UNIT:unit.pop(key)
        if local:
            slot = 'unit_' + unit['key']
            unit['model'] = slot;value['resources']['models'][slot] = {'capabilities':['text','json']}
    for index, agent in enumerate(value.get('agents', [])):
        local = agent.get('model_id')
        for key in set(agent)-AGENT:agent.pop(key)
        if local:
            slot = 'role_' + str(index)
            agent['model'] = slot;value['resources']['models'][slot] = {'capabilities':model_capabilities(agent.get('capabilities',['text']))}
    for op_index, operation in enumerate(value['operations'].values()):
        for key in set(operation)-OPERATION:operation.pop(key)
        for node_index, node in enumerate(operation.get('execution_plan', {}).get('nodes', [])):
            staff = node.get('staff')
            if staff and staff.pop('model_id',None):
                slot = f'node_{op_index}_{node_index}'
                staff['model'] = slot;value['resources']['models'][slot] = {'capabilities':model_capabilities(staff.get('capabilities',['text']))}
    if package.get('_tool_definitions') is not None:
        value['resources']['tools'] = deepcopy(package['_tool_definitions'])
    return validate_definition(value)
