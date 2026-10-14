"""Terminal upgrades must release active indexes only after durable proof."""
import os

import pytest

from core import updater, update_jobs, update_target_state, update_retirement
from core.update_reconcile import reconcile
from test_update_reconcile import recovery as recovery


@pytest.mark.parametrize("outcome", ["applied", "failed", "not_launched"])
def test_proven_upgrade_terminal_is_retired_before_index_release(recovery, monkeypatch, outcome):
    target, backup, job, metadata, transaction = recovery
    if outcome == "not_launched":
        updater._write_update_record(job / "job.json", {**metadata, "state": "preparing"})
        monkeypatch.setattr(update_jobs, "helper_status", lambda *args: pytest.fail("unlaunched job has no process identity"))
    else:
        updater._write_update_record(job / "running.json", {"schema_version": 1,
            "job_id": job.name, "version": metadata["version"], "created_ticks": 123,
            "pid": os.getpid()})
    if outcome == "applied":
        target.write_bytes(b"new")
        updater._write_update_record(updater._get_health_path(), {"build": metadata["version"],
            "confirmed": True, "fails": 0, "backup_id": backup.name, "job_id": job.name})
    report = reconcile()
    assert report["state"] == ("applied" if outcome == "applied" else "failed")
    retired = updater._read_update_record(job / "job.json")
    assert retired["state"] == "retired"
    assert update_retirement.receipt(retired)["outcome"] == ("applied" if outcome == "applied" else "failed")
    cache = updater._read_update_record(updater._get_cache_path())
    assert cache["apply_job"] is None and cache["last_apply_job"] == job.name
    assert reconcile() == {"state": "no_job"}
    assert target.read_bytes() == (b"new" if outcome == "applied" else b"old")
    assert backup.read_bytes() == b"old"
    assert update_target_state.read(target)["phase"] == ("applied" if outcome == "applied" else "aborted")


def test_unconfirmed_applied_job_keeps_active_recovery_and_backup(recovery):
    target, backup, job, metadata, transaction = recovery
    target.write_bytes(b"new")
    assert reconcile()["state"] == "applied"
    assert updater._read_update_record(job / "job.json")["state"] == "scheduled"
    assert updater._read_update_record(updater._get_cache_path())["apply_job"] == job.name
    assert backup.exists()


@pytest.mark.parametrize("helper", ["alive", "unknown", "identity_mismatch"])
def test_terminal_bytes_without_exited_identity_do_not_retire(recovery, monkeypatch, helper):
    target, backup, job, metadata, transaction = recovery
    target.write_bytes(b"new")
    updater._write_update_record(updater._get_health_path(), {"build": metadata["version"],
        "confirmed": True, "fails": 0, "backup_id": backup.name, "job_id": job.name})
    monkeypatch.setattr(update_jobs, "helper_status", lambda *args: helper)
    before = (job / "job.json").read_bytes(), updater._get_cache_path().read_bytes()
    assert reconcile() == {"id": job.name, "state": "deferred", "reason": "helper_" + helper}
    assert ((job / "job.json").read_bytes(), updater._get_cache_path().read_bytes()) == before


@pytest.mark.parametrize("failure", ["ownership", "index"])
def test_unlaunched_retirement_publication_interruption_retries(recovery, monkeypatch, failure):
    target, backup, job, metadata, transaction = recovery
    updater._write_update_record(job / "job.json", {**metadata, "state": "preparing"})
    write = updater._write_update_record
    with monkeypatch.context() as patch:
        def interrupted(path, record):
            if ((failure == "ownership" and path == job / "job.json" and record.get("state") == "retired")
                    or (failure == "index" and path == updater._get_cache_path() and record.get("last_apply_job") == job.name)):
                raise OSError("terminal retirement interrupted")
            return write(path, record)
        patch.setattr(updater, "_write_update_record", interrupted)
        with pytest.raises(OSError, match="interrupted"):
            reconcile()
    assert updater._read_update_record(updater._get_cache_path())["apply_job"] == job.name
    assert target.read_bytes() == backup.read_bytes() == b"old"
    assert reconcile()["state"] == "failed"
    assert updater._read_update_record(updater._get_cache_path())["last_apply_job"] == job.name
    assert update_retirement.receipt(updater._read_update_record(job / "job.json"))["helper_proof"] == "not_launched"


@pytest.mark.parametrize("field,value", [("helper_proof", []), ("outcome", []),
    ("helper_created_ticks", 123), ("terminal_build", True), ("target_sha256", "c" * 64)])
def test_bad_terminal_proof_cannot_release_index(recovery, field, value):
    target, backup, job, metadata, transaction = recovery
    updater._write_update_record(job / "job.json", {**metadata, "state": "preparing"})
    reconcile()
    retired = updater._read_update_record(job / "job.json")
    updater._write_update_record(job / "job.json", {**retired,
        "retirement": {**retired["retirement"], field: value}})
    updater._write_update_record(updater._get_cache_path(), {"apply_job": job.name})
    before = updater._get_cache_path().read_bytes()
    with pytest.raises(ValueError, match="retirement"):
        reconcile()
    assert updater._get_cache_path().read_bytes() == before


def test_unreferenced_terminal_job_reclaims_without_process_probe(recovery, monkeypatch):
    from core import update_job_cache
    target, backup, job, metadata, transaction = recovery
    updater._write_update_record(job / "job.json", {**metadata, "state": "preparing"})
    (job / "artifact.exe").write_bytes(b"new")
    reconcile()
    updater._write_update_record(updater._get_cache_path(), {"apply_job": None, "last_apply_job": None})
    monkeypatch.setattr(update_job_cache, "MAX_JOBS", 1)
    monkeypatch.setattr(update_jobs, "helper_status", lambda *args: pytest.fail("durable proof needs no PID query"))
    with updater._update_resource.hold():
        assert update_job_cache.reserve_job(updater._get_cache_path(), 100)["removed"] == 1
    assert not job.exists() and target.read_bytes() == backup.read_bytes() == b"old"


def test_retired_failure_has_readonly_history(recovery):
    from core.update_cli import status
    target, backup, job, metadata, transaction = recovery
    updater._write_update_record(job / "job.json", {**metadata, "state": "preparing"})
    reconcile()
    before = updater._get_cache_path().read_bytes(), (job / "job.json").read_bytes()
    snapshot = status(timeout=15)["apply_job"]
    assert snapshot["state"] == "failed" and snapshot["active"] is False
    assert snapshot["reconciled"] is True and snapshot["health_confirmed"] is False
    assert "restored_version" not in snapshot
    assert (updater._get_cache_path().read_bytes(), (job / "job.json").read_bytes()) == before
