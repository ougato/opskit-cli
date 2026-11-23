"""Known health-confirmation gaps during an unsuccessful rollback.

Remaining strict xfails record gaps that need cross-process coordination.
"""
import multiprocessing
import os
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

from core import update_target_state, updater


_RUNNING_BUILD = 2


@pytest.fixture(autouse=True)
def rollback_release(monkeypatch):
    monkeypatch.setattr(updater, "APP_VERSION", _RUNNING_BUILD)


@pytest.mark.skipif(os.name != "nt", reason="Windows executable sharing behavior")
def test_windows_running_executable_cannot_be_replaced(tmp_path):
    system_cmd = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
    target = tmp_path / "owned-cmd.exe"
    replacement = tmp_path / "owned-replacement.exe"
    shutil.copyfile(system_cmd, target)
    replacement.write_bytes(b"test replacement")
    original_digest = updater._sha256_file(target)
    process = subprocess.Popen([str(target), "/d", "/c", "pause"], stdin=subprocess.PIPE,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if process.poll() is not None:
            raise RuntimeError("isolated command exited before replacement test")
        with pytest.raises(PermissionError) as failure:
            os.replace(replacement, target)
        assert failure.value.winerror == 5
        assert updater._sha256_file(target) == original_digest
        assert replacement.read_bytes() == b"test replacement"
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
        if process.stdin is not None:
            process.stdin.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows delayed executable replacement")
def test_windows_rollback_helper_waits_for_instances_and_preserves_health(tmp_path, monkeypatch):
    """Exercise replacement primitives only; this is not a rollback scheduler."""
    import ctypes
    import json
    import time
    from ctypes import wintypes

    from core import update_windows

    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    system = Path(os.environ["SystemRoot"]) / "System32"
    target, backup = tmp_path / "isolated.exe", tmp_path / "backup.exe"
    shutil.copyfile(system / "cmd.exe", target)
    shutil.copyfile(system / "where.exe", backup)
    current_digest, restored_digest = updater._sha256_file(target), updater._sha256_file(backup)
    backup_id = "opskit.v1.123." + "b" * 32 + ".bak"
    transaction = update_target_state.prepare(
        target, operation="rollback", from_build=2, to_build=1,
        from_digest=current_digest, to_digest=restored_digest,
        owner_data_dir=tmp_path / "data", source_transaction_id="a" * 32, backup_id=backup_id,
    )
    health = updater._get_health_path()
    health.parent.mkdir(parents=True, exist_ok=True)
    updater._write_update_record(health, {"build": 2, "confirmed": False, "fails": 3, "backup_id": backup_id})
    original_health = health.read_bytes()
    lock, target_lock = updater._update_lock_path(), updater._update_target_lock_path(target)
    lock.parent.mkdir(parents=True, exist_ok=True)
    target_lock.parent.mkdir(parents=True, exist_ok=True)

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    children = []

    def launch(parent, suffix):
        handle = kernel.OpenProcess(0x00100000, True, parent.pid)  # SYNCHRONIZE
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        job = tmp_path / f"job-{suffix}"
        job.mkdir()
        helper, result = job / "apply.ps1", job / "result.json"
        content = update_windows.script(
            target=target, artifact=backup, result=result, health=health, lock=lock, target_lock=target_lock,
            old_digest=current_digest, new_digest=restored_digest, version=1, parent_pid=parent.pid,
            parent_handle=handle, recovery={"transaction": transaction, "job_id": "a" * 32}, timeout=30,
        )
        helper.write_text(content, encoding="utf-8-sig")
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [handle]}
        try:
            process = subprocess.Popen(
                [str(system / "WindowsPowerShell" / "v1.0" / "powershell.exe"), "-NoProfile",
                 "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(helper)],
                startupinfo=startup, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            children.append(process)
        finally:
            kernel.CloseHandle(handle)
        deadline = time.monotonic() + 15
        while not (job / "running.json").exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (job / "running.json").exists(), "helper did not acknowledge launch"
        return process, result

    try:
        for _ in range(2):
            children.append(subprocess.Popen(
                [str(target), "/d", "/c", "pause"], stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ))
        first, second = children[:2]
        assert first.poll() is None and second.poll() is None
        helper, result = launch(first, "occupied")
        assert helper.poll() is None and updater._sha256_file(target) == current_digest
        first.terminate()
        first.wait(timeout=10)
        helper.wait(timeout=30)
        failure = json.loads(result.read_text(encoding="utf-8-sig"))
        assert failure["state"] == "failed" and failure["phase"] == "commit"
        assert updater._sha256_file(target) == current_digest
        assert update_target_state.read(target) == transaction and health.read_bytes() == original_health

        helper, result = launch(second, "released")
        assert helper.poll() is None
        second.terminate()
        second.wait(timeout=10)
        helper.wait(timeout=30)
        assert json.loads(result.read_text(encoding="utf-8-sig"))["state"] == "applied"
        assert updater._sha256_file(target) == restored_digest
        assert update_target_state.read(target) == {**transaction, "phase": "applied"}
        assert health.read_bytes() == original_health
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=10)
            if child.stdin is not None:
                child.stdin.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows delayed executable replacement")
def test_windows_rollback_helper_rejects_health_confirmation_during_parent_wait(tmp_path, monkeypatch):
    """A prepared transaction alone must not override a later health decision."""
    import json
    import time
    from core import update_windows

    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    system = Path(os.environ["SystemRoot"]) / "System32"
    target, backup = tmp_path / "isolated.exe", tmp_path / "backup.exe"
    shutil.copyfile(system / "cmd.exe", target)
    shutil.copyfile(system / "where.exe", backup)
    original = updater._sha256_file(target)
    backup_id = "opskit.v1.123." + "b" * 32 + ".bak"
    transaction = update_target_state.prepare(
        target, operation="rollback", from_build=2, to_build=1,
        from_digest=original, to_digest=updater._sha256_file(backup),
        owner_data_dir=tmp_path / "data", source_transaction_id="a" * 32, backup_id=backup_id,
    )
    health = updater._get_health_path()
    updater._write_update_record(health, {"build": 2, "confirmed": False, "fails": 3, "backup_id": backup_id})
    lock, target_lock = updater._update_lock_path(), updater._update_target_lock_path(target)
    lock.parent.mkdir(parents=True, exist_ok=True)
    target_lock.parent.mkdir(parents=True, exist_ok=True)
    job = tmp_path / "job"
    job.mkdir()
    helper, result = job / "apply.ps1", job / "result.json"
    parent = subprocess.Popen([str(target), "/d", "/c", "pause"], stdin=subprocess.PIPE,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    process = None
    try:
        # The test runs from another process: open an inheritable handle to
        # the actual executable instead of passing the test process handle.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, True, parent.pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            content = update_windows.script(
                target=target, artifact=backup, result=result, health=health, lock=lock,
                target_lock=target_lock, old_digest=original, new_digest=updater._sha256_file(backup),
                version=1, parent_pid=parent.pid, parent_handle=handle,
                recovery={"transaction": transaction, "job_id": "a" * 32}, timeout=30,
            )
            helper.write_text(content, encoding="utf-8-sig")
            startup = subprocess.STARTUPINFO()
            startup.lpAttributeList = {"handle_list": [handle]}
            process = subprocess.Popen(
                [str(system / "WindowsPowerShell" / "v1.0" / "powershell.exe"), "-NoProfile",
                 "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(helper)],
                startupinfo=startup, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        finally:
            kernel.CloseHandle(handle)
        deadline = time.monotonic() + 15
        while not (job / "running.json").exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (job / "running.json").exists(), "helper did not acknowledge launch"
        assert process.poll() is None and parent.poll() is None
        updater._write_update_record(health, {"build": 2, "confirmed": True, "fails": 3, "backup_id": backup_id})
        parent.terminate()
        parent.wait(timeout=10)
        process.wait(timeout=30)
        result_data = json.loads(result.read_text(encoding="utf-8-sig"))
        digest = updater._sha256_file(target)
        assert result_data["state"] == "failed" and result_data["phase"] == "rollback_health_validation"
        assert digest == original
        assert json.loads(health.read_text(encoding="utf-8"))["confirmed"] is True
    finally:
        if parent.poll() is None:
            parent.terminate()
        parent.wait(timeout=10)
        if parent.stdin is not None:
            parent.stdin.close()
        if process is not None:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)


def _failed_decision_process(directory, ready, release, error_seen):
    os.environ["OPSKIT_DATA_DIR"] = directory
    from core import updater as child_updater
    child_updater.APP_VERSION = _RUNNING_BUILD
    original = child_updater._write_update_record
    health = child_updater._get_health_path()

    def fail_health_write(path, record):
        if path == health:
            raise OSError("simulated health persistence failure")
        return original(path, record)

    child_updater._write_update_record = fail_health_write
    child_updater.rollback = lambda: False
    try:
        child_updater._check_health()
    except child_updater.HealthDecisionPersistenceError:
        pass  # This boot stopped, but its failed decision is not visible to other processes.
    except Exception:
        error_seen.value = 1
    finally:
        ready.set()
        release.wait(10)


def _confirm_process(directory, target, connection):
    os.environ["OPSKIT_DATA_DIR"] = directory
    from core import updater as child_updater
    child_updater.APP_VERSION = _RUNNING_BUILD
    child_updater._self_path = lambda: Path(target)
    child_updater._is_frozen = lambda: True
    try:
        connection.send(child_updater.confirm_health())
    finally:
        connection.close()


def _check_process(directory, target, connection):
    os.environ["OPSKIT_DATA_DIR"] = directory
    from core import updater as child_updater
    child_updater.APP_VERSION = _RUNNING_BUILD
    child_updater._self_path = lambda: Path(target)
    child_updater._is_frozen = lambda: True
    try:
        connection.send(child_updater._check_health())
    finally:
        connection.close()


def test_failed_rollback_must_not_confirm_unhealthy_build(monkeypatch):
    health = updater._get_health_path()
    updater._write_update_record(health, {
        "build": updater.APP_VERSION, "confirmed": False,
        "fails": updater.MAX_HEALTH_FAILS - 1,
    })
    def failed_rollback():
        decision = updater._read_health_record(health)
        assert decision["fails"] == updater.MAX_HEALTH_FAILS
        assert decision["confirmed"] is False
        return False

    monkeypatch.setattr(updater, "rollback", failed_rollback)

    assert updater._check_health() is False
    assert updater.confirm_health() is False

    record = updater._read_health_record(health)
    assert record["fails"] == updater.MAX_HEALTH_FAILS
    assert record["confirmed"] is False


def test_rollback_does_not_run_if_decision_cannot_be_persisted(monkeypatch):
    health = updater._get_health_path()
    original = {
        "build": updater.APP_VERSION, "confirmed": False,
        "fails": updater.MAX_HEALTH_FAILS - 1,
    }
    updater._write_update_record(health, original)
    rollback_calls = []
    monkeypatch.setattr(updater, "rollback", lambda: rollback_calls.append(True))

    def fail_write(path, data):
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(updater, "_write_update_record", fail_write)
    with pytest.raises(updater.HealthDecisionPersistenceError):
        updater._check_health()
    assert not rollback_calls
    assert updater._read_health_record(health) == original


def test_post_update_boot_checks_unconfirmed_health(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "_reconcile_indexed_job_on_boot", lambda: (False, False))
    checks = []
    monkeypatch.setattr(updater, "_check_health", lambda: checks.append(True) or False)
    assert updater.check_and_apply_pending(post_update=True) is False
    assert checks == [True]


def test_post_update_health_rollback_requests_restart_without_reapplying(monkeypatch):
    monkeypatch.setattr(updater, "_reconcile_indexed_job_on_boot", lambda: (False, False))
    monkeypatch.setattr(updater, "_check_health", lambda: True)
    monkeypatch.setattr(updater, "_apply_owned_pending", lambda *_: pytest.fail("must not reapply"))
    assert updater.check_and_apply_pending(post_update=True) is True
    assert updater.pending_version() == updater.APP_VERSION


def test_post_update_first_boot_records_health_without_touching_pending(tmp_path, monkeypatch):
    target = tmp_path / "isolated.exe"
    target.write_bytes(b"new version")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    monkeypatch.setattr(updater, "_reconcile_indexed_job_on_boot", lambda: (False, False))
    backup_id = "opskit.v1.123." + "a" * 32 + ".bak"
    health = updater._get_health_path()
    updater._write_update_record(health, {"build": updater.APP_VERSION, "confirmed": False,
                                         "fails": 0, "backup_id": backup_id})
    pending = updater._get_pending_path()
    pending.write_bytes(b"unowned pending")
    import hashlib
    transaction = update_target_state.prepare(target, operation="upgrade", from_build=updater.APP_VERSION - 1,
        to_build=updater.APP_VERSION, from_digest=hashlib.sha256(b"old version").hexdigest(),
        to_digest=updater._sha256_file(target), owner_data_dir=tmp_path, backup_id=backup_id)
    update_target_state.completed(target, transaction)
    assert updater.check_and_apply_pending(post_update=True) is False
    assert updater._read_health_record(health)["fails"] == 1
    assert pending.read_bytes() == b"unowned pending" and target.read_bytes() == b"new version"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="another process can confirm after failed rollback decision persistence")
def test_other_process_must_not_confirm_after_decision_write_failure(tmp_path):
    health = updater._get_health_path()
    updater._write_update_record(health, {
        "build": updater.APP_VERSION, "confirmed": False,
        "fails": updater.MAX_HEALTH_FAILS - 1,
    })
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    error_seen = context.Value("b", 0)
    process = context.Process(target=_failed_decision_process,
                              args=(str(tmp_path), ready, release, error_seen))
    process.start()
    try:
        if not ready.wait(10):
            raise RuntimeError("isolated decision process did not report readiness")
        if error_seen.value:
            raise RuntimeError("isolated decision process failed unexpectedly")
        updater.confirm_health()
        assert updater._read_health_record(health)["confirmed"] is False
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)


