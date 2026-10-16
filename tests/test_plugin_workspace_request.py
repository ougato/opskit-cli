"""The allocation worker writes requests under the same deadline as mkdir."""
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_workspace as workspace
from test_plugin_workspace import block


def blocked_request_worker(connection, request, *arguments):
    stage = os.environ["OPSKIT_TEST_WORKSPACE_REQUEST_STAGE"]
    if stage == "open":
        original = os.open
        def open_file(path, *args, **kwargs):
            if Path(path).name == "request.json":
                block()
            return original(path, *args, **kwargs)
        os.open = open_file
    elif stage == "metadata":
        original = Path.lstat
        def lstat(path, *args, **kwargs):
            if path.name == "request.json":
                block()
            return original(path, *args, **kwargs)
        Path.lstat = lstat
    else:
        original = os.fdopen
        class Stream:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def fileno(self):
                return self.stream.fileno()
            def write(self, value):
                if stage == "write":
                    block()
                return self.stream.write(value)
            def flush(self):
                if stage == "flush":
                    block()
                return self.stream.flush()
        def fdopen(descriptor, mode="r", *args, **kwargs):
            stream = original(descriptor, mode, *args, **kwargs)
            # Fault only request output, not the newly acquired budget lock.
            return Stream(stream) if mode == "wb" else stream
        os.fdopen = fdopen
    workspace._allocate_worker(connection, request, *arguments)


@pytest.mark.parametrize("stage", ["open", "write", "flush", "metadata"])
@pytest.mark.parametrize("cancel", [False, True])
def test_blocked_request_is_reaped_and_unknown_workspace_retained(tmp_path, monkeypatch,
                                                               expire_worker_after_marker, stage, cancel):
    from network import worker
    marker = tmp_path / "pid"
    for variable in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(variable, str(tmp_path.resolve()))
    monkeypatch.setenv("OPSKIT_TEST_WORKSPACE_PID", str(marker))
    monkeypatch.setenv("OPSKIT_TEST_WORKSPACE_REQUEST_STAGE", stage)
    monkeypatch.setattr(workspace, "_allocate_worker", blocked_request_worker)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
        workspace.allocate(timeout=30, request_text='{"rpc_version":1}')
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert len(list((tmp_path / "plugin-runtimes/workspaces").glob("opskit-sandbox-*"))) == 1


def test_request_bytes_written_only_by_existing_allocation_worker(monkeypatch):
    from network import worker
    calls = []
    original = worker.run
    def observed(target, arguments, **kwargs):
        calls.append(target)
        return original(target, arguments, **kwargs)
    monkeypatch.setattr(worker, "run", observed)
    monkeypatch.setattr(Path, "write_text", lambda *args, **kwargs: pytest.fail("parent wrote request"))
    text = '{"rpc_version":1,"args":["中文"]}'
    lease = workspace.allocate(timeout=30, request_text=text)
    try:
        assert calls == [workspace._allocate_worker]
        assert (lease.root / "request.json").read_bytes() == text.encode("utf-8")
        if os.name == "posix":
            assert (lease.root / "request.json").stat().st_mode & 0o777 == 0o444
    finally:
        workspace.cleanup(lease, timeout=30)


@pytest.mark.parametrize("value", ["", 1, False, b"{}"])
def test_invalid_request_never_starts_worker(monkeypatch, value):
    from network import worker
    monkeypatch.setattr(worker, "run", lambda *args, **kwargs: pytest.fail("invalid request started worker"))
    with pytest.raises(ValueError, match="invalid workspace request"):
        workspace.allocate(timeout=30, request_text=value)


def test_encoded_byte_limit_checked_before_worker(monkeypatch):
    from core import plugin_json
    from network import worker
    monkeypatch.setattr(plugin_json, "MAX_TEXT", 8)
    monkeypatch.setattr(worker, "run", lambda *args, **kwargs: pytest.fail("oversized request started worker"))
    with pytest.raises(ValueError, match="byte limit"):
        workspace.allocate(timeout=30, request_text="中文中文")
