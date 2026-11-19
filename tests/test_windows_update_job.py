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


def run_job(job, *, snapshot=None, timeout=20, parent_pid=0, recovery=None, target_lock=None,
            lock_probe=None, delay_before_parent=0):
    folder, target, artifact, old, new = job
    result, health = folder / "result.json", folder / "health.json"
    helper = folder / "job.ps1"
    body = script(target=target, artifact=artifact, result=result, health=health,
                  lock=folder / "update.lock", old_digest=hashlib.sha256(old).hexdigest(),
                  new_digest=hashlib.sha256(new).hexdigest(), version=12345,
                  parent_pid=parent_pid, recovery=recovery, timeout=timeout, target_lock=target_lock)
    if delay_before_parent:
        marker = "    $phase = 'parent_wait'"
        assert body.count(marker) == 1
        body = body.replace(marker, f"    Start-Sleep -Milliseconds {int(delay_before_parent)}\n" + marker)
    if lock_probe is not None:
        from core.update_windows import _literal
        marker = "    Deadline\n    $phase = 'resource_lock'"
        catch = "catch [IO.IOException] { Start-Sleep -Milliseconds 100 }"
        assert body.count(marker) == body.count(catch) == 1
        # Isolate lock contention from unrelated PowerShell startup/compilation
        # time and record an actual failed lock attempt, not just its phase.
        body = body.replace(marker, "    $clock.Restart()\n" + marker)
        body = body.replace(catch, "catch [IO.IOException] { "
                            f"[IO.File]::WriteAllText({_literal(lock_probe)}, 'locked'); "
                            "Start-Sleep -Milliseconds 100 }")
    helper.write_bytes(body.encode("utf-8-sig"))
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


@pytest.mark.parametrize("change", ["confirmed", "fails", "backup", "backup_array", "malformed"])
def test_rollback_helper_rejects_changed_health_without_touching_target(job, change):
    from core import update_target_state

    folder, target, artifact, old, new = job
    backup_id = "opskit.v12344.123." + "b" * 32 + ".bak"
    transaction = update_target_state.prepare(
        target, operation="rollback", from_build=12345, to_build=12344,
        from_digest=hashlib.sha256(old).hexdigest(), to_digest=hashlib.sha256(new).hexdigest(),
        owner_data_dir=folder, source_transaction_id="a" * 32, backup_id=backup_id,
    )
    record = {"build": 12345, "confirmed": False, "fails": 3, "backup_id": backup_id}
    if change == "confirmed":
        record["confirmed"] = True
    elif change == "fails":
        record["fails"] = 1
    elif change == "backup":
        record["backup_id"] = "other"
    elif change == "backup_array":
        record["backup_id"] = [backup_id]
    else:
        record["fails"] = True
    health = folder / "health.json"
    health.write_text(json.dumps(record), encoding="utf-8")
    before = health.read_bytes()
    code, result, _ = run_job(job, recovery={"transaction": transaction, "backup_id": backup_id})
    assert code != 0 and result["state"] == "failed" and result["phase"] == "rollback_health_validation"
    assert target.read_bytes() == old and artifact.read_bytes() == new
    assert health.read_bytes() == before and update_target_state.read(target) == transaction


def test_rollback_helper_preserves_health_even_with_backup_id(job):
    from core import update_target_state

    folder, target, _, old, new = job
    backup_id = "opskit.v12344.123." + "b" * 32 + ".bak"
    transaction = update_target_state.prepare(
        target, operation="rollback", from_build=12345, to_build=12344,
        from_digest=hashlib.sha256(old).hexdigest(), to_digest=hashlib.sha256(new).hexdigest(),
        owner_data_dir=folder, source_transaction_id="a" * 32, backup_id=backup_id,
    )
    health = folder / "health.json"
    health.write_text(json.dumps({"build": 12345, "confirmed": False, "fails": 3, "backup_id": backup_id}),
                      encoding="utf-8")
    before = health.read_bytes()
    code, result, _ = run_job(job, recovery={"transaction": transaction, "backup_id": backup_id})
    assert code == 0 and result["state"] == "applied" and target.read_bytes() == new
    assert health.read_bytes() == before


