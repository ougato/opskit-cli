"""Abrupt parent termination must reclaim a worker stuck in actual OS I/O."""
import json
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest
from worker_test_markers import process_birth


def alive(process):
    try:
        if sys.platform == "darwin":
            if process_birth(process.pid) != getattr(process, "_opskit_test_birth", None):
                return False
        elif not process.is_running():
            return False
        if process.status() != psutil.STATUS_ZOMBIE:
            return True
        # Linux can expose a zombie thread-group leader while another task is
        # still exiting and retaining the file table/OS locks. Do not treat
        # that observation as confirmed worker exit.
        if sys.platform.startswith('linux'):
            for thread in process.threads():
                try:
                    if psutil.Process(thread.id).status() != psutil.STATUS_ZOMBIE:
                        return True
                except psutil.NoSuchProcess:
                    pass
        return False
    except psutil.NoSuchProcess:
        return False


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux thread-group leader exit semantics')
def test_zombie_leader_with_live_task_is_not_worker_exit(monkeypatch):
    from types import SimpleNamespace
    leader = SimpleNamespace(is_running=lambda: True, status=lambda: psutil.STATUS_ZOMBIE,
                             threads=lambda: [SimpleNamespace(id=123)])
    monkeypatch.setattr(psutil, 'Process', lambda pid: SimpleNamespace(status=lambda: psutil.STATUS_RUNNING))
    assert alive(leader), 'a running task can still retain the worker file table'


def marking_worker(connection, marker):
    Path(marker).write_text("started")
    from network.worker import send
    send(connection, {"ok": True})


@pytest.mark.parametrize("observation", ["live", "reused", "missing", "denied"])
def test_worker_marker_checks_birth_and_preserves_inspection_errors(tmp_path, monkeypatch, observation):
    import os
    import worker_test_markers as markers
    marker = tmp_path / "worker"
    markers.publish_process_marker(marker)
    identity = json.loads(marker.read_text())
    assert identity["pid"] == os.getpid()
    assert markers.marked_process_exists(marker)
    if observation == "live":
        assert markers.marked_process_exists(marker)
    elif observation == "reused":
        # The numeric PID still exists, but a different birth cannot be ours.
        assert psutil.pid_exists(identity["pid"])
        birth = identity["birth"]
        replacement = [birth[0] + 1, birth[1]] if isinstance(birth, list) else birth + 1
        monkeypatch.setattr(markers, "process_birth", lambda pid: replacement)
        assert not markers.marked_process_exists(marker)
    else:
        error = psutil.NoSuchProcess(identity["pid"]) if observation == "missing" else psutil.AccessDenied(identity["pid"])
        def unavailable(pid):
            raise error
        monkeypatch.setattr(markers, "process_birth", unavailable)
        if observation == "missing":
            assert not markers.marked_process_exists(marker)
        else:
            with pytest.raises(psutil.AccessDenied):
                markers.marked_process_exists(marker)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin process-local psutil clock correction")
def test_native_process_birth_survives_local_clock_correction(monkeypatch):
    import os
    import psutil._psosx as backend
    expected = process_birth(os.getpid())
    monkeypatch.setattr(backend, "INIT_BOOT_TIME", backend.boot_time() - 60)
    adjusted = psutil.Process(os.getpid()).create_time()
    assert adjusted > expected[0] + expected[1] / 1_000_000 + 1
    assert process_birth(os.getpid()) == expected


@pytest.mark.parametrize("mode", ["disposable", "session", "quota", "preparation-extract", "preparation-permissions",
                                  "transaction-sync", "transaction-scan", "transaction-unlink", "discard",
                                  "publication-read", "publication-sync", "publication-old", "publication-new",
                                  "allocation-mkdir", "allocation-identity", "allocation-after_allocation",
                                  "allocation-before_ack", "allocation-record_sync"])