def test_prepared_rollback_must_not_confirm_unhealthy_build(tmp_path, monkeypatch):
    target = tmp_path / "isolated.exe"
    target.write_bytes(b"isolated fixture")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    health = updater._get_health_path()
    backup_id = "opskit.v1.123." + "a" * 32 + ".bak"
    backup = tmp_path / "backups" / backup_id
    backup.parent.mkdir()
    backup.write_bytes(b"isolated previous build")
    updater._write_update_record(health, {
        "build": updater.APP_VERSION, "confirmed": False,
        "fails": updater.MAX_HEALTH_FAILS - 1,
        "backup_id": backup_id,
    })
    old_digest, new_digest = updater._sha256_file(backup), updater._sha256_file(target)
    updater._write_update_record(backup.with_name(backup.name + ".json"), {
        "schema_version": 1, "backup_id": backup_id, "target": str(target.resolve()),
        "from_build": updater.APP_VERSION - 1, "to_build": updater.APP_VERSION,
        "sha256": old_digest, "replacement_sha256": new_digest,
    })
    upgrade = update_target_state.prepare(
        target, operation="upgrade", from_build=updater.APP_VERSION - 1,
        to_build=updater.APP_VERSION, from_digest=old_digest, to_digest=new_digest,
    )
    update_target_state.completed(target, upgrade)
    health_before = health.read_bytes()
    with monkeypatch.context() as patch:
        def interrupted(*args, **kwargs):
            raise OSError("simulated replacement failure")
        patch.setattr(updater, "_atomic_copy", interrupted)
        assert updater.rollback() is False
    transaction = update_target_state.read(target)
    assert transaction["operation"] == "rollback" and transaction["phase"] == "prepared"

    assert updater.confirm_health() is False
    assert updater.confirm_health_after(0, retry_timeout=0) is False

    assert health.read_bytes() == health_before
    assert update_target_state.read(target) == transaction
    assert updater._sha256_file(target) == new_digest and updater._sha256_file(backup) == old_digest


