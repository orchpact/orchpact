"""Shared provider response limits and durable receipt writes."""
import json
import os
from pathlib import Path

from .db import ident


MAX_RESPONSE_BYTES = 32 * 1024 * 1024


def bounded_json(response, maximum=MAX_RESPONSE_BYTES):
    """Reject oversized provider payloads before JSON materialization.

    Transport streaming remains adapter-owned; this check covers both declared and
    actual response sizes and gives every adapter the same deterministic limit.
    """
    declared = response.headers.get('content-length')
    if declared:
        try:
            if int(declared) > maximum:
                raise ValueError('供应商响应超过大小限制')
        except ValueError as exc:
            if str(exc) == '供应商响应超过大小限制':
                raise
            raise ValueError('供应商响应长度无效') from None
    raw = response.content
    if len(raw) > maximum:
        raise ValueError('供应商响应超过大小限制')
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('供应商响应不是有效JSON') from exc


def write_receipt(path, value):
    """Atomically persist a receipt before attempting settlement."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + ident() + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
