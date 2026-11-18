import json
import pytest
from typer.testing import CliRunner
from core.cli import configure

runner = CliRunner()

@pytest.fixture(autouse=True)
def reset_contract():
    configure("text", False)
    yield
    configure("text", False)


def test_monitor_json_once():
    import main
    result = runner.invoke(main.app, ["--output", "json", "monitor", "memory", "--once"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] and payload["data"]["total"] > 0


def test_monitor_count_json():
    import main
    result = runner.invoke(main.app, ["--output", "json", "monitor", "memory", "--count", "2", "--interval", "0.1"])
    assert result.exit_code == 0
    assert len([json.loads(line) for line in result.stdout.splitlines()]) == 2


def test_monitor_conflict_returns_json_error():
    import main
    result = runner.invoke(main.app, ["--output", "json", "monitor", "cpu", "--once", "--watch"])
    assert result.exit_code == 2
    assert not json.loads(result.stdout)["ok"]


def test_noninteractive_root_rejects_menu():
    import main
    result = runner.invoke(main.app, ["--non-interactive"])
    assert result.exit_code == 2


def test_noninteractive_input_guard():
    import typer
    from core.prompt import text_input
    configure("json", True)
    with pytest.raises(typer.Exit) as error:
        text_input([], "secret")
    assert error.value.exit_code == 2


@pytest.mark.parametrize("args", [
    ["monitor", "memory", "--count", "-1"],
    ["monitor", "memory", "--interval", "not-a-number"],
    ["software", "switch", "python"],
    ["unknown-command"],
    ["--unknown-root-option"],
])
def test_parser_failure_has_one_json_error(args):
    import main
    result = runner.invoke(main.app, ["--output", "json", *args])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == 2 and not payload["ok"]


def test_parser_error_does_not_echo_sensitive_argument():
    import main
    result = runner.invoke(main.app, ["--output=json", "monitor", "memory", "--count", "synthetic-secret"])
    assert result.exit_code == 2
    assert "synthetic-secret" not in result.output
    assert not json.loads(result.stdout)["ok"]


def test_plugin_output_flag_is_not_interpreted_as_root_flag(monkeypatch):
    import main
    from plugin import cli
    def run(name, args):
        assert name == "synthetic" and args == ["--output", "json"]
        print("plugin-owned-output")
        return 0
    monkeypatch.setattr(cli, "run_plugin", run)
    result = runner.invoke(main.app, ["plugin", "run", "synthetic", "--", "--output", "json"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "plugin-owned-output"


def test_root_version_json():
    import main
    from core.constants import APP_VERSION
    result = runner.invoke(main.app, ["--output", "json", "--version"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["data"] == {"version": APP_VERSION}


def test_unexpected_query_failure_is_json_without_exception_values(monkeypatch):
    import main
    from software import query
    def broken_inventory(**kwargs):
        raise KeyError("synthetic-secret")
    monkeypatch.setattr(query, "inventory", broken_inventory)
    result = runner.invoke(main.app, ["--output", "json", "software", "list"])
    assert result.exit_code == 1
    assert "synthetic-secret" not in result.output
    assert json.loads(result.stdout)["error"]["code"] == 1
