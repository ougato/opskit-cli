"""Exact process creation identity and argv binding, without signalling PIDs."""
from pathlib import Path
import sys
from types import SimpleNamespace

import psutil
import pytest

from core import updater, update_jobs


@pytest.fixture
def identity(tmp_path, monkeypatch):
    job = tmp_path / ("a" * 32)
    job.mkdir()
    executable = str(Path(sys.executable).resolve())
    metadata = {"job_id": job.name, "version": 12345, "helper_pid": 123,
                "helper_executable": executable}
    ack = {"schema_version": 1, "job_id": job.name, "version": 12345, "pid": 123,
           "created_ticks": 134098765432109876, "script": str(job / "apply.ps1"), "executable": executable}
    updater._write_update_record(job / "running.json", ack)
    monkeypatch.setattr(update_jobs, "_creation_ticks", lambda pid: ack["created_ticks"])
    process = SimpleNamespace(exe=lambda: executable,
        cmdline=lambda: [executable, "-NoProfile", "-File", str(job / "apply.ps1")],
        is_running=lambda: True, status=lambda: psutil.STATUS_RUNNING)
    monkeypatch.setattr(psutil, "Process", lambda pid: process)
    return job, metadata, ack, process


def test_exact_identity_image_and_file_argument_prove_live_snapshot(identity):
    job, metadata, _, _ = identity
    assert update_jobs.helper_status(job, metadata) == "alive"


def test_missing_ack_never_infers_liveness_from_recorded_pid(identity):
    job, metadata, _, _ = identity
    (job / "running.json").unlink()
    assert update_jobs.helper_status(job, metadata) == "unknown"


@pytest.mark.parametrize("delta", [1, 10000])
def test_reused_pid_creation_tick_mismatch_never_claims_alive(identity, monkeypatch, delta):
    job, metadata, ack, _ = identity
    monkeypatch.setattr(update_jobs, "_creation_ticks", lambda pid: ack["created_ticks"] + delta)
    assert update_jobs.helper_status(job, metadata) == "identity_mismatch"


def test_pid_identity_is_rechecked_after_reading_argv(identity, monkeypatch):
    job, metadata, ack, _ = identity
    ticks = iter([ack["created_ticks"], ack["created_ticks"] + 1])
    monkeypatch.setattr(update_jobs, "_creation_ticks", lambda pid: next(ticks))
    assert update_jobs.helper_status(job, metadata) == "identity_mismatch"


def test_missing_native_pid_is_exited_without_querying_other_processes(identity, monkeypatch):
    job, metadata, _, _ = identity
    monkeypatch.setattr(update_jobs, "_creation_ticks", lambda pid: None)
    monkeypatch.setattr(psutil, "Process", lambda pid: pytest.fail("no native process must not query argv"))
    assert update_jobs.helper_status(job, metadata) == "exited"


def test_access_denied_keeps_helper_status_unknown(identity, monkeypatch):
    job, metadata, _, _ = identity
    def denied(pid):
        raise psutil.AccessDenied(pid)
    monkeypatch.setattr(psutil, "Process", denied)
    assert update_jobs.helper_status(job, metadata) == "unknown"


@pytest.mark.parametrize("field,value", [("pid", True), ("created_ticks", True),
    ("created_ticks", -1), ("job_id", "b" * 32), ("schema_version", True),
    ("version", True), ("script", "relative.ps1")])
def test_invalid_ack_is_rejected_before_process_lookup(identity, monkeypatch, field, value):
    job, metadata, ack, _ = identity
    updater._write_update_record(job / "running.json", {**ack, field: value})
    monkeypatch.setattr(update_jobs, "_creation_ticks", lambda pid: pytest.fail("bad record must not query native PID"))
    with pytest.raises(ValueError, match="acknowledgement"):
        update_jobs.helper_status(job, metadata)


@pytest.mark.parametrize("mismatch", ["image", "file", "substring", "pid"])
def test_wrong_image_script_or_pid_never_claims_alive(identity, mismatch):
    job, metadata, _, process = identity
    if mismatch == "image":
        process.exe = lambda: str(job / "unrelated.exe")
    elif mismatch == "file":
        process.cmdline = lambda: [metadata["helper_executable"], "-File", str(job / "unrelated.ps1")]
    elif mismatch == "substring":
        process.cmdline = lambda: [metadata["helper_executable"], "-Command", "-File " + str(job / "apply.ps1")]
    else:
        metadata["helper_pid"] += 1
    assert update_jobs.helper_status(job, metadata) == "identity_mismatch"


def test_probe_does_not_modify_ack_or_metadata(identity):
    job, metadata, _, _ = identity
    before = (job / "running.json").read_bytes(), dict(metadata)
    assert update_jobs.helper_status(job, metadata) == "alive"
    assert ((job / "running.json").read_bytes(), metadata) == before


def test_cached_identity_paths_are_compared_without_filesystem_resolution(identity, monkeypatch):
    job, metadata, _, _ = identity
    monkeypatch.setattr(Path, "resolve", lambda *a, **kw: pytest.fail("cache path must not resolve filesystem destinations"))
    assert update_jobs.helper_status(job, metadata) == "alive"
