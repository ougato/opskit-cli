"""Partial traceroute output must not imply a successful destination probe."""
import json
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from core import process
from network import commands


def invoke():
    import main
    return CliRunner().invoke(main.app, ["--output", "json", "network", "traceroute", "192.0.2.1"])


@pytest.mark.parametrize("line,code,status,exit_code", [
    (" 1 192.0.2.1 1 ms", 0, "reached", 0),
    (" 1 <1 ms <1 ms <1 ms 192.0.2.1", 0, "reached", 0),
    ("traceroute to 192.0.2.1", 0, "not_reached", 1),
    (" 1 * * *", 0, "not_reached", 1),
    (" 1 192.0.2.1 1 ms !H", 0, "not_reached", 1),
    (" 1 192.0.2.1 1 ms", 7, "failed", 1),
])
def test_actual_process_terminal_status_and_cleanup(monkeypatch, line, code, status, exit_code):
    original = process.stream
    children = []
    def synthetic(command, **kwargs):
        kwargs["process_ref"] = children
        return original([sys.executable, "-c", "print(" + repr(line) + ",flush=True); raise SystemExit(" + str(code) + ")"], **kwargs)
    monkeypatch.setattr(process, "stream", synthetic)
    result = invoke()
    assert result.exit_code == exit_code, result.output
    records = [json.loads(item) for item in result.stdout.splitlines()]
    assert records[0]["data"]["hop"] == line
    assert records[-1]["data"]["event"] == "complete"
    assert records[-1]["data"]["status"] == status
    assert records[-1]["ok"] is (exit_code == 0)
    assert len(children) == 1 and children[0].poll() is not None
    assert children[0].stdout.closed


@pytest.mark.parametrize("failure,status,exit_code", [
    (subprocess.TimeoutExpired(["synthetic"], 1), "timeout", 1),
    (KeyboardInterrupt(), "cancelled", 130),
    (FileNotFoundError(), "failed", 1),
])
def test_partial_output_has_error_terminal_record(monkeypatch, failure, status, exit_code):
    def stream(*args, **kwargs):
        yield " 1 * * *"
        raise failure
    monkeypatch.setattr(process, "stream", stream)
    result = invoke()
    assert result.exit_code == exit_code
    terminal = json.loads(result.stdout.splitlines()[-1])
    assert terminal["data"]["status"] == status
    assert terminal["error"]["code"] == exit_code


def test_dns_and_command_share_deadline_and_fixed_target(monkeypatch):
    from network import resolver
    clock = [100.0]
    monkeypatch.setattr(commands.time, "monotonic", lambda: clock[0])
    def resolve(host, **kwargs):
        assert kwargs["timeout"] == 3
        clock[0] += 2
        return ["2001:db8::1"]
    def stream(command, **kwargs):
        assert command[-1] == "2001:db8::1"
        assert kwargs["timeout"] == 1
        yield " 1 2001:db8::1 1 ms"
        return 0
    monkeypatch.setattr(resolver, "resolve", resolve)
    monkeypatch.setattr(process, "stream", stream)
    iterator = commands.traceroute("example.invalid", timeout=3, strict=True)
    assert next(iterator) == " 1 2001:db8::1 1 ms"
    with pytest.raises(StopIteration) as done:
        next(iterator)
    assert done.value.value["status"] == "reached"


def test_expired_dns_does_not_launch_command(monkeypatch):
    from network import resolver
    clock = [100.0]
    monkeypatch.setattr(commands.time, "monotonic", lambda: clock[0])
    def resolve(host, **kwargs):
        clock[0] += 3
        return ["192.0.2.1"]
    monkeypatch.setattr(resolver, "resolve", resolve)
    monkeypatch.setattr(process, "stream", lambda *a, **kw: pytest.fail("expired DNS launched process"))
    with pytest.raises(TimeoutError, match="traceroute deadline"):
        list(commands.traceroute("example.invalid", timeout=3, strict=True))
