"""Execute the actual Windows helper against disposable files, never programs."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from core import updater
from core.update_windows import script


POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
pytestmark = pytest.mark.skipif(sys.platform != "win32" or not POWERSHELL.is_file(),
                                reason="native Windows PowerShell helper execution")


@pytest.fixture
def job(tmp_path):
    folder = tmp_path / "中文 [目录] ' $name"
    folder.mkdir()
    target = folder / "old ' [program].exe"
    artifact = folder / "artifact ' [signed].exe"
    old, new = b"old-program", b"new-program\0" * 10000
    target.write_bytes(old)
    artifact.write_bytes(new)
    return folder, target, artifact, old, new


def run_job(job, *, snapshot=None, timeout=20, parent_pid=0, recovery=None, target_lock=None):
    folder, target, artifact, old, new = job
    result, health = folder / "result.json", folder / "health.json"
    helper = folder / "job.ps1"
    helper.write_bytes(script(target=target, artifact=artifact, result=result, health=health,
                              lock=folder / "update.lock", old_digest=hashlib.sha256(old).hexdigest(),
                              new_digest=hashlib.sha256(new).hexdigest(), version=12345,
                              parent_pid=parent_pid, recovery=recovery, timeout=timeout,
                              target_lock=target_lock).encode("utf-8-sig"))
    if snapshot is not None:
        artifact.write_bytes(snapshot)
    process = subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-File", str(helper)], capture_output=True, timeout=30)
    assert result.is_file(), (process.returncode, process.stdout, process.stderr)
    return process.returncode, json.loads(result.read_text("utf-8")), health


def test_real_helper_handles_unicode_quotes_and_wildcards(job):
    folder, target, artifact, old, new = job
    sentinel = folder / "old other program.exe"
    sentinel.write_bytes(b"unrelated")
    code, result, health = run_job(job, recovery={"backup_id": "recorded-backup"})
    assert code == 0 and result == {"state": "applied", "version": 12345, "health_confirmed": False}
    assert target.read_bytes() == new and artifact.read_bytes() == new
    assert sentinel.read_bytes() == b"unrelated"
    assert json.loads(health.read_text("utf-8"))["backup_id"] == "recorded-backup"
    assert not list(folder.glob(".opskit-replace-*"))


def test_real_helper_health_is_job_bound_and_confirmation_retains_identity(job, monkeypatch):
    job_id = "a" * 32
    code, result, health = run_job(job, recovery={"job_id": job_id, "backup_id": "recorded-backup"})
    assert code == 0 and result["state"] == "applied"
    record = json.loads(health.read_text("utf-8"))
    assert record["job_id"] == job_id and record["confirmed"] is False
    monkeypatch.setattr(updater, "APP_VERSION", 12345)
    monkeypatch.setattr(updater, "_get_health_path", lambda: health)
    updater.confirm_health()
    confirmed = json.loads(health.read_text("utf-8"))
    assert confirmed["job_id"] == job_id and confirmed["confirmed"] is True


def test_real_waiting_helper_is_identified_and_termination_is_observed(job, monkeypatch):
    from core.update_cli import status
    from core.update_jobs import _creation_ticks
    folder, target, artifact, old, new = job
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(folder))
    job_id = "a" * 32
    cache = updater._get_cache_path()
    root = cache.parent / "update-jobs" / job_id
    root.mkdir(parents=True)
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    helper = root / "apply.ps1"
    helper.write_bytes(script(target=target, artifact=artifact, result=root / "result.json",
        health=updater._get_health_path(), lock=folder / "test.lock",
        old_digest=hashlib.sha256(old).hexdigest(), new_digest=hashlib.sha256(new).hexdigest(),
        version=12345, parent_pid=parent.pid, recovery={"job_id": job_id}, timeout=20).encode("utf-8-sig"))
    process = subprocess.Popen([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(helper)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        updater._write_update_record(root / "job.json", {"schema_version": 1, "job_id": job_id,
            "version": 12345, "state": "scheduled", "helper_pid": process.pid,
            "helper_executable": str(POWERSHELL.resolve())})
        updater._write_update_record(cache, {"apply_job": job_id})
        deadline = time.monotonic() + 8
        while True:
            assert process.poll() is None, "helper must survive until acknowledgement"
            try:
                ack = updater._read_update_record(root / "running.json")
                break
            except FileNotFoundError:
                pass
            except PermissionError as error:
                # A visible atomic rename can briefly remain unreadable on
                # Windows. Only known native access/sharing failures may wait.
                if error.winerror not in (5, 32):
                    raise
            assert time.monotonic() < deadline, "helper acknowledgement must become readable"
            time.sleep(.05)
        assert ack["pid"] == process.pid
        assert ack["created_ticks"] == _creation_ticks(process.pid)
        assert status(timeout=10)["apply_job"]["helper_status"] == "alive"
        assert target.read_bytes() == old and parent.poll() is None
        process.terminate()
        process.wait(timeout=5)
        report = status(timeout=10)["apply_job"]
        assert report["helper_status"] == "exited"
        assert report["state"] == "scheduled", "readonly status must not guess transaction outcome"
        assert target.read_bytes() == old and not (root / "result.json").exists()
        assert not updater._get_health_path().exists()
    finally:
        for owned in (process, parent):
            if owned.poll() is None:
                owned.terminate()
            owned.wait(timeout=5)


def test_real_helper_refuses_changed_snapshot(job):
    folder, target, artifact, old, new = job
    code, result, health = run_job(job, snapshot=b"changed after signature gate")
    assert code == 1 and result["state"] == "failed"
    assert result["phase"] == "artifact_validation"
    assert target.read_bytes() == old and not health.exists()
    assert not list(folder.glob(".opskit-replace-*"))


def test_real_helper_refuses_changed_target(job):
    folder, target, artifact, old, new = job
    target.write_bytes(b"different installation")
    code, result, health = run_job(job)
    assert code == 1 and result["state"] == "failed"
    assert result["phase"] == "target_validation"
    assert target.read_bytes() == b"different installation" and not health.exists()


def test_real_helper_atomic_commit_failure_preserves_current(job):
    import ctypes
    from ctypes import wintypes
    folder, target, artifact, old, new = job
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # Readers are allowed, deletion/replacement is denied until this closes.
    handle = kernel.CreateFileW(str(target), 0x80000000, 1, None, 3, 0, None)
    assert handle != wintypes.HANDLE(-1).value
    try:
        code, result, health = run_job(job)
    finally:
        kernel.CloseHandle(handle)
    assert code == 1 and result["state"] == "failed"
    assert result["phase"] == "commit"
    assert target.read_bytes() == old and not health.exists()
    assert not list(folder.glob(".opskit-replace-*"))


def test_real_helper_wait_deadline_does_not_replace_live_parent(job):
    folder, target, artifact, old, new = job
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        code, result, health = run_job(job, timeout=2, parent_pid=parent.pid)
        assert code == 1 and result["state"] == "failed"
        assert result["phase"] == "parent_wait"
        assert parent.poll() is None
        assert target.read_bytes() == old and not health.exists()
    finally:
        parent.terminate()
        parent.wait(timeout=5)


def test_scheduling_uses_independent_snapshots(job, monkeypatch):
    folder, target, artifact, old, new = job
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(folder))
    launches = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kwargs: launches.append(args))
    from core.update_windows import schedule
    first = schedule(artifact, target, 12345)
    artifact.write_bytes(b"second candidate")
    second = schedule(artifact, target, 12346)
    assert first != second
    assert (folder / "cache" / "update-jobs" / first / "artifact.exe").read_bytes() == new
    assert (folder / "cache" / "update-jobs" / second / "artifact.exe").read_bytes() == b"second candidate"
    assert Path(launches[0][-1]) != Path(launches[1][-1])


def test_real_helper_respects_platform_staging_lock(job):
    from core.resource_lock import ResourceLock
    folder, target, artifact, old, new = job
    with ResourceLock(lambda: folder / "update.lock", label="native updater test").hold():
        code, result, health = run_job(job, timeout=2)
    assert code == 1 and result["state"] == "failed" and result["phase"] == "resource_lock"
    assert target.read_bytes() == old and not health.exists()


def test_real_helper_reports_recovery_record_failure_after_commit(job):
    folder, target, artifact, old, new = job
    (folder / "health.json").mkdir()
    code, result, health = run_job(job, recovery={"backup_id": "recorded-backup"})
    assert code == 1 and result["state"] == "applied_recovery_incomplete"
    assert result["phase"] == "recovery_record"
    assert target.read_bytes() == new
    assert health.is_dir() and not list(folder.glob(".opskit-replace-*"))


def test_python_atomic_replacement_preserves_private_windows_dacl(job):
    from core.secrets import restrict_permissions
    from core.windows_permissions import _descriptor
    folder, target, artifact, old, new = job
    restrict_permissions(target)
    before, protected = _descriptor(target)
    updater._atomic_copy(artifact, target)
    after, after_protected = _descriptor(target)
    assert before.raw == after.raw and protected == after_protected
    assert target.read_bytes() == new


def test_permission_copy_failure_aborts_before_replacement(job, monkeypatch):
    from core import windows_permissions
    folder, target, artifact, old, new = job
    monkeypatch.setattr(windows_permissions, "copy_dacl",
                        lambda *a: (_ for _ in ()).throw(PermissionError("ACL copy denied")))
    with pytest.raises(PermissionError):
        updater._atomic_copy(artifact, target)
    assert target.read_bytes() == old and not list(folder.glob(".opskit-replace-*"))


def test_real_helper_respects_shared_executable_lock(job):
    from core.resource_lock import ResourceLock
    folder, target, artifact, old, new = job
    shared = updater._update_target_lock_path(target)
    with ResourceLock(lambda: shared, label="native shared target test").hold():
        code, result, health = run_job(job, timeout=2, target_lock=shared)
    assert code == 1 and result["state"] == "failed" and result["phase"] == "resource_lock"
    assert target.read_bytes() == old and not health.exists()
    # Both acquired staging and partially acquired target handles were released.
    with ResourceLock(lambda: folder / "update.lock", label="released stage").hold():
        pass
    code, result, health = run_job(job, target_lock=shared)
    assert code == 0 and target.read_bytes() == new


def test_real_helper_rejects_superseded_target_transaction(job):
    from core import update_target_state as state
    folder, target, artifact, old, new = job
    first = state.prepare(target, operation="upgrade", from_build=1, to_build=12345,
                          from_digest=hashlib.sha256(old).hexdigest(), to_digest=hashlib.sha256(new).hexdigest())
    second = state.prepare(target, operation="upgrade", from_build=1, to_build=12346,
                           from_digest=first["from_sha256"], to_digest="b" * 64)
    code, result, health = run_job(job, recovery={"transaction": first})
    assert code == 1 and result["phase"] == "transaction_validation"
    assert target.read_bytes() == old and state.read(target) == second


def test_real_helper_commits_bound_shared_target_record(job):
    from core import update_target_state as state
    folder, target, artifact, old, new = job
    transaction = state.prepare(target, operation="upgrade", from_build=1, to_build=12345,
                                from_digest=hashlib.sha256(old).hexdigest(), to_digest=hashlib.sha256(new).hexdigest())
    code, result, health = run_job(job, recovery={"transaction": transaction})
    assert code == 0 and target.read_bytes() == new
    assert state.read(target) == {**transaction, "phase": "applied"}
