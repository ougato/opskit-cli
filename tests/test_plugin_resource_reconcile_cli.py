import json

import pytest
from typer.testing import CliRunner

from core import plugin_resources, plugin_sandbox
from core.cli import configure


@pytest.mark.parametrize("pending,invalid,code", [(0, 0, 0), (1, 0, 1), (0, 1, 1)])
def test_reconcile_reports_recovery_status_under_total_budget(tmp_path, monkeypatch, pending, invalid, code):
    import main
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(plugin_sandbox, "docker_command", lambda: ["docker"])
    def recover(command, control):
        assert command == ["docker"]
        assert plugin_sandbox._remaining() > 0
        return {"recovered": 2, "active": 1, "pending": pending, "invalid": invalid}
    monkeypatch.setattr(plugin_resources, "reconcile", recover)
    try:
        result = CliRunner().invoke(main.app, ["--output", "json", "plugin", "reconcile"])
        assert result.exit_code == code, result.output
        value = json.loads(result.output)
        assert value["ok"] == (code == 0)
        assert value["data"]["active"] == 1
        assert value["data"]["recovered"] == 2
    finally:
        configure("text", False)
