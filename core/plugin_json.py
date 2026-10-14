"""Unambiguous finite JSON values for the sandbox wire protocol."""
import json
import math
import re

RPC_VERSION = 1
MAX_DEPTH = 64
MAX_VALUES = 131072
MAX_ATOM = 4300
MAX_TEXT = 32 * 1024**2
_ATOM_END = re.compile(r'[ \t\r\n,\[\]{}:"]')


def _preflight(text):
    """Bound the object parser before it allocates; scan strings without copies."""
    if not isinstance(text, str):
        raise TypeError("JSON transport requires text")
    if len(text) > MAX_TEXT:
        raise ValueError("JSON text exceeds limit")
    stack = []
    values = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char in " \t\r\n,:":
            index += 1
            continue
        if char in "[{":
            stack.append(char)
            if len(stack) > MAX_DEPTH:
                raise ValueError("JSON nesting exceeds limit")
            values += 1
            index += 1
        elif char in "]}":
            if not stack or (stack.pop(), char) not in (("[", "]"), ("{", "}")):
                raise ValueError("invalid JSON nesting")
            index += 1
        elif char == '"':
            values += 1
            cursor = index + 1
            while True:
                quote = text.find('"', cursor)
                if quote < 0:
                    raise ValueError("unterminated JSON string")
                preceding = quote - 1
                while preceding >= cursor and text[preceding] == "\\":
                    preceding -= 1
                if (quote - 1 - preceding) % 2 == 0:
                    index = quote + 1
                    break
                cursor = quote + 1
        else:
            values += 1
            end = _ATOM_END.search(text, index)
            next_index = end.start() if end else len(text)
            if next_index - index > MAX_ATOM:
                raise ValueError("JSON numeric token exceeds limit")
            index = next_index
        if values > MAX_VALUES:
            raise ValueError("JSON values exceed limit")
    if stack:
        raise ValueError("invalid JSON nesting")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("non-finite JSON number")


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def loads(text):
    _preflight(text)
    return json.loads(text, object_pairs_hook=_object, parse_constant=_constant,
                      parse_float=_float)


def dumps(value, *, default=None):
    text = json.dumps(value, allow_nan=False, default=default)
    _preflight(text)
    return text


def require_version(value):
    if (not isinstance(value, dict) or type(value.get("rpc_version")) is not int
            or value["rpc_version"] != RPC_VERSION):
        raise ValueError("unsupported RPC protocol version")
    return value


def loads_message(text):
    return require_version(loads(text))


def require_result(value):
    require_version(value)
    fields = set(value)
    if value.get("type") != "result":
        raise ValueError("invalid result envelope")
    if fields == {"rpc_version", "type", "value", "state"} and isinstance(value["state"], str):
        return value
    if (fields == {"rpc_version", "type", "error"}
            and isinstance(value["error"], str) and value["error"]):
        return value
    raise ValueError("invalid result envelope")


def require_reply(value):
    require_version(value)
    fields = set(value)
    if fields == {"rpc_version", "value"}:
        return value
    if fields == {"rpc_version", "cancel"} and value["cancel"] is True:
        return value
    if (fields == {"rpc_version", "error"}
            and isinstance(value["error"], str) and value["error"]):
        return value
    raise ValueError("invalid reply envelope")


def dumps_message(value, *, default=None):
    if not isinstance(value, dict) or "rpc_version" in value:
        raise ValueError("RPC version belongs to the transport")
    return dumps({"rpc_version": RPC_VERSION, **value}, default=default)