@pytest.mark.parametrize("expected,recorded", [
    ("a" * 32, "b" * 32), ("a" * 32, None), (None, "a" * 32),
    ("a" * 32, ["a" * 32]),
])
def test_rollback_helper_rejects_changed_source_job(job, expected, recorded):
    from core import update_target_state

    folder, target, artifact, old, new = job
    backup_id = "opskit.v12344.123." + "b" * 32 + ".bak"
    transaction = update_target_state.prepare(
        target, operation="rollback", from_build=12345, to_build=12344,
        from_digest=hashlib.sha256(old).hexdigest(), to_digest=hashlib.sha256(new).hexdigest(),
        owner_data_dir=folder, source_transaction_id="a" * 32, backup_id=backup_id,
    )
    record = {"build": 12345, "confirmed": False, "fails": 3, "backup_id": backup_id}
    if recorded is not None:
        record["job_id"] = recorded
    health = folder / "health.json"
    health.write_text(json.dumps(record), encoding="utf-8")
    before = health.read_bytes()
    code, result, _ = run_job(job, recovery={"transaction": transaction, "backup_id": backup_id,
                                            "source_job_id": expected})
    assert code != 0 and result["state"] == "failed" and result["phase"] == "rollback_health_validation"
    assert target.read_bytes() == old and artifact.read_bytes() == new
    assert health.read_bytes() == before and update_target_state.read(target) == transaction


def test_rollback_helper_accepts_and_retains_matching_source_job(job):
    from core import update_target_state

    folder, target, _, old, new = job
    backup_id = "opskit.v12344.123." + "b" * 32 + ".bak"
    transaction = update_target_state.prepare(
        target, operation="rollback", from_build=12345, to_build=12344,
        from_digest=hashlib.sha256(old).hexdigest(), to_digest=hashlib.sha256(new).hexdigest(),
        owner_data_dir=folder, source_transaction_id="a" * 32, backup_id=backup_id,
    )
    health = folder / "health.json"
    health.write_text(json.dumps({"build": 12345, "confirmed": False, "fails": 3,
                                  "backup_id": backup_id, "job_id": "a" * 32}), encoding="utf-8")
    before = health.read_bytes()
    code, result, _ = run_job(job, recovery={"transaction": transaction, "backup_id": backup_id,
                                            "source_job_id": "a" * 32})
    assert code == 0 and result["state"] == "applied" and target.read_bytes() == new
    assert health.read_bytes() == before


def test_upgrade_scheduler_refuses_rollback_before_publishing_a_job(job, monkeypatch):
    from core import update_target_state
    from core.update_windows import schedule

    folder, target, artifact, old, new = job
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(folder / "data"))
    transaction = update_target_state.prepare(
        target, operation="rollback", from_build=12345, to_build=12344,
        from_digest=hashlib.sha256(old).hexdigest(), to_digest=hashlib.sha256(new).hexdigest(),
        owner_data_dir=folder / "data", source_transaction_id="a" * 32,
        backup_id="opskit.v12344.123." + "b" * 32 + ".bak",
    )
    with pytest.raises(ValueError, match="separate recovery scheduler"):
        schedule(artifact, target, 12344, {"transaction": transaction})
    assert target.read_bytes() == old and artifact.read_bytes() == new
    assert not (folder / "data" / "cache" / "update-jobs").exists()
    assert update_target_state.read(target) == transaction


@pytest.mark.parametrize("marker_exists", [False, True])
def test_upgrade_apply_refuses_rollback_without_publishing_fallback(job, monkeypatch, marker_exists):
    from core import update_target_state, update_windows

    folder, target, artifact, old, new = job
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(folder / "data"))
    backup_id = "opskit.v12344.123." + "b" * 32 + ".bak"
    transaction = update_target_state.prepare(
        target, operation="rollback", from_build=12345, to_build=12344,
        from_digest=hashlib.sha256(old).hexdigest(), to_digest=hashlib.sha256(new).hexdigest(),
        owner_data_dir=folder / "data", source_transaction_id="a" * 32, backup_id=backup_id,
    )
    cache, health = updater._get_cache_path(), updater._get_health_path()
    updater._write_update_record(cache, {"apply_job": "c" * 32})
    updater._write_update_record(health, {"build": 12345, "confirmed": False, "fails": 2, "backup_id": backup_id})
    marker = cache.parent / "update_pending_path.json"
    if marker_exists:
        marker.write_bytes(b"unrelated marker")
    user_file = folder / "user data"
    user_file.write_bytes(b"unrelated user data")
    before = cache.read_bytes(), health.read_bytes(), user_file.read_bytes()
    monkeypatch.setattr(update_windows, "schedule", lambda *_args, **_kwargs: pytest.fail("upgrade job launched"))

    with pytest.raises(ValueError, match="separate recovery scheduler"):
        updater._apply_windows(artifact, target, 12344, {"transaction": transaction, "backup_id": backup_id})

    assert (cache.read_bytes(), health.read_bytes(), user_file.read_bytes()) == before
    assert marker.exists() is marker_exists
    if marker_exists:
        assert marker.read_bytes() == b"unrelated marker"
    assert target.read_bytes() == old and artifact.read_bytes() == new
    assert update_target_state.read(target) == transaction
    assert not (cache.parent / "update-jobs").exists()


