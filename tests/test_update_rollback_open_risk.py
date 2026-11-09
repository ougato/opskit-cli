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


def _failed_decision_process(directory, ready, release, error_seen):
    os.environ["OPSKIT_DATA_DIR"] = directory
    from core import updater as child_updater
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
    except Exception:
        error_seen.value = 1
    finally:
        ready.set()
        release.wait(10)


def _confirm_process(directory, target, connection):
    os.environ["OPSKIT_DATA_DIR"] = directory
    from core import updater as child_updater
    child_updater._self_path = lambda: Path(target)
    child_updater._is_frozen = lambda: True
    try:
        connection.send(child_updater.confirm_health())
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
    assert updater._check_health() is False
    assert not rollback_calls
    assert updater._read_health_record(health) == original


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="post-update boot currently bypasses the health decision")
def test_post_update_boot_checks_unconfirmed_health(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "_reconcile_indexed_job_on_boot", lambda: (False, False))
    checks = []
    monkeypatch.setattr(updater, "_check_health", lambda: checks.append(True) or False)
    assert updater.check_and_apply_pending(post_update=True) is False
    assert checks == [True]


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

    assert updater.confirm_health() is True
    assert updater._read_health_record(health)["confirmed"] is True


@pytest.mark.parametrize("phase,contents,from_build,to_build", [
    ("prepared", b"not the recorded update", updater.APP_VERSION - 1, updater.APP_VERSION),
    ("aborted", b"previous executable", updater.APP_VERSION - 1, updater.APP_VERSION),
    ("applied", b"recorded update", updater.APP_VERSION, updater.APP_VERSION + 1),
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

    assert updater.confirm_health() is True
    assert updater._read_health_record(health)["confirmed"] is True


@pytest.mark.parametrize("job_condition", ["missing", "wrong_transaction", "valid"])
def test_health_job_must_match_target_transaction_and_backup(tmp_path, monkeypatch, job_condition):
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

    assert updater.confirm_health() is (job_condition == "valid")
    if job_condition == "valid":
        assert updater._read_health_record(health)["confirmed"] is True
    else:
        assert health.read_bytes() == original


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
