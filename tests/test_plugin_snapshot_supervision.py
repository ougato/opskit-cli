"""Real spawned snapshot workers, including blocked I/O and cancellation."""
import os
import time
from pathlib import Path

import psutil
import pytest

from core import plugin_sandbox as sandbox, plugin_snapshot as snapshot


def _blocked_copy(connection, source, target, limit, skip_dirs):
    read, write = os.pipe()
    Path(target).write_text(str(os.getpid()))
    try:
        os.read(read, 1)
    finally:
        os.close(read)
        os.close(write)
        connection.close()


def test_supervised_snapshot_copies_real_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "code").write_bytes(b"x" * 65537)
    (source / ".git").mkdir()
    (source / ".git" / "ignored").write_text("metadata")
    sandbox._copy_supervised(source, tmp_path / "dest", 65537)
    assert (tmp_path / "dest" / "code").read_bytes() == b"x" * 65537
    assert not (tmp_path / "dest" / ".git").exists()


def test_supervised_state_keeps_cache_named_data(tmp_path):
    source = tmp_path / "source"
    (source / ".git").mkdir(parents=True)
    (source / ".git" / "data").write_text("user data")
    sandbox._copy_supervised(source, tmp_path / "dest", 128, skip_dirs=frozenset())
    assert (tmp_path / "dest" / ".git" / "data").read_text() == "user data"


def test_overbudget_snapshot_never_returns_success(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_bytes(b"x" * 65)
    with pytest.raises(sandbox.SandboxError, match="snapshot worker failed"):
        sandbox._copy_supervised(source, tmp_path / "dest", 64)
    assert not (tmp_path / "dest" / "file").exists()


def test_deadline_reaps_worker_blocked_in_actual_read(tmp_path, monkeypatch, expire_worker_after_marker):
    marker = tmp_path / "pid"
    monkeypatch.setattr(snapshot, "_copy_worker", _blocked_copy)
    monkeypatch.setattr(sandbox._invocations, "deadline", time.monotonic() + 30, raising=False)
    expire_worker_after_marker(marker)
    with pytest.raises(sandbox.SandboxError, match="execution deadline during snapshot"):
        sandbox._copy_supervised(tmp_path / "source", marker, 64)
    assert not psutil.pid_exists(int(marker.read_text()))


def test_copy_worker_errors_do_not_echo_source_contents(tmp_path):
    source = tmp_path / "synthetic-sensitive-path"
    source.write_text("not a directory")
    with pytest.raises(sandbox.SandboxError) as failure:
        sandbox._copy_supervised(source, tmp_path / "dest", 64)
    assert str(failure.value) == "plugin snapshot worker failed"


def test_cancel_reaps_actual_blocked_worker(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from network import worker
    marker = tmp_path / "pid"
    monkeypatch.setattr(snapshot, "_copy_worker", _blocked_copy)
    def cancelled_clock():
        if marker.exists():
            raise KeyboardInterrupt
        return time.monotonic()
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=cancelled_clock))
    with pytest.raises(KeyboardInterrupt):
        sandbox._copy_supervised(tmp_path / "source", marker, 64)
    assert not psutil.pid_exists(int(marker.read_text()))
