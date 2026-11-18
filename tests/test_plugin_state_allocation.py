"""Staging allocation and its preflight never block the invocation thread."""
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_sandbox as sandbox, plugin_state as state, plugin_state_prepare as preparation
from state_publication_fixture import state_packet
from worker_test_markers import process_birth, publish_marker


def allocation_worker(connection, *args):
    stage = os.environ["OPSKIT_TEST_ALLOCATION_STAGE"]
    def block(*arguments, **kwargs):
        read, write = os.pipe()
        publish_marker(os.environ["OPSKIT_TEST_ALLOCATION_MARKER"],
                       json.dumps({"pid": os.getpid(), "created": process_birth(os.getpid())}))
        try:
            os.read(read, 1)
        finally:
            os.close(read)
            os.close(write)
    if stage == "preflight":
        sandbox._require_resolved_state = block
    elif stage == "quota":
        state.reserve_staging = block
    elif stage == "mkdir":
        original = Path.mkdir
        def mkdir(path, *arguments, **kwargs):
            if path.name.startswith(".state-"):
                block()
            return original(path, *arguments, **kwargs)
        Path.mkdir = mkdir
    elif stage == "identity":
        original = state._directory
        def directory(path):
            if path.name.startswith(".state-"):
                block()
            return original(path)
        state._directory = directory
    elif stage == "before_ack":
        from network import worker
        original = worker.send
        def send(target, value):
            if value.get("stage") == "allocated":
                block()
            original(target, value)
        worker.send = send
    elif stage == "record_sync":
        state.os.fsync = block
    else:
        preparation.base64.b64decode = block
    preparation._prepare_worker(connection, *args)


@pytest.mark.parametrize("stage", ["preflight", "quota", "mkdir", "identity", "after_allocation"])
@pytest.mark.parametrize("cancel", [False, True])
def test_blocked_allocation_is_reaped_and_unknown_directories_retained(tmp_path, monkeypatch,
                                                                    expire_worker_after_marker, stage, cancel):
    from network import worker
    from core import plugin_state_io
    root = tmp_path.resolve() / "self"
    root.mkdir()
    (root / "value").write_bytes(b"old")
    marker = tmp_path / "allocation-pid"
    monkeypatch.setenv("OPSKIT_TEST_ALLOCATION_STAGE", stage)
    monkeypatch.setenv("OPSKIT_TEST_ALLOCATION_MARKER", str(marker))
    monkeypatch.setattr(preparation, "_prepare_worker", allocation_worker)
    original = plugin_state_io.discard_supervised
    def discard(lease, **kwargs):
        identity = json.loads(marker.read_text())
        assert not psutil.pid_exists(identity["pid"])
        monkeypatch.setattr(worker, "time", time)
        return original(lease, **kwargs)
    monkeypatch.setattr(plugin_state_io, "discard_supervised", discard)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
        sandbox._save_state(state_packet(), root, plugin="owner", caller="platform")
    monkeypatch.setattr(worker, "time", time)
    identity = json.loads(marker.read_text())
    assert not psutil.pid_exists(identity["pid"])
    assert (root / "value").read_bytes() == b"old"
    assert not state.record_path(root).exists()
    wrappers = list(root.parent.glob(".state-*"))
    assert len(wrappers) == int(stage == "identity")
    with state.staging_guard(root), state.transaction_guard(root):
        pass


def malformed_allocation_worker(connection, *args):
    from network import worker
    original = worker.send
    def send(connection, result):
        if result.get("stage") == "allocated":
            result.update(json.loads(os.environ["OPSKIT_TEST_ALLOCATION_RESULT"]))
        original(connection, result)
    worker.send = send
    preparation._prepare_worker(connection, *args)


@pytest.mark.parametrize("change", [{"request": "stale"}, {"root": "/other"}, {"path": "/other"},
                                    {"parent": [False, 1]}, {"wrapper": [0, 0]}, {"extra": True}])
def test_unverified_allocation_response_retains_journaled_empty_candidate(tmp_path, monkeypatch, change):
    root = tmp_path.resolve() / "self"
    root.mkdir()
    monkeypatch.setenv("OPSKIT_TEST_ALLOCATION_RESULT", json.dumps(change))
    monkeypatch.setattr(preparation, "_prepare_worker", malformed_allocation_worker)
    with pytest.raises(preparation.PreparationCleanupError, match="invalid state allocation result"):
        sandbox._save_state(state_packet(), root, plugin="owner", caller="platform")
    wrappers = list(root.parent.glob(".state-*"))
    assert len(wrappers) == 1
    assert [path.name for path in wrappers[0].iterdir()] == ["new"]
    assert not list((wrappers[0] / "new").iterdir()), "candidate bytes require acknowledged ownership"
    assert state.record_path(root).exists()
    sandbox._recover_state(root, plugin="owner", caller="platform")
    assert not wrappers[0].exists() and not state.record_path(root).exists()


def test_success_has_no_allocation_or_state_metadata_io_in_parent(tmp_path, monkeypatch):
    from core.plugin_trust import grant
    root = tmp_path.resolve() / "self"
    root.mkdir()
    grant("owner", "a" * 64, "1")
    monkeypatch.setattr(sandbox._invocations, "authorizations", (("owner", "a" * 64),), raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail("state filesystem I/O ran in parent")
    import tempfile
    monkeypatch.setattr(tempfile, "mkdtemp", forbidden)
    monkeypatch.setattr(sandbox, "_require_resolved_state", forbidden)
    monkeypatch.setattr(state, "_directory", forbidden)
    monkeypatch.setattr(state, "reserve_staging", forbidden)
    sandbox._save_state(state_packet(), root, plugin="owner", caller="platform")
    assert (root / "value").read_bytes() == b"new"
    assert not state.record_path(root).exists()
