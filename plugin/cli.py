"""Generic plugin routing; business argument parsing belongs to the plugin."""
from __future__ import annotations

from core.plugin import list_manifests
from core.plugin_sandbox import SandboxError, _execution_budget, _remaining, invoke


def run_plugin(name: str, argv: list[str]) -> int:
    # Discovery and invocation share one deadline and supervised reader. invoke
    # rechecks enabled/trust itself; do not synchronously reload config here.
    with _execution_budget():
        matches = [m for m in list_manifests() if m.name == name]
        _remaining()
        if len(matches) != 1:
            raise SandboxError("plugin missing or identity ambiguous")
        result = invoke(matches[0], "cli", args=argv)
    if result is None:
        return 0
    if not isinstance(result, int) or not 0 <= result <= 255:
        raise SandboxError("plugin CLI must return an exit code from 0 to 255")
    return result
