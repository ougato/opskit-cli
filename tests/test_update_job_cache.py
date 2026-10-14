"""Job quotas reclaim only proven owned, inactive records."""
import os

import pytest

from core import updater, update_job_cache, update_jobs


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(update_job_cache, "MAX_JOBS", 2)
    return updater._get_cache_path()


def job(cache, number, **changes):
    root = cache.parent / "update-jobs" / f"{number:032x}"
    root.mkdir(parents=True)
    record = {"schema_version": 2, "job_id": root.name, "version": 123,
              "state": "preparing", "backup_id": None, "transaction_id": None}
    updater._write_update_record(root / "job.json", {**record, **changes})
    (root / "artifact.exe").write_bytes(b"snapshot")
    os.utime(root, ns=(number * 1000000000, number * 1000000000))
    return root


def reserve(cache, incoming=100):
    with updater._update_resource.hold():
        return update_job_cache.reserve_job(cache, incoming)


def test_count_reclaims_oldest_preparing_and_preserves_index(cache):
    first, second = job(cache, 1), job(cache, 2)
    updater._write_update_record(cache, {"apply_job": second.name})
    before = cache.read_bytes()
    assert reserve(cache)["removed"] == 1
    assert not first.exists() and second.exists()
    assert cache.read_bytes() == before


def test_byte_quota_counts_metadata(cache, monkeypatch):
    first, second = job(cache, 1), job(cache, 2)
    total = sum(p.stat().st_size for node in (first, second) for p in node.iterdir())
    monkeypatch.setattr(update_job_cache, "MAX_JOB_BYTES", total + 99)
    result = reserve(cache)
    assert result["removed"] == 1 and result["bytes"] + 100 <= total + 99


@pytest.mark.parametrize("reference", ["index", "health", "marker", "backup"])
def test_references_pin_jobs(cache, monkeypatch, reference):
    from core.constants import FILE_UPDATE_HEALTH
    root = job(cache, 1, backup_id="backup.exe")
    monkeypatch.setattr(update_job_cache, "MAX_JOBS", 1)
    if reference == "index":
        path, record = cache, {"apply_job": root.name}
    elif reference == "health":
        path, record = cache.parent / FILE_UPDATE_HEALTH, {"job_id": root.name}
    elif reference == "marker":
        path, record = cache.parent / "update_pending_path.json", {"job_id": root.name}
    else:
        path, record = cache, {"backup_id": "backup.exe"}
    updater._write_update_record(path, record)
    with pytest.raises(RuntimeError, match="quota exhausted"):
        reserve(cache)
    assert (root / "artifact.exe").read_bytes() == b"snapshot"


@pytest.mark.parametrize("kind", ["legacy", "unknown", "extra"])
def test_unowned_or_unknown_content_preserved(cache, monkeypatch, kind):
    root = job(cache, 1, schema_version=1 if kind == "legacy" else 2)
    if kind == "unknown":
        (root / "job.json").unlink()
    if kind == "extra":
        (root / "user-notes.txt").write_text("keep")
    monkeypatch.setattr(update_job_cache, "MAX_JOBS", 1)
    with pytest.raises(RuntimeError, match="quota exhausted"):
        reserve(cache)
    assert (root / "artifact.exe").exists()


@pytest.mark.parametrize("changes", [{"version": True}, {"job_id": "wrong"},
    {"backup_id": "../outside"}, {"transaction_id": "bad"}, {"state": "bad"}])
def test_bad_ownership_aborts_inventory_before_deletion(cache, changes):
    first, second = job(cache, 1), job(cache, 2, **changes)
    with pytest.raises(ValueError, match="ownership"):
        reserve(cache)
    assert first.exists() and second.exists()


@pytest.mark.parametrize("status", ["alive", "unknown", "identity_mismatch", "exited"])
@pytest.mark.parametrize("result", [None, "failed", "applied", "applied_recovery_incomplete"])
def test_final_result_and_authenticated_exit_required(cache, monkeypatch, status, result):
    root = job(cache, 1, state="scheduled")
    updater._write_update_record(root / "running.json", {"ack": "probe fixture"})
    if result:
        updater._write_update_record(root / "result.json", {"version": 123, "state": result})
    monkeypatch.setattr(update_jobs, "helper_status", lambda *args: status)
    monkeypatch.setattr(update_job_cache, "MAX_JOBS", 1)
    if status == "exited" and result == "failed":
        assert reserve(cache)["removed"] == 1
        assert not root.exists()
    else:
        with pytest.raises(RuntimeError, match="quota exhausted"):
            reserve(cache)
        assert root.exists()


def test_failed_without_ack_is_ambiguous(cache, monkeypatch):
    root = job(cache, 1, state="launching")
    updater._write_update_record(root / "result.json", {"version": 123, "state": "launch_failed"})
    monkeypatch.setattr(update_job_cache, "MAX_JOBS", 1)
    with pytest.raises(RuntimeError, match="quota exhausted"):
        reserve(cache)
    assert root.exists()


def test_hardlink_cannot_delete_external_data(cache, tmp_path):
    first, second = job(cache, 1), job(cache, 2)
    outside = tmp_path / "private.txt"
    outside.write_text("keep")
    artifact = second / "artifact.exe"
    artifact.unlink()
    os.link(outside, artifact)
    with pytest.raises(ValueError):
        reserve(cache)
    assert first.exists() and outside.read_text() == "keep"


def test_unlink_failure_keeps_ownership_record(cache, monkeypatch):
    root = job(cache, 1)
    monkeypatch.setattr(update_job_cache, "MAX_JOBS", 1)
    def fail(*args):
        raise OSError("injected unlink failure")
    monkeypatch.setattr(update_job_cache, "unlink_owned", fail)
    with pytest.raises(OSError, match="injected"):
        reserve(cache)
    assert (root / "job.json").exists()


@pytest.mark.parametrize("state", ["preparing", "scheduled"])
def test_transaction_evidence_is_not_retired(cache, monkeypatch, state):
    root = job(cache, 1, state=state, transaction_id="b" * 32)
    if state == "scheduled":
        updater._write_update_record(root / "running.json", {"ack": "probe fixture"})
        updater._write_update_record(root / "result.json", {"version": 123, "state": "failed"})
    monkeypatch.setattr(update_jobs, "helper_status", lambda *args: "exited")
    monkeypatch.setattr(update_job_cache, "MAX_JOBS", 1)
    with pytest.raises(RuntimeError, match="quota exhausted"):
        reserve(cache)
    assert (root / "job.json").exists()


def test_schedule_quota_refusal_precedes_allocation_and_launch(cache, tmp_path, monkeypatch):
    import subprocess
    from core.update_windows import schedule
    root = job(cache, 1, state="launching")
    updater._write_update_record(cache, {"apply_job": root.name})
    before = cache.read_bytes()
    pending, target = tmp_path / "pending.exe", tmp_path / "program.exe"
    pending.write_bytes(b"new")
    target.write_bytes(b"old")
    monkeypatch.setattr(update_job_cache, "MAX_JOBS", 1)
    def unexpected(*args, **kwargs):
        pytest.fail("quota refusal must not launch")
    monkeypatch.setattr(subprocess, "Popen", unexpected)
    with pytest.raises(RuntimeError, match="quota exhausted"):
        schedule(pending, target, 123)
    assert list(root.parent.iterdir()) == [root]
    assert cache.read_bytes() == before and target.read_bytes() == b"old"
