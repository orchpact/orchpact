"""Compile-time JSON components; the runtime receives only ordinary workflow definitions."""
from copy import deepcopy
import json
from pathlib import Path
import re

from .contracts import check_schema, validate
from .tools import validate_version
from .workflows import definition_digest, validate_definition

IDENTIFIER=r'[A-Za-z][A-Za-z0-9_-]{0,63}'
COMPONENT_ID=r'[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+'
AUTHOR_FIELDS={'format','id','name','version','description','components','workflow'}
COMPONENT_FIELDS={'format','component_id','version','description','parameters_schema','inputs','outputs','resources','units','agents','operation','components'}
INSTANCE_FIELDS={'use','sha256','with','inputs','ports'}
PLACEHOLDER_FIELDS={'component','inputs','ports','depends_on','parent','outputs','artifact_outputs','repair_from'}


def fields(value,allowed,label):
    if not isinstance(value,dict) or set(value)-allowed:raise ValueError(label+' 包含不支持的字段')


def prefixed(value,prefix):
    if not value:raise ValueError('组件命名空间不能为空')
    result=prefix+'__'+value
    if not re.fullmatch(IDENTIFIER,result):raise ValueError('组件命名空间或标识过长：'+result)
    return result


def namespace_values(value,prefix,local_keys=None):
    if isinstance(value,dict):
        result={}
        for key,item in value.items():
            if key in ('unit','model','tool_ref') and isinstance(item,str):item=prefixed(item,prefix)
            elif key in ('parent','result_node','node','node_key') and isinstance(item,str) and (local_keys is None or item in local_keys):item=prefixed(item,prefix)
            elif key in ('depends_on','repair_from','participants') and isinstance(item,list):item=[prefixed(entry,prefix) if local_keys is None or entry in local_keys else entry for entry in item]
            elif key=='capabilities' and isinstance(item,list):item=[prefix+entry[5:] if isinstance(entry,str) and entry.startswith('tool:') else entry for entry in item]
            else:item=namespace_values(item,prefix,local_keys)
            result[key]=item
        return result
    if isinstance(value,list):return [namespace_values(item,prefix,local_keys) for item in value]
    return value


def namespace_node(node,prefix,local_keys=None):
    result=deepcopy(node);result['key']=prefixed(result['key'],prefix)
    return namespace_values(result,prefix,local_keys)


def namespace_unit(unit,prefix):
    result=deepcopy(unit);result['key']=prefixed(result['key'],prefix)
    if (result.get('crew') or {}).get('members'):
        result['crew']['members']=[dict(member,key=prefixed(member['key'],prefix)) for member in result['crew']['members']]
    return namespace_values(result,prefix)


def namespace_resources(resources,prefix):
    result=deepcopy(resources or {})
    for kind in ('models','tools'):
        if kind in result:result[kind]={prefixed(key,prefix):value for key,value in result[kind].items()}
    return result


def substitute(value,parameters,path=''):
    if isinstance(value,dict):
        if '$param' in value:
            if set(value)-{'$param','path'}:raise ValueError('参数引用包含其他字段：'+path)
            name=value['$param']
            if not isinstance(name,str) or not re.fullmatch(IDENTIFIER,name):raise ValueError('参数名称无效：'+path)
            current=parameters
            for segment in [name]+(value.get('path','').split('.') if value.get('path') else []):
                if not isinstance(current,dict) or segment not in current:raise ValueError('参数引用不存在：'+name)
                current=current[segment]
            return deepcopy(current)
        return {key:substitute(item,parameters,path+'.'+key) for key,item in value.items()}
    if isinstance(value,list):return [substitute(item,parameters,path) for item in value]
    return value


def parameter_value(value,path):
    current=value
    for segment in path.split('.') if path else []:
        if not isinstance(current,dict) or segment not in current:raise ValueError('参数路径不存在：'+path)
        current=current[segment]
    return current


