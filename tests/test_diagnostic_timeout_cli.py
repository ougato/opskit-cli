"""All official diagnostic entry points forward budgets and preserve failures."""
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import main
from core import platform
from software import registry


CASES = [
    ("rustdesk", "software.recipes.rustdesk.server", "diagnostic_report", []),
    ("tailscale", "tailscale.diagnostics", "report", ["tailscale", "diagnose"]),
    ("xui", "xui.diagnostics", "report", ["xui", "diagnose"]),
    ("wg_server", "wireguard.diagnostics", "server_report", ["wireguard", "server", "diagnose"]),
    ("wg_client", "wireguard.diagnostics", "client_report", ["wireguard", "client", "diagnose"]),
]


@pytest.fixture
def supported(monkeypatch):
    from tailscale import server
    monkeypatch.setattr(platform, "get_platform", lambda: SimpleNamespace(os_type="linux"))
    monkeypatch.setattr(server, "_ensure_supported", lambda: None)
    monkeypatch.setattr(server, "command_exists", lambda *args: True)
    monkeypatch.setattr(main, "_boot", lambda: pytest.fail("named diagnostic must not boot"))


@pytest.mark.parametrize("name,module,function,direct", CASES)
@pytest.mark.parametrize("value", [None, "3.5"])
def test_every_recipe_and_dedicated_cli_forwards_budget(monkeypatch, supported, name, module, function, direct, value):
    import importlib
    calls = []
    def report(*args, timeout):
        calls.append(timeout)
        return {"checks": [{"name": "budget", "ok": True}]}
    monkeypatch.setattr(importlib.import_module(module), function, report)
    commands = [["software", "diagnose", name]] + ([direct] if direct else [])
    for command in commands:
        args = ["--output", "json", *command] + (["--timeout", value] if value else [])
        result = CliRunner().invoke(main.app, args)
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["ok"]
    assert calls == [30 if value is None else 3.5] * len(commands)


@pytest.mark.parametrize("name,module,function,direct", CASES)
@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_or_cancel_cannot_report_success(monkeypatch, supported, name, module, function, direct, cancel):
    import importlib
    def failed(*args, **kwargs):
        if cancel:
            raise KeyboardInterrupt()
        raise TimeoutError("diagnostic deadline exceeded")
    monkeypatch.setattr(importlib.import_module(module), function, failed)
    commands = [["software", "diagnose", name]] + ([direct] if direct else [])
    for command in commands:
        result = CliRunner().invoke(main.app, ["--output", "json", *command, "--timeout", "1"])
        assert result.exit_code == (130 if cancel else 1), result.output
        if not cancel:
            payload = json.loads(result.stdout)
            assert not payload["ok"] and payload["error"]["code"] == 1


@pytest.mark.parametrize("value", ["0", "-1", "3601"])
def test_invalid_budget_rejected_before_report(monkeypatch, supported, value):
    cls = registry.get("xui")
    monkeypatch.setattr(cls, "diagnostic_report", lambda *a, **k: pytest.fail("invalid budget entered report"))
    result = CliRunner().invoke(main.app, ["--output", "json", "software", "diagnose", "xui", "--timeout", value])
    assert result.exit_code == 2
    assert not json.loads(result.stdout)["ok"]
