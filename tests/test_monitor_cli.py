"""Public monitoring options and bounded sampling behavior."""
import json
import pytest
from typer.testing import CliRunner

import main
from monitor import cli, commands

runner = CliRunner()


@pytest.mark.parametrize("name,expected", [
    ("dashboard", set()), ("cpu", set()), ("memory", set()),
    ("disk", {"--mount"}), ("network", {"--interface"}),
    ("processes", {"--pid", "--limit", "--sort"}),
])
def test_help_only_advertises_applicable_filters(name, expected):
    result = runner.invoke(main.app, ["monitor", name, "--help"])
    assert result.exit_code == 0, result.output
    # Strip Rich/ANSI presentation; Click parameters are the authoritative option list.
    import typer.main
    root = typer.main.get_command(main.app)
    registered = root.commands["monitor"].commands[name]
    options = {option for param in registered.params for option in param.opts}
    filters = {"--pid", "--limit", "--sort", "--mount", "--interface"}
    assert options & filters == expected
    assert {"--once", "--watch", "--count", "--interval"} <= options


@pytest.mark.parametrize("name,option,value", [
    ("cpu", "--pid", "1"), ("memory", "--limit", "15"),
    ("disk", "--interface", "eth0"), ("processes", "--mount", "/"),
])
def test_inapplicable_filter_rejected_even_with_default_value(name, option, value):
    result = runner.invoke(main.app, ["--output", "json", "monitor", name, option, value])
    assert result.exit_code == 2
    assert not json.loads(result.stdout)["ok"]


@pytest.mark.parametrize("name,option,collector", [
    ("network", "--interface", "get_net"), ("disk", "--mount", "get_disks"),
])
def test_unknown_filter_is_not_reported_as_success(monkeypatch, name, option, collector):
    monkeypatch.setattr(commands, collector, lambda: [])
    result = runner.invoke(main.app, ["--output", "json", "monitor", name, option, "missing"])
    assert result.exit_code == 2
    assert not json.loads(result.stdout)["ok"]


def test_watch_count_is_finite_and_cancellation_exits_130(monkeypatch):
    monkeypatch.setattr(commands, "get_mem", lambda: {"total": 1})
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    result = runner.invoke(main.app, ["--output", "json", "monitor", "memory", "--watch", "--count", "3"])
    assert result.exit_code == 0
    assert len(result.stdout.splitlines()) == 3

    def cancel(_):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli.time, "sleep", cancel)
    result = runner.invoke(main.app, ["--output", "json", "monitor", "memory", "--watch"])
    assert result.exit_code == 130
    assert len(result.stdout.splitlines()) == 1


def test_pid_reads_target_without_enumerating_all_processes(monkeypatch):
    def forbidden(*args):
        pytest.fail("PID query must not enumerate/sort all processes")

    monkeypatch.setattr(commands, "get_top_processes", forbidden)
    monkeypatch.setattr(commands, "get_process", lambda pid: commands.ProcessEntry(pid, "target", 0, 0, "running"))
    result = runner.invoke(main.app, ["--output", "json", "monitor", "processes", "--pid", "123", "--once"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["data"][0]["pid"] == 123


@pytest.mark.parametrize("error", ["NoSuchProcess", "AccessDenied"])
def test_pid_disappeared_or_unreadable_reports_distinct_failure(monkeypatch, error):
    import psutil

    def inaccessible(pid):
        raise getattr(psutil, error)(pid)

    monkeypatch.setattr(psutil, "Process", inaccessible)
    result = runner.invoke(main.app, ["--output", "json", "monitor", "processes", "--pid", "123", "--once"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False and payload["data"][0]["pid"] == 123
    assert payload["data"][0]["rate_status"] == ("gone" if error == "NoSuchProcess" else "access_denied")
    assert payload["data"][0]["cpu_percent"] is None


def test_real_pid_collector():
    import os
    process = commands.get_process(os.getpid())
    assert process is not None and process.pid == os.getpid()


@pytest.mark.parametrize("options", [
    ["--limit", "15"], ["--limit=1"], ["--sort", "cpu"],
    ["--sort=memory"], ["--limit", "15", "--sort", "cpu"],
])
def test_pid_rejects_explicit_list_options_before_sampling(monkeypatch, options):
    def forbidden(*args, **kwargs):
        pytest.fail("conflicting options must fail before sampling")
    monkeypatch.setattr(commands, "get_process", forbidden)
    monkeypatch.setattr(commands, "get_top_processes", forbidden)
    result = runner.invoke(main.app, ["--output", "json", "monitor", "processes",
                                      "--pid", "123", *options])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == 2 and payload["data"] is None
    assert "--pid" in payload["error"]["message"]


def test_explicit_default_list_options_remain_valid_without_pid(monkeypatch):
    seen = []
    monkeypatch.setattr(commands, "get_top_processes", lambda limit, sort: seen.append((limit, sort)) or [])
    result = runner.invoke(main.app, ["--output", "json", "monitor", "processes",
                                      "--limit", "15", "--sort", "cpu", "--once"])
    assert result.exit_code == 0, result.output
    assert seen == [(15, "cpu")]
