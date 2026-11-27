from .models import ExecutionPlan
import json
def dumps(value): return json.dumps(value, ensure_ascii=False, allow_nan=False)
def values(data,path):
    rows=[data]
    for segment in path.split('.') if path else []:
        next_rows=[]
        for row in rows:
            if segment=='*' and isinstance(row,list):next_rows.extend(row)
            elif isinstance(row,dict) and segment in row:next_rows.append(row[segment])
            else:raise ValueError('校验路径不存在：'+path)
        rows=next_rows
    return rows


def node_kind(node):
    declared=node.get('kind')
    if declared:return declared
    return 'agent' if node.get('staff') or node.get('participants') else 'group'


def is_execution(node):
    return node_kind(node) in ('agent','tool')


def operation_requires_model(operation):
    plan=operation.get('execution_plan')
    return not plan or any(node_kind(node)=='agent' for node in plan['nodes'])


def package_requires_model(package):
    if package.get('agents'):return True
    if any(unit.get('model_id') or unit.get('crew') for unit in package.get('units',[])):return True
    return any(operation_requires_model(operation) for operation in package['operations'].values())


def schema_at(schema,path):
    if not isinstance(schema,dict):return {}
    segments=path.split('.') if isinstance(path,str) and path else list(path)
    for segment in segments:
        if '$ref' in schema or 'anyOf' in schema:return {}
        if segment=='*':schema=schema.get('items',{})
        elif isinstance(schema.get('properties'),dict) and segment in schema['properties']:schema=schema['properties'][segment]
        elif isinstance(schema.get('additionalProperties'),dict):schema=schema['additionalProperties']
        elif schema.get('additionalProperties') is False:raise ValueError('参数引用路径不在封闭 Schema 中：'+'.'.join(segments))
        else:return {}
        if not isinstance(schema,dict):return {}
    return schema


def schema_types(schema):
    if not isinstance(schema,dict):return set()
    declared=schema.get('type')
    types={declared} if isinstance(declared,str) else set(declared or []) if isinstance(declared,list) else set()
    for option in schema.get('anyOf',[]) if isinstance(schema.get('anyOf'),list) else []:types.update(schema_types(option))
    return types


def compatible_types(source,target):
    left=schema_types(source);right=schema_types(target)
    if not left or not right:return
    expanded=set(left)
    if 'integer' in expanded:expanded.add('number')
    if not expanded&right:raise ValueError('参数引用类型与工具输入 Schema 不匹配')


def map_arguments(value,resolve,depth=0):
    if depth>24:raise ValueError('工具参数嵌套过深')
    if isinstance(value,dict):
        if '$ref' in value:return resolve(value)
        return {key:map_arguments(item,resolve,depth+1) for key,item in value.items()}
    if isinstance(value,list):return [map_arguments(item,resolve,depth+1) for item in value]
    return value


def resolve_tool_arguments(arguments,input_value,delivery):
    def resolve(ref):
        kind=ref['$ref']
        if kind=='input':return values(input_value,ref['path'])[0]
        artifact=delivery(ref['node'],ref['file'],kind)
        if kind=='artifact':return artifact
        return values(artifact,ref['path'])[0]
    return map_arguments(arguments,resolve)


def output_schema(nodes,node,path,artifact=False):
    if path in node['outputs']:return node['outputs'][path]
    if path in node.get('artifact_outputs',{}):
        if artifact:return {}
        raise ValueError('JSON 参数引用必须指向 JSON 文件')
    if path not in node['exports']:raise ValueError('工具参数引用未声明的上游文件')
    matches=[]
    for child in nodes.values():
        if child['parent']!=node['key']:continue
        try:matches.append(output_schema(nodes,child,path,artifact))
        except ValueError:pass
    if len(matches)!=1:raise ValueError('工具参数引用必须对应唯一的组织导出文件')
    return matches[0]


def validate_tool_arguments(arguments,tool,nodes,upstream,input_schema):
    from .contracts import validate
    if len(dumps(arguments).encode())>64000:raise ValueError('工具参数超过大小限制')
    def walk(value,target=tool['input_schema'],depth=0):
        if depth>24:raise ValueError('工具参数嵌套过深')
        if isinstance(value,dict):
            if '$ref' not in value:
                for key,item in value.items():walk(item,schema_at(target,(key,)),depth+1)
                return
            kind=value['$ref']
            allowed={'$ref','path'} if kind=='input' else {'$ref','node','file'} if kind=='artifact' else {'$ref','node','file','path'} if kind=='json' else set()
            if not allowed or set(value)!=allowed:raise ValueError('工具参数引用字段无效')
            if kind=='input':
                path=value['path']
                if not isinstance(path,str) or len(path)>200:raise ValueError('输入引用路径无效')
                source=schema_at(input_schema,path)
            else:
                if not isinstance(value['node'],str) or not isinstance(value['file'],str) or value['node'] not in upstream:raise ValueError('工具参数只能引用明确依赖的上游节点')
                source_node=nodes[value['node']]
                declared=output_schema(nodes,source_node,value['file'],kind=='artifact')
                if kind=='artifact':source={'type':'string'}
                else:
                    path=value['path']
                    if not isinstance(path,str) or len(path)>200:raise ValueError('Artifact JSON 引用路径无效')
                    source=schema_at(declared,path)
            compatible_types(source,target)
        elif isinstance(value,list):
            for item in value:walk(item,schema_at(target,('*',)),depth+1)
    walk(arguments)
    def has_reference(value):
        if isinstance(value,dict):return '$ref' in value or any(has_reference(item) for item in value.values())
        if isinstance(value,list):return any(has_reference(item) for item in value)
        return False
    if not has_reference(arguments):validate(arguments,tool['input_schema'])


