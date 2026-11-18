"""Real spawned snapshot workers, including blocked I/O and cancellation."""
import os
import threading
import time
from pathlib import Path

import psutil
import pytest

from worker_test_markers import publish_marker

from core import plugin_sandbox as sandbox, plugin_snapshot as snapshot


def _blocked_copy(connection, source, target, limit, skip_dirs):
    read, write = os.pipe()
    publish_marker(Path(target), str(os.getpid()))
    try:
        os.read(read, 1)
    finally:
        os.close(read)
        os.close(write)
        connection.close()


def _blocked_pair(connection, items):
    _blocked_copy(connection, items[0][0], items[0][1], items[0][2], items[0][3])


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


def test_paired_snapshots_keep_distinct_limits_and_skip_rules(tmp_path):
    from core.plugin_tree import SKIP_DIRS
    plugin, state = tmp_path / "plugin", tmp_path / "state"
    (plugin / ".git").mkdir(parents=True)
    (plugin / ".git" / "ignored").write_text("metadata")
    (plugin / "code").write_text("code")
    (state / ".git").mkdir(parents=True)
    (state / ".git" / "data").write_text("state")
    items = ((plugin, tmp_path / "copy", 64, SKIP_DIRS),
             (state, tmp_path / "seed", 64, frozenset()))
    snapshot.copy_snapshot_pair(items, timeout=30)
    assert (tmp_path / "copy" / "code").read_text() == "code"
    assert not (tmp_path / "copy" / ".git").exists()
    assert (tmp_path / "seed" / ".git" / "data").read_text() == "state"


def test_paired_snapshots_reject_state_over_budget(tmp_path):
    plugin, state = tmp_path / "plugin", tmp_path / "state"
    plugin.mkdir()
    state.mkdir()
    (plugin / "code").write_text("code")
    (state / "data").write_bytes(b"x" * 65)
    with pytest.raises(RuntimeError, match="snapshot copy failed"):
        snapshot.copy_snapshot_pair(((plugin, tmp_path / "copy", 64, frozenset()),
                                     (state, tmp_path / "seed", 64, frozenset())), timeout=30)


def test_paired_snapshot_deadline_reaps_blocked_worker(tmp_path, monkeypatch, expire_worker_after_marker):
    marker = tmp_path / "pid"
    monkeypatch.setattr(snapshot, "_copy_pair_worker", _blocked_pair)
    expire_worker_after_marker(marker)
    with pytest.raises(TimeoutError):
        snapshot.copy_snapshot_pair(((tmp_path / "source", marker, 64, frozenset()),
                                     (tmp_path / "state", tmp_path / "seed", 64, frozenset())), timeout=30)
    assert not psutil.pid_exists(int(marker.read_text()))


def test_cancel_event_reaps_blocked_paired_snapshot(tmp_path, monkeypatch):
    marker = tmp_path / "pid"
    cancelled = threading.Event()
    monkeypatch.setattr(snapshot, "_copy_pair_worker", _blocked_pair)
    def cancel_after_start():
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        cancelled.set()
    thread = threading.Thread(target=cancel_after_start)
    thread.start()
    try:
        with pytest.raises(InterruptedError):
            snapshot.copy_snapshot_pair(((tmp_path / "source", marker, 64, frozenset()),
                                         (tmp_path / "state", tmp_path / "seed", 64, frozenset())),
                                        timeout=30, cancel_event=cancelled)
    finally:
        thread.join(timeout=25)
    assert marker.exists(), "snapshot worker did not start"
    assert not psutil.pid_exists(int(marker.read_text()))


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
