"""Portable tool declarations and installation binding inputs."""
import json
import re
from typing import Literal

from pydantic import Field, model_validator

from .contracts import check_schema
from .files import MAX_BYTES, MAX_FILES
from .models import Strict

SLOT = r'[A-Za-z][A-Za-z0-9_-]{0,63}'
PROVIDER = r'[A-Za-z0-9][A-Za-z0-9_:-]{0,79}'


def validate_version(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', value) or value.casefold() in ('latest', '*'):
        raise ValueError('工具接口或实现版本无效')
    return value


def check_tool_schema(schema):
    if not isinstance(schema, dict) or len(json.dumps(schema, ensure_ascii=False, allow_nan=False)) > 64_000:
        raise ValueError('工具 Schema 须为不超过6.4万字符的 JSON 对象')

    def walk(value, depth=0):
        if depth > 24:
            raise ValueError('工具 Schema 嵌套过深')
        if isinstance(value, dict):
            for item in value.values():
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)

    walk(schema)
    check_schema(schema)


class ToolArtifactContract(Strict):
    media_types: list[str] = Field(default_factory=list, max_length=20)
    max_files: int = Field(default=1, ge=1, le=MAX_FILES)
    max_bytes: int = Field(default=MAX_BYTES, ge=1, le=MAX_BYTES)

    @model_validator(mode='after')
    def valid_media_types(self):
        if len(self.media_types) != len(set(self.media_types)) or any(not re.fullmatch(r'[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+', item) for item in self.media_types):
            raise ValueError('工具 Artifact 媒体类型无效')
        return self


class ToolDefinition(Strict):
    interface: str = Field(pattern=r'[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+', max_length=120)
    version: str = Field(min_length=1, max_length=80)
    description: str = Field(default='', max_length=4000)
    input_schema: dict
    output_schema: dict
    effect: Literal['read', 'create_artifact', 'external_write']
    timeout_ms: int = Field(default=30_000, ge=1, le=120_000)
    max_output_bytes: int = Field(default=1_048_576, ge=1, le=MAX_BYTES)
    retry_policy: Literal['never', 'safe_only'] = 'never'
    idempotency: Literal['none', 'key_required'] = 'none'
    required_permissions: list[str] = Field(default_factory=list, max_length=8)
    artifact_contract: ToolArtifactContract | None = None

    @model_validator(mode='after')
    def valid_definition(self):
        validate_version(self.version)
        check_tool_schema(self.input_schema)
        check_tool_schema(self.output_schema)
        if len(self.required_permissions) != len(set(self.required_permissions)) or any(not re.fullmatch(r'[a-z][a-z0-9_-]*(:[a-z][a-z0-9_-]*)+', item) for item in self.required_permissions):
            raise ValueError('工具权限声明无效')
        if self.effect == 'create_artifact' and self.artifact_contract is None:
            raise ValueError('产物工具必须声明 artifact_contract')
        return self


class ToolBindingInput(Strict):
    endpoint_ref: str = Field(pattern=PROVIDER)
    implementation_version: str = Field(min_length=1, max_length=80)

    @model_validator(mode='after')
    def valid_version(self):
        validate_version(self.implementation_version)
        return self


def validate_tool_definitions(value):
    if not isinstance(value, dict) or len(value) > 64:
        raise ValueError('工具能力槽位数量无效')
    result = {}
    for key, requirement in value.items():
        if not isinstance(key, str) or not re.fullmatch(SLOT, key):
            raise ValueError('工具能力槽位名称无效')
        result[key] = ToolDefinition.model_validate(requirement).model_dump()
    return result


def bind_tool_definitions(value, requirements, choose):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) != set(requirements):
        raise ValueError('工具绑定须恰好覆盖工作流声明的能力槽位')
    result = {}
    for slot, requirement in requirements.items():
        binding = ToolBindingInput.model_validate(value[slot]).model_dump()
        result[slot] = choose(requirement, binding)
    return result
