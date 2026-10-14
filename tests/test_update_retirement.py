"""Rollback retirement requires target/backup/health and exited-helper proof."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from core import updater, update_jobs, update_job_cache, update_target_state, update_retirement
from core.update_reconcile import reconcile
from test_update_reconcile import recovery as recovery, NATIVE_HELPER_STATUS


@pytest.fixture
def rolled_job(recovery):
    target, backup, job, metadata, transaction = recovery
    target.write_bytes(b"new")
    updater._write_update_record(updater._get_health_path(), {"build": metadata["version"], "confirmed": False,
        "fails": updater.MAX_HEALTH_FAILS - 1, "job_id": job.name, "backup_id": backup.name})
    updater._write_update_record(job / "running.json", {"schema_version": 1, "job_id": job.name,
        "version": metadata["version"], "created_ticks": 123, "pid": os.getpid(),
        "script": str(job / "apply.ps1"), "executable": str(Path(sys.executable).resolve())})
    (job / "artifact.exe").write_bytes(b"new")
    assert updater.rollback()
    return recovery


def test_retirement_is_durable_before_index_moves_to_history(rolled_job, monkeypatch):
    target, backup, job, metadata, transaction = rolled_job
    health_before = updater._get_health_path().read_bytes()
    report = reconcile()
    assert report == {"id": job.name, "version": metadata["version"], "state": "rolled_back",
                      "active": False, "restored_version": transaction["from_build"]}
    retired = updater._read_update_record(job / "job.json")
    assert retired["state"] == "retired" and update_retirement.receipt(retired)["outcome"] == "rolled_back"
    cache = updater._read_update_record(updater._get_cache_path())
    assert cache["apply_job"] is None and cache["last_apply_job"] == job.name
    assert reconcile() == {"state": "no_job"}
    # The source-mode fixture cannot carry mocked executable identity into the
    # spawned frozen worker. Exercise boot discovery with this fixture's target.
    from core import update_cli
    from core.update_reconcile import reconcile_on_boot
    monkeypatch.setattr(update_cli, "reconcile_on_boot", lambda **kwargs: reconcile_on_boot())
    assert updater._reconcile_indexed_job_on_boot() == (False, False)
    from core.update_cli import status
    snapshot = status(timeout=15)["apply_job"]
    assert snapshot["state"] == "rolled_back" and snapshot["active"] is False
    assert snapshot["restored_version"] == transaction["from_build"] and not snapshot["health_confirmed"]
    assert target.read_bytes() == b"old" and backup.read_bytes() == b"old"
    assert updater._get_health_path().read_bytes() == health_before


@pytest.mark.parametrize("failure", ["ownership", "index"])
def test_retirement_write_interruption_is_retryable(rolled_job, monkeypatch, failure):
    target, backup, job, metadata, transaction = rolled_job
    cache_path = updater._get_cache_path()
    write = updater._write_update_record
    with monkeypatch.context() as patch:
        def fail(path, record):
            if ((failure == "ownership" and path == job / "job.json" and record.get("state") == "retired")
                    or (failure == "index" and path == cache_path and record.get("last_apply_job") == job.name)):
                raise OSError("retirement write interrupted")
            return write(path, record)
        patch.setattr(updater, "_write_update_record", fail)
        with pytest.raises(OSError, match="interrupted"):
            reconcile()
    assert updater._read_update_record(cache_path)["apply_job"] == job.name
    assert target.read_bytes() == b"old" and backup.exists()
    assert updater._read_update_record(job / "job.json")["state"] == ("retired" if failure == "index" else "scheduled")
    assert reconcile()["state"] == "rolled_back"
    assert updater._read_update_record(cache_path)["last_apply_job"] == job.name


@pytest.mark.parametrize("helper", ["alive", "unknown", "identity_mismatch"])
def test_uncertain_helper_cannot_retire(rolled_job, monkeypatch, helper):
    target, backup, job, metadata, transaction = rolled_job
    monkeypatch.setattr(update_jobs, "helper_status", lambda *args: helper)
    before = (job / "job.json").read_bytes(), updater._get_cache_path().read_bytes()
    assert reconcile()["state"] == "deferred"
    assert ((job / "job.json").read_bytes(), updater._get_cache_path().read_bytes()) == before


@pytest.mark.parametrize("change", ["health-job", "health-backup", "backup", "target"])
def test_retirement_refuses_changed_authority(rolled_job, change):
    target, backup, job, metadata, transaction = rolled_job
    if change.startswith("health"):
        record = updater._read_update_record(updater._get_health_path())
        updater._write_update_record(updater._get_health_path(), {**record,
            "job_id" if change == "health-job" else "backup_id": "b" * 32})
    elif change == "backup":
        backup.write_bytes(b"tampered")
    else:
        target.write_bytes(b"unrelated")
    before = (job / "job.json").read_bytes(), updater._get_cache_path().read_bytes()
    with pytest.raises(ValueError):
        reconcile()
    assert ((job / "job.json").read_bytes(), updater._get_cache_path().read_bytes()) == before


def test_last_history_and_recovery_backup_pin_retired_job(rolled_job, monkeypatch):
    target, backup, job, metadata, transaction = rolled_job
    reconcile()
    monkeypatch.setattr(update_job_cache, "MAX_JOBS", 1)
    with updater._update_resource.hold(), pytest.raises(RuntimeError, match="quota exhausted"):
        update_job_cache.reserve_job(updater._get_cache_path(), 100)
    assert (job / "job.json").exists() and backup.exists()


def unpin(job):
    cache = updater._get_cache_path()
    updater._write_update_record(cache, {"apply_job": None, "last_apply_job": None})
    updater._write_update_record(updater._get_health_path(), {"build": updater.APP_VERSION + 2,
        "confirmed": True, "fails": 0, "job_id": "b" * 32})


def test_unreferenced_retired_job_reclaims_after_pid_reuse_and_partial_cleanup(rolled_job, monkeypatch):
    target, backup, job, metadata, transaction = rolled_job
    reconcile()
    unpin(job)
    monkeypatch.setattr(update_job_cache, "MAX_JOBS", 1)
    monkeypatch.setattr(update_jobs, "helper_status", lambda *args: pytest.fail("durable exit proof must survive PID reuse"))
    unlink = update_job_cache.unlink_owned
    with monkeypatch.context() as patch:
        def fail(path, previous):
            if path.name == "running.json":
                raise OSError("unlink interrupted")
            return unlink(path, previous)
        patch.setattr(update_job_cache, "unlink_owned", fail)
        with updater._update_resource.hold(), pytest.raises(OSError, match="interrupted"):
            update_job_cache.reserve_job(updater._get_cache_path(), 100)
    assert (job / "job.json").exists()
    assert update_retirement.receipt(updater._read_update_record(job / "job.json")) is not None
    with updater._update_resource.hold():
        assert update_job_cache.reserve_job(updater._get_cache_path(), 100)["removed"] == 1
    assert not job.exists() and target.read_bytes() == b"old" and backup.exists()


@pytest.mark.parametrize("field,value", [("helper_created_ticks", True), ("restored_build", True),
    ("target_sha256", "c" * 64), ("rollback_transaction_id", "bad"), ("outcome", "unknown")])
def test_bad_retirement_aborts_cleanup(rolled_job, monkeypatch, field, value):
    target, backup, job, metadata, transaction = rolled_job
    reconcile()
    record = updater._read_update_record(job / "job.json")
    updater._write_update_record(job / "job.json", {**record, "retirement": {**record["retirement"], field: value}})
    unpin(job)
    monkeypatch.setattr(update_job_cache, "MAX_JOBS", 1)
    with updater._update_resource.hold(), pytest.raises(ValueError, match="retirement"):
        update_job_cache.reserve_job(updater._get_cache_path(), 100)
    assert (job / "job.json").exists() and (job / "artifact.exe").exists()


def test_index_cannot_be_cleared_without_retirement_proof(recovery):
    target, backup, job, metadata, transaction = recovery
    before = updater._get_cache_path().read_bytes()
    with pytest.raises(ValueError, match="durable proof"):
        update_retirement.clear_index(updater._get_cache_path(), metadata)
    assert updater._get_cache_path().read_bytes() == before


def test_interrupted_restore_preserves_transaction_and_readonly_reconcile(recovery, monkeypatch):
    target, backup, job, metadata, original = recovery
    target.write_bytes(b"new")
    updater._write_update_record(updater._get_health_path(), {"build": metadata["version"], "confirmed": False,
        "fails": updater.MAX_HEALTH_FAILS - 1, "job_id": job.name, "backup_id": backup.name})
    updater._write_update_record(job / "running.json", {"schema_version": 1, "job_id": job.name,
        "version": metadata["version"], "created_ticks": 123})
    with monkeypatch.context() as patch:
        patch.setattr(updater, "_atomic_copy", lambda *a, **k: (_ for _ in ()).throw(OSError("interrupted restore")))
        assert updater.rollback() is False
    pending = update_target_state.read(target)
    assert pending["operation"] == "rollback" and pending["phase"] == "prepared"
    before = target.read_bytes(), update_target_state.path(target).read_bytes(), updater._get_cache_path().read_bytes()
    assert reconcile() == {"id": job.name, "state": "deferred", "reason": "rollback_pending"}
    assert (target.read_bytes(), update_target_state.path(target).read_bytes(), updater._get_cache_path().read_bytes()) == before
    assert updater.rollback() is True
    committed = update_target_state.read(target)
    assert committed["transaction_id"] == pending["transaction_id"] and committed["phase"] == "applied"
    assert target.read_bytes() == backup.read_bytes() == b"old"
    assert reconcile()["state"] == "rolled_back"


@pytest.mark.parametrize("phase", ["aborted", "applied"])
def test_uncommitted_bytes_do_not_revive_closed_rollback(recovery, phase):
    target, backup, job, metadata, original = recovery
    target.write_bytes(b"new")
    updater._write_update_record(updater._get_health_path(), {"build": metadata["version"], "confirmed": False,
        "fails": 2, "job_id": job.name, "backup_id": backup.name})
    pending = update_target_state.prepare(target, operation="rollback", from_build=metadata["version"],
        to_build=original["from_build"], from_digest=metadata["new_sha256"], to_digest=metadata["old_sha256"])
    updater._write_update_record(update_target_state.path(target), {**pending, "phase": phase})
    before = target.read_bytes(), update_target_state.path(target).read_bytes()
    assert updater.rollback() is False
    assert (target.read_bytes(), update_target_state.path(target).read_bytes()) == before


def pending_rollback(recovery):
    target, backup, job, metadata, original = recovery
    target.write_bytes(b"new")
    updater._write_update_record(updater._get_health_path(), {"build": metadata["version"], "confirmed": False,
        "fails": 2, "job_id": job.name, "backup_id": backup.name})
    updater._write_update_record(job / "running.json", {"schema_version": 1, "job_id": job.name,
        "version": metadata["version"], "created_ticks": 123})
    return update_target_state.prepare(target, operation="rollback", from_build=metadata["version"],
        to_build=original["from_build"], from_digest=metadata["new_sha256"], to_digest=metadata["old_sha256"])


def test_boot_resumes_pending_restore_before_post_update_cleanup(recovery, monkeypatch):
    from core import update_cli, update_reconcile
    target, backup, job, metadata, original = recovery
    pending = pending_rollback(recovery)
    monkeypatch.setattr(update_cli, "reconcile_on_boot", lambda **kw: update_reconcile.reconcile_on_boot())
    monkeypatch.setattr(updater.sys, "argv", ["opskit", "--post-update"])
    monkeypatch.setattr(updater, "_get_pending_path", lambda: pytest.fail("must restart before cleanup"))
    assert updater.check_and_apply_pending() is True
    assert target.read_bytes() == b"old"
    assert update_target_state.read(target)["transaction_id"] == pending["transaction_id"]
    assert updater._read_update_record(job / "job.json")["state"] == "retired"


@pytest.mark.parametrize("helper", ["alive", "unknown", "identity_mismatch"])
def test_boot_never_resumes_rollback_with_uncertain_helper(recovery, monkeypatch, helper):
    from core.update_reconcile import reconcile_on_boot
    target, backup, job, metadata, original = recovery
    pending_rollback(recovery)
    monkeypatch.setattr(update_jobs, "helper_status", lambda *args: helper)
    before = target.read_bytes(), update_target_state.path(target).read_bytes()
    assert reconcile_on_boot()["reason"] == "helper_" + helper
    assert (target.read_bytes(), update_target_state.path(target).read_bytes()) == before


def test_boot_restoration_retirement_failure_still_requires_restart(recovery, monkeypatch):
    from core.update_reconcile import reconcile_on_boot
    target, backup, job, metadata, original = recovery
    pending_rollback(recovery)
    def interrupted(*args):
        raise OSError("retirement interrupted")
    monkeypatch.setattr(update_retirement, "rolled_back", interrupted)
    report = reconcile_on_boot()
    assert report["restart_required"] is True and report["retirement_pending"] is True
    assert target.read_bytes() == b"old"
    assert updater._read_update_record(updater._get_cache_path())["apply_job"] == job.name


def test_boot_restore_failure_retains_pending_transaction(recovery, monkeypatch):
    from core.update_reconcile import reconcile_on_boot
    target, backup, job, metadata, original = recovery
    pending = pending_rollback(recovery)
    def interrupted(*args, **kwargs):
        raise OSError("restore interrupted")
    monkeypatch.setattr(updater, "_atomic_copy", interrupted)
    assert reconcile_on_boot()["reason"] == "rollback_pending"
    assert update_target_state.read(target) == pending and target.read_bytes() == b"new"


def test_download_rejects_rolled_back_build_before_transfer(rolled_job, monkeypatch):
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from core import update_download
    from test_update_download import Capture
    target, backup, job, metadata, transaction = rolled_job
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    monkeypatch.setattr(updater, "query_update_target", lambda cfg: (metadata["version"], "https://example.org/asset", None))
    monkeypatch.setattr(updater, "_download_update", lambda *args: pytest.fail("bad version must not transfer"))
    before = updater._get_cache_path().read_bytes()
    connection = Capture()
    update_download._worker(connection, str(updater._get_cache_path()), time.monotonic() + 10,
                            {"signing_public_key": public})
    assert connection.result["ok"] is False and "version_policy" in connection.result["error"]
    assert updater._get_cache_path().read_bytes() == before
    assert not (updater._get_cache_path().parent / "update-candidates").exists()


def test_readonly_check_excludes_rolled_back_version(rolled_job, monkeypatch):
    from core import config, update_cli
    from test_update_download import Capture
    target, backup, job, metadata, transaction = rolled_job
    monkeypatch.setattr(config, "load_config", lambda **kwargs: {"update": {}})
    monkeypatch.setattr(updater, "query_update_target", lambda cfg: (metadata["version"], "https://example.org/asset", None))
    before = update_target_state.path(target).read_bytes(), updater._get_cache_path().read_bytes()
    connection = Capture()
    update_cli._check_worker(connection)
    assert connection.result["ok"] is True
    assert connection.result["data"]["available"] is False
    assert connection.result["data"]["reason"] == "rolled_back_version"
    assert "candidate_version" not in connection.result["data"]
    assert (update_target_state.path(target).read_bytes(), updater._get_cache_path().read_bytes()) == before


def test_retired_unknown_user_file_is_never_reclaimed(rolled_job, monkeypatch):
    target, backup, job, metadata, transaction = rolled_job
    reconcile()
    unpin(job)
    private = job / "user-notes.txt"
    private.write_text("keep")
    monkeypatch.setattr(update_job_cache, "MAX_JOBS", 1)
    with updater._update_resource.hold(), pytest.raises(RuntimeError, match="quota exhausted"):
        update_job_cache.reserve_job(updater._get_cache_path(), 100)
    assert private.read_text() == "keep" and (job / "job.json").exists()


def test_retirement_does_not_clear_other_active_job(rolled_job):
    target, backup, job, metadata, transaction = rolled_job
    reconcile()
    retired = updater._read_update_record(job / "job.json")
    cache = updater._get_cache_path()
    updater._write_update_record(cache, {"apply_job": "b" * 32, "pending_version": 999})
    before = cache.read_bytes()
    update_retirement.clear_index(cache, retired)
    assert cache.read_bytes() == before


@pytest.mark.skipif(os.name != "nt", reason="native Windows rollback retirement")
@pytest.mark.parametrize("interrupted_restore", [False, True])
def test_real_helper_commit_rollback_and_retirement_preserve_history(recovery, monkeypatch, interrupted_restore):
    from core.update_windows import script
    from core.update_cli import status
    target, backup, job, metadata, transaction = recovery
    executable = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    if not executable.is_file():
        pytest.skip("Windows PowerShell unavailable")
    monkeypatch.setattr(update_jobs, "helper_status", NATIVE_HELPER_STATUS)
    metadata["helper_executable"] = str(executable.resolve())
    updater._write_update_record(job / "job.json", metadata)
    artifact = job / "artifact.exe"
    artifact.write_bytes(b"new")
    updater._update_lock_path().parent.mkdir(parents=True, exist_ok=True)
    helper = job / "apply.ps1"
    helper.write_bytes(script(target=target, artifact=artifact, result=job / "result.json",
        health=updater._get_health_path(), lock=updater._update_lock_path(),
        old_digest=metadata["old_sha256"], new_digest=metadata["new_sha256"], version=metadata["version"],
        parent_pid=0, target_lock=updater._update_target_lock_path(target),
        recovery={"job_id": job.name, "backup_id": backup.name, "transaction": transaction}).encode("utf-8-sig"))
    process = subprocess.Popen([str(executable), "-NoProfile", "-NonInteractive", "-File", str(helper)],
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert process.wait(timeout=20) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    assert target.read_bytes() == b"new" and NATIVE_HELPER_STATUS(job, metadata) == "exited"
    if interrupted_restore:
        from core.update_reconcile import reconcile_on_boot
        with monkeypatch.context() as patch:
            def interrupted(*args, **kwargs):
                raise OSError("restore interrupted")
            patch.setattr(updater, "_atomic_copy", interrupted)
            assert updater.rollback() is False
        prepared = update_target_state.read(target)
        assert reconcile()["reason"] == "rollback_pending"
        assert reconcile_on_boot()["restart_required"] is True
        assert update_target_state.read(target)["transaction_id"] == prepared["transaction_id"]
    else:
        assert updater.rollback()
    assert target.read_bytes() == b"old"
    assert reconcile()["state"] == ("no_job" if interrupted_restore else "rolled_back")
    (job / "result.json").write_text("lost old result")
    report = status(timeout=15)["apply_job"]
    assert report["state"] == "rolled_back" and report["active"] is False
    assert report["helper_status"] == "exited" and report["restored_version"] == transaction["from_build"]
    proof = update_retirement.receipt(updater._read_update_record(job / "job.json"))
    assert proof["helper_created_ticks"] == updater._read_update_record(job / "running.json")["created_ticks"]
    assert updater._read_update_record(updater._get_cache_path())["apply_job"] is None
    assert backup.read_bytes() == b"old" and target.read_bytes() == b"old"
