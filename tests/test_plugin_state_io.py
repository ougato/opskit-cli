"""Blocking transaction I/O must not outlive cancellation or lose its journal."""
import os
import json
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from worker_test_markers import publish_marker

from core import plugin_sandbox as sandbox, plugin_state as state, plugin_state_io as transaction


def blocked_finish_worker(connection, *args):
    stage = os.environ["OPSKIT_TEST_STATE_IO_STAGE"]
    def block():
        read, write = os.pipe()
        publish_marker(os.environ["OPSKIT_TEST_STATE_IO_PID"], str(os.getpid()))
        try:
            os.read(read, 1)
        finally:
            os.close(read)
            os.close(write)
    if stage == "sync":
        state.os.fsync = lambda *args: block()
    elif stage == "scan":
        original = os.scandir
        def scan(path):
            if str(path).endswith(".previous"):
                block()
            return original(path)
        os.scandir = scan
    else:
        original = Path.unlink
        def unlink(path, *args, **kwargs):
            if path.parent.name.endswith(".previous"):
                block()
            return original(path, *args, **kwargs)
        Path.unlink = unlink
    transaction._finish_worker(connection, *args)


def committed_transaction(tmp_path):
    root = tmp_path.resolve() / "self"
    root.mkdir()
    (root / "value").write_bytes(b"old")
    candidate = tmp_path / ".state-owned" / "new"
    candidate.mkdir(parents=True)
    (candidate / "value").write_bytes(b"new")
    state.prepare_record(root, candidate, plugin="owner", caller="platform")
    root.rename(root.with_name("self.previous"))
    candidate.rename(root)
    return root


@pytest.mark.parametrize("stage", ["sync", "scan", "unlink"])
@pytest.mark.parametrize("cancel", [False, True])
def test_blocked_recovery_is_reaped_and_retry_finishes(tmp_path, monkeypatch, expire_worker_after_marker, stage, cancel):
    from network import worker
    root = committed_transaction(tmp_path)
    marker = tmp_path / "io-pid"
    monkeypatch.setenv("OPSKIT_TEST_STATE_IO_STAGE", stage)
    monkeypatch.setenv("OPSKIT_TEST_STATE_IO_PID", str(marker))
    with monkeypatch.context() as patch:
        patch.setattr(transaction, "_finish_worker", blocked_finish_worker)
        if cancel:
            def clock():
                if marker.exists():
                    raise KeyboardInterrupt
                return time.monotonic()
            patch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
        else:
            # This fixture patches worker.time and must also be undone before
            # a fresh retry below; it does not alter the real monotonic clock.
            expire_worker_after_marker(marker)
        with pytest.raises(KeyboardInterrupt if cancel else sandbox.SandboxError,
                           match=None if cancel else "deadline during state recovery"):
            sandbox._recover_state(root, plugin="owner", caller="platform")
    if not cancel:
        monkeypatch.setattr(worker, "time", time)
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert (root / "value").read_bytes() == b"new"
    assert (root.with_name("self.previous") / "value").read_bytes() == b"old"
    assert state.record_path(root).exists()
    with state.transaction_guard(root):
        pass
    sandbox._recover_state(root, plugin="owner", caller="platform")
    assert (root / "value").read_bytes() == b"new"
    assert not root.with_name("self.previous").exists()
    assert not state.record_path(root).exists()


def test_parent_does_not_read_or_mutate_transaction_tree(tmp_path, monkeypatch):
    root = committed_transaction(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("transaction traversal ran in parent")
    monkeypatch.setattr(state, "finish_transaction", forbidden)
    monkeypatch.setattr(state, "transaction_guard", forbidden)
    sandbox._recover_state(root, plugin="owner", caller="platform")
    assert (root / "value").read_bytes() == b"new"
    assert not state.record_path(root).exists()


def altered_result_worker(connection, *args):
    from network import worker
    original = worker.send
    def send(connection, result):
        result.update(json.loads(os.environ["OPSKIT_TEST_STATE_IO_RESULT"]))
        original(connection, result)
    worker.send = send
    transaction._finish_worker(connection, *args)


@pytest.mark.parametrize("change", [{"request": "stale"}, {"root": "/other"}, {"outcome": True}, {"extra": 1}])
def test_invalid_result_reports_failure_without_speculative_rollback(tmp_path, monkeypatch, change):
    root = committed_transaction(tmp_path)
    with monkeypatch.context() as patch:
        patch.setenv("OPSKIT_TEST_STATE_IO_RESULT", json.dumps(change))
        patch.setattr(transaction, "_finish_worker", altered_result_worker)
        with pytest.raises(RuntimeError, match="invalid state recovery result"):
            sandbox._recover_state(root, plugin="owner", caller="platform")
    assert (root / "value").read_bytes() == b"new"
    sandbox._recover_state(root, plugin="owner", caller="platform")


def crash_after_retirement_worker(connection, *args):
    original = Path.unlink
    def unlink(path, *args, **kwargs):
        original(path, *args, **kwargs)
        if path.name == "self.transaction.json":
            publish_marker(os.environ["OPSKIT_TEST_STATE_IO_PID"], str(os.getpid()))
            os._exit(73)
    Path.unlink = unlink
    transaction._finish_worker(connection, *args)


def test_actual_worker_exit_after_journal_retirement_is_repeatable(tmp_path, monkeypatch):
    root = committed_transaction(tmp_path)
    marker = tmp_path / "exited-pid"
    with monkeypatch.context() as patch:
        patch.setenv("OPSKIT_TEST_STATE_IO_PID", str(marker))
        patch.setattr(transaction, "_finish_worker", crash_after_retirement_worker)
        with pytest.raises(RuntimeError, match="worker failed"):
            sandbox._recover_state(root, plugin="owner", caller="platform")
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert (root / "value").read_bytes() == b"new"
    assert not state.record_path(root).exists()
    sandbox._recover_state(root, plugin="owner", caller="platform")


def authorization_probe_worker(connection, *args):
    from core.config import configuration_transaction
    from core.plugin_trust import authorization_transaction
    with configuration_transaction(), authorization_transaction():
        Path(os.environ["OPSKIT_TEST_STATE_IO_PID"]).write_text("authorization locks released")
    transaction._finish_worker(connection, *args)


def test_postcommit_cleanup_does_not_hold_authorization_locks(tmp_path, monkeypatch):
    import base64
    import io
    import tarfile
    root = tmp_path.resolve() / "self"
    root.mkdir()
    (root / "value").write_bytes(b"old")
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo("value")
        member.size = 3
        archive.addfile(member, io.BytesIO(b"new"))
    marker = tmp_path / "authorization-probe"
    monkeypatch.setenv("OPSKIT_TEST_STATE_IO_PID", str(marker))
    monkeypatch.setattr(transaction, "_finish_worker", authorization_probe_worker)
    from core.plugin_trust import grant
    grant("owner", "a" * 64, "1")
    monkeypatch.setattr(sandbox._invocations, "authorizations", (("owner", "a" * 64),), raising=False)
    sandbox._save_state(base64.b64encode(output.getvalue()).decode("ascii"), root, plugin="owner", caller="platform")
    assert marker.read_text() == "authorization locks released"
    assert (root / "value").read_bytes() == b"new"