def test_worker_exits_when_parent_is_killed(tmp_path, mode, monkeypatch):
    marker = tmp_path / "child.json"
    # Do not use core.process.start: its existing Windows Job would mask whether
    # the multiprocessing worker has its own parent-lifetime protection.
    parent = subprocess.Popen([sys.executable, str(Path(__file__).with_name("worker_parent_crash_fixture.py")), str(marker), mode],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    child = None
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                identity = json.loads(marker.read_text())
                child = psutil.Process(identity["pid"])
                assert process_birth(child.pid) == identity["created"]
                child._opskit_test_birth = identity["created"]
                break
            except (FileNotFoundError, json.JSONDecodeError, PermissionError) as error:
                # Windows can briefly deny a read while the marker is being
                # atomically published. Persistent denial still fails at the
                # startup deadline; it must never bypass the identity check.
                if isinstance(error, PermissionError) and sys.platform != "win32":
                    raise
                assert parent.poll() is None, "fixture parent failed during startup"
                assert time.monotonic() < deadline, "worker did not enter blocking I/O"
                time.sleep(.02)
        if mode.startswith(("transaction-", "publication-", "allocation-")) or mode == "discard":
            from core import plugin_sandbox, plugin_state
            # A second caller must contend on the worker's own lock, not just
            # on a lock held by the waiting supervisor.
            with monkeypatch.context() as patch:
                patch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "state"))
                with pytest.raises(plugin_sandbox.SandboxError, match="transaction is busy"):
                    plugin_sandbox._recover_state(tmp_path.resolve() / "self", plugin="owner", caller="platform")
                if mode.startswith("allocation-"):
                    from core.resource_lock import ResourceBusyError
                    from state_publication_fixture import state_packet
                    other = tmp_path.resolve() / "other"
                    other.mkdir()
                    with pytest.raises(ResourceBusyError):
                        with plugin_state.staging_guard(other):
                            pytest.fail("another target bypassed the shared parent quota guard")
                    with pytest.raises(plugin_sandbox.SandboxError, match="staging or transaction is busy"):
                        plugin_sandbox._save_state(state_packet(), other, plugin="owner", caller="platform")
                    independent = tmp_path / "independent"
                    independent.mkdir()
                    with plugin_state.staging_guard(independent / "self"):
                        pass
        parent.kill()
        parent.wait(timeout=5)
        deadline = time.monotonic() + 5
        while alive(child):
            assert time.monotonic() < deadline, "worker survived abrupt parent termination"
            time.sleep(.02)
        if mode.startswith("transaction-"):
            root = tmp_path.resolve() / "self"
            assert (root / "value").read_bytes() == b"new"
            assert (root.with_name("self.previous") / "value").read_bytes() == b"old"
            assert plugin_state.record_path(root).exists()
            # The replacement caller uses exactly the original platform scope.
            with monkeypatch.context() as patch:
                patch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "state"))
                plugin_sandbox._recover_state(root, plugin="owner", caller="platform")
            assert (root / "value").read_bytes() == b"new"
            assert not plugin_state.record_path(root).exists()
        if mode == "discard":
            from core import plugin_state_io
            root = tmp_path.resolve() / "self"
            wrapper = root.parent / ".state-owned"
            assert (root / "value").read_bytes() == b"old"
            assert (wrapper / "new" / "value").read_bytes() == b"new"
            lease = plugin_state_io.OwnedStaging(root, wrapper, plugin_state._directory(root.parent),
                                                plugin_state._directory(wrapper))
            with monkeypatch.context() as patch:
                patch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "state"))
                assert plugin_state_io.discard_supervised(lease, timeout=30) == "removed"
            assert not wrapper.exists()
        if mode.startswith("publication-"):
            from core.config import configuration_transaction
            from core.plugin_trust import authorization_transaction
            root = tmp_path.resolve() / "self"
            with monkeypatch.context() as patch:
                patch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "state"))
                with configuration_transaction(), authorization_transaction():
                    pass
                plugin_sandbox._recover_state(root, plugin="owner", caller="platform")
            expected = b"new" if mode == "publication-new" else b"old"
            assert (root / "value").read_bytes() == expected
            assert not plugin_state.record_path(root).exists()
            wrapper = root.parent / ".state-owned"
            assert not wrapper.exists()
        if mode.startswith("allocation-"):
            root = tmp_path.resolve() / "self"
            with monkeypatch.context() as patch:
                patch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "state"))
                with plugin_state.staging_guard(root), plugin_state.transaction_guard(root):
                    pass
                plugin_sandbox._recover_state(root, plugin="owner", caller="platform")
            assert (root / "value").read_bytes() == b"old"
            assert not plugin_state.record_path(root).exists()
            wrappers = list(root.parent.glob(".state-*"))
            assert len(wrappers) == int(mode == "allocation-identity")
            assert all(not list(wrapper.iterdir()) for wrapper in wrappers)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if child is not None and alive(child):
            try:
                child.kill()
                child.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass
        parent.stdout.close()


