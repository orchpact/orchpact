"""Bounded, local JSON contracts. Never resolve remote schema resources."""
import json
import math

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing.exceptions import Unresolvable, NoSuchResource
from referencing import Registry

def reject_remote(uri):
    raise NoSuchResource(ref=uri)

LOCAL_REGISTRY=Registry(retrieve=reject_remote)


def check_schema(schema):
    if not isinstance(schema, dict) or len(json.dumps(schema)) > 200_000:
        raise ValueError("结构约定必须是有界 JSON Schema 对象")

    def walk(value, depth=0):
        if depth > 48:
            raise ValueError("结构约定嵌套过深")
        if isinstance(value, dict):
            if any(k in value and (not isinstance(value[k], str) or not value[k].startswith('#'))
                   for k in ('$ref', '$dynamicRef')):
                raise ValueError("结构约定只允许本地引用")
            for item in value.values():
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)

    walk(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise ValueError("无效 JSON Schema") from None


def validate(value, schema):
    check_schema(schema)
    try:
        error = next(Draft202012Validator(schema,registry=LOCAL_REGISTRY).iter_errors(value), None)
        if error:
            path = '.'.join(map(str, error.absolute_path)) or '<root>'
            detail = ''
            if error.validator == 'required' and isinstance(error.instance, dict):
                missing = [key for key in error.validator_value if key not in error.instance]
                detail = '，缺少字段' + json.dumps(missing, ensure_ascii=False)[:500]
            elif error.validator == 'additionalProperties':
                detail = '，允许字段' + json.dumps(list(error.schema.get('properties', {})), ensure_ascii=False)[:500]
            raise ValueError(f"输入/交付不符合结构约定：字段{path}，规则{error.validator}{detail}")
    except (Unresolvable, RecursionError):
        raise ValueError("结构约定包含无法解析或无限递归的引用") from None


def read_json_delivery(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("交付 JSON 包含重复字段")
            result[key] = value
        return result

    def reject(value):
        raise ValueError("JSON 不允许 NaN/Infinity")

    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            reject(value)
        return number

    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=reject, parse_float=finite)
    except (UnicodeError, json.JSONDecodeError, RecursionError, TypeError):
        raise ValueError("交付不是有效 JSON 原文") from None


def output_model(node):
    """Use native JSON objects for files; the platform performs serialization exactly once."""

    contract=dict(type='object',additionalProperties=False,required=['files'],properties=dict(
        summary=dict(type='string',maxLength=4000),
        files=dict(type='object',additionalProperties=False,required=list(node['outputs']),properties={path:{} for path in node['outputs']})))
    class StageOutput:
        @classmethod
        def document_schemas(cls):return node['outputs']
        @classmethod
        def model_json_schema(cls):return contract
        @classmethod
        def model_validate(cls,value):
            validate(value,contract)
            # Validate each document as its own resource: local $defs/$ref must
            # not accidentally resolve against the outer transport envelope.
            for path,schema in node['outputs'].items():
                try:validate(value['files'][path],schema)
                except ValueError as error:
                    raise ValueError('交付文件 files[' + json.dumps(path, ensure_ascii=False) + ']：' + str(error)) from None
            instance=cls();instance.value=value;return instance
        def model_dump(self):return self.value
    return StageOutput
