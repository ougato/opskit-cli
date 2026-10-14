"""Recovery requires actual target bytes, backup authority and exited identity."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from core import updater, update_jobs, update_target_state
from core.update_reconcile import reconcile, read_receipt

NATIVE_HELPER_STATUS = update_jobs.helper_status


def digest(content):
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    target = tmp_path / "program.exe"
    target.write_bytes(b"old")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    build = updater.APP_VERSION + 1
    transaction = update_target_state.prepare(target, operation="upgrade", from_build=updater.APP_VERSION,
        to_build=build, from_digest=digest(b"old"), to_digest=digest(b"new"))
    backup = updater._get_backup_path()
    backup.parent.mkdir()
    backup.write_bytes(b"old")
    updater._write_update_record(backup.with_name(backup.name + ".json"), {
        "schema_version": 1, "backup_id": backup.name, "target": str(target.resolve()),
        "from_build": updater.APP_VERSION, "to_build": build, "sha256": digest(b"old"),
        "replacement_sha256": digest(b"new")})
    job = updater._get_cache_path().parent / "update-jobs" / ("a" * 32)
    metadata = {"schema_version": 2, "job_id": job.name, "version": build, "state": "scheduled",
        "target": str(target.resolve()), "backup_id": backup.name, "transaction_id": transaction["transaction_id"],
        "old_sha256": digest(b"old"), "new_sha256": digest(b"new")}
    updater._write_update_record(job / "job.json", metadata)
    updater._write_update_record(updater._get_cache_path(), {"apply_job": job.name})
    monkeypatch.setattr(update_jobs, "helper_status", lambda *args: "exited")
    return target, backup, job, metadata, transaction


def test_committed_target_rebuilds_missing_health_and_result_idempotently(recovery):
    target, backup, job, metadata, transaction = recovery
    target.write_bytes(b"new")
    report = reconcile()
    assert report["state"] == "applied" and report["reason"] == "target_committed"
    assert update_target_state.read(target)["phase"] == "applied"
    health = updater._read_update_record(updater._get_health_path())
    assert health == {"build": metadata["version"], "confirmed": False, "fails": 0,
                      "backup_id": backup.name, "job_id": job.name}
    assert read_receipt(job, metadata)["target_sha256"] == digest(b"new")
    assert reconcile() == report
    assert target.read_bytes() == b"new" and backup.read_bytes() == b"old"
    assert not (job / "result.json").exists()


def test_original_target_closes_transaction_without_replacing_executable(recovery):
    target, backup, job, metadata, transaction = recovery
    report = reconcile()
    assert report["state"] == "failed" and report["reason"] == "target_unchanged"
    assert update_target_state.read(target)["phase"] == "aborted"
    assert update_target_state.current_build(target, digest(b"old"), 999) == transaction["from_build"]
    assert reconcile() == report
    assert target.read_bytes() == b"old" and not updater._get_health_path().exists()
    target.write_bytes(b"new")
    with pytest.raises(ValueError, match="bytes do not match"):
        update_target_state.current_build(target, digest(b"new"), 999)


@pytest.mark.parametrize("helper", ["alive", "unknown", "identity_mismatch"])
def test_uncertain_or_running_helper_cannot_mutate_records(recovery, monkeypatch, helper):
    target, backup, job, metadata, transaction = recovery
    target.write_bytes(b"new")
    before = update_target_state.path(target).read_bytes()
    monkeypatch.setattr(update_jobs, "helper_status", lambda *args: helper)
    assert reconcile()["reason"] == "helper_" + helper
    assert update_target_state.path(target).read_bytes() == before
    assert not updater._get_health_path().exists() and not (job / "recovery.json").exists()


@pytest.mark.parametrize("change", ["target", "backup", "backup-link", "transaction", "metadata-target",
                                   "metadata-digest", "backup-build", "missing-backup"])
def test_changed_authority_refuses_recovery_without_mutation(recovery, tmp_path, change):
    target, backup, job, metadata, transaction = recovery
    target.write_bytes(b"new")
    if change == "target":
        target.write_bytes(b"unrelated")
    elif change == "backup":
        backup.write_bytes(b"tampered")
    elif change == "backup-link":
        outside = tmp_path / "outside"
        outside.write_bytes(b"old")
        backup.unlink()
        os.link(outside, backup)
    elif change == "missing-backup":
        backup.unlink()
    elif change == "transaction":
        updater._write_update_record(update_target_state.path(target), {**transaction, "transaction_id": "b" * 32})
    elif change == "backup-build":
        sidecar = backup.with_name(backup.name + ".json")
        updater._write_update_record(sidecar, {**updater._read_update_record(sidecar), "from_build": True})
    else:
        field, value = ("target", "//untrusted-server/program.exe") if change == "metadata-target" else ("new_sha256", "c" * 64)
        updater._write_update_record(job / "job.json", {**metadata, field: value})
    before = target.read_bytes(), update_target_state.path(target).read_bytes()
    with pytest.raises((ValueError, OSError)):
        reconcile()
    assert (target.read_bytes(), update_target_state.path(target).read_bytes()) == before
    assert not updater._get_health_path().exists() and not (job / "recovery.json").exists()


def test_recovery_preserves_confirmed_health_and_failure_count(recovery):
    target, backup, job, metadata, transaction = recovery
    target.write_bytes(b"new")
    health = {"build": metadata["version"], "job_id": job.name, "backup_id": backup.name,
              "confirmed": True, "fails": 2}
    updater._write_update_record(updater._get_health_path(), health)
    reconcile()
    assert updater._read_update_record(updater._get_health_path()) == health


def test_other_current_health_is_not_overwritten(recovery):
    target, backup, job, metadata, transaction = recovery
    target.write_bytes(b"new")
    updater._write_update_record(updater._get_health_path(), {"build": metadata["version"], "job_id": "b" * 32,
        "confirmed": False, "fails": 0, "backup_id": backup.name})
    before = updater._get_health_path().read_bytes(), update_target_state.path(target).read_bytes()
    with pytest.raises(ValueError, match="another update"):
        reconcile()
    assert (updater._get_health_path().read_bytes(), update_target_state.path(target).read_bytes()) == before


@pytest.mark.parametrize("failure", ["health", "receipt", "aborted"])
def test_record_write_failure_retains_evidence_and_can_retry(recovery, monkeypatch, failure):
    target, backup, job, metadata, transaction = recovery
    if failure != "aborted":
        target.write_bytes(b"new")
    write = updater._write_update_record
    with monkeypatch.context() as patch:
        def fail(path, record):
            if ((failure == "health" and path == updater._get_health_path())
                    or (failure == "receipt" and path.name == "recovery.json")
                    or (failure == "aborted" and record.get("phase") == "aborted")):
                raise OSError("disk full")
            return write(path, record)
        patch.setattr(updater, "_write_update_record", fail)
        with pytest.raises(OSError, match="disk full"):
            reconcile()
    assert backup.exists() and (job / "job.json").exists()
    assert not (job / "recovery.json").exists()
    assert reconcile()["state"] == ("failed" if failure == "aborted" else "applied")


def test_source_and_no_job_do_not_initialize_runtime(tmp_path, monkeypatch):
    data = tmp_path / "absent"
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(data))
    monkeypatch.setattr(updater, "_is_frozen", lambda: False)
    with pytest.raises(ValueError, match="packaged"):
        reconcile()
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    assert reconcile() == {"state": "no_job"}
    assert not data.exists()


@pytest.mark.parametrize("missing_cache", [False, True])
@pytest.mark.parametrize("committed", [False, True])
def test_unindexed_job_restores_unique_transaction_and_preserves_cache(recovery, missing_cache, committed):
    target, backup, job, metadata, transaction = recovery
    cache = updater._get_cache_path()
    if missing_cache:
        cache.unlink()
    else:
        updater._write_update_record(cache, {"other": "preserved"})
    if committed:
        target.write_bytes(b"new")
    result = reconcile()
    assert result["state"] == ("applied" if committed else "failed")
    restored = updater._read_update_record(cache)
    assert restored["apply_job"] == job.name
    if not missing_cache:
        assert restored["other"] == "preserved"
    assert read_receipt(job, metadata) is not None
    assert backup.read_bytes() == b"old"


@pytest.mark.parametrize("helper", ["alive", "unknown", "identity_mismatch"])
def test_unindexed_live_or_uncertain_job_repairs_index_without_committing(recovery, monkeypatch, helper):
    target, backup, job, metadata, transaction = recovery
    cache = updater._get_cache_path()
    cache.unlink()
    before = update_target_state.path(target).read_bytes()
    monkeypatch.setattr(update_jobs, "helper_status", lambda *args: helper)
    assert reconcile()["reason"] == "helper_" + helper
    assert updater._read_update_record(cache)["apply_job"] == job.name
    assert update_target_state.path(target).read_bytes() == before
    assert not (job / "recovery.json").exists()


@pytest.mark.parametrize("change", ["target", "backup", "digest", "version", "duplicate", "legacy", "unknown"])
def test_unindexed_job_without_unique_authority_does_not_publish_index(recovery, change):
    target, backup, job, metadata, transaction = recovery
    cache = updater._get_cache_path()
    updater._write_update_record(cache, {"other": "preserved"})
    if change == "target":
        target.write_bytes(b"unrelated")
    elif change == "backup":
        backup.write_bytes(b"tampered")
    elif change in {"digest", "version", "legacy"}:
        field, value = {"digest": ("new_sha256", "c" * 64), "version": ("version", True),
                        "legacy": ("schema_version", 1)}[change]
        updater._write_update_record(job / "job.json", {**metadata, field: value})
    elif change == "duplicate":
        duplicate = job.with_name("b" * 32)
        updater._write_update_record(duplicate / "job.json", {**metadata, "job_id": duplicate.name})
    else:
        (job / "unknown.bin").write_bytes(b"keep")
    before = cache.read_bytes(), update_target_state.path(target).read_bytes(), target.read_bytes()
    if change in {"duplicate", "legacy", "unknown"}:
        assert reconcile()["state"] == "deferred"
    else:
        with pytest.raises((OSError, ValueError)):
            reconcile()
    assert before == (cache.read_bytes(), update_target_state.path(target).read_bytes(), target.read_bytes())
    assert not (job / "recovery.json").exists()


def test_boot_does_not_skip_unindexed_jobs(recovery, monkeypatch):
    from core import update_cli
    target, backup, job, metadata, transaction = recovery
    updater._get_cache_path().unlink()
    calls = []
    monkeypatch.setattr(update_cli, "reconcile_on_boot", lambda **kwargs: calls.append(kwargs) or {"state": "deferred"})
    assert updater._reconcile_indexed_job_on_boot() == (True, False)
    assert calls == [{"timeout": 30}]


@pytest.mark.parametrize("remove_directory", [False, True])
def test_missing_job_evidence_does_not_hide_prepared_target_transaction(recovery, remove_directory):
    target, backup, job, metadata, transaction = recovery
    cache = updater._get_cache_path()
    cache.unlink()
    for child in job.iterdir():
        child.unlink()
    job.rmdir()
    if remove_directory:
        job.parent.rmdir()
    assert reconcile() == {"state": "deferred", "reason": "missing_job_evidence"}
    assert not cache.exists()
    assert update_target_state.read(target)["phase"] == "prepared"
    assert target.read_bytes() == backup.read_bytes() == b"old"


@pytest.mark.skipif(os.name != "nt", reason="native Windows helper identity")
@pytest.mark.parametrize("unindexed", [False, True])
def test_native_exited_ack_reconciles_actual_bytes_and_status(recovery, monkeypatch, unindexed):
    target, backup, job, metadata, transaction = recovery
    # Restore the genuine identity probe and write a genuine process ACK.
    native_probe = NATIVE_HELPER_STATUS
    monkeypatch.setattr(update_jobs, "helper_status", native_probe)
    executable = shutil.which("powershell") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    if not Path(executable).is_file():
        pytest.skip("Windows PowerShell unavailable")
    metadata["helper_executable"] = str(Path(executable).resolve())
    updater._write_update_record(job / "job.json", metadata)
    literal = lambda value: "'" + str(value).replace("'", "''") + "'"
    script = job / "apply.ps1"
    script.write_text("$p = [Diagnostics.Process]::GetCurrentProcess()\n"
        "$ack = @{schema_version=1;job_id=" + literal(job.name) + ";version=" + str(metadata["version"])
        + ";pid=$p.Id;created_ticks=$p.StartTime.ToUniversalTime().ToFileTimeUtc();"
        "script=$PSCommandPath;executable=$p.MainModule.FileName}\n"
        "[IO.File]::WriteAllText(" + literal(job / "running.json")
        + ", ($ack | ConvertTo-Json -Compress))\n", encoding="utf-8-sig")
    process = subprocess.Popen([executable, "-NoProfile", "-NonInteractive", "-File", str(script)],
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert process.wait(timeout=15) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    assert native_probe(job, metadata) == "exited"
    if unindexed:
        updater._get_cache_path().unlink()
    target.write_bytes(b"new")
    assert reconcile()["state"] == "applied"
    assert updater._read_update_record(updater._get_cache_path())["apply_job"] == job.name
    from core.update_cli import status
    report = status(timeout=15)["apply_job"]
    assert report["state"] == "applied" and report["reconciled"] is True
    assert report["helper_status"] == "exited" and report["health_confirmed"] is False
    assert target.read_bytes() == b"new" and backup.read_bytes() == b"old"
    # The real exited helper supplies process proof. Health confirmation here
    # is a fixture input, not evidence of executing the replacement program.
    confirmed = updater._read_health_record(updater._get_health_path())
    updater._write_update_record(updater._get_health_path(), {**confirmed, "confirmed": True})
    assert reconcile()["state"] == "applied"
    assert updater._read_update_record(job / "job.json")["state"] == "retired"
    cache = updater._read_update_record(updater._get_cache_path())
    assert cache["apply_job"] is None and cache["last_apply_job"] == job.name
    retired_status = status(timeout=15)["apply_job"]
    assert retired_status["state"] == "applied" and retired_status["active"] is False
    assert retired_status["reconciled"] is True and retired_status["health_confirmed"] is True


@pytest.mark.skipif(os.name != "nt", reason="native Windows replacement helper")
@pytest.mark.parametrize("failure", ["post_commit_health", "bad_artifact"])
def test_real_replacement_helper_failure_is_reconciled_by_actual_digest(recovery, monkeypatch, failure):
    from core.update_windows import script
    target, backup, job, metadata, transaction = recovery
    executable = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    if not executable.is_file():
        pytest.skip("Windows PowerShell unavailable")
    monkeypatch.setattr(update_jobs, "helper_status", NATIVE_HELPER_STATUS)
    metadata["helper_executable"] = str(executable.resolve())
    updater._write_update_record(job / "job.json", metadata)
    artifact = job / "artifact.exe"
    artifact.write_bytes(b"new" if failure == "post_commit_health" else b"tampered")
    health = updater._get_health_path()
    if failure == "post_commit_health":
        health.mkdir()  # Owned test obstacle forces a failure after atomic commit.
    helper = job / "apply.ps1"
    updater._update_lock_path().parent.mkdir(parents=True, exist_ok=True)
    helper.write_bytes(script(target=target, artifact=artifact, result=job / "result.json", health=health,
        lock=updater._update_lock_path(), old_digest=metadata["old_sha256"], new_digest=metadata["new_sha256"],
        version=metadata["version"], parent_pid=0, target_lock=updater._update_target_lock_path(target),
        recovery={"job_id": job.name, "backup_id": backup.name, "transaction": transaction}).encode("utf-8-sig"))
    process = subprocess.Popen([str(executable), "-NoProfile", "-NonInteractive", "-File", str(helper)],
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert process.wait(timeout=20) == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    original_result = updater._read_update_record(job / "result.json")
    if failure == "post_commit_health":
        assert original_result["state"] == "applied_recovery_incomplete"
        assert target.read_bytes() == b"new"
        health.rmdir()
        (job / "result.json").unlink()  # Simulate lost result; target is the evidence.
        assert reconcile()["state"] == "applied"
        assert updater._read_update_record(health)["backup_id"] == backup.name
    else:
        assert original_result["state"] == "failed" and target.read_bytes() == b"old"
        assert original_result["phase"] == "artifact_validation"
        assert reconcile()["state"] == "failed"
        assert not health.exists()
    assert backup.read_bytes() == b"old"


@pytest.fixture
def old_backups(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    target = tmp_path / "program.exe"
    target.write_bytes(b"old")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    backups = []
    for index in range(8):
        backup = updater._get_backup_path()
        backup.parent.mkdir(exist_ok=True)
        backup.write_bytes(b"old")
        updater._write_update_record(backup.with_name(backup.name + ".json"),
                                    {"backup_id": backup.name, "target": str(target.resolve())})
        os.utime(backup, ns=((index + 1) * 1000000000, (index + 1) * 1000000000))
        backups.append(backup)
    return backups


def test_backup_cleanup_pins_jobs_markers_and_health(old_backups):
    backups = old_backups
    cache = updater._get_cache_path()
    job = cache.parent / "update-jobs" / ("a" * 32)
    updater._write_update_record(job / "job.json", {"schema_version": 2, "job_id": job.name,
                                "backup_id": backups[0].name, "version": 123,
                                "state": "scheduled", "transaction_id": "b" * 32})
    updater._write_update_record(cache.parent / "update_pending_path.json", {"backup_id": backups[1].name})
    updater._write_update_record(updater._get_health_path(), {"backup_id": backups[2].name})
    updater._cleanup_old_backups()
    assert all(backups[index].exists() for index in (0, 1, 2, 5, 6, 7))
    assert not backups[3].exists() and not backups[4].exists()


@pytest.mark.parametrize("kind", ["legacy", "unknown", "malformed", "extra"])
def test_ambiguous_backup_references_prevent_cleanup(old_backups, kind):
    cache = updater._get_cache_path()
    job = cache.parent / "update-jobs" / ("a" * 32)
    job.mkdir(parents=True)
    if kind != "unknown":
        updater._write_update_record(job / "job.json", {"schema_version": 1 if kind == "legacy" else 2,
            "job_id": job.name, "version": 123, "state": "scheduled", "transaction_id": "b" * 32,
            "backup_id": "../outside" if kind == "malformed" else old_backups[0].name})
    if kind == "extra":
        (job / "user-data.txt").write_text("keep")
    if kind == "malformed":
        with pytest.raises(ValueError, match="reference"):
            updater._cleanup_old_backups()
    else:
        updater._cleanup_old_backups()
    assert all(path.exists() for path in old_backups)


def test_reconcile_cli_budget_and_source_refusal(tmp_path, monkeypatch):
    import json
    import main
    from core import update_cli
    from typer.testing import CliRunner
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(main, "_boot", lambda: pytest.fail("explicit reconcile must not boot"))
    monkeypatch.setattr(updater, "_is_frozen", lambda: False)
    result = CliRunner().invoke(main.app, ["--output", "json", "update", "reconcile"])
    assert result.exit_code == 2 and json.loads(result.stdout)["ok"] is False
    assert not (tmp_path / "absent").exists()
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    seen = []
    monkeypatch.setattr(update_cli, "reconcile", lambda **kw: seen.append(kw) or {"state": "no_job"})
    result = CliRunner().invoke(main.app, ["--output", "json", "update", "reconcile", "--timeout", "2"])
    assert result.exit_code == 0 and seen == [{"timeout": 2}]
    assert json.loads(result.stdout)["data"]["state"] == "no_job"


@pytest.mark.parametrize("resource", ["cache", "target"])
def test_reconcile_refuses_competing_resource_lock(recovery, resource):
    from core.resource_lock import ResourceLock
    target, backup, job, metadata, transaction = recovery
    target.write_bytes(b"new")
    path = updater._update_lock_path() if resource == "cache" else updater._update_target_lock_path(target)
    before = update_target_state.path(target).read_bytes()
    with ResourceLock(lambda: path, label="competing updater").hold():
        with pytest.raises(RuntimeError, match="busy"):
            reconcile()
    assert update_target_state.path(target).read_bytes() == before
    assert not updater._get_health_path().exists() and not (job / "recovery.json").exists()
    assert reconcile()["state"] == "applied"


@pytest.mark.parametrize("change", ["version", "digest", "job_id", "transaction_id", "state"])
def test_bad_receipt_does_not_override_authority(recovery, change):
    target, backup, job, metadata, transaction = recovery
    target.write_bytes(b"new")
    reconcile()
    receipt_path = job / "recovery.json"
    record = updater._read_update_record(receipt_path)
    field, value = {"version": ("version", True), "digest": ("target_sha256", "c" * 64),
                   "job_id": ("job_id", "b" * 32), "transaction_id": ("transaction_id", "b" * 32),
                   "state": ("state", "scheduled")}[change]
    updater._write_update_record(receipt_path, {**record, field: value})
    before = target.read_bytes(), updater._get_health_path().read_bytes()
    with pytest.raises(ValueError, match="receipt"):
        reconcile()
    assert (target.read_bytes(), updater._get_health_path().read_bytes()) == before


def test_preparing_without_ack_closes_unlaunched_transaction(recovery, monkeypatch):
    target, backup, job, metadata, transaction = recovery
    updater._write_update_record(job / "job.json", {**metadata, "state": "preparing"})
    monkeypatch.setattr(update_jobs, "helper_status", lambda *args: pytest.fail("unlaunched proof needs no PID"))
    assert reconcile()["state"] == "failed"
    assert update_target_state.read(target)["phase"] == "aborted"
    assert target.read_bytes() == b"old" and backup.exists()


def test_preparing_cannot_claim_committed_target(recovery):
    target, backup, job, metadata, transaction = recovery
    updater._write_update_record(job / "job.json", {**metadata, "state": "preparing"})
    target.write_bytes(b"new")
    with pytest.raises(ValueError, match="target changed"):
        reconcile()
    assert update_target_state.read(target)["phase"] == "prepared"
    assert not updater._get_health_path().exists()


@pytest.mark.parametrize("outcome", ["alive", "error", "applied"])
def test_boot_reconciles_before_any_other_update_write(recovery, monkeypatch, outcome):
    from core import update_cli
    target, backup, job, metadata, transaction = recovery
    seen = []
    def probe(**kwargs):
        seen.append(kwargs)
        if outcome == "error":
            raise RuntimeError("private-recovery-path")
        return {"state": "deferred" if outcome == "alive" else "applied"}
    monkeypatch.setattr(update_cli, "reconcile_on_boot", probe)
    assert updater._reconcile_indexed_job_on_boot() == (outcome != "applied", False)
    assert seen == [{"timeout": 30}]
    if outcome != "applied":
        monkeypatch.setattr(updater, "_check_health", lambda: pytest.fail("unresolved job must not roll back"))
        monkeypatch.setattr(updater, "_apply_update_pending_path", lambda: pytest.fail("unresolved job must not retry"))
        monkeypatch.setattr(updater, "_get_pending_path", lambda: pytest.fail("must not clear post-update files"))
        monkeypatch.setattr(updater.sys, "argv", ["opskit", "--post-update"])
        assert updater.check_and_apply_pending() is False


def test_valid_recovery_receipt_overrides_corrupt_helper_result(recovery):
    target, backup, job, metadata, transaction = recovery
    target.write_bytes(b"new")
    reconcile()
    (job / "result.json").write_text("interrupted JSON")
    from core.update_cli import status
    before = (job / "result.json").read_bytes()
    report = status(timeout=15)["apply_job"]
    assert report["state"] == "applied" and report["reconciled"] is True
    assert (job / "result.json").read_bytes() == before


def _blocking_reconcile_worker(connection, directory):
    import time
    from core import update_cli, update_reconcile
    from core.resource_lock import ResourceLock
    root = Path(directory)
    def blocked():
        with ResourceLock(lambda: root / "worker.lock", label="reconciliation test").hold():
            (root / "worker-entered").write_text("ready")
            time.sleep(20)
    update_reconcile.reconcile = blocked
    update_cli._reconcile_worker(connection)


def test_reconciliation_worker_timeout_releases_owned_lock(tmp_path, expire_worker_after_marker):
    from network.worker import run
    from core.resource_lock import ResourceLock
    root = tmp_path / "worker"
    budget, observed = expire_worker_after_marker(root / "worker-entered")
    with pytest.raises(TimeoutError, match="deadline exceeded"):
        run(_blocking_reconcile_worker, (str(root),), timeout=budget, label="update reconciliation")
    assert (root / "worker-entered").read_text() == "ready"
    assert observed, "deadline injection must follow the real lock acquisition"
    with ResourceLock(lambda: root / "worker.lock", label="reconciliation test").hold():
        pass
