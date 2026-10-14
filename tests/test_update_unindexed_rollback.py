"""Losing the cache index must not lose authenticated rollback recovery."""
import pytest

from core import updater, update_target_state
from core.update_reconcile import reconcile, reconcile_on_boot
from test_update_reconcile import recovery as recovery
from test_update_retirement import rolled_job as rolled_job


@pytest.mark.parametrize("missing_cache", [False, True])
def test_committed_unindexed_rollback_retires_and_moves_index_to_history(rolled_job, missing_cache):
    target, backup, job, metadata, original = rolled_job
    cache = updater._get_cache_path()
    if missing_cache:
        cache.unlink()
    else:
        updater._write_update_record(cache, {"keep": "value"})
    assert reconcile()["state"] == "rolled_back"
    record = updater._read_update_record(cache)
    assert record["apply_job"] is None and record["last_apply_job"] == job.name
    if not missing_cache:
        assert record["keep"] == "value"
    assert target.read_bytes() == backup.read_bytes() == b"old"
    assert updater._read_update_record(job / "job.json")["state"] == "retired"


@pytest.mark.parametrize("authority", ["health_job", "health_backup", "backup", "target",
    "missing_health", "job_transaction", "closed_phase", "phase_bytes", "job_version"])
def test_unindexed_rollback_refuses_changed_authority_without_index_mutation(rolled_job, authority):
    target, backup, job, metadata, original = rolled_job
    cache = updater._get_cache_path()
    updater._write_update_record(cache, {"keep": "value"})
    if authority.startswith("health"):
        path = updater._get_health_path()
        record = updater._read_update_record(path)
        field = "job_id" if authority == "health_job" else "backup_id"
        updater._write_update_record(path, {**record, field: "b" * 32})
    elif authority == "backup":
        backup.write_bytes(b"tampered")
    elif authority == "missing_health":
        updater._get_health_path().unlink()
    elif authority in {"job_transaction", "job_version"}:
        field, value = ("transaction_id", update_target_state.read(target)["transaction_id"]) if authority == "job_transaction" else ("version", True)
        updater._write_update_record(job / "job.json", {**metadata, field: value})
    elif authority == "closed_phase":
        path = update_target_state.path(target)
        record = updater._read_update_record(path)
        updater._write_update_record(path, {**record, "phase": "aborted"})
    elif authority == "phase_bytes":
        target.write_bytes(b"new")  # Applied rollback cannot still have new bytes.
    else:
        target.write_bytes(b"unrelated")
    before = cache.read_bytes(), (job / "job.json").read_bytes(), target.read_bytes()
    with pytest.raises((ValueError, OSError)):
        reconcile()
    assert before == (cache.read_bytes(), (job / "job.json").read_bytes(), target.read_bytes())


def test_boot_restores_unindexed_prepared_rollback_using_existing_transaction(recovery, monkeypatch):
    target, backup, job, metadata, original = recovery
    target.write_bytes(b"new")
    updater._write_update_record(updater._get_health_path(), {"build": metadata["version"], "confirmed": False,
        "fails": updater.MAX_HEALTH_FAILS - 1, "job_id": job.name, "backup_id": backup.name})
    updater._write_update_record(job / "running.json", {"schema_version": 1, "job_id": job.name,
        "version": metadata["version"], "created_ticks": 123})
    with monkeypatch.context() as patch:
        def interrupted(*args, **kwargs):
            raise OSError("interrupted owned restore")
        patch.setattr(updater, "_atomic_copy", interrupted)
        assert updater.rollback() is False
    pending = update_target_state.read(target)
    assert pending["operation"] == "rollback" and pending["phase"] == "prepared"
    updater._get_cache_path().unlink()
    report = reconcile_on_boot()
    assert report["state"] == "rolled_back" and report["restart_required"] is True
    assert update_target_state.read(target)["transaction_id"] == pending["transaction_id"]
    assert target.read_bytes() == backup.read_bytes() == b"old"