def test_prepared_upgrade_can_still_confirm(tmp_path, monkeypatch):
    target = tmp_path / "isolated.exe"
    target.write_bytes(b"isolated current build")
    previous = tmp_path / "previous.exe"
    previous.write_bytes(b"isolated previous build")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    health = updater._get_health_path()
    updater._write_update_record(health, {"build": updater.APP_VERSION, "confirmed": False, "fails": 0})
    update_target_state.prepare(
        target, operation="upgrade", from_build=updater.APP_VERSION - 1,
        to_build=updater.APP_VERSION, from_digest=updater._sha256_file(previous),
        to_digest=updater._sha256_file(target),
    )

    assert updater._check_health() is False
    assert updater._read_health_record(health)["fails"] == 1
    assert updater.confirm_health() is True
    assert updater._read_health_record(health)["confirmed"] is True


@pytest.mark.parametrize("phase,contents,from_build,to_build", [
    ("prepared", b"not the recorded update", _RUNNING_BUILD - 1, _RUNNING_BUILD),
    ("aborted", b"previous executable", _RUNNING_BUILD - 1, _RUNNING_BUILD),
    ("applied", b"recorded update", _RUNNING_BUILD, _RUNNING_BUILD + 1),
])
def test_upgrade_transaction_must_match_running_build_and_target(
    tmp_path, monkeypatch, phase, contents, from_build, to_build,
):
    target = tmp_path / "isolated.exe"
    target.write_bytes(contents)
    previous = tmp_path / "previous.exe"
    previous.write_bytes(b"previous executable")
    update = tmp_path / "update.exe"
    update.write_bytes(b"recorded update")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    health = updater._get_health_path()
    updater._write_update_record(health, {"build": updater.APP_VERSION, "confirmed": False, "fails": 0})
    transaction = update_target_state.prepare(
        target, operation="upgrade", from_build=from_build, to_build=to_build,
        from_digest=updater._sha256_file(previous), to_digest=updater._sha256_file(update),
    )
    if phase != "prepared":
        updater._write_update_record(update_target_state.path(target), {**transaction, "phase": phase})
    before = health.read_bytes(), target.read_bytes(), update_target_state.path(target).read_bytes()

    assert updater._check_health() is False
    assert (health.read_bytes(), target.read_bytes(), update_target_state.path(target).read_bytes()) == before
    assert updater.confirm_health() is False
    assert (health.read_bytes(), target.read_bytes(), update_target_state.path(target).read_bytes()) == before