def validate_tool_node(node,tool,nodes,upstream,input_schema):
    schema=tool['output_schema']
    if not isinstance(schema,dict) or schema.get('type')!='object':raise ValueError('工具节点输出 Schema 必须为对象')
    required=set(schema.get('required',[]));properties=schema.get('properties',{})
    if node['outputs']:
        files=properties.get('files',{})
        if 'files' not in required or not isinstance(files,dict) or files.get('type')!='object':raise ValueError('工具节点输出 Schema 必须声明 files 对象')
        if not set(node['outputs'])<=set(files.get('required',[])) or not set(node['outputs'])<=set(files.get('properties',{})):
            raise ValueError('工具输出 Schema 必须要求全部节点 JSON 交付文件')
    artifacts=node.get('artifact_outputs',{})
    if artifacts:
        contract=tool.get('artifact_contract')
        files=properties.get('artifacts',{})
        if not contract or 'artifacts' not in required or not isinstance(files,dict) or files.get('type')!='object':raise ValueError('工具输出 Schema 必须声明 artifacts 对象与契约')
        if not set(artifacts)<=set(files.get('required',[])) or not set(artifacts)<=set(files.get('properties',{})):
            raise ValueError('工具输出 Schema 必须要求全部 Artifact 交付文件')
        if len(artifacts)>contract['max_files'] or any(item['media_type'] not in contract['media_types'] for item in artifacts.values()):
            raise ValueError('节点 Artifact 契约超出工具声明')
    if tool['effect']=='create_artifact' and not artifacts:raise ValueError('create_artifact 工具节点必须声明 artifact_outputs')
    validate_tool_arguments(node['arguments'],tool,nodes,upstream,input_schema)


