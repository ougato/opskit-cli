"""Disposable real-image recovery experiments; not a production scheduler.

The EXE is a copy of the local Python interpreter, not a packaged OpsKit
release. Source-job and backup-receipt authenticity are test assumptions:
the helper compares their exact pre-validated fixture bytes under both locks.
No recovered executable is restarted automatically. Run explicitly with
``python -m pytest research/rollback_protocol/windows_live.py`` on Windows.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import time

import pytest

from core import update_jobs, update_target_state

POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
pytestmark = pytest.mark.skipif(os.name != "nt" or not POWERSHELL.is_file(), reason="Windows helper experiment")

_RUNNER = r'''
import json, os, subprocess, sys
from pathlib import Path
from core.update_windows import _inherited_parent_handle, _literal, script
root = Path(sys.argv[1])
details = json.loads((root / "fixture.json").read_text(encoding="utf-8"))
job = root / details["rollback_id"]
job.mkdir()
source, receipt, intent = (root / name for name in ("source-job.json", "backup.json", "rollback-intent.json"))
intent.write_text(json.dumps({"id": details["rollback_id"], "transaction": details["transaction"]["transaction_id"],
                              "source": details["source_id"]}), encoding="utf-8")
checks = [(path, path.read_text(encoding="utf-8")) for path in (source, receipt, intent)]
with _inherited_parent_handle() as parent_handle:
    body = script(target=root / "application.exe", artifact=root / "backup.exe", result=job / "result.json",
        health=root / "health.json", lock=root / "data.lock", target_lock=root / "target.lock",
        old_digest=details["new_digest"], new_digest=details["old_digest"], version=details["from_build"],
        parent_pid=os.getpid(), parent_handle=parent_handle, timeout=90,
        recovery={"transaction": details["transaction"], "backup_id": details["backup_id"],
                  "job_id": details["rollback_id"], "source_job_id": details["source_id"]})
    guard = "    $phase = 'prototype_source_authentication'\n"
    for path, content in checks:
        guard += ("    $entry = New-Object IO.FileInfo(" + _literal(path) + ")\n"
                  "    if (-not $entry.Exists -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) "
                  "-or $entry.Length -gt 65536) { throw 'invalid_source_evidence' }\n"
                  "    if ([IO.File]::ReadAllText(" + _literal(path) + ") -cne " + _literal(content) + ") "
                  "{ throw 'source_evidence_changed' }\n")
    # The helper also verifies the staged copy against the rollback digest.
    guard += "    if ((Digest " + _literal(root / "backup.exe") + ") -ne " + _literal(details["old_digest"]) + ") { throw 'backup_changed' }\n"
    marker = "    $phase = 'transaction_validation'"
    assert body.count(marker) == 1
    body = body.replace(marker, guard + marker)
    if details.get("crash_after_commit"):
        marker = "    $phase = 'recovery_record'"
        assert body.count(marker) == 1
        body = body.replace(marker, "    exit 9\n" + marker)
    (job / "apply.ps1").write_bytes(body.encode("utf-8-sig"))
    metadata = {"schema_version": 2, "job_id": details["rollback_id"], "version": details["from_build"],
                "helper_executable": details["powershell"], "state": "launching"}
    (job / "job.json").write_text(json.dumps(metadata), encoding="utf-8")
    startup = subprocess.STARTUPINFO()
    startup.lpAttributeList = {"handle_list": [parent_handle]}
    process = subprocess.Popen([details["powershell"], "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                "-File", str(job / "apply.ps1")], close_fds=True, startupinfo=startup,
                               creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    metadata.update(state="scheduled", helper_pid=process.pid)
    (job / "job.json").write_text(json.dumps(metadata), encoding="utf-8")
print(json.dumps({"pid": process.pid}), flush=True)
sys.stdin.readline()
'''


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wait_for(path, *, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.is_file():
            return
        time.sleep(.1)
    pytest.fail(f"timed out waiting for {path.name}")


def _wait_for_exit(job, metadata, *, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        status = update_jobs.helper_status(job, metadata)
        if status == "exited":
            return
        if status != "alive":
            pytest.fail(f"unproven helper identity: {status}")
        time.sleep(.1)
    pytest.fail("helper did not exit")


def _prepare(root):
    target, backup = root / "application.exe", root / "backup.exe"
    shutil.copy2(sys._base_executable, target)
    shutil.copy2(target, backup)
    with backup.open("ab") as stream:
        stream.write(b"opskit-disposable-older-image")
    old_digest, new_digest = _digest(backup), _digest(target)
    backup_id = "opskit.v1.123." + "b" * 32 + ".bak"
    source_id, rollback_id = "a" * 32, "c" * 32
    transaction = update_target_state.prepare(target, operation="rollback", from_build=12345, to_build=12344,
        from_digest=new_digest, to_digest=old_digest, owner_data_dir=root, backup_id=backup_id,
        source_transaction_id="d" * 32)
    details = {"old_digest": old_digest, "new_digest": new_digest, "backup_id": backup_id,
               "source_id": source_id, "rollback_id": rollback_id, "transaction": transaction,
               "from_build": 12345, "powershell": str(POWERSHELL.resolve())}
    (root / "fixture.json").write_text(json.dumps(details), encoding="utf-8")
    (root / "source-job.json").write_text(json.dumps({"job_id": source_id, "backup_id": backup_id,
        "transaction_id": transaction["source_transaction_id"], "old_digest": old_digest,
        "new_digest": new_digest}), encoding="utf-8")
    (root / "backup.json").write_text(json.dumps({"backup_id": backup_id, "digest": old_digest,
        "target": str(target.resolve())}), encoding="utf-8")
    (root / "health.json").write_text(json.dumps({"build": 12345, "confirmed": False, "fails": 3,
        "backup_id": backup_id, "job_id": source_id}), encoding="utf-8")
    (root / "user-file").write_bytes(b"untouched")
    return details


@pytest.mark.parametrize("case", ["valid", "source-changed-after-launch", "crash-after-commit"])
def test_real_executable_waits_for_parent_and_preserves_evidence(tmp_path, case):
    root = tmp_path
    details = _prepare(root)
    if case == "crash-after-commit":
        details["crash_after_commit"] = True
        (root / "fixture.json").write_text(json.dumps(details), encoding="utf-8")
    original = {name: (root / name).read_bytes() for name in ("backup.exe", "backup.json", "health.json", "user-file")}
    env = os.environ.copy()
    env["PYTHONHOME"] = sys.base_prefix
    env["PYTHONPATH"] = os.pathsep.join((str(Path(__file__).resolve().parents[2]), sysconfig.get_paths()["purelib"]))
    env.pop("VIRTUAL_ENV", None)
    host = subprocess.Popen([str(root / "application.exe"), "-c", _RUNNER, str(root)], env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        launch = json.loads(host.stdout.readline())
        job = root / details["rollback_id"]
        _wait_for(job / "running.json")
        metadata = json.loads((job / "job.json").read_text(encoding="utf-8"))
        assert metadata["helper_pid"] == launch["pid"]
        assert update_jobs.helper_status(job, metadata) == "alive"
        assert _digest(root / "application.exe") == details["new_digest"]
        assert not (job / "result.json").exists()  # Exact parent is still alive.
        if case == "source-changed-after-launch":
            (root / "source-job.json").write_text('{"changed":true}', encoding="utf-8")
        host.stdin.write("\n")
        host.stdin.flush()
        host.wait(timeout=20)
        assert host.returncode == 0
        _wait_for_exit(job, metadata)
        if case == "crash-after-commit":
            assert not (job / "result.json").exists()
            assert _digest(root / "application.exe") == details["old_digest"]
            assert update_target_state.read(root / "application.exe")["phase"] == "prepared"
        elif case == "source-changed-after-launch":
            result = json.loads((job / "result.json").read_text(encoding="utf-8"))
            assert result["state"] == "failed" and result["phase"] == "prototype_source_authentication"
            assert _digest(root / "application.exe") == details["new_digest"]
            assert update_target_state.read(root / "application.exe")["phase"] == "prepared"
        else:
            result = json.loads((job / "result.json").read_text(encoding="utf-8"))
            assert result["state"] == "applied"
            assert _digest(root / "application.exe") == details["old_digest"]
            assert update_target_state.read(root / "application.exe")["phase"] == "applied"
            probe = subprocess.run([str(root / "application.exe"), "-c", "print('restored')"],
                                   env=env, capture_output=True, text=True, timeout=20)
            assert probe.returncode == 0 and probe.stdout.strip() == "restored"
        for name, value in original.items():
            assert (root / name).read_bytes() == value
        # Deliberately do not relaunch an unverified older build.
    finally:
        if host.poll() is None:
            host.kill()
        host.communicate(timeout=10)


def test_historical_post_update_removes_existing_pending_without_ownership(tmp_path):
    project = Path(__file__).resolve().parents[2]
    source = subprocess.run(["git", "show", "f8ecec8:core/updater.py"], cwd=project,
                            capture_output=True, text=True, encoding="utf-8", check=True).stdout
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "check_and_apply_pending")
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    pending, temporary = tmp_path / "pending", tmp_path / "pending.tmp"
    pending.write_bytes(b"user file or recovery candidate")
    temporary.write_bytes(b"other retained recovery data")
    from types import SimpleNamespace
    namespace = {"sys": SimpleNamespace(argv=["application.exe", "--post-update"]),
        "_reconcile_indexed_job_on_boot": lambda: (False, False), "_get_cache_path": lambda: tmp_path / "cache.json",
        "_update_record_path": lambda path: None, "_get_pending_path": lambda: pending,
        "_get_pending_tmp_path": lambda: temporary, "_log": SimpleNamespace(info=lambda *args: None)}
    exec(compile(module, "historical-updater", "exec"), namespace)
    assert namespace["check_and_apply_pending"]() is False
    assert not pending.exists() and not temporary.exists()