def test_stale_process_cannot_confirm_after_rollback_applied(tmp_path, monkeypatch):
    target = tmp_path / "isolated.exe"
    target.write_bytes(b"previous executable")
    update = tmp_path / "update.exe"
    update.write_bytes(b"recorded update")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    health = updater._get_health_path()
    updater._write_update_record(health, {"build": updater.APP_VERSION, "confirmed": False, "fails": 0})
    transaction = update_target_state.prepare(
        target, operation="rollback", from_build=updater.APP_VERSION, to_build=updater.APP_VERSION - 1,
        from_digest=updater._sha256_file(update), to_digest=updater._sha256_file(target),
    )
    update_target_state.completed(target, transaction)
    before = health.read_bytes(), target.read_bytes(), update_target_state.path(target).read_bytes()

    assert updater.confirm_health() is False
    assert (health.read_bytes(), target.read_bytes(), update_target_state.path(target).read_bytes()) == before


def test_frozen_health_job_cannot_confirm_without_target_transaction(tmp_path, monkeypatch):
    target = tmp_path / "isolated.exe"
    target.write_bytes(b"unverified executable")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    health = updater._get_health_path()
    updater._write_update_record(health, {
        "build": updater.APP_VERSION, "confirmed": False, "fails": 0,
        "job_id": "a" * 32, "backup_id": "opskit.v1.123." + "b" * 32 + ".bak",
    })
    before = health.read_bytes(), target.read_bytes()

    assert updater._check_health() is False
    assert updater.confirm_health() is False
    assert updater.confirm_health_after(0, retry_timeout=0) is False
    assert (health.read_bytes(), target.read_bytes()) == before
    assert not update_target_state.path(target).exists()