def test_real_schema2_job_and_helper_can_confirm_matching_update(job, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from core import update_target_state
    from core import update_windows

    folder, target, artifact, old, new = job
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(folder))
    transaction = update_target_state.prepare(
        target, operation="upgrade", from_build=12344, to_build=12345,
        from_digest=hashlib.sha256(old).hexdigest(), to_digest=hashlib.sha256(new).hexdigest(),
    )
    backup_id = "opskit.v12344.123." + "b" * 32 + ".bak"
    recovery = {"backup_id": backup_id, "sha256": transaction["from_sha256"],
                "replacement_sha256": transaction["to_sha256"], "transaction": transaction}
    backup = folder / "backups" / backup_id
    backup.parent.mkdir()
    backup.write_bytes(old)
    updater._write_update_record(backup.with_name(backup.name + ".json"), {
        "schema_version": 1, "backup_id": backup_id, "target": str(target.resolve()),
        "from_build": 12344, "to_build": 12345, "sha256": transaction["from_sha256"],
        "replacement_sha256": transaction["to_sha256"]})
    # Schedule writes the real schema 2 records and snapshot. Suppress only
    # its async launcher: the test starts the actual generated helper itself.
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(pid=1234))
        patch.setattr(update_windows, "_inherited_parent_handle", lambda: nullcontext(0))
        job_id = update_windows.schedule(artifact, target, 12345, recovery)
    job_root = folder / "cache" / "update-jobs" / job_id
    # Simulate the launcher exiting after Popen but before its PID record.
    metadata = updater._read_update_record(job_root / "job.json")
    metadata.pop("helper_pid")
    updater._write_update_record(job_root / "job.json", {**metadata, "state": "launching"})
    import os
    helper = job_root / "apply.ps1"
    body = helper.read_text(encoding="utf-8-sig")
    parent_line = f"$pid_to_wait = {os.getpid()}"
    assert body.count(parent_line) == 1
    helper.write_bytes(body.replace(parent_line, "$pid_to_wait = 0").encode("utf-8-sig"))
    process = subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-File", str(helper)], capture_output=True, timeout=30)
    result = updater._read_update_record(job_root / "result.json")
    health = updater._get_health_path()
    assert process.returncode == 0 and result["state"] == "applied"
    monkeypatch.setattr(updater, "APP_VERSION", 12345)
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr(updater, "_get_health_path", lambda: health)
    assert updater.confirm_health() is True
    assert json.loads(health.read_text("utf-8"))["confirmed"] is True


@pytest.mark.parametrize("change", ["valid", "pinned", "missing-backup", "backup", "receipt", "receipt-array",
                                     "job", "job-array", "helper-pid", "scheduled-no-pid", "helper-executable", "index", "hardlink", "backup-parent-junction"])
