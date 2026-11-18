"""Network adapters must supervise silent commands, including menu cancellation."""
import sys
import time
import pytest

from core import process
from network import commands


def test_ping_silent_deadline_reaps_process(monkeypatch):
    original = process.stream

    def silent(cmd, **kwargs):
        return original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)

    monkeypatch.setattr(process, "stream", silent)
    ref = []
    started = time.monotonic()
    result = commands.ping("127.0.0.1", kill_ref=ref, timeout=0.2)
    assert not result.reachable
    assert time.monotonic() - started < 5
    assert len(ref) == 1 and ref[0].poll() is not None


def test_traceroute_generator_close_reaps_process(monkeypatch):
    original = process.stream

    def output_then_wait(cmd, **kwargs):
        return original([sys.executable, "-u", "-c",
                         "import time; print('hop'); time.sleep(30)"], **kwargs)

    monkeypatch.setattr(process, "stream", output_then_wait)
    ref = []
    iterator = commands.traceroute("127.0.0.1", kill_ref=ref, timeout=5)
    assert next(iterator) == "hop"
    iterator.close()
    assert ref[0].poll() is not None


def test_ping_dns_and_command_share_one_budget(monkeypatch):
    from network import resolver
    clock = [100.0]
    monkeypatch.setattr(commands.time, "monotonic", lambda: clock[0])

    def resolve(host, **kwargs):
        assert kwargs["timeout"] == 3
        clock[0] += 2
        return ["127.0.0.1"]

    def stream(cmd, **kwargs):
        assert cmd[-1] == "127.0.0.1"
        assert kwargs["timeout"] == 1
        yield "1 packets transmitted, 1 received"
        return 0

    monkeypatch.setattr(resolver, "resolve", resolve)
    monkeypatch.setattr(process, "stream", stream)
    result = commands.ping("example.invalid", count=1, timeout=3, strict=True)
    assert result.reachable and result.host == "example.invalid" and result.ip == "127.0.0.1"


def test_ping_does_not_start_after_dns_exhausts_budget(monkeypatch):
    from network import resolver
    clock = [100.0]
    monkeypatch.setattr(commands.time, "monotonic", lambda: clock[0])

    def resolve(host, **kwargs):
        clock[0] += 3
        return ["127.0.0.1"]

    monkeypatch.setattr(resolver, "resolve", resolve)
    monkeypatch.setattr(process, "stream", lambda *args, **kwargs: pytest.fail("no time for ping"))
    with pytest.raises(TimeoutError, match="ping deadline"):
        commands.ping("example.invalid", timeout=3, strict=True)


def test_numeric_ping_never_uses_system_dns(monkeypatch):
    from network import resolver
    monkeypatch.setattr(resolver, "resolve", lambda *args, **kwargs: pytest.fail("numeric address should not resolve"))

    def stream(cmd, **kwargs):
        yield "1 packets transmitted, 1 received"

    monkeypatch.setattr(process, "stream", stream)
    assert commands.ping("127.0.0.1", count=1, strict=True).reachable