def test_legacy_health_without_job_or_transaction_keeps_confirmation_behavior(tmp_path, monkeypatch):
    target = tmp_path / "isolated.exe"
    target.write_bytes(b"legacy executable")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    health = updater._get_health_path()
    updater._write_update_record(health, {"build": updater.APP_VERSION, "confirmed": False, "fails": 0})

    assert updater._check_health() is False
    assert updater._read_health_record(health)["fails"] == 1
    assert updater.confirm_health() is True
    assert updater._read_health_record(health)["confirmed"] is True


@pytest.mark.parametrize("job_condition", ["missing", "wrong_transaction", "valid"])
@pytest.mark.parametrize("phase", ["prepared", "applied"])
def test_health_job_must_match_target_transaction_and_backup(tmp_path, monkeypatch, job_condition, phase):
    target = tmp_path / "isolated.exe"
    target.write_bytes(b"recorded update")
    previous = tmp_path / "previous.exe"
    previous.write_bytes(b"previous executable")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    health = updater._get_health_path()
    job_id = "a" * 32
    backup_id = "opskit.v1.123." + "b" * 32 + ".bak"
    updater._write_update_record(health, {
        "build": updater.APP_VERSION, "confirmed": False, "fails": 0,
        "job_id": job_id, "backup_id": backup_id,
    })
    transaction = update_target_state.prepare(
        target, operation="upgrade", from_build=updater.APP_VERSION - 1,
        to_build=updater.APP_VERSION, from_digest=updater._sha256_file(previous),
        to_digest=updater._sha256_file(target),
    )
    if phase == "applied":
        update_target_state.completed(target, transaction)
    if job_condition != "missing":
        metadata = {
            "schema_version": 2, "job_id": job_id, "state": "scheduled",
            "transaction_id": transaction["transaction_id"] if job_condition == "valid" else "c" * 32,
            "version": updater.APP_VERSION, "target": str(target.resolve()), "backup_id": backup_id,
            "old_sha256": transaction["from_sha256"], "new_sha256": transaction["to_sha256"],
        }
        updater._write_update_record(updater._get_cache_path().parent / "update-jobs" / job_id / "job.json", metadata)
    original = health.read_bytes()

    # Startup must not count an unverified job as a failed boot: crossing the
    # rollback threshold would otherwise strand valid recovery evidence.
    assert updater._check_health() is False
    if job_condition == "valid":
        assert updater._read_health_record(health)["fails"] == 1
    else:
        assert health.read_bytes() == original

    assert updater.confirm_health() is (job_condition == "valid")
    if job_condition == "valid":
        assert updater._read_health_record(health)["confirmed"] is True
    else:
        assert health.read_bytes() == original


