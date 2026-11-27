"""Portable organization packages: data only, validated before any installation writes."""
from copy import deepcopy
import json
import re

from .contracts import check_schema
from .models import AgentInput,UnitInput
from .plan import validate_plan
from .verification import ArtifactOutput, verification_policy


def validate_package(value):
    if isinstance(value,dict) and 'format' in value and value['format'] != 'orchpact-workflow':
        raise ValueError('不支持的工作流定义格式')
    if isinstance(value,dict) and value.get('format') == 'orchpact-workflow':
        from .workflows import validate_definition
        return validate_definition(value)
    if not isinstance(value, dict) or len(json.dumps(value, ensure_ascii=False)) > 1_500_000:
        raise ValueError('组织包须为有界 JSON 对象')
    value = deepcopy(value)
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}', value.get('id', '')):
        raise ValueError('组织包 namespace 无效')
    if not isinstance(value.get('name'), str) or not 1 <= len(value['name']) <= 120:
        raise ValueError('组织包名称无效')
    for flag in ('enabled','require_signature'):
        if flag in value and type(value[flag]) is not bool:raise ValueError(flag+' 必须是布尔值')
    units = value.get('units') or [dict(key='entry', name=value['name'], purpose=value.get('description', value['name']), prompt='', parent=None)]
    if not isinstance(units, list) or not 1 <= len(units) <= 48:
        raise ValueError('组织包组织数量无效')
    if any(not isinstance(u,dict) or not isinstance(u.get('key'),str) for u in units):raise ValueError('组织定义缺少稳定别名')
    aliases = {u['key'] for u in units}
    if len(aliases) != len(units) or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', key) for key in aliases):
        raise ValueError('组织稳定别名重复或无效')
    for unit in units:
        UnitInput.model_validate({k:unit[k] for k in ('name','purpose','prompt','crew') if k in unit}|dict(parent_id=unit.get('parent'),model_id='configured-at-install'))
        if unit.get('unit_id') or unit.get('id'):raise ValueError('组织包不能携带实例 ID')
        if unit.get('parent') and unit['parent'] not in aliases:
            raise ValueError('组织隶属引用不存在')
        seen = {unit['key']};parent = unit.get('parent')
        while parent:
            if parent in seen:
                raise ValueError('组织隶属循环')
            seen.add(parent);parent = next(u for u in units if u['key'] == parent).get('parent')
        if not isinstance(unit.get('purpose'), str) or not unit['purpose'].strip():
            raise ValueError('组织职责不能为空')
    agents=value.get('agents',[])
    if not isinstance(agents,list) or len(agents)>100:raise ValueError('角色数量超限')
    for agent in agents:
        if not isinstance(agent,dict):raise ValueError('角色必须为对象')
        if agent['unit'] not in aliases:
            raise ValueError('角色引用了不存在的组织')
        AgentInput.model_validate({k: v for k, v in agent.items() if k != 'unit'})
    operations = value.get('operations')
    if not isinstance(operations, dict) or not 1 <= len(operations) <= 30:
        raise ValueError('组织包须声明 1–30 个操作')
    for name, operation in operations.items():
        if not isinstance(operation,dict):raise ValueError('操作必须为对象')
        if 'unit_id' in operation:raise ValueError('组织包不能携带实例 unit_id')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,79}', name):
            raise ValueError('操作别名无效')
        if operation.get('unit', units[0]['key']) not in aliases:
            raise ValueError('操作引用了不存在的组织')
        check_schema(operation.get('input_schema', {}))
        if not isinstance(operation.get('instruction'), str) or not 1 <= len(operation['instruction']) <= 12000:
            raise ValueError('操作职责无效')
        filename = operation.setdefault('output_file', 'result.json')
        output_kind=operation.get('output_kind','json')
        if output_kind not in ('json','artifact'):raise ValueError('操作 output_kind 无效')
        from .files import encode_files
        if not isinstance(filename,str):raise ValueError('操作输出文件路径无效')
        encode_files([dict(path=filename,data=b'')])
        if output_kind=='artifact':
            if 'output_schema' in operation or 'artifact_output' not in operation:raise ValueError('Artifact 根输出必须声明独立媒体契约')
            operation['artifact_output']=ArtifactOutput.model_validate(operation['artifact_output']).model_dump()
        else:
            if 'artifact_output' in operation:raise ValueError('JSON 根输出不能声明 Artifact 媒体契约')
            check_schema(operation['output_schema'])
            if not filename.endswith('.json'):raise ValueError('操作输出必须是命名 JSON 文件')
        if 'verification' in operation:
            if not operation.get('execution_plan'):raise ValueError('根验证策略要求固定执行计划')
            operation['verification']=verification_policy(operation['verification'])
            tool=value.get('_tool_definitions',{}).get(operation['verification']['tool_ref'])
            if not tool or tool['effect']!='read':raise ValueError('根验证必须引用只读 Verifier 工具')
        for field, default, low, high in [('request_limit', 32, 1, 256), ('search_limit', 0, 0, 100), ('delivery_context_limit', 9000, 1, 120000)]:
            amount = operation.setdefault(field, default)
            if type(amount) is not int or not low <= amount <= high:
                raise ValueError(field + ' 超出范围')
        if 'tool_limit' in operation and (type(operation['tool_limit']) is not int or not 0 <= operation['tool_limit'] <= 64):
            raise ValueError('tool_limit 超出范围')
        if operation.get('execution_plan'):
            plan = deepcopy(operation['execution_plan'])
            for node in plan['nodes']:
                alias = node.pop('unit', operation.get('unit', units[0]['key']))
                if alias not in aliases:
                    raise ValueError('计划引用了不存在的组织')
                if node.get('unit_id'):
                    raise ValueError('可移植组织包不能携带实例 unit_id')
                node['unit_id'] = alias
                if 'participants' in node and (not isinstance(node['participants'],list) or any(not isinstance(k,str) for k in node['participants'])):
                    raise ValueError('节点参与岗位必须是 key 字符串列表')
                if node.get('participants'):
                    team=next(u for u in units if u['key']==alias).get('crew')
                    if not team or not set(node['participants']) <= {m['key'] for m in team['members']}:
                        raise ValueError('节点参与岗位必须属于本团队声明')
            parsed, _ = validate_plan(plan,value.get('_tool_definitions'),operation.get('input_schema', {}))
            final = next(n for n in parsed['nodes'] if n['key'] == parsed['result_node'])
            if output_kind=='artifact':
                if final.get('artifact_outputs',{}).get(filename)!=operation['artifact_output']:raise ValueError('最终 Artifact 契约必须与操作输出一致')
            elif final['outputs'].get(filename) != operation['output_schema']:
                raise ValueError('最终工作项契约必须与操作输出一致')
    value['units'] = units
    return value
