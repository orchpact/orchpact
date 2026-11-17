"""Explain frozen reference budgets together, without editing or accepting output."""
import json
import re
from copy import deepcopy
from .kernel.plan import values


def _values(value,path):
    try:return values(value,path)
    except (ValueError,TypeError):return []


def contracts(node,deliveries,output=None):
    if not node:return []
    groups={}
    for rule in node.get('rules',[]):
        if rule.get('kind') not in ('references','text_references'):continue
        file=rule['file']
        if rule.get('target_file',file)!=file:continue
        target=rule['target_path'];group=groups.setdefault((file,target),[]);group.append(rule)
    documents=[]
    for delivery in deliveries:
        for file in delivery.get('files',[]):
            if not file.get('path','').endswith('.json') or file.get('truncated'):continue
            try:
                doc=json.loads(file.get('content',''))
            except (ValueError,TypeError):continue
            documents.append(doc)
    result=[]
    for (file,target),rules in groups.items():
        parts=target.split('.');schema=node.get('outputs',{}).get(file,{})
        limit=schema.get('properties',{}).get(parts[0],{}).get('maxItems') if len(parts)==3 and parts[1]=='*' else None
        item={'file':file,'target_path':target,'max_items':limit,
              'structured_paths':[r['path'] for r in rules if r['kind']=='references'],
              'text_rules':[dict(paths=r['paths'],prefix=r['prefix']) for r in rules if r['kind']=='text_references'],
              'text_paths':list(dict.fromkeys(p for r in rules if r['kind']=='text_references' for p in r['paths'])),
              'dependency_ids':sorted({str(v) for doc in documents for v in _values(doc,target)}),
              'instruction':'先选择符合数量上限且覆盖核心问题的引用集合，再写全部正文与结构化引用，并完整列出集合中每个条目。所有提到的编号都须在本文件引用清单中存在；不能只删除清单条目而保留正文/结构化引用，也不能只补清单而突破上限。未选引用所支撑的内容需据实际已选证据重新表述或明确列为待核实，不能凭空替换编号或删掉研究局限。'}
        doc=(output or {}).get('files',{}).get(file) if isinstance((output or {}).get('files',{}),dict) else None
        if isinstance(doc,dict):
            ids={str(v) for v in _values(doc,target)};refs=set()
            for rule in rules:
                if rule['kind']=='references':refs.update(str(v) for v in _values(doc,rule['path']))
                else:
                    text=' '.join(str(v) for path in rule['paths'] for v in _values(doc,path))
                    refs.update(re.findall(r'(?<![A-Za-z0-9])'+re.escape(rule['prefix'])+r'[1-9][0-9]*(?![0-9])',text))
            required=ids|refs
            item.update(declared_ids=sorted(ids),referenced_ids=sorted(refs),missing_ids=sorted(refs-ids),
                        required_count=len(required),over_budget=limit is not None and len(required)>limit)
            if item['over_budget']:
                item['budget_conflict']=f'保留当前清单及全部引用共需{len(required)}个条目，但冻结约束最多{limit}个；必须同步重写正文、结构化引用与清单，不能在两种错误之间来回修补。'
        result.append(item)
    return result


def selection_outputs(contracts):
    """Request a small, validated authoring scope before an oversized synthesis."""
    return {f'reference-selection-{i}.json':dict(type='object',additionalProperties=False,
        required=['selected_ids','reason'],properties=dict(
            selected_ids=dict(type='array',minItems=1,maxItems=c['max_items'],uniqueItems=True,
                              items=dict(type='string',enum=c['dependency_ids'])),
            reason=dict(type='string',minLength=1,maxLength=1600)))
        for i,c in enumerate(contracts)
        if c['max_items'] is not None and len(c['dependency_ids'])>c['max_items']}


def authoring_node(node,contracts):
    """Constrain the new draft to its chosen sources; never mutate the frozen node."""
    draft=deepcopy(node)
    def field_at(schema,path):
        for part in path.split('.'):
            schema=schema.get('items',{}) if part=='*' else schema.get('properties',{}).get(part,{})
        return schema
    for c in contracts:
        if not c.get('selected_ids'):continue
        schema=draft['outputs'][c['file']]
        for path in [c['target_path']]+c['structured_paths']:
            field=field_at(schema,path)
            if field.get('type')=='string':field['enum']=list(c['selected_ids'])
        for rule in c.get('text_rules',[]):
            allowed='|'.join(re.escape(v) for v in c['selected_ids'])
            forbidden=r'(?<![A-Za-z0-9])(?!(?:'+allowed+r')(?![0-9]))'+re.escape(rule['prefix'])+r'[1-9][0-9]*(?![0-9])'
            pattern=r'^(?![\s\S]*'+forbidden+r')[\s\S]*$'
            for path in rule['paths']:
                field=field_at(schema,path)
                if field.get('type')=='string':field.setdefault('allOf',[]).append(dict(pattern=pattern))
    return draft