def test_other_process_checks_job_authority_before_counting(tmp_path, monkeypatch):
    target = tmp_path / "isolated.exe"
    target.write_bytes(b"current build")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    health = updater._get_health_path()
    job_id, backup_id = "a" * 32, "opskit.v1.123." + "b" * 32 + ".bak"
    updater._write_update_record(health, {"build": updater.APP_VERSION, "confirmed": False,
                                         "fails": 0, "job_id": job_id, "backup_id": backup_id})
    import hashlib
    transaction = update_target_state.prepare(
        target, operation="upgrade", from_build=updater.APP_VERSION - 1,
        to_build=updater.APP_VERSION, from_digest=hashlib.sha256(b"old build").hexdigest(),
        to_digest=updater._sha256_file(target), owner_data_dir=tmp_path, backup_id=backup_id,
    )
    update_target_state.completed(target, transaction)
    job = updater._get_cache_path().parent / "update-jobs" / job_id / "job.json"
    metadata = {"schema_version": 2, "job_id": job_id, "state": "scheduled",
                "transaction_id": "c" * 32, "version": updater.APP_VERSION,
                "target": str(target.resolve()), "backup_id": backup_id,
                "old_sha256": transaction["from_sha256"], "new_sha256": transaction["to_sha256"]}
    updater._write_update_record(job, metadata)

    def check_in_child():
        ctx = multiprocessing.get_context("spawn")
        receiver, sender = ctx.Pipe(duplex=False)
        process = ctx.Process(target=_check_process, args=(str(tmp_path), str(target), sender))
        process.start()
        sender.close()
        try:
            assert receiver.poll(15), "isolated health process did not report"
            assert receiver.recv() is False
            process.join(10)
            assert process.exitcode == 0
        finally:
            receiver.close()
            if process.is_alive():
                process.terminate()
                process.join(5)
            process.close()

    original = health.read_bytes()
    check_in_child()
    assert health.read_bytes() == original
    updater._write_update_record(job, {**metadata, "transaction_id": transaction["transaction_id"]})
    check_in_child()
    assert updater._read_health_record(health)["fails"] == 1
    assert target.read_bytes() == b"current build"


def test_other_data_directory_process_observes_target_rollback(tmp_path, monkeypatch):
    target = tmp_path / "isolated.exe"
    target.write_bytes(b"isolated current build")
    old = tmp_path / "isolated-old.exe"
    old.write_bytes(b"isolated previous build")
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "first-data"))
    transaction = update_target_state.prepare(
        target, operation="rollback", from_build=updater.APP_VERSION, to_build=updater.APP_VERSION - 1,
        from_digest=updater._sha256_file(target), to_digest=updater._sha256_file(old),
    )
    data_dir = tmp_path / "second-data"
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(data_dir))
    health = updater._get_health_path()
    updater._write_update_record(health, {"build": updater.APP_VERSION, "confirmed": False, "fails": 1})
    before = health.read_bytes()
    context = multiprocessing.get_context("spawn")
    receive, transmit = context.Pipe(duplex=False)
    process = context.Process(target=_confirm_process, args=(str(data_dir), str(target), transmit))
    process.start()
    transmit.close()
    try:
        if not receive.poll(10):
            raise RuntimeError("isolated confirmation process did not respond")
        assert receive.recv() is False
    finally:
        receive.close()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0
    assert health.read_bytes() == before
    assert update_target_state.read(target) == transaction


def test_other_data_directory_without_job_cannot_confirm_upgrade(tmp_path, monkeypatch):
    target = tmp_path / "isolated.exe"
    target.write_bytes(b"isolated current build")
    previous = tmp_path / "previous.exe"
    previous.write_bytes(b"isolated previous build")
    job_id = "a" * 32
    backup_id = "opskit.v1.123." + "b" * 32 + ".bak"
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "first-data"))
    transaction = update_target_state.prepare(
        target, operation="upgrade", from_build=updater.APP_VERSION - 1, to_build=updater.APP_VERSION,
        from_digest=updater._sha256_file(previous), to_digest=updater._sha256_file(target),
    )
    update_target_state.completed(target, transaction)
    health_record = {"build": updater.APP_VERSION, "confirmed": False, "fails": 0,
                     "job_id": job_id, "backup_id": backup_id}
    first_health = updater._get_health_path()
    updater._write_update_record(first_health, health_record)
    updater._write_update_record(updater._get_cache_path().parent / "update-jobs" / job_id / "job.json", {
        "schema_version": 2, "job_id": job_id, "state": "scheduled",
        "transaction_id": transaction["transaction_id"], "version": updater.APP_VERSION,
        "target": str(target.resolve()), "backup_id": backup_id,
        "old_sha256": transaction["from_sha256"], "new_sha256": transaction["to_sha256"],
    })
    second = tmp_path / "second-data"
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(second))
    second_health = updater._get_health_path()
    updater._write_update_record(second_health, health_record)
    before = first_health.read_bytes(), second_health.read_bytes()
    context = multiprocessing.get_context("spawn")
    receive, transmit = context.Pipe(duplex=False)
    process = context.Process(target=_confirm_process, args=(str(second), str(target), transmit))
    process.start()
    transmit.close()
    try:
        if not receive.poll(10):
            raise RuntimeError("isolated confirmation process did not respond")
        assert receive.recv() is False
    finally:
        receive.close()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0
    assert (first_health.read_bytes(), second_health.read_bytes()) == before
    assert update_target_state.read(target) == {**transaction, "phase": "applied"}