def input_schema(operation,inputs):
    schema=deepcopy(operation.get('input_schema',{'type':'object','additionalProperties':False}))
    if not inputs:return schema
    if schema.get('type')!='object':raise ValueError('组件 input_schema 必须为对象')
    properties=schema.setdefault('properties',{});required=set(schema.get('required',[]))
    for name,declaration in inputs.items():
        properties[name]=deepcopy(declaration.get('schema',{'type':'object'}) if declaration['kind']=='json' else {'type':'string'})
        required.add(name)
    schema['required']=sorted(required)
    return schema


class ComponentExpander:
    def __init__(self,directory):
        self.directory=Path(directory).resolve()
        self.count=0;self.components=[];self.source_map=[];self.active=set()
        self.extra_resources={'models':{},'tools':{}};self.extra_units=[];self.extra_agents=[];self.operation_aliases={}

    def load(self,use):
        parts=use.split('@')
        if len(parts)!=2 or not re.fullmatch(COMPONENT_ID,parts[0]):raise ValueError('组件引用必须精确为 component_id@version')
        validate_version(parts[1])
        path=self.directory/(use+'.json')
        if not path.is_file():raise ValueError('组件不存在：'+use)
        value=json.loads(path.read_text())
        if len(json.dumps(value,ensure_ascii=False,allow_nan=False))>1000000:raise ValueError('组件源文件超过大小限制')
        fields(value,COMPONENT_FIELDS,'组件定义')
        if value.get('format')!='orchpact-component' or value.get('component_id')!=parts[0] or value.get('version')!=parts[1]:
            raise ValueError('组件文件名与内部标识或版本不一致')
        return value

    def instances(self,value):
        if not isinstance(value,dict):raise ValueError('components 必须是对象')
        for alias,config in value.items():
            if not re.fullmatch(IDENTIFIER,alias) or '__' in alias:raise ValueError('组件实例名无效：'+str(alias))
            fields(config,INSTANCE_FIELDS,'组件实例')
            if not isinstance(config.get('use'),str) or not re.fullmatch(r'[a-f0-9]{64}',config.get('sha256','')):
                raise ValueError('组件实例必须固定 use 和 canonical SHA-256')
            if not isinstance(config.get('with',{}),dict) or not isinstance(config.get('inputs',{}),dict) or not isinstance(config.get('ports',{}),dict):
                raise ValueError('组件 with/inputs/ports 必须为对象')
            for path,port in config.get('ports',{}).items():
                if not isinstance(path,str) or not isinstance(port,str):raise ValueError('组件输出端口映射无效')
        return value

    def expand(self,author):
        fields(author,AUTHOR_FIELDS,'组件宿主')
        if author.get('format')!='orchpact-component-workflow':raise ValueError('宿主 format 必须为 orchpact-component-workflow')
        workflow=deepcopy(author['workflow']);fields(workflow,{'resources','units','agents','operations'},'宿主工作流')
        instances=self.instances(author.get('components',{}))
        for name,operation in workflow['operations'].items():
            if 'execution_plan' in operation:operation['execution_plan']=self.expand_operation(operation['execution_plan'],instances,'',name,True)
        resources=workflow.setdefault('resources',{'models':{}})
        for kind,items in self.extra_resources.items():
            if items:
                target=resources.setdefault(kind,{})
                if set(target)&set(items):raise ValueError('组件资源命名空间冲突：'+kind)
                target.update(items)
        if self.extra_units:workflow['units']=workflow.get('units',[])+self.extra_units
        if self.extra_agents:workflow['agents']=workflow.get('agents',[])+self.extra_agents
        result=dict(format='orchpact-workflow',id=author['id'],name=author['name'],version=author['version'],**workflow)
        if 'description' in author:result['description']=author['description']
        checked=validate_definition(result)
        report=dict(format='orchpact-component-build',expander_version='1',components=self.components,
            source_map=self.source_map,definition_sha256=definition_digest(checked))
        return checked,report

    def expand_operation(self,plan,instances,prefix,operation_name,resolve_inputs):
        result=deepcopy(plan);nodes=[];aliases={};normal_keys=set()
        for node in result['nodes']:
            if 'component' not in node:
                nodes.append(node);normal_keys.add(node['key']);continue
            fields(node,PLACEHOLDER_FIELDS,'组件占位节点')
            alias=node['component']
            if alias not in instances:raise ValueError('占位节点引用未声明的组件实例：'+alias)
            expanded,mapping=self.expand_placeholder(node,instances[alias],alias,prefix,operation_name,resolve_inputs)
            nodes.extend(expanded)
            for path,source in mapping.items():aliases.setdefault(alias,{})[path]=source
        result['nodes']=nodes
        self.operation_aliases[prefix]=aliases
        result=self.rewrite_aliases(result,aliases)
        if prefix:
            result['nodes']=[namespace_node(node,prefix,normal_keys) if node['key'] in normal_keys else node for node in result['nodes']]
            if result.get('unit'):result['unit']=prefixed(result['unit'],prefix)
            if result.get('result_node') in normal_keys:result['result_node']=prefixed(result['result_node'],prefix)
        return result

    def expand_placeholder(self,placeholder,config,alias,prefix,operation_name,resolve_inputs):
        namespace=prefixed(alias,prefix) if prefix else alias
        existing_resources={kind:set(self.extra_resources[kind]) for kind in ('models','tools')}
        existing_units={unit['key'] for unit in self.extra_units};existing_agents={agent['name'] for agent in self.extra_agents}
        component=self.expand_component(config,namespace,operation_name)
        operation=component['operation'];nodes=operation['execution_plan']['nodes'];by_key={node['key']:node for node in nodes}
        ports=config.get('ports',{})
        if set(placeholder.get('outputs',{}))&set(placeholder.get('artifact_outputs',{})):raise ValueError('组件占位的 JSON 与 Artifact 输出路径不能重叠')
        expected=set(placeholder.get('outputs',{}))|set(placeholder.get('artifact_outputs',{}))
        if set(ports)!=expected:raise ValueError('组件 ports 必须恰好覆盖占位输出文件')
        mapping={};file_map={}
        for path,port in ports.items():
            if port not in component['outputs']:raise ValueError('组件输出端口不存在：'+port)
            reference=component['outputs'][port]
            fields(reference,{'node','file'},'组件输出声明')
            source=reference['node'];source_file=reference['file']
            if source not in by_key:raise ValueError('组件输出引用不存在的节点')
            node=by_key[source]
            if source_file in node['outputs']:contract=node['outputs'][source_file];kind='outputs'
            elif source_file in node.get('artifact_outputs',{}):contract=node['artifact_outputs'][source_file];kind='artifact_outputs'
            else:raise ValueError('组件输出引用未声明的文件')
            if placeholder.get(kind,{}).get(path)!=contract:raise ValueError('宿主与组件输出契约不一致：'+path)
            mapping[path]=source;file_map[(source,source_file)]=path
        component['resources']=self.rewrite_tool_outputs(component['resources'],operation,file_map)
        if resolve_inputs:operation=self.outer_inputs(operation,config.get('inputs',{}),component['inputs'])
        operation=self.rewrite_files(operation,file_map)
        for kind in ('models','tools'):
            if existing_resources[kind]&set(component['resources'][kind]):raise ValueError('组件资源命名空间冲突：'+kind)
            self.extra_resources[kind].update({key:value for key,value in component['resources'][kind].items() if key not in existing_resources[kind]})
        component_unit_keys={unit['key'] for unit in component['units']};component_agent_names={agent['name'] for agent in component['agents']}
        if existing_units&component_unit_keys or existing_agents&component_agent_names:raise ValueError('组件 unit 或 Agent 命名空间冲突')
        current_units={unit['key'] for unit in self.extra_units};current_agents={agent['name'] for agent in self.extra_agents}
        self.extra_units.extend(unit for unit in component['units'] if unit['key'] not in current_units)
        self.extra_agents.extend(agent for agent in component['agents'] if agent['name'] not in current_agents)
        dependencies=placeholder.get('depends_on',[])
        for node in operation['execution_plan']['nodes']:
            if not node.get('depends_on'):node['depends_on']=list(dependencies)
        self.source_map.extend(dict(operation=operation_name,node=node['key'],component_id=component['component_id'],
            version=component['version'],component_sha256=component['sha256'],instance=namespace,
            source_node=component['source_nodes'][node['key']]) for node in operation['execution_plan']['nodes'] if node['key'] in component['source_nodes'])
        return operation['execution_plan']['nodes'],mapping

    def rewrite_outputs(self,outputs,namespace):
        result=deepcopy(outputs)
        for reference in result.values():
            alias=reference['node'];mapped=self.operation_aliases.get(namespace,{})
            reference['node']=mapped[alias][reference['file']] if alias in mapped else prefixed(alias,namespace)
        return result

    def rewrite_tool_outputs(self,resources,operation,file_map):
        result=deepcopy(resources)
        for node in operation['execution_plan']['nodes']:
            slot=node.get('tool_ref')
            if not slot:continue
            mappings={key:path for key,path in file_map.items() if key[0]==node['key']}
            if not mappings:continue
            schema=result['tools'][slot]['output_schema']
            for channel in ('files','artifacts'):
                envelope=schema.get('properties',{}).get(channel)
                if not envelope:continue
                envelope['required']=[mappings.get((node['key'],path),path) for path in envelope.get('required',[])]
                envelope['properties']={mappings.get((node['key'],path),path):contract for path,contract in envelope.get('properties',{}).items()}
        return result

    def expand_component(self,config,namespace,operation_name):
        use=config['use']
        if use in self.active:raise ValueError('组件循环引用：'+use)
        self.active.add(use)
        source=deepcopy(self.load(use));digest=definition_digest(source)
        if digest!=config['sha256']:raise ValueError('组件内容摘要与实例声明不一致：'+use)
        if self.count>=32:raise ValueError('组件实例数量超过32个')
        if len(namespace.split('__'))>4:raise ValueError('组件嵌套超过4层')
        self.count+=1
        parameters=source.get('parameters_schema',{'type':'object','additionalProperties':False})
        check_schema(parameters);validate(config.get('with',{}),parameters)
        source=substitute(source,config.get('with',{}))
        inputs=source.get('inputs',{})
        if not isinstance(inputs,dict):raise ValueError('组件 inputs 必须为对象')
        for name,declaration in inputs.items():
            if not re.fullmatch(IDENTIFIER,name):raise ValueError('组件输入端口名无效')
            fields(declaration,{'kind','schema'},'组件输入端口')
            if declaration['kind'] not in ('json','artifact'):raise ValueError('组件输入端口类型无效')
            if declaration['kind']=='json':check_schema(declaration.get('schema',{'type':'object'}))
        if set(config.get('inputs',{}))!=set(inputs):raise ValueError('组件实例必须恰好绑定全部输入端口')
        nested=self.instances(source.get('components',{}))
        start_units=len(self.extra_units);start_agents=len(self.extra_agents)
        start_keys={kind:set(self.extra_resources[kind]) for kind in ('models','tools')}
        operation=deepcopy(source['operation'])
        source_nodes={prefixed(node['key'],namespace):node['key'] for node in operation['execution_plan']['nodes'] if 'component' not in node}
        operation['input_schema']=input_schema(operation,inputs)
        operation['execution_plan']=self.expand_operation(operation['execution_plan'],nested,namespace,operation_name,False)
        if operation.get('unit'):operation['unit']=prefixed(operation['unit'],namespace)
        resources=namespace_resources(source.get('resources',{}),namespace)
        units=[namespace_unit(unit,namespace) for unit in source.get('units',[])]
        agents=namespace_values(source.get('agents',[]),namespace)
        outputs=self.rewrite_outputs(source.get('outputs',{}),namespace)
        standalone_resources=deepcopy(resources)
        for kind in ('models','tools'):
            standalone_resources[kind].update({key:value for key,value in self.extra_resources[kind].items() if key not in start_keys[kind]})
        standalone=dict(format='orchpact-workflow',id=source['component_id'].replace('.','-'),name=source.get('name',source['component_id']),
            version=source['version'],resources=standalone_resources,units=units+self.extra_units[start_units:],operations={'component':operation})
        if agents or self.extra_agents[start_agents:]:standalone['agents']=agents+self.extra_agents[start_agents:]
        validate_definition(standalone)
        resources=standalone_resources;units=standalone['units'];agents=standalone.get('agents',agents)
        self.components.append(dict(instance=namespace,component_id=source['component_id'],version=source['version'],sha256=digest))
        self.active.remove(use)
        return dict(component_id=source['component_id'],version=source['version'],sha256=digest,
            operation=operation,inputs=inputs,outputs=outputs,resources=resources,units=units,agents=agents,source_nodes=source_nodes)

    def outer_inputs(self,operation,bindings,declarations):
        def walk(value):
            if isinstance(value,dict):
                if value.get('$ref')=='input':
                    path=value.get('path')
                    if not isinstance(path,str) or not path:raise ValueError('组件输入引用需要非空 path')
                    port,_,nested=path.partition('.')
                    if port not in declarations or port not in bindings:raise ValueError('组件输入端口未绑定：'+port)
                    reference=deepcopy(bindings[port]);kind=declarations[port]['kind']
                    expected={'$ref','node','file','path'} if kind=='json' else {'$ref','node','file'}
                    if set(reference)!=expected or reference['$ref']!=kind:raise ValueError('组件输入端口引用类型不符：'+port)
                    if kind=='artifact' and nested:raise ValueError('Artifact 输入不能追加 JSON 路径')
                    if nested:reference['path']=(reference.get('path','')+'.' if reference.get('path') else '')+nested
                    return reference
                return {key:walk(item) for key,item in value.items()}
            if isinstance(value,list):return [walk(item) for item in value]
            return value
        result=deepcopy(operation);result['execution_plan']=walk(result['execution_plan']);return result

    def rewrite_files(self,operation,file_map):
        def exported(source,name):
            if (source,name) in file_map:return file_map[(source,name)]
            matches={target for (node,old),target in file_map.items() if old==name}
            return matches.pop() if len(matches)==1 else name
        def walk(value,source):
            if isinstance(value,dict):
                if '$ref' in value and 'file' in value:
                    value=deepcopy(value);value['file']=file_map.get((source,value['file']),value['file']);return value
                result={}
                for key,item in value.items():
                    if key in ('outputs','artifact_outputs') and isinstance(item,dict):item={file_map.get((source,name),name):contract for name,contract in item.items()}
                    elif key=='exports' and isinstance(item,list):item=[exported(source,name) for name in item]
                    elif key in ('file','target_file') and isinstance(item,str):item=file_map.get((source,item),item)
                    else:item=walk(item,source)
                    result[key]=item
                return result
            if isinstance(value,list):return [walk(item,source) for item in value]
            return value
        result=deepcopy(operation)
        result['execution_plan']['nodes']=[walk(node,node['key']) for node in result['execution_plan']['nodes']]
        return result

    def rewrite_aliases(self,plan,aliases):
        def sources(alias,path=None):
            if alias not in aliases:return [alias]
            if path is not None and path in aliases[alias]:return [aliases[alias][path]]
            values=sorted(set(aliases[alias].values()))
            if not values:raise ValueError('组件输出引用不存在：'+alias)
            return values
        def single(alias,path=None):
            values=sources(alias,path)
            if len(values)!=1:raise ValueError('组件引用必须指向唯一输出节点：'+alias)
            return values[0]
        def walk(value,key=None):
            if isinstance(value,dict):
                if '$ref' in value and 'node' in value:
                    value=deepcopy(value);value['node']=single(value['node'],value.get('file'));return value
                result={}
                for name,item in value.items():
                    if name in ('depends_on','repair_from') and isinstance(item,list):item=sorted({source for alias in item for source in sources(alias)})
                    elif name in ('parent','result_node') and isinstance(item,str):item=single(item)
                    else:item=walk(item,name)
                    result[name]=item
                return result
            if isinstance(value,list):return [walk(item,key) for item in value]
            return value
        return walk(plan)


def expand_components(author,components_dir):
    return ComponentExpander(components_dir).expand(author)