def validate_plan(value,tools=None,input_schema=None):
    """Validate the expanded DAG, including implicit group completion edges."""
    from .verification import artifact_outputs, verification_policy
    plan=ExecutionPlan.model_validate(value).model_dump()
    for node in plan['nodes']:
        for field in ('title','kind','tool_ref','arguments','artifact_outputs','verification'):
            if node[field] is None:node.pop(field)
    if len(dumps(plan))>240000:raise ValueError('完整执行计划超过24万字符')
    nodes={n['key']:n for n in plan['nodes']}
    if len(nodes)!=len(plan['nodes']):raise ValueError('计划节点key重复')
    if plan['result_node'] not in nodes:raise ValueError('计划result_node不存在')
    tools=tools or {};input_schema=input_schema or {}
    from .contracts import check_schema
    from .files import encode_files
    for n in nodes.values():
        kind=node_kind(n);declared=n.get('kind');selected=n['participants']
        if len(selected)!=len(set(selected)) or any(not isinstance(k,str) or not k for k in selected):raise ValueError('参与岗位必须是不重复的 key')
        if n['staff'] and selected:raise ValueError('节点不能同时声明 staff 和 participants')
        if declared=='agent' and not (n['staff'] or selected):raise ValueError('Agent 节点必须声明 staff 或 participants')
        if declared=='tool' and (not n.get('tool_ref') or n.get('arguments') is None):raise ValueError('工具节点必须声明 tool_ref 和 arguments')
        if declared=='group' and (n['staff'] or selected or n['outputs']):raise ValueError('组织节点不能声明执行角色或输出 Schema')
        if declared and declared!='tool' and (n.get('tool_ref') is not None or n.get('arguments') is not None):raise ValueError('只有工具节点可以声明工具字段')
        if not declared and (n.get('tool_ref') is not None or n.get('arguments') is not None):raise ValueError('旧节点不能隐式声明工具字段')
        if kind!='tool' and n.get('artifact_outputs') is not None:raise ValueError('只有工具节点可以声明 artifact_outputs')
        if kind=='tool' and (n['staff'] or selected or n['exports'] or n['gate'] or n['repair_from']):raise ValueError('工具节点不能复用 Agent 或组织汇总字段')
        if n.get('verification') is not None:n['verification']=verification_policy(n['verification'])
        if n['parent'] is not None and (n['parent'] not in nodes or is_execution(nodes[n['parent']])):raise ValueError('计划parent必须是组织节点key')
        if is_execution(n) and not (n['outputs'] or n.get('artifact_outputs')):raise ValueError('执行节点必须声明交付文件与结构约定')
        if n['staff'] and 'image' in n['staff']['capabilities']:raise ValueError('原生JSON固定计划适用于文本员工；图像使用独立出图操作')
        if kind=='group' and not any(x['parent']==n['key'] for x in nodes.values()):raise ValueError('组织节点不能没有下级')
        for path,schema in n['outputs'].items():
            encode_files([dict(path=path,content='')]);check_schema(schema)
            if not path.endswith('.json'):raise ValueError('计划执行交付必须是JSON文件')
        if n.get('artifact_outputs') is not None:
            n['artifact_outputs']=artifact_outputs(n['artifact_outputs'])
            if set(n['outputs'])&set(n['artifact_outputs']):raise ValueError('JSON 与 Artifact 输出路径不能重叠')
            for path in n['artifact_outputs']:encode_files([dict(path=path,data=b'x')])
        if n['gate'] and (kind!='agent' or len(n['outputs'])!=1 or n.get('artifact_outputs')):raise ValueError('审校门必须是单文件 Agent 节点')
        if any(k not in nodes or k==n['key'] for k in n['depends_on']+n['repair_from']):raise ValueError('计划依赖/返工目标必须是其他节点key')
    effective={}
    for key,n in nodes.items():
        deps=set(n['depends_on']);parent=n['parent'];ancestors={key}
        while parent:
            if parent in ancestors:raise ValueError('计划组织层级循环')
            ancestors.add(parent)
            if len(ancestors)>8:raise ValueError('计划最多8层组织')
            deps.update(nodes[parent]['depends_on']);parent=nodes[parent]['parent']
        if deps&ancestors:raise ValueError('计划不能依赖自己或等待自己的上级')
        effective[key]=deps
    edges={key:set(deps) for key,deps in effective.items()}
    for n in nodes.values():
        if n['parent']:edges[n['parent']].add(n['key'])
    done=set()
    while len(done)<len(nodes):
        ready={k for k,v in edges.items() if k not in done and v<=done}
        if not ready:raise ValueError('计划依赖存在循环（含组织汇总与继承依赖）')
        done.update(ready)
    supported={'unique','references','sum_equals_input','same_length','nonempty_references','current_inputs','source_urls','equals_dependency','text_references','scoped_references'}
    for n in nodes.values():
        upstream=set(edges[n['key']]);changed=True
        while changed:
            before=set(upstream)
            for key in list(upstream):upstream.update(edges[key])
            changed=before!=upstream
        if not set(n['repair_from'])<=upstream:raise ValueError('审校返工只能指向本节点的上游')
        if n['repair_from'] and not n['gate']:raise ValueError('只有独立审校门可以声明自动返工范围')
        if n.get('verification'):
            verifier=tools.get(n['verification']['tool_ref'])
            if not verifier or verifier['effect']!='read':raise ValueError('验证策略必须引用只读 Verifier 工具')
        if node_kind(n)=='tool':
            if n['tool_ref'] not in tools:raise ValueError('工具节点引用未声明的工具槽位')
            validate_tool_node(n,tools[n['tool_ref']],nodes,edges[n['key']],input_schema)
        if node_kind(n)=='group':
            available=[path for child in nodes.values() if child['parent']==n['key'] for path in ((list(child['outputs'])+list(child.get('artifact_outputs',{}))) if is_execution(child) else child['exports'])]
            if set(n['exports'])-set(available) or len(n['exports'])!=len(set(n['exports'])) or any(available.count(path)!=1 for path in n['exports']):raise ValueError('组织汇总必须引用唯一的真实下级交付文件')
        if n['gate']:
            schema=next(iter(n['outputs'].values()))
            if not isinstance(schema,dict) or 'passed' not in schema.get('required',[]) or schema.get('properties',{}).get('passed',{}).get('type')!='boolean':raise ValueError('审校门必须要求布尔passed字段')
        for r in n['rules']:
            if not isinstance(r.get('kind'),str) or r['kind'] not in supported:raise ValueError('未知计划校验规则')
            if not isinstance(r.get('file'),str) or r['file'] not in n['outputs']:raise ValueError('校验规则必须指定本节点交付文件')
            required={'unique':['path'],'references':['path','target_path'],'sum_equals_input':['path','input_path'],
                      'same_length':['rows','left','right'],'nonempty_references':['rows','type_field','type_value','reference_field'],
                      'current_inputs':['path'],'source_urls':['path'],'equals_dependency':['path','target_file','target_path'],
                      'text_references':['prefix','target_path'],
                      'scoped_references':['rows','owners','items','scope','groups_path','group_key','items_path','item_key','allowed_field']}[r['kind']]
            if any(not isinstance(r.get(k),str) or not r[k] or len(r[k])>200 for k in required):raise ValueError('校验规则缺少有效字段：'+','.join(required))
            if r['kind']=='text_references' and (not isinstance(r.get('paths'),list) or not r['paths'] or len(r['paths'])>20 or any(not isinstance(p,str) or len(p)>200 for p in r['paths'])):raise ValueError('正文引用校验需要有界paths列表')
    return plan,effective
