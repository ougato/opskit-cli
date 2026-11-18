"""Login stages consume one budget, including initial command and status calls."""
import json
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import main
from tailscale import cli, server

runner = CliRunner()


@pytest.fixture
def clock(monkeypatch):
    current = [100.0]
    monkeypatch.setattr(server, "_ensure_supported", lambda: None)
    monkeypatch.setattr(server, "command_exists", lambda _: True)
    monkeypatch.setattr(cli.time, "monotonic", lambda: current[0])
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: current.__setitem__(0, current[0] + seconds))
    return current


def test_initial_login_exhaustion_does_not_query_status(clock, monkeypatch):
    def login(**kwargs):
        assert kwargs["timeout"] == 2
        clock[0] += 2
        return "https://login.tailscale.com/a/example"

    monkeypatch.setattr(server, "start_login", login)
    monkeypatch.setattr(server, "_status_json", lambda **kw: pytest.fail("no budget for status"))
    result = runner.invoke(main.app, ["--output", "json", "tailscale", "login", "--timeout", "2"])
    assert result.exit_code == 1
    assert not json.loads(result.stdout)["ok"]


def test_initial_command_and_poll_share_remaining_budget(clock, monkeypatch):
    budgets = []

    def login(**kwargs):
        clock[0] += 1
        return "https://login.tailscale.com/a/example"

    def status(**kwargs):
        budgets.append(kwargs["timeout"])
        if len(budgets) == 1:
            clock[0] += 0.25
            return {"AuthURL": "https://login.tailscale.com/a/example"}
        return {"BackendState": server.TAILSCALE_BACKEND_RUNNING}

    monkeypatch.setattr(server, "start_login", login)
    monkeypatch.setattr(server, "_status_json", status)
    result = runner.invoke(main.app, ["--output", "json", "tailscale", "login", "--wait", "--timeout", "3"])
    assert result.exit_code == 0, result.output
    assert budgets == [2, 1.25]
    assert json.loads(result.stdout)["data"]["logged_in"]


def test_late_logged_in_status_is_not_success(clock, monkeypatch):
    monkeypatch.setattr(server, "start_login", lambda **kw: "")

    def late(**kwargs):
        clock[0] += kwargs["timeout"] + 0.1
        return {"BackendState": server.TAILSCALE_BACKEND_RUNNING}

    monkeypatch.setattr(server, "_status_json", late)
    result = runner.invoke(main.app, ["--output", "json", "tailscale", "login", "--timeout", "2"])
    assert result.exit_code == 1
    assert not json.loads(result.stdout)["ok"]


def test_wait_stops_before_next_query_when_sleep_exhausts_budget(clock, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "start_login", lambda **kw: "")

    def status(**kwargs):
        calls.append(kwargs["timeout"])
        clock[0] += 0.75
        return {"AuthURL": "https://login.tailscale.com/a/example"}

    monkeypatch.setattr(server, "_status_json", status)
    result = runner.invoke(main.app, ["--output", "json", "tailscale", "login", "--wait", "--timeout", "1"])
    assert result.exit_code == 1
    assert len(calls) == 1


def test_explicit_command_failure_is_not_an_authorization_url(monkeypatch):
    monkeypatch.setattr(server, "_run_root", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout="", stderr="synthetic-secret"))
    with pytest.raises(RuntimeError, match="login command failed") as error:
        server.start_login(timeout=1)
    assert "synthetic-secret" not in str(error.value)


def test_real_status_subprocess_uses_supplied_timeout(monkeypatch):
    monkeypatch.setattr(server, "command_exists", lambda _: True)
    run = server._run
    monkeypatch.setattr(server, "_run", lambda command, **kwargs: run(
        [sys.executable, "-c", "import time; time.sleep(10)"], **kwargs))
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        server._status_json(timeout=0.2)
    assert time.monotonic() - started < 3


def test_macos_exit_node_option_is_rejected_before_login(clock, monkeypatch):
    monkeypatch.setattr(server, "_is_darwin", lambda: True)
    monkeypatch.setattr(server, "start_login", lambda **kw: pytest.fail("unsupported flag must not initiate login"))
    result = runner.invoke(main.app, ["--output", "json", "tailscale", "login", "--advertise-exit-node"])
    assert result.exit_code == 2
    assert not json.loads(result.stdout)["ok"]


def test_login_cancellation_does_not_query_status(clock, monkeypatch):
    def cancel(**kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(server, "start_login", cancel)
    monkeypatch.setattr(server, "_status_json", lambda **kw: pytest.fail("cancelled login must not query status"))
    result = runner.invoke(main.app, ["--output", "json", "tailscale", "login", "--wait"])
    assert result.exit_code == 130