def test_upgrade_helper_revalidates_bound_backup_and_job_under_lock(job, change):
    from core import update_target_state
    folder, target, artifact, old, new = job
    job_id = "a" * 32
    backup_id = "opskit.v12344.123." + "b" * 32 + ".bak"
    old_digest, new_digest = hashlib.sha256(old).hexdigest(), hashlib.sha256(new).hexdigest()
    transaction = update_target_state.prepare(target, operation="upgrade", from_build=12344, to_build=12345,
        from_digest=old_digest, to_digest=new_digest, owner_data_dir=folder, backup_id=backup_id)
    backup = folder / "backups" / backup_id
    backup.parent.mkdir()
    backup.write_bytes(old)
    receipt = backup.with_name(backup.name + ".json")
    receipt_data = {"schema_version": 1, "backup_id": backup_id, "target": str(target.resolve()),
        "from_build": 12344, "to_build": 12345, "sha256": old_digest, "replacement_sha256": new_digest}
    receipt.write_text(json.dumps(receipt_data), encoding="utf-8")
    job_root = folder / "cache" / "update-jobs" / job_id
    job_root.mkdir(parents=True)
    job_path, index = job_root / "job.json", folder / "cache" / "index.json"
    metadata = {"schema_version": 2, "job_id": job_id, "state": "launching", "version": 12345,
        "target": str(target.resolve()), "transaction_id": transaction["transaction_id"],
        "backup_id": backup_id, "old_sha256": old_digest, "new_sha256": new_digest,
        "owner_data_dir": str(folder.resolve()), "helper_executable": str(POWERSHELL.resolve())}
    job_path.write_text(json.dumps(metadata), encoding="utf-8")
    index.write_text(json.dumps({"apply_job": job_id}), encoding="utf-8")
    external = folder / "external"
    if change == "missing-backup":
        backup.unlink()
    elif change == "backup":
        backup.write_bytes(b"different backup")
    elif change == "receipt":
        receipt_data["from_build"] = 12343
        receipt.write_text(json.dumps(receipt_data), encoding="utf-8")
    elif change == "receipt-array":
        receipt_data["backup_id"] = [backup_id]
        receipt.write_text(json.dumps(receipt_data), encoding="utf-8")
    elif change == "job":
        metadata["transaction_id"] = "d" * 32
        job_path.write_text(json.dumps(metadata), encoding="utf-8")
    elif change == "job-array":
        metadata["job_id"] = [job_id]
        job_path.write_text(json.dumps(metadata), encoding="utf-8")
    elif change == "helper-pid":
        metadata.update(state="scheduled", helper_pid=0)
        job_path.write_text(json.dumps(metadata), encoding="utf-8")
    elif change == "scheduled-no-pid":
        metadata["state"] = "scheduled"
        job_path.write_text(json.dumps(metadata), encoding="utf-8")
    elif change == "helper-executable":
        metadata["helper_executable"] = str(target.resolve())
        job_path.write_text(json.dumps(metadata), encoding="utf-8")
    elif change == "index":
        index.write_text(json.dumps({"apply_job": "e" * 32}), encoding="utf-8")
    elif change == "hardlink":
        import os
        os.link(backup, external)
    from contextlib import ExitStack
    from core.update_windows import _pin_source_directories
    with ExitStack() as held:
        directories = _pin_source_directories([job_root, backup.parent, index.parent, folder], held)
    source_guard = {"job": job_path, "cache": index, "backup": backup,
                    "receipt": receipt, "owner": str(folder.resolve()), "directories": directories}
    helper, result, health = job_root / "apply.ps1", job_root / "result.json", folder / "health.json"
    body = script(target=target, artifact=artifact, result=result, health=health,
        lock=folder / "update.lock", target_lock=folder / "target.lock", old_digest=old_digest,
        new_digest=new_digest, version=12345, parent_pid=0,
        recovery={"transaction": transaction, "job_id": job_id, "backup_id": backup_id},
        source_guard=source_guard)
    if change == "pinned":
        from core.update_windows import _literal
        ready, release = folder / "guard-ready", folder / "guard-release"
        marker = "    $phase = 'target_validation'"
        assert body.count(marker) == 1
        body = body.replace(marker, f"    [IO.File]::WriteAllText({_literal(ready)}, 'ready')\n"
            f"    while (-not [IO.File]::Exists({_literal(release)})) {{ Deadline; Start-Sleep -Milliseconds 25 }}\n"
            + marker)
    helper.write_bytes(body.encode("utf-8-sig"))
    if change == "backup-parent-junction":
        alternate = folder / "alternate"
        backup.parent.rename(alternate)
        subprocess.run(["cmd", "/c", "mklink", "/J", str(backup.parent), str(alternate)],
                       check=True, capture_output=True)
    command = [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(helper)]
    if change == "pinned":
        import os
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            end = time.monotonic() + 20
            while not ready.exists() and process.poll() is None and time.monotonic() < end:
                time.sleep(.05)
            assert ready.exists(), "helper did not finish opening source evidence"
            assert target.read_bytes() == old
            with pytest.raises(PermissionError):
                backup.parent.rename(folder / "moved-backups")
            for path in (backup, receipt, job_path, index):
                with pytest.raises(PermissionError):
                    path.write_bytes(b"unrelated replacement")
            external.write_bytes(b"unrelated file")
            with pytest.raises(PermissionError):
                os.replace(external, backup)
            assert external.read_bytes() == b"unrelated file" and backup.read_bytes() == old
        finally:
            release.write_text("release", encoding="utf-8")
            process.communicate(timeout=30)
    else:
        process = subprocess.run(command, capture_output=True, timeout=30)
    if change == "backup-parent-junction":
        assert process.returncode != 0 and result.exists()
        assert json.loads(result.read_text("utf-8"))["phase"] == "source_directory_validation"
        assert target.read_bytes() == old and not health.exists()
        assert (folder / "alternate" / backup_id).read_bytes() == old
        assert update_target_state.read(target) == transaction
        return
    assert result.is_file(), (process.returncode, process.stdout, process.stderr)
    report = json.loads(result.read_text("utf-8"))
    if change in {"valid", "pinned"}:
        assert process.returncode == 0 and report["state"] == "applied"
        assert target.read_bytes() == new and backup.read_bytes() == old
        assert update_target_state.read(target)["phase"] == "applied"
    else:
        assert process.returncode != 0 and report["state"] == "failed"
        assert report["phase"] == ("source_job_validation" if change in {"job", "job-array", "helper-pid", "scheduled-no-pid", "helper-executable", "index"}
                                    else "backup_validation")
        assert target.read_bytes() == old and not health.exists()
        assert update_target_state.read(target) == transaction
        if change == "hardlink":
            assert backup.read_bytes() == external.read_bytes() == old


