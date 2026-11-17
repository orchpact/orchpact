"""Strict JSON decoding with narrowly bounded terminal-delimiter recovery."""
import json


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('重复JSON字段：' + key)
            result[key] = value
        return result

    def finite(value):
        raise ValueError('JSON不允许非有限数值：' + value)

    def load(text):
        return json.loads(text, object_pairs_hook=unique, parse_constant=finite)

    try:
        return load(raw), ''
    except json.JSONDecodeError as error:
        if error.msg == 'Extra data':
            # A complete object/array followed only by extra copies of its
            # root delimiter is unambiguous. Never discard prose, a second
            # value, a mismatched delimiter, or anything inside the value.
            value = load(raw[:error.pos])
            closing = '}' if isinstance(value, dict) else ']' if isinstance(value, list) else None
            trailing = raw[error.pos:]
            delimiters = trailing.translate(str.maketrans('', '', ' \t\r\n'))
            if closing and 1 <= len(delimiters) <= 32 and set(delimiters) == {closing}:
                return value, dict(kind='redundant_terminal_delimiters', removed=trailing)
            raise
        # Only EOF outside a string can be completed. Never change content,
        # repair an interior error or infer a value.
        if error.pos < len(raw.rstrip()):
            raise
        stack = []
        quoted = escaped = False
        for char in raw:
            if quoted:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in '{[':
                stack.append('}' if char == '{' else ']')
            elif char in '}]':
                if not stack or stack.pop() != char:
                    raise error
        if quoted or not stack or len(stack) > 32:
            raise error
        suffix = ''.join(reversed(stack))
        # Empty objects, absent array members and missing values are not
        # recoverable: a complete value must immediately precede the suffix.
        if not raw.rstrip() or raw.rstrip()[-1] in '{[:,':
            raise error
        return load(raw + suffix), suffix
