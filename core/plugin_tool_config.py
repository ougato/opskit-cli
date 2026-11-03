"""Host-owned named tool paths. Configuration never grants execution permission."""
import re

from core.config import configuration_transaction, load_config, set_config_value


def _name(name):
    if not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) is None:
        raise ValueError("invalid Creator tool name")


def creators():
    config = load_config()
    tools = config.get("host_tools", {})
    if not isinstance(tools, dict):
        raise ValueError("invalid host tools configuration")
    values = tools.get("creators", {})
    if not isinstance(values, dict) or len(values) > 32:
        raise ValueError("invalid Creator tool configuration")
    for name, path in values.items():
        _name(name)
        if not isinstance(path, str) or not path or len(path) > 32768 or any(ord(c) < 32 for c in path):
            raise ValueError("invalid configured Creator path")
    return dict(values)


def configure_creator(name, executable):
    from core.plugin_host_tools import _ordinary
    import os
    _name(name)
    tool = _ordinary(executable)
    if os.name != "nt" or tool.name.casefold() not in {"creator.exe", "cocoscreator.exe"}:
        raise ValueError("unsupported Creator executable")
    with configuration_transaction():
        values = creators()
        values[name] = str(tool)
        if len(values) > 32:
            raise ValueError("Creator configuration capacity exhausted")
        set_config_value({}, "host_tools.creators", values)
    return {"name": name, "executable": str(tool), "authorized": False}


def remove_creator(name):
    _name(name)
    with configuration_transaction():
        values = creators()
        if name not in values:
            raise ValueError("Creator configuration missing")
        del values[name]
        set_config_value({}, "host_tools.creators", values)
    return {"name": name, "removed": True}


def resolve_creator(value):
    if isinstance(value, str) and value.startswith("@creator/"):
        name = value[len("@creator/"):]
        _name(name)
        values = creators()
        if name not in values:
            raise ValueError("Creator configuration missing")
        return values[name]
    return value