def test_invalid_target_transaction_defers_confirmation(tmp_path, monkeypatch):
    target = tmp_path / "isolated.exe"
    target.write_bytes(b"isolated current build")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    health = updater._get_health_path()
    updater._write_update_record(health, {"build": updater.APP_VERSION, "confirmed": False, "fails": 0})
    state = update_target_state.path(target)
    updater._write_update_record(state, {"operation": "invalid"})
    before = health.read_bytes(), state.read_bytes()

    assert updater.confirm_health() is False
    assert (health.read_bytes(), state.read_bytes()) == before


def test_health_confirmation_defers_when_target_lock_is_busy(tmp_path, monkeypatch):
    target = tmp_path / "isolated.exe"
    target.write_bytes(b"isolated current build")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    health = updater._get_health_path()
    updater._write_update_record(health, {"build": updater.APP_VERSION, "confirmed": False, "fails": 0})
    acquired, release = threading.Event(), threading.Event()
    errors = []

    def hold_target():
        try:
            with updater._update_target_resource.hold():
                acquired.set()
                if not release.wait(10):
                    raise RuntimeError("isolated target lock release timed out")
        except Exception as error:
            errors.append(error)
            acquired.set()

    thread = threading.Thread(target=hold_target)
    thread.start()
    try:
        if not acquired.wait(10) or errors:
            raise RuntimeError("isolated target lock could not be acquired")
        assert updater.confirm_health() is False
        assert updater._read_health_record(health)["confirmed"] is False
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and not errors
    assert updater.confirm_health() is True


@pytest.mark.parametrize("with_job", [True, False])
def test_aborted_rollback_job_requires_proven_original_update(tmp_path, monkeypatch, with_job):
    target = tmp_path / "isolated.exe"
    target.write_bytes(b"recorded new executable")
    previous = tmp_path / "previous.exe"
    previous.write_bytes(b"recorded old executable")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    health = updater._get_health_path()
    data = {"build": updater.APP_VERSION, "confirmed": False, "fails": 0}
    if with_job:
        data.update(job_id="a" * 32, backup_id="opskit.v1.123." + "b" * 32 + ".bak")
    updater._write_update_record(health, data)
    transaction = update_target_state.prepare(
        target, operation="rollback", from_build=updater.APP_VERSION,
        to_build=updater.APP_VERSION - 1, from_digest=updater._sha256_file(target),
        to_digest=updater._sha256_file(previous),
    )
    update_target_state.aborted(target, transaction)
    original = health.read_bytes(), target.read_bytes(), update_target_state.path(target).read_bytes()

    assert updater.confirm_health() is (not with_job)
    if with_job:
        assert (health.read_bytes(), target.read_bytes(), update_target_state.path(target).read_bytes()) == original
    else:
        assert updater._read_health_record(health)["confirmed"] is True


@pytest.mark.parametrize("job_condition", ["missing", "wrong_transaction", "wrong_owner", "copied_evidence", "valid"])
def test_rollback_job_must_match_owning_upgrade_before_target_change(tmp_path, monkeypatch, job_condition):
    target = tmp_path / "isolated.exe"
    target.write_bytes(b"recorded current executable")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    health = updater._get_health_path()
    backup_id = "opskit.v1.123." + "b" * 32 + ".bak"
    job_id = "a" * 32
    backup = tmp_path / "backups" / backup_id
    backup.parent.mkdir()
    backup.write_bytes(b"recorded previous executable")
    old_digest, new_digest = updater._sha256_file(backup), updater._sha256_file(target)
    updater._write_update_record(health, {
        "build": updater.APP_VERSION, "confirmed": False, "fails": 2,
        "job_id": job_id, "backup_id": backup_id,
    })
    updater._write_update_record(backup.with_name(backup.name + ".json"), {
        "schema_version": 1, "backup_id": backup_id, "target": str(target.resolve()),
        "from_build": updater.APP_VERSION - 1, "to_build": updater.APP_VERSION,
        "sha256": old_digest, "replacement_sha256": new_digest,
    })
    transaction = update_target_state.prepare(
        target, operation="upgrade", from_build=updater.APP_VERSION - 1,
        to_build=updater.APP_VERSION, from_digest=old_digest, to_digest=new_digest,
        owner_data_dir=tmp_path,
    )
    update_target_state.completed(target, transaction)
    if job_condition != "missing":
        metadata = {
            "schema_version": 2, "job_id": job_id, "state": "scheduled",
            "transaction_id": "c" * 32 if job_condition == "wrong_transaction" else transaction["transaction_id"],
            "version": updater.APP_VERSION, "target": str(target.resolve()), "backup_id": backup_id,
            "old_sha256": old_digest, "new_sha256": new_digest,
        }
        if job_condition == "wrong_owner":
            metadata["owner_data_dir"] = update_target_state.data_directory_identity(tmp_path / "another-data")
        updater._write_update_record(updater._get_cache_path().parent / "update-jobs" / job_id / "job.json", metadata)
    before = health.read_bytes(), backup.read_bytes(), update_target_state.path(target).read_bytes()
    if job_condition == "copied_evidence":
        copied = tmp_path / "other-data"
        shutil.copytree(tmp_path / "backups", copied / "backups")
        shutil.copytree(tmp_path / "cache", copied / "cache")
        monkeypatch.setenv("OPSKIT_DATA_DIR", str(copied))
        copied_health = updater._get_health_path()
        copied_record = updater._read_health_record(copied_health)
        updater._write_update_record(copied_health, {**copied_record, "fails": 0})
        copied_before = copied_health.read_bytes()
        assert updater._check_health() is False
        assert updater.confirm_health() is False
        assert copied_health.read_bytes() == copied_before

    assert updater.rollback() is (job_condition == "valid")
    assert health.read_bytes() == before[0] and backup.read_bytes() == before[1]
    if job_condition == "valid":
        assert updater._sha256_file(target) == old_digest
    else:
        assert updater._sha256_file(target) == new_digest
        assert update_target_state.path(target).read_bytes() == before[2]