@pytest.mark.parametrize("tamper", [False, True], ids=["valid", "backup-changed-while-waiting"])
def test_scheduled_upgrade_helper_checks_backup_after_exact_parent_exit(job, monkeypatch, tamper):
    from core import update_jobs, update_target_state
    folder, target, artifact, old, new = job
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(folder))
    backup_id = "opskit.v12344.123." + "b" * 32 + ".bak"
    old_digest, new_digest = hashlib.sha256(old).hexdigest(), hashlib.sha256(new).hexdigest()
    transaction = update_target_state.prepare(target, operation="upgrade", from_build=12344, to_build=12345,
        from_digest=old_digest, to_digest=new_digest, owner_data_dir=folder, backup_id=backup_id)
    backup = folder / "backups" / backup_id
    backup.parent.mkdir()
    backup.write_bytes(old)
    receipt = backup.with_name(backup.name + ".json")
    updater._write_update_record(receipt, {"schema_version": 1, "backup_id": backup_id,
        "target": str(target.resolve()), "from_build": 12344, "to_build": 12345,
        "sha256": old_digest, "replacement_sha256": new_digest})
    recovery = {"backup_id": backup_id, "sha256": old_digest, "replacement_sha256": new_digest,
                "transaction": transaction}
    code = """
import json, sys
from pathlib import Path
from core.update_windows import schedule
job = schedule(Path(sys.argv[1]), Path(sys.argv[2]), 12345, json.loads(sys.argv[3]))
print(job, flush=True)
sys.stdin.readline()
"""
    host = subprocess.Popen([sys.executable, "-c", code, str(artifact), str(target), json.dumps(recovery)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        job_id = host.stdout.readline().strip()
        assert len(job_id) == 32, host.stderr.read() if host.poll() is not None else job_id
        root = folder / "cache" / "update-jobs" / job_id
        ack = root / "running.json"
        end = time.monotonic() + 25
        while not ack.exists() and host.poll() is None and time.monotonic() < end:
            time.sleep(.05)
        assert ack.exists() and target.read_bytes() == old
        metadata = updater._read_update_record(root / "job.json")
        assert update_jobs.helper_status(root, metadata) == "alive"
        if tamper:
            backup.write_bytes(b"changed while helper waits")
        host.stdin.write("\n")
        host.stdin.flush()
        host.wait(timeout=15)
        assert host.returncode == 0
        result = root / "result.json"
        end = time.monotonic() + 30
        while not result.exists() and time.monotonic() < end:
            time.sleep(.05)
        assert result.exists()
        report = updater._read_update_record(result)
        if tamper:
            assert report["state"] == "failed" and report["phase"] == "backup_validation"
            assert target.read_bytes() == old and backup.read_bytes() == b"changed while helper waits"
            assert update_target_state.read(target) == transaction
        else:
            assert report["state"] == "applied" and target.read_bytes() == new
            assert backup.read_bytes() == old and update_target_state.read(target)["phase"] == "applied"
        assert updater._read_update_record(receipt)["sha256"] == old_digest
        assert updater._read_update_record(updater._get_cache_path())["apply_job"] == job_id
    finally:
        if host.poll() is None:
            host.kill()
        host.communicate(timeout=10)


def test_schedule_refuses_inaccessible_directory_without_launch(job, monkeypatch):
    from core import native_short_path, update_target_state, update_windows
    folder, target, artifact, old, new = job
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(folder))
    backup_id = "opskit.v12344.123." + "b" * 32 + ".bak"
    old_digest, new_digest = hashlib.sha256(old).hexdigest(), hashlib.sha256(new).hexdigest()
    transaction = update_target_state.prepare(target, operation="upgrade", from_build=12344, to_build=12345,
        from_digest=old_digest, to_digest=new_digest, owner_data_dir=folder, backup_id=backup_id)
    backup = folder / "backups" / backup_id
    backup.parent.mkdir()
    backup.write_bytes(old)
    recovery = {"backup_id": backup_id, "sha256": old_digest, "replacement_sha256": new_digest,
                "transaction": transaction}
    def deny(*_args, **_kwargs):
        raise PermissionError("directory access denied")
    monkeypatch.setattr(native_short_path, "_pin_identity", deny)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("helper launched"))
    with pytest.raises(PermissionError):
        update_windows.schedule(artifact, target, 12345, recovery)
    assert target.read_bytes() == old and backup.read_bytes() == old
    assert update_target_state.read(target) == transaction
    assert not updater._get_cache_path().exists()


