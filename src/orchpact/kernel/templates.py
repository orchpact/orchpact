"""Fixed author-side workflow templates; previews remain ordinary portable definitions."""
from copy import deepcopy
import re

from .components import expand_components, substitute
from .contracts import check_schema, validate
from .tools import validate_version
from .workflows import definition_digest, validate_definition

FORMAT = 'orchpact-workflow-template'
FIELDS = {'format', 'template_id', 'version', 'name', 'description', 'parameters_schema', 'definition'}
IDENTIFIER = r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}'

JSON_DELIVERY = {
    'format': FORMAT,
    'template_id': 'json-delivery',
    'version': '1.0.0',
    'name': 'JSON 成果交付',
    'description': '一个处理组和固定交付节点，生成可由现有目录发布、绑定和安装的普通工作流。',
    'parameters_schema': {
        'type': 'object', 'additionalProperties': False,
        'required': ['workflow_id', 'workflow_name', 'instruction'],
        'properties': {
            'workflow_id': {'type': 'string', 'title': '工作流标识', 'description': '小写字母开头的稳定目录标识。', 'pattern': '^[a-z][a-z0-9_-]{0,79}$', 'default': 'my-workflow'},
            'workflow_version': {'type': 'string', 'title': '作者版本', 'default': '1.0.0', 'minLength': 1, 'maxLength': 80},
            'workflow_name': {'type': 'string', 'title': '工作流名称', 'minLength': 1, 'maxLength': 120},
            'unit_name': {'type': 'string', 'title': '处理组名称', 'default': '处理组', 'minLength': 1, 'maxLength': 100},
            'instruction': {'type': 'string', 'title': '交付指令', 'description': '说明需要完成的工作；不要填写账号、凭据或实例绑定。', 'minLength': 1, 'maxLength': 6000},
            'request_limit': {'type': 'integer', 'title': '单节点模型步骤上限', 'default': 32, 'minimum': 1, 'maximum': 256},
        },
    },
    'definition': {
        'format': 'orchpact-workflow',
        'id': {'$param': 'workflow_id'},
        'name': {'$param': 'workflow_name'},
        'version': {'$param': 'workflow_version'},
        'resources': {'models': {'reasoning': {'capabilities': ['text', 'json']}}},
        'units': [{'key': 'entry', 'name': {'$param': 'unit_name'}, 'purpose': '按指令交付约定结果', 'model': 'reasoning'}],
        'operations': {'generate': {
            'unit': 'entry',
            'instruction': {'$param': 'instruction'},
            'input_schema': {'type': 'object'},
            'output_schema': {'type': 'object'},
            'output_file': 'result.json',
            'request_limit': {'$param': 'request_limit'},
            'execution_plan': {'result_node': 'deliver', 'nodes': [{
                'key': 'deliver', 'unit': 'entry', 'description': '按指令生成 JSON 成果', 'expected': 'result.json',
                'staff': {'name': '交付执行者', 'role': '按输入和指令生成约定 JSON 成果', 'reason': '执行固定交付节点', 'capabilities': ['text', 'json'], 'model': 'reasoning'},
                'outputs': {'result.json': {'type': 'object'}},
            }]},
        }},
    },
}
TEMPLATES = {JSON_DELIVERY['template_id']: JSON_DELIVERY}


def check_form_schema(schema):
    check_schema(schema)
    def walk(value, path='parameters'):
        if not isinstance(value, dict) or value.get('type') not in ('object', 'string', 'integer', 'number', 'boolean', 'array'):
            raise ValueError('模板表单 Schema 缺少受支持的 type：' + path)
        kind = value['type']
        if kind == 'object':
            if value.get('additionalProperties') is not False or not isinstance(value.get('properties'), dict):
                raise ValueError('模板对象参数必须声明 properties 且 additionalProperties=false：' + path)
            for name, child in value['properties'].items():
                if not isinstance(name, str) or not re.fullmatch(IDENTIFIER, name):
                    raise ValueError('模板参数名无效：' + path)
                walk(child, path + '.' + name)
        elif kind == 'array':
            if not isinstance(value.get('items'), dict) or value['items'].get('type') not in ('string', 'integer', 'number', 'boolean'):
                raise ValueError('模板数组只支持标量 items：' + path)
            walk(value['items'], path + '[]')
    walk(schema)


def validate_template(template):
    if not isinstance(template, dict) or set(template) - FIELDS:
        raise ValueError('工作流模板包含不支持的字段')
    if template.get('format') != FORMAT or not isinstance(template.get('template_id'), str) or not re.fullmatch(IDENTIFIER, template['template_id']):
        raise ValueError('工作流模板 format 或 template_id 无效')
    validate_version(template.get('version'))
    if not isinstance(template.get('name'), str) or not 1 <= len(template['name']) <= 120 or not isinstance(template.get('description'), str) or len(template['description']) > 2000:
        raise ValueError('工作流模板名称或说明无效')
    check_form_schema(template.get('parameters_schema'))
    if not isinstance(template.get('definition'), dict) or template['definition'].get('format') not in ('orchpact-workflow', 'orchpact-component-workflow'):
        raise ValueError('模板 definition 必须是工作流或组件宿主')
    optional=deepcopy(template['parameters_schema'])
    def strip_required(schema):
        schema.pop('required',None)
        for child in schema.get('properties',{}).values():strip_required(child)
    strip_required(optional)
    validate(defaults(template['parameters_schema'], {}), optional)
    return template


def metadata(template):
    validate_template(template)
    return {key: deepcopy(template[key]) for key in ('template_id', 'version', 'name', 'description', 'parameters_schema')} | {'sha256': definition_digest(template)}


def defaults(schema, value):
    result = deepcopy(value)
    if not isinstance(result, dict):
        return result
    for name, child in schema.get('properties', {}).items():
        if name not in result and 'default' in child:
            result[name] = deepcopy(child['default'])
        if name in result and child.get('type') == 'object':
            result[name] = defaults(child, result[name])
    return result


def list_templates():
    return [metadata(template) for template in TEMPLATES.values()]


def compile_template(template, parameters, components_dir=None):
    validate_template(template)
    resolved = defaults(template['parameters_schema'], parameters)
    validate(resolved, template['parameters_schema'])
    source = substitute(deepcopy(template['definition']), resolved)
    report = None
    if source['format'] == 'orchpact-component-workflow':
        if components_dir is None:
            raise ValueError('组件模板预览需要显式本地组件目录')
        definition, report = expand_components(source, components_dir)
    else:
        definition = validate_definition(source)
    return {
        'template': metadata(template),
        'parameters': resolved,
        'definition': definition,
        'sha256': definition_digest(definition),
        'component_report': report,
    }


def preview_template(template_id, expected_sha256, parameters):
    if template_id not in TEMPLATES:
        raise KeyError(template_id)
    template = TEMPLATES[template_id]
    if expected_sha256 != metadata(template)['sha256']:
        raise ValueError('模板版本或内容摘要已变化，请重新选择模板')
    return compile_template(template, parameters)
