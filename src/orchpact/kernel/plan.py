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


def is_execution(node):
    return bool(node['staff'] or node.get('participants'))

def validate_plan(value):
    """Validate the expanded DAG, including implicit group completion edges."""
    plan=ExecutionPlan.model_validate(value).model_dump()
    if len(dumps(plan))>240000:raise ValueError('完整执行计划超过24万字符')
    nodes={n['key']:n for n in plan['nodes']}
    if len(nodes)!=len(plan['nodes']):raise ValueError('计划节点key重复')
    if plan['result_node'] not in nodes:raise ValueError('计划result_node不存在')
    from .contracts import check_schema
    from .files import encode_files
    for n in nodes.values():
        selected=n['participants']
        if len(selected)!=len(set(selected)) or any(not isinstance(k,str) or not k for k in selected):raise ValueError('参与岗位必须是不重复的 key')
        if n['staff'] and selected:raise ValueError('节点不能同时声明 staff 和 participants')
        if n['parent'] is not None and (n['parent'] not in nodes or is_execution(nodes[n['parent']])):raise ValueError('计划parent必须是组织节点key')
        if is_execution(n) and not n['outputs']:raise ValueError('员工节点必须声明交付文件与结构约定')
        if n['staff'] and 'image' in n['staff']['capabilities']:raise ValueError('原生JSON固定计划适用于文本员工；图像使用独立出图操作')
        if not is_execution(n) and not any(x['parent']==n['key'] for x in nodes.values()):raise ValueError('组织节点不能没有下级')
        for path,schema in n['outputs'].items():
            encode_files([dict(path=path,content='')]);check_schema(schema)
            if not path.endswith('.json'):raise ValueError('计划文本交付必须是JSON文件')
        if n['gate'] and (not is_execution(n) or len(n['outputs'])!=1):raise ValueError('审校门必须是单文件员工节点')
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
        if not is_execution(n):
            available=[path for child in nodes.values() if child['parent']==n['key'] for path in (child['outputs'] if is_execution(child) else child['exports'])]
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