@pytest.mark.parametrize("change", ["old-digest", "new-digest", "version", "target", "superseded"])
def test_schedule_refuses_unbound_transaction_before_job_or_cache_changes(job, monkeypatch, change):
    from core import update_target_state
    from core.update_windows import schedule
    folder, target, artifact, old, new = job
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(folder))
    backup_id = "opskit.v12344.123." + "b" * 32 + ".bak"
    old_digest, new_digest = hashlib.sha256(old).hexdigest(), hashlib.sha256(new).hexdigest()
    transaction = update_target_state.prepare(target, operation="upgrade", from_build=12344, to_build=12345,
        from_digest=old_digest, to_digest=new_digest, owner_data_dir=folder, backup_id=backup_id)
    recovery = {"backup_id": backup_id, "sha256": old_digest, "replacement_sha256": new_digest,
                "transaction": dict(transaction)}
    version = 12345
    if change == "old-digest":
        recovery["sha256"] = "1" * 64
    elif change == "new-digest":
        recovery["replacement_sha256"] = "2" * 64
    elif change == "version":
        version += 1
    elif change == "target":
        recovery["transaction"]["target"] = str((folder / "other.exe").resolve())
    else:
        update_target_state.aborted(target, transaction)
    cache = updater._get_cache_path()
    updater._write_update_record(cache, {"unrelated": "preserve"})
    before = cache.read_bytes(), target.read_bytes(), artifact.read_bytes()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("unbound helper was launched"))
    with pytest.raises(ValueError):
        schedule(artifact, target, version, recovery)
    assert (cache.read_bytes(), target.read_bytes(), artifact.read_bytes()) == before
    assert not (cache.parent / "update-jobs").exists()


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
    staged, = folder.glob(".opskit-replace-*")
    assert staged.read_bytes() == b"changed after signature gate"


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
    staged, = folder.glob(".opskit-replace-*")
    assert staged.read_bytes() == new


