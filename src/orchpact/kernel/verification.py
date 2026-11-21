"""Bounded artifact and verification contracts shared by publication and acceptance."""
from hashlib import sha256
import json
import re
from typing import Literal

from pydantic import Field, model_validator

from .files import MAX_BYTES
from .models import Strict

MEDIA = r'[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+'
CHECK = r'[a-z][a-z0-9_-]{0,79}'


def canonical_digest(value):
    raw=json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
    return sha256(raw).hexdigest()


class ArtifactOutput(Strict):
    media_type: str = Field(pattern=MEDIA)
    max_bytes: int = Field(default=MAX_BYTES,ge=1,le=MAX_BYTES)


class VerificationPolicy(Strict):
    tool_ref: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    required_checks: list[str] = Field(min_length=1,max_length=32)
    parameters: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def valid_policy(self):
        if len(self.required_checks)!=len(set(self.required_checks)) or any(not re.fullmatch(CHECK,item) for item in self.required_checks):
            raise ValueError('验证检查标识无效或重复')
        if len(json.dumps(self.parameters,ensure_ascii=False,allow_nan=False).encode())>16000:
            raise ValueError('验证参数超过大小限制')
        def walk(value):
            if isinstance(value,dict):
                if '$ref' in value:raise ValueError('验证参数只允许字面 JSON')
                for item in value.values():walk(item)
            elif isinstance(value,list):
                for item in value:walk(item)
        walk(self.parameters)
        return self


class VerifierMetadata(Strict):
    checks: list[str] = Field(min_length=1,max_length=64)
    media_types: list[str] = Field(min_length=1,max_length=20)
    trust_level: Literal['trusted','untrusted']
    environment: str = Field(min_length=1,max_length=200)

    @model_validator(mode='after')
    def valid_metadata(self):
        if len(self.checks)!=len(set(self.checks)) or any(not re.fullmatch(CHECK,item) for item in self.checks):
            raise ValueError('Verifier 检查标识无效或重复')
        if len(self.media_types)!=len(set(self.media_types)) or any(not re.fullmatch(MEDIA,item) for item in self.media_types):
            raise ValueError('Verifier 媒体类型无效')
        return self


class CheckResult(Strict):
    check_id: str = Field(pattern=CHECK)
    status: Literal['PASS','FAIL','INCONCLUSIVE']
    code: str | None = Field(default=None,max_length=120)
    node_key: str | None = Field(default=None,pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')
    artifact_path: str | None = Field(default=None,max_length=160)
    path: str | None = Field(default=None,max_length=160)
    field_path: str | None = Field(default=None,max_length=200)
    message: str = Field(default='',max_length=4000)
    repair: str = Field(default='',max_length=4000)


class VerifierResult(Strict):
    status: Literal['PASS','FAIL','INCONCLUSIVE']
    checks: list[CheckResult] = Field(min_length=1,max_length=64)
    evidence: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def valid_result(self):
        identifiers=[item.check_id for item in self.checks]
        if len(identifiers)!=len(set(identifiers)):raise ValueError('验证检查标识重复')
        if len(json.dumps(self.evidence,ensure_ascii=False,allow_nan=False).encode())>64000:
            raise ValueError('验证证据元数据超过大小限制')
        return self


def artifact_outputs(value):
    if not isinstance(value,dict) or not value or len(value)>12:raise ValueError('Artifact 输出声明无效')
    return {path:ArtifactOutput.model_validate(contract).model_dump() for path,contract in value.items()}


def verification_policy(value):
    return VerificationPolicy.model_validate(value).model_dump()


def verifier_metadata(value):
    if value is None:return None
    return VerifierMetadata.model_validate(value).model_dump()


def verifier_result(value):
    return VerifierResult.model_validate(value).model_dump()
