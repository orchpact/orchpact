"""File contracts shared by every planner and execution mode."""
import json
from ..adapters.database import loads
from ..kernel.contracts import read_json_delivery, validate
from ..kernel.plan import values, is_execution

class DeliveryContracts:
    def __init__(self, service):
        self.s = service

    def node(self, c, task):
        node = loads(task['definition'])
        return node if is_execution(node) else None

    def documents(self, c, task):
        docs = {}
        for row in c.execute('SELECT t.artifact_id FROM dependencies d JOIN tasks t ON t.id=d.depends_on WHERE d.task_id=%s', (task['id'],)):
            if not row['artifact_id']:
                continue
            for f in self.s.artifacts.read_files(self.s.row(c, 'artifacts', row['artifact_id'])):
                if f['path'].endswith('.json'):
                    value = read_json_delivery(f['data'])
                    if f['path'] in docs and docs[f['path']] != value:
                        raise ValueError('上游同名文件内容冲突：' + f['path'])
                    docs[f['path']] = value
        return docs

    def check(self,c,task,files):
        node=self.node(c,task)
        binding = c.execute('SELECT binding,root_task_id FROM invocations WHERE id=%s', (task['project_id'],)).fetchone()
        if binding:
            root = binding['root_task_id'] == task['id']
            # A fixed-plan node owns its declared files. Independent branches can use
            # the same filename with different contracts; the root checks its chosen result.
            if root or not loads(task['definition']).get('key'):
                operation = loads(binding['binding'])
                self.s.result(operation, files, required=root and task['status'] != 'partial')
        if not node:
            for file in files:
                if file['path'].endswith('.json'):
                    read_json_delivery(file.get('content', file.get('data')))
            return
        from ..kernel.contracts import validate
        from .tool_artifacts import inspect_media
        actual={f['path']:f for f in files}
        expected=set(node['outputs'])|set(node.get('artifact_outputs',{}))
        if set(actual)!=expected or len(actual)!=len(files):raise ValueError('阶段交付文件必须恰好为：'+','.join(sorted(expected)))
        for path,contract in node.get('artifact_outputs',{}).items():
            data=actual[path].get('data')
            if data is None:data=actual[path]['content'].encode('utf-8')
            if len(data)>contract['max_bytes']:raise ValueError('Artifact 超过节点大小限制：'+path)
            inspect_media(data,contract['media_type'],path)
        docs=self.documents(c,task)
        for path,schema in node['outputs'].items():
            f=actual[path];raw=f.get('content')
            if raw is None:raw=f['data'].decode('utf-8')
            value=self.s.result(dict(output_file=path,output_schema=schema),[dict(path=path,content=raw)])
            docs[path]=value
        row=c.execute('SELECT input FROM invocations WHERE id=%s',(task['project_id'],)).fetchone()
        original=loads(row['input']) if row else {}
        for rule in node['rules']:
            doc=docs[rule['file']];kind=rule['kind'];path=rule.get('path','')
            vals=values(doc,path) if path else []
            if kind=='unique':
                if len(vals)!=len(set(vals)):raise ValueError(path+'编号重复')
            elif kind=='sum_equals_input':
                expected=values(original,rule['input_path'])[0]
                if sum(vals)!=expected:raise ValueError(f'{path}合计必须为{expected}，实际为{sum(vals)}')
            elif kind=='references':
                target=docs.get(rule.get('target_file',rule['file']))
                if target is None:raise ValueError('引用校验缺少上游文件')
                missing=set(vals)-set(values(target,rule['target_path']))
                if missing:raise ValueError(path+'引用不存在：'+','.join(str(v) for v in sorted(missing,key=str))[:200])
            elif kind=='same_length':
                rows=values(doc,rule['rows'])
                if any(len(values(r,rule['left'])[0])!=len(values(r,rule['right'])[0]) for r in rows):raise ValueError('关联列表数量不一致：'+rule['rows'])
            elif kind=='equals_dependency':
                target=docs.get(rule['target_file'])
                if target is None or vals!=values(target,rule['target_path']):raise ValueError(path+'必须原样保留已验收上游文件'+rule['target_file']+'的'+rule['target_path'])
            elif kind=='text_references':
                import re
                text=' '.join(str(v) for p in rule['paths'] for v in values(doc,p))
                refs=set(re.findall(r'(?<![A-Za-z0-9])'+re.escape(rule['prefix'])+r'[1-9][0-9]*(?![0-9])',text))
                missing=refs-set(values(doc,rule['target_path']))
                if missing:raise ValueError('正文引用不存在：'+','.join(sorted(missing))[:200])
            elif kind=='scoped_references':
                target=docs.get(rule.get('target_file',rule['file']))
                if target is None:raise ValueError('关联归属校验缺少上游文件')
                index={}
                for group in values(target,rule['groups_path']):
                    for item in values(group,rule['items_path']):index[item[rule['item_key']]]=(group[rule['group_key']],item[rule['allowed_field']])
                for position,row in enumerate(values(doc,rule['rows'])):
                    owners=row[rule['owners']];items=row[rule['items']];scope=row[rule['scope']]
                    resolved=[index.get(item) for item in items]
                    if len(items)!=len(owners) or len(set(owners))!=len(owners) or any(v is None or scope not in v[1] for v in resolved) or {v[0] for v in resolved if v}!=set(owners):raise ValueError(f"{rule['rows']}[{position}]关联归属或适用范围不符：{rule['owners']}/{rule['items']}/{rule['scope']}")
            elif kind=='nonempty_references':
                for r in values(doc,rule['rows']):
                    if r.get(rule['type_field'])==rule['type_value'] and not r.get(rule['reference_field']):raise ValueError(rule['rows']+'事实必须有证据引用')
            elif kind=='current_inputs':
                expected={r[0] for r in c.execute('SELECT t.artifact_id FROM dependencies d JOIN tasks t ON t.id=d.depends_on WHERE d.task_id=%s AND t.artifact_id IS NOT NULL',(task['id'],))}
                if not expected<=set(vals):raise ValueError('审校记录必须包含全部本次最新依赖artifact_id')
            elif kind=='source_urls':
                urls=set()
                from ..search import normalize
                for request in c.execute("SELECT * FROM requests WHERE project_id=%s AND kind='search' AND status='completed'",(task['project_id'],)):
                    receipt=self.s.settings.data/'receipts'/request['attempt_id']/(request['id']+'.json')
                    if not receipt.is_file():continue
                    raw=loads(receipt.read_text())
                    if raw.get('operation')=='fetch':urls.add(raw['query'])
                    else:urls.update(s['url'] for s in normalize(raw['provider'],raw.get('response',{}),100))
                missing=set(vals)-urls
                if missing:raise ValueError(path+'包含本轮真实检索回执之外的URL：'+str(sorted(missing))[:250])