@pytest.mark.parametrize("record", [False, True], ids=["replacement-stage", "record-temp"])
def test_failed_helper_preserves_file_replaced_at_temporary_path(job, record):
    """An uncooperative process can replace a name even while the helper holds locks."""
    from core.update_windows import _literal

    folder, target, artifact, old, new = job
    result, health = folder / "result.json", folder / "health.json"
    ready, release = folder / "ready", folder / "release"
    helper = folder / "race.ps1"
    if record:
        health.mkdir()
    else:
        artifact.write_bytes(b"invalid snapshot")
    body = script(target=target, artifact=artifact, result=result, health=health,
                  lock=folder / "update.lock", old_digest=hashlib.sha256(old).hexdigest(),
                  new_digest=hashlib.sha256(new).hexdigest(), version=12345, parent_pid=0,
                  recovery={"backup_id": "recorded-backup"}, timeout=20)
    if record:
        marker = "if (-not [OpsKitReplace]::MoveFileEx($tmp, $path, 9))"
        gate = (f"if ($path -eq $health) {{ [IO.File]::WriteAllText({_literal(ready)}, $tmp); "
                f"while (-not [IO.File]::Exists({_literal(release)})) {{ Start-Sleep -Milliseconds 10 }} }}\n        ")
    else:
        marker = "# Preserve stage paths after failure: the name could have been replaced."
        gate = (f"[IO.File]::WriteAllText({_literal(ready)}, $stage)\n    "
                f"while (-not [IO.File]::Exists({_literal(release)})) {{ Start-Sleep -Milliseconds 10 }}\n    ")
    assert body.count(marker) == 1
    helper.write_bytes(body.replace(marker, gate + marker).encode("utf-8-sig"))
    process = subprocess.Popen([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                "-File", str(helper)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic() + 15
        while not ready.exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("helper did not reach the isolated cleanup gate")
            time.sleep(.02)
        temporary = Path(ready.read_text(encoding="utf-8"))
        assert temporary.parent == folder and temporary.is_file()
        temporary.replace(folder / "original-temporary")
        temporary.write_bytes(b"unrelated user file")
        release.write_text("release", encoding="utf-8")
        process.communicate(timeout=30)
        assert process.returncode == 1
        assert temporary.read_bytes() == b"unrelated user file"
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)


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


@pytest.mark.timeout(90)
def test_scheduled_helper_waits_for_exact_parent_process(job):
    folder, target, artifact, old, new = job
    ready, release = folder / "scheduler-ready", folder / "scheduler-release"
    scheduler = folder / "scheduler.py"
    scheduler.write_text(
        "import os, pathlib, sys, time\n"
        "from core.update_windows import schedule\n"
        "folder, target, artifact, ready, release = map(pathlib.Path, sys.argv[1:])\n"
        "os.environ['OPSKIT_DATA_DIR'] = str(folder)\n"
        "job = schedule(artifact, target, 12345)\n"
        "ready.write_text(job, encoding='ascii')\n"
        "while not release.exists(): time.sleep(.02)\n",
        encoding="utf-8",
    )
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    parent = subprocess.Popen([sys.executable, str(scheduler), str(folder), str(target), str(artifact),
                               str(ready), str(release)], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    helper_result = None
    try:
        deadline = time.monotonic() + 30
        while not ready.exists():
            if parent.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("scheduler did not launch the update helper")
            time.sleep(.02)
        job_dir = folder / "cache" / "update-jobs" / ready.read_text(encoding="ascii")
        deadline = time.monotonic() + 30
        while not (job_dir / "running.json").exists():
            if (job_dir / "result.json").exists():
                raise RuntimeError(f"scheduled helper failed before acknowledgement: {(job_dir / 'result.json').read_text(encoding='utf-8')}")
            if parent.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("scheduled helper did not acknowledge launch")
            time.sleep(.02)
        assert target.read_bytes() == old
        assert not (job_dir / "result.json").exists()
        release.write_text("release", encoding="ascii")
        stdout, stderr = parent.communicate(timeout=10)
        assert parent.returncode == 0, (stdout, stderr)
        deadline = time.monotonic() + 20
        while not (job_dir / "result.json").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("scheduled helper did not finish after its parent exited")
            time.sleep(.02)
        helper_result = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
        assert helper_result["state"] == "applied"
        assert target.read_bytes() == new
    finally:
        if parent.poll() is None:
            parent.terminate()
            parent.communicate(timeout=10)


def test_invalid_inherited_parent_handle_refuses_replacement(job):
    folder, target, artifact, old, new = job
    result = folder / "result.json"
    helper = folder / "invalid-handle.ps1"
    helper.write_bytes(script(target=target, artifact=artifact, result=result,
        health=folder / "health.json", lock=folder / "update.lock",
        old_digest=hashlib.sha256(old).hexdigest(), new_digest=hashlib.sha256(new).hexdigest(),
        version=12345, parent_pid=0, parent_handle=1).encode("utf-8-sig"))
    process = subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-File", str(helper)], capture_output=True, timeout=30)
    assert process.returncode == 1
    assert json.loads(result.read_text("utf-8"))["phase"] == "parent_wait"
    assert target.read_bytes() == old


def test_scheduled_helper_identifies_parent_after_it_has_exited(job):
    folder, target, artifact, old, new = job
    ready, release = folder / "scheduler-ready", folder / "helper-release"
    scheduler = folder / "scheduler-exits.py"
    scheduler.write_text(
        "import os, pathlib, sys\n"
        "from core import update_windows\n"
        "folder, target, artifact, ready, release = map(pathlib.Path, sys.argv[1:])\n"
        "os.environ['OPSKIT_DATA_DIR'] = str(folder)\n"
        "original = update_windows.script\n"
        "def delayed(**kwargs):\n"
        "    gate = 'while (-not [IO.File]::Exists(' + update_windows._literal(release) + ')) { Start-Sleep -Milliseconds 10 }\\n'\n"
        "    return original(**kwargs).replace('Add-Type -TypeDefinition', gate + 'Add-Type -TypeDefinition', 1)\n"
        "update_windows.script = delayed\n"
        "ready.write_text(update_windows.schedule(artifact, target, 12345), encoding='ascii')\n",
        encoding="utf-8",
    )
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    try:
        parent = subprocess.run([sys.executable, str(scheduler), str(folder), str(target), str(artifact),
                                 str(ready), str(release)], env=env, capture_output=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        assert parent.returncode == 0, (parent.stdout, parent.stderr)
        job_dir = folder / "cache" / "update-jobs" / ready.read_text(encoding="ascii")
        assert target.read_bytes() == old and not (job_dir / "running.json").exists()
        release.write_text("release", encoding="ascii")
        deadline = time.monotonic() + 15
        while not (job_dir / "result.json").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("scheduled helper did not finish after the parent had exited")
            time.sleep(.02)
        assert json.loads((job_dir / "result.json").read_text(encoding="utf-8"))["state"] == "applied"
        assert target.read_bytes() == new
    finally:
        release.write_text("release", encoding="ascii")


def test_inherited_parent_handle_timeout_preserves_target(job):
    from core.update_windows import _inherited_parent_handle
    folder, target, artifact, old, new = job
    result, helper = folder / "result.json", folder / "waiting.ps1"
    with _inherited_parent_handle() as handle:
        helper.write_bytes(script(target=target, artifact=artifact, result=result,
            health=folder / "health.json", lock=folder / "update.lock",
            old_digest=hashlib.sha256(old).hexdigest(), new_digest=hashlib.sha256(new).hexdigest(),
            version=12345, parent_pid=0, parent_handle=handle, timeout=2).encode("utf-8-sig"))
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [handle]}
        process = subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                  "-File", str(helper)], capture_output=True, timeout=30,
                                 startupinfo=startup, close_fds=True)
    assert process.returncode == 1
    assert json.loads(result.read_text("utf-8"))["phase"] == "parent_wait"
    assert target.read_bytes() == old


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