@pytest.mark.parametrize("stage", [3, 4, 5])
def test_repair_worker_and_locks_are_reclaimed_after_cli_parent_death(tmp_path, monkeypatch, stage):
    from core import plugin_sandbox, plugin_state, plugin_state_operator
    from core.resource_lock import ResourceBusyError
    marker = tmp_path / "repair-child.json"
    parent = subprocess.Popen([sys.executable, str(Path(__file__).with_name("worker_parent_crash_fixture.py")),
                               str(marker), "repair-" + str(stage)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    child = None
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "state"))
    root = tmp_path / "state" / "plugin-data" / "owner" / "self"
    try:
        deadline = time.monotonic() + 30
        while child is None:
            try:
                identity = json.loads(marker.read_text())
                child = psutil.Process(identity["pid"])
                assert process_birth(child.pid) == identity["created"]
                child._opskit_test_birth = identity["created"]
            except (FileNotFoundError, json.JSONDecodeError, PermissionError):
                assert parent.poll() is None, "CLI fixture exited before repair checkpoint"
                assert time.monotonic() < deadline, "repair worker did not reach checkpoint"
                time.sleep(.02)
        # Both locks must be real and held independently by the intended owner.
        with pytest.raises(ResourceBusyError):
            with plugin_state.transaction_guard(root):
                pytest.fail("worker transaction lock was bypassed")
        with pytest.raises(plugin_sandbox.SandboxError, match="busy"):
            with plugin_sandbox._execution_budget():
                with plugin_sandbox._state_lock("owner"):
                    pytest.fail("CLI invocation lock was bypassed")
        evidence = next(root.parent.glob("self.repair-*.evidence"))
        raw = evidence.read_bytes()
        parent.kill()
        parent.wait(timeout=5)
        deadline = time.monotonic() + 5
        while alive(child):
            assert time.monotonic() < deadline, "repair worker survived CLI parent death"
            time.sleep(.02)
        with plugin_sandbox._execution_budget():
            with plugin_sandbox._state_lock("owner"):
                if stage < 5:
                    import hashlib
                    assert plugin_state.record_path(root).read_bytes() == raw
                    result = plugin_state_operator.state_operation(
                        "owner", digest=hashlib.sha256(raw).hexdigest(), timeout=30)
                    assert result["status"] == "aborted"
                else:
                    with plugin_state.transaction_guard(root):
                        assert plugin_state.finish_transaction(root, plugin="owner", caller="platform") == "aborted"
        assert evidence.read_bytes() == raw
        assert (root / "value").read_bytes() == b"old"
        assert not plugin_state.record_path(root).exists()
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if child is not None and alive(child):
            try:
                child.kill()
                child.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass
        parent.stdout.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job assignment gate")
def test_job_assignment_failure_never_starts_work(tmp_path, monkeypatch):
    import multiprocessing
    from core import windows_job
    from network.worker import run
    marker = tmp_path / "must-not-start"
    before = {child.pid for child in multiprocessing.active_children()}
    def refused(handle):
        raise OSError("synthetic assignment refusal")
    monkeypatch.setattr(windows_job, "attach_running", refused)
    with pytest.raises(OSError, match="assignment refusal"):
        run(marking_worker, (str(marker),), timeout=30, label="assignment gate")
    assert not marker.exists()
    assert {child.pid for child in multiprocessing.active_children()} == before
