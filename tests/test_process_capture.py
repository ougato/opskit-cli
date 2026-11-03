"""Bounded process capture and child-tree cleanup, independent of plugins."""
import subprocess
import sys
import pytest
from core.process import capture


def test_native_capture_closes_default_stdin():
    result = capture([sys.executable, "-c",
                      "import sys; assert sys.stdin.read()==''; print('identity')"], timeout=5)
    assert result.returncode == 0 and result.stdout.strip() == "identity"


def test_completed_leader_cannot_leave_inherited_output_pipe_open(tmp_path):
    import time
    import psutil
    marker = tmp_path / "child.pid"
    program = "\n".join([
        "import sys,subprocess",
        "from pathlib import Path",
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])",
        "Path(sys.argv[1]).write_text(str(child.pid))",
        "print('completed',flush=True)",
    ])
    started = time.monotonic()
    result = capture([sys.executable, "-c", program, str(marker)], timeout=10)
    assert time.monotonic() - started < 7
    assert result.returncode == 0 and result.stdout.strip() == "completed"
    try:
        child = psutil.Process(int(marker.read_text()))
        if sys.platform == "win32":
            assert child.wait(timeout=3) is not None
        else:
            assert child.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        pass


def test_bounded_native_client_output_and_nonzero_fail_without_log_leak(capfd):
    command = [sys.executable, "-c", "print('token=SECRET'); raise SystemExit(3)"]
    result = capture(command, timeout=5)
    assert result.returncode == 3
    with pytest.raises(RuntimeError, match="output exceeds limit"):
        capture([sys.executable, "-c", "print('x' * 8192)"], timeout=5, max_bytes=4096)
    assert capfd.readouterr() == ("", "")


def test_native_file_input_exact_bytes_and_caller_ownership(tmp_path):
    payload = bytes(range(256)) * 32
    source = tmp_path / "context.tar"
    source.write_bytes(payload)
    with source.open("rb") as input_file:
        command = [sys.executable, "-c", "import sys,hashlib; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())"]
        digest = capture(command, timeout=5, stdin=input_file)
        assert not input_file.closed
    import hashlib
    assert digest.stdout.strip() == hashlib.sha256(payload).hexdigest() and source.read_bytes() == payload


@pytest.mark.parametrize("outcome", ["timeout", "cancel", "output_limit"])
def test_native_client_failure_reclaims_child_tree(tmp_path, monkeypatch, outcome):
    import json
    import psutil
    record = tmp_path / "child.json"
    program = "\n".join([
        "import sys,subprocess,time,json,psutil",
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])",
        "with open(sys.argv[1],'w') as out: json.dump([child.pid,psutil.Process(child.pid).create_time()],out)",
        "if sys.argv[2]=='cancel': print('ready',flush=True)",
        "if sys.argv[2]=='output_limit': print('x'*8192,flush=True)",
        "time.sleep(60)",
    ])
    if outcome == "cancel":
        from core import process
        native_stream = process.stream
        def cancelled(command, **kwargs):
            iterator = native_stream(command, **kwargs)
            try:
                assert next(iterator) == "ready"
                raise KeyboardInterrupt
                yield  # Keep this an iterator; cancellation occurs after startup.
            finally:
                iterator.close()
        monkeypatch.setattr(process, "stream", cancelled)
    expected = {"timeout": subprocess.TimeoutExpired, "cancel": KeyboardInterrupt,
                "output_limit": RuntimeError}[outcome]
    with pytest.raises(expected):
        capture([sys.executable, "-c", program, str(record), outcome],
                                       timeout=2 if outcome == "timeout" else 5, max_bytes=1024)
    assert record.exists(), "child must have started before timeout"
    pid, created = json.loads(record.read_text())
    try:
        child = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    assert child.create_time() != created or child.status() == psutil.STATUS_ZOMBIE