def test_scheduling_rejects_transaction_owned_by_another_data_directory(job, monkeypatch):
    from core.update_target_state import UpdateOwnerMismatch, data_directory_identity
    from core.update_windows import schedule
    folder, target, artifact, old, new = job
    data = folder / "data"
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(data))
    recovery = {"backup_id": "opskit.v1.2." + "a" * 32 + ".bak",
                "transaction": {"owner_data_dir": data_directory_identity(folder / "elsewhere")}}
    with pytest.raises(UpdateOwnerMismatch):
        schedule(artifact, target, 12345, recovery)
    assert target.read_bytes() == old and artifact.read_bytes() == new
    assert not (data / "cache" / "update-jobs").exists()


def test_real_helper_respects_platform_staging_lock(job):
    from core.resource_lock import ResourceLock
    folder, target, artifact, old, new = job
    with ResourceLock(lambda: folder / "update.lock", label="native updater test").hold():
        probe = folder / "staging-lock-observed"
        code, result, health = run_job(job, timeout=2, lock_probe=probe)
    assert code == 1 and result["state"] == "failed" and result["phase"] == "resource_lock"
    assert probe.read_text(encoding="utf-8") == "locked"
    assert target.read_bytes() == old and not health.exists()


def test_helper_total_deadline_includes_initialization(job):
    folder, target, artifact, old, new = job
    code, result, health = run_job(job, timeout=2, delay_before_parent=2200)
    assert code == 1 and result["state"] == "failed" and result["phase"] == "parent_wait"
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
    monkeypatch.setattr(windows_permissions, "copy_dacl_to_verified_file",
                        lambda *a: (_ for _ in ()).throw(PermissionError("ACL copy denied")))
    with pytest.raises(PermissionError):
        updater._atomic_copy(artifact, target)
    staged, = folder.glob(".opskit-replace-*")
    assert target.read_bytes() == old and staged.read_bytes() == new


def test_permission_copy_rejects_substituted_file_without_changing_acl(job):
    from core.secrets import restrict_permissions
    from core.windows_permissions import _descriptor, copy_dacl_to_verified_file
    folder, target, artifact, old, new = job
    restrict_permissions(target)
    other = folder / "other"
    other.write_bytes(b"other data")
    before, protection = _descriptor(other)
    with pytest.raises(RuntimeError, match="staging identity changed"):
        copy_dacl_to_verified_file(target, other, artifact.stat())
    after, after_protection = _descriptor(other)
    assert after.raw == before.raw and after_protection == protection
    assert other.read_bytes() == b"other data"


def test_real_helper_respects_shared_executable_lock(job):
    from core.resource_lock import ResourceLock
    folder, target, artifact, old, new = job
    shared = updater._update_target_lock_path(target)
    with ResourceLock(lambda: shared, label="native shared target test").hold():
        probe = folder / "target-lock-observed"
        code, result, health = run_job(job, timeout=2, target_lock=shared, lock_probe=probe)
    assert code == 1 and result["state"] == "failed" and result["phase"] == "resource_lock"
    assert probe.read_text(encoding="utf-8") == "locked"
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