@pytest.mark.parametrize("retry_condition", ["valid", "missing_job", "copied_evidence", "wrong_transaction"])
def test_job_bound_rollback_retry_keeps_backup_and_requires_job(tmp_path, monkeypatch, retry_condition):
    target = tmp_path / "isolated.exe"
    target.write_bytes(b"recorded current executable")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    health = updater._get_health_path()
    backup_id = "opskit.v1.123." + "b" * 32 + ".bak"
    job_id = "a" * 32
    backup = tmp_path / "backups" / backup_id
    backup.parent.mkdir()
    backup.write_bytes(b"recorded previous executable")
    old_digest, new_digest = updater._sha256_file(backup), updater._sha256_file(target)
    updater._write_update_record(health, {
        "build": updater.APP_VERSION, "confirmed": False, "fails": 2,
        "job_id": job_id, "backup_id": backup_id,
    })
    updater._write_update_record(backup.with_name(backup.name + ".json"), {
        "schema_version": 1, "backup_id": backup_id, "target": str(target.resolve()),
        "from_build": updater.APP_VERSION - 1, "to_build": updater.APP_VERSION,
        "sha256": old_digest, "replacement_sha256": new_digest,
    })
    transaction = update_target_state.prepare(
        target, operation="upgrade", from_build=updater.APP_VERSION - 1,
        to_build=updater.APP_VERSION, from_digest=old_digest, to_digest=new_digest,
    )
    update_target_state.completed(target, transaction)
    job = updater._get_cache_path().parent / "update-jobs" / job_id / "job.json"
    updater._write_update_record(job, {
        "schema_version": 2, "job_id": job_id, "state": "scheduled",
        "transaction_id": transaction["transaction_id"], "version": updater.APP_VERSION,
        "target": str(target.resolve()), "backup_id": backup_id,
        "old_sha256": old_digest, "new_sha256": new_digest,
    })
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise PermissionError("isolated failure")
        patch.setattr(updater, "_atomic_copy", fail)
        assert updater.rollback() is False
    prepared = update_target_state.read(target)
    assert prepared["operation"] == "rollback" and prepared["phase"] == "prepared"
    assert prepared["owner_data_dir"] == update_target_state.data_directory_identity(tmp_path)
    assert prepared["source_transaction_id"] == transaction["transaction_id"]
    before = health.read_bytes(), backup.read_bytes(), target.read_bytes()
    if retry_condition == "missing_job":
        job.unlink()
    elif retry_condition == "copied_evidence":
        copied = tmp_path / "other-data"
        shutil.copytree(tmp_path / "backups", copied / "backups")
        shutil.copytree(tmp_path / "cache", copied / "cache")
        monkeypatch.setenv("OPSKIT_DATA_DIR", str(copied))
    elif retry_condition == "wrong_transaction":
        metadata = updater._read_update_record(job)
        updater._write_update_record(job, {**metadata, "transaction_id": "c" * 32})

    attempts = []
    def attempted_rollback():
        attempts.append(True)
        return False
    with monkeypatch.context() as patch:
        patch.setattr(updater, "rollback", attempted_rollback)
        assert updater._check_health() is False
    assert bool(attempts) is (retry_condition == "valid")
    assert health.read_bytes() == before[0]

    assert updater.rollback() is (retry_condition == "valid")
    assert health.read_bytes() == before[0] and backup.read_bytes() == before[1]
    if retry_condition != "valid":
        assert target.read_bytes() == before[2]
        assert update_target_state.read(target) == prepared
    else:
        assert updater._sha256_file(target) == old_digest
        assert update_target_state.read(target)["phase"] == "applied"
