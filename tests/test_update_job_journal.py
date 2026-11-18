"""Async launch intent is indexed before Popen; uncertain writes stay honest."""
import subprocess
from types import SimpleNamespace

import pytest

from core import updater
from core.update_cli import status


@pytest.fixture
def launch(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    pending = updater._get_pending_path()
    pending.parent.mkdir(parents=True)
    pending.write_bytes(b"new-signed-test-bytes")
    target = tmp_path / "program.exe"
    target.write_bytes(b"old-program")
    return pending, target, updater.APP_VERSION + 1


def test_failed_launch_is_indexed_and_readonly_status_reports_it(launch, monkeypatch, caplog):
    pending, target, build = launch
    def fail(*args, **kwargs):
        cache = updater._read_update_record(updater._get_cache_path())
        record = updater._read_update_record(pending.parent / "update-jobs" / cache["apply_job"] / "job.json")
        assert record["state"] == "launching"
        assert record["job_id"] == cache["apply_job"]
        raise OSError("private-launcher-token")
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", fail)
        with pytest.raises(RuntimeError, match="could not be scheduled"):
            updater._apply_windows(pending, target, build)
    cache_before = updater._get_cache_path().read_bytes()
    report = status(timeout=10)
    assert report["apply_job"]["state"] == "launch_failed"
    assert report["apply_job"]["version"] == build
    assert not report["apply_job"]["health_confirmed"]
    assert "private-launcher-token" not in caplog.text
    assert "private-launcher-token" not in str(report)
    assert updater._get_cache_path().read_bytes() == cache_before
    assert target.read_bytes() == b"old-program" and pending.exists()


@pytest.mark.parametrize("failure", ["index", "intent", "script", "snapshot"])
def test_prelaunch_persistence_failure_preserves_old_index_and_does_not_launch(launch, monkeypatch, failure):
    pending, target, build = launch
    cache = updater._get_cache_path()
    updater._write_update_record(cache, {"apply_job": "a" * 32, "pending_version": build})
    before = cache.read_bytes()
    launches = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: launches.append(a))
    write = updater._write_update_record
    def write_record(path, record):
        if ((failure == "index" and path == cache)
                or (failure == "intent" and path.name == "job.json" and record["state"] == "launching")):
            raise OSError("injected persistence failure")
        return write(path, record)
    monkeypatch.setattr(updater, "_write_update_record", write_record)
    if failure == "snapshot":
        monkeypatch.setattr(updater, "_atomic_copy", lambda *a, **kw: (_ for _ in ()).throw(OSError("snapshot failed")))
    if failure == "script":
        from core import secrets
        atomic = secrets.atomic_write
        def write_script(path, content):
            if path.name == "apply.ps1":
                raise OSError("script failed")
            return atomic(path, content)
        monkeypatch.setattr(secrets, "atomic_write", write_script)
    with pytest.raises(RuntimeError, match="could not be scheduled"):
        updater._apply_windows(pending, target, build)
    assert launches == []
    assert cache.read_bytes() == before
    assert target.read_bytes() == b"old-program"
    assert pending.read_bytes() == b"new-signed-test-bytes"


def test_postlaunch_record_failure_retains_indexed_intent_without_retry(launch, monkeypatch):
    pending, target, build = launch
    write = updater._write_update_record
    def refused(path, record):
        if path.name == "job.json" and record["state"] == "scheduled":
            raise OSError("scheduled record failed")
        return write(path, record)
    monkeypatch.setattr(updater, "_write_update_record", refused)
    calls = []
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", lambda *a, **kw: calls.append(a) or SimpleNamespace(pid=123))
        updater._apply_windows(pending, target, build)
    assert len(calls) == 1
    assert status(timeout=10)["apply_job"]["state"] == "launching"
    assert not (pending.parent / "update_pending_path.json").exists()
    assert not list((pending.parent / "update-jobs").glob("*/result.json"))
    assert target.read_bytes() == b"old-program"


def test_launch_failure_with_lost_result_never_reports_scheduled(launch, monkeypatch):
    pending, target, build = launch
    write = updater._write_update_record
    def refused(path, record):
        if path.name == "result.json":
            raise OSError("failure result lost")
        return write(path, record)
    monkeypatch.setattr(updater, "_write_update_record", refused)
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", lambda *a, **kw: (_ for _ in ()).throw(OSError("launch failed")))
        with pytest.raises(RuntimeError, match="could not be scheduled"):
            updater._apply_windows(pending, target, build)
    assert status(timeout=10)["apply_job"]["state"] == "launching"
    assert target.read_bytes() == b"old-program"


@pytest.mark.parametrize("health_job", ["matching", "other", "missing"])
def test_modern_job_health_is_bound_to_job_identity(launch, monkeypatch, health_job):
    pending, target, build = launch
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", lambda *a, **kw: SimpleNamespace(pid=123))
        updater._apply_windows(pending, target, build)
    job_id = updater._read_update_record(updater._get_cache_path())["apply_job"]
    health = {"build": build, "confirmed": True, "fails": 0}
    if health_job != "missing":
        health["job_id"] = job_id if health_job == "matching" else "f" * 32
    updater._write_update_record(updater._get_health_path(), health)
    assert status(timeout=10)["apply_job"]["health_confirmed"] is (health_job == "matching")


def test_status_rejects_moved_modern_job_record(launch):
    pending, _, build = launch
    job_id = "a" * 32
    updater._write_update_record(updater._get_cache_path(), {"apply_job": job_id})
    updater._write_update_record(pending.parent / "update-jobs" / job_id / "job.json", {
        "schema_version": 1, "job_id": "b" * 32, "version": build, "state": "launching"})
    with pytest.raises(RuntimeError, match="unavailable or invalid"):
        status(timeout=10)


def test_legacy_job_cannot_claim_health_from_version_only(launch):
    pending, _, build = launch
    job_id = "a" * 32
    updater._write_update_record(updater._get_cache_path(), {"apply_job": job_id})
    updater._write_update_record(pending.parent / "update-jobs" / job_id / "job.json", {
        "version": build, "state": "scheduled"})
    updater._write_update_record(updater._get_health_path(), {"build": build, "confirmed": True})
    assert status(timeout=10)["apply_job"]["health_confirmed"] is False
