"""Software command wiring remains independent of the main entry module."""
from types import SimpleNamespace

import typer
from typer.testing import CliRunner

from software.direct_cli import register


def test_software_group_uses_injected_host_at_command_execution():
    app = typer.Typer()
    calls = []
    host = SimpleNamespace(_sw_action_by_name=lambda *args, **kwargs: calls.append((args, kwargs)))
    exports = register(app, lambda key: key, host)
    host._sw_action_by_name = lambda *args, **kwargs: calls.append(("replacement", args, kwargs))

    result = CliRunner().invoke(app, ["switch", "python", "--version", "3.12"])

    assert result.exit_code == 0, result.exception
    assert calls == [("replacement", ("python", "switch"), {"version": "3.12"})]
    assert callable(exports[0])
