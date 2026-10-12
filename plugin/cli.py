"""Generic plugin routing; business argument parsing belongs to the plugin."""
from __future__ import annotations

from core.plugin import list_manifests
from core.plugin_sandbox import SandboxError, invoke


def run_plugin(name: str, argv: list[str]) -> int:
    from plugin.commands import is_enabled
    matches = [m for m in list_manifests() if m.name == name]
    if len(matches) != 1 or not is_enabled(name):
        raise SandboxError("plugin missing, disabled or identity ambiguous")
    result = invoke(matches[0], "cli", args=argv)
    if result is None:
        return 0
    if not isinstance(result, int) or not 0 <= result <= 255:
        raise SandboxError("plugin CLI must return an exit code from 0 to 255")
    return result
