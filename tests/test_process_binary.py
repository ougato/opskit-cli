"""Real binary clients: exact bytes, separate stderr and silent deadlines."""
import sys
import time

import psutil
import pytest

from core import process_binary as binary


def command(program):
    return [sys.executable, "-I", "-c", program]


def test_exact_binary_stdout_separate_from_stderr_and_stdin():
    program = "import os,sys;data=sys.stdin.buffer.read();os.write(2,b'private diagnostic');os.write(1,data+b'\\x00\\xff\\r\\n')"
    with binary.capture(command(program), b"request", timeout=10) as stream:
        assert stream.read() == b"request\x00\xff\r\n"
        owned = stream
    assert owned.closed


def test_explicit_larger_private_request_preserves_bytes():
    request = b"sensitive-fixture" * 5000
    with binary.capture(command("import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())"),
                        request, timeout=10, request_limit=131072) as stream:
        assert stream.read() == request
    with pytest.raises(ValueError, match="budget"):
        with binary.capture(command("raise SystemExit(99)"), request, timeout=10):
            pytest.fail("default request bound widened")


@pytest.mark.parametrize("limit", [True, 0, 131073, 1.5])
def test_invalid_request_budget_refuses_process(limit, monkeypatch):
    monkeypatch.setattr(binary, "start", lambda *a, **k: pytest.fail("invalid budget started process"))
    with pytest.raises(ValueError, match="budget"):
        with binary.capture([], b"", timeout=10, request_limit=limit):
            pass


@pytest.mark.parametrize("pipe", [1, 2])
def test_each_output_stream_has_a_limit(pipe):
    with pytest.raises(RuntimeError, match="output budget"):
        with binary.capture(command(f"import os;os.write({pipe},b'x'*131072)"), b"", timeout=10, max_bytes=1024):
            pytest.fail("oversized response yielded")


def test_nonzero_exit_does_not_disclose_output():
    with pytest.raises(RuntimeError, match="command failed") as caught:
        with binary.capture(command("import os;os.write(1,b'fixture-secret');os.write(2,b'fixture-secret');raise SystemExit(7)"), b"", timeout=10):
            pytest.fail("failed response yielded")
    assert "fixture-secret" not in str(caught.value)


def test_silent_client_expires_and_is_reaped(monkeypatch):
    original = binary.start
    children = []
    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(binary, "start", start)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="deadline"):
        with binary.capture(command("import time;time.sleep(60)"), b"", timeout=1):
            pytest.fail("silent response yielded")
    assert time.monotonic() - started < 10
    assert children[0].poll() is not None
    assert children[0].stdout.closed and children[0].stderr.closed


def test_descendant_holding_pipes_cannot_bypass_deadline(tmp_path):
    marker = tmp_path / "descendant.pid"
    program = ("import pathlib,subprocess,sys;child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
               f"pathlib.Path({str(marker)!r}).write_text(str(child.pid))")
    with pytest.raises(TimeoutError, match="deadline"):
        with binary.capture(command(program), b"", timeout=2):
            pytest.fail("inherited pipes bypassed deadline")
    assert marker.exists()
    try:
        descendant = psutil.Process(int(marker.read_text()))
        assert not descendant.is_running() or descendant.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        pass


def test_cleanup_error_still_closes_both_pipes(monkeypatch):
    original_start, original_stop = binary.start, binary.terminate_tree
    children = []
    def start(*args, **kwargs):
        child = original_start(*args, **kwargs)
        children.append(child)
        return child
    def stop(child):
        original_stop(child)
        raise PermissionError("injected cleanup failure")
    monkeypatch.setattr(binary, "start", start)
    monkeypatch.setattr(binary, "terminate_tree", stop)
    with pytest.raises(PermissionError, match="injected"):
        with binary.capture(command("pass"), b"", timeout=10):
            pytest.fail("cleanup error ignored")
    assert children[0].stdout.closed and children[0].stderr.closed


@pytest.mark.parametrize("kwargs", [{"timeout": 0}, {"timeout": float('inf')}, {"timeout": True},
                                   {"timeout": 1, "max_bytes": True}, {"timeout": 1, "max_bytes": 0}])
def test_invalid_budgets_refused_before_launch(monkeypatch, kwargs):
    monkeypatch.setattr(binary, "start", lambda *a, **k: pytest.fail("invalid request launched"))
    with pytest.raises(ValueError):
        with binary.capture(["unused"], b"", **kwargs):
            pass


def test_large_request_refused_before_launch(monkeypatch):
    monkeypatch.setattr(binary, "start", lambda *a, **k: pytest.fail("invalid request launched"))
    with pytest.raises(ValueError):
        with binary.capture(["unused"], b"x" * 4097, timeout=1):
            pass
