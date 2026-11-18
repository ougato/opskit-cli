"""A broken frozen artifact must not produce a successful release gate report."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "verify_frozen_cli", Path(__file__).resolve().parents[1] / "scripts/verify_frozen_cli.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


@pytest.mark.parametrize("failure", ["version", "json", "envelope", "exit", "storage", "timeout", "update_job",
                                     "missing_error", "schema_bool", "success_error", "failure_error",
                                     "error_code", "error_message", "extra_field", "array"])
def test_gate_rejects_broken_artifacts(tmp_path, monkeypatch, failure):
    binary = tmp_path / "built-executable"
    binary.write_bytes(b"test fixture; execution is intercepted")
    output = tmp_path / "acceptance"

    def run(command, **options):
        assert options["stdin"] is subprocess.DEVNULL
        assert options["cwd"] == output / "outside-source"
        if os.name == "nt":
            assert options["env"].get("USERPROFILE") == os.environ.get("USERPROFILE")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, options["timeout"])
        code = (2 if "repair-state" in command else
                1 if "inspect-native-profiles" in command and gate.os.name != "nt" else 0)
        payload = {"schema_version": 1, "ok": code == 0, "data": {},
                   "error": None if code == 0 else {"code": code, "message": "invalid input"}}
        if "--version" in command:
            payload["data"] = {"version": 99 if failure == "version" else 7}
        if command[-2:] == ["update", "reconcile"]:
            payload["data"] = {"state": "pending" if failure == "update_job" else "no_job"}
        if failure == "envelope":
            payload["ok"] = "true"
        if failure == "missing_error":
            payload.pop("error")
        if failure == "schema_bool":
            payload["schema_version"] = True
        if failure == "success_error" and code == 0:
            payload["error"] = {"code": 1, "message": "failed"}
        if code != 0:
            if failure == "failure_error":
                payload["error"] = None
            if failure == "error_code":
                payload["error"]["code"] = 1
            if failure == "error_message":
                payload["error"]["message"] = " "
        if failure == "extra_field":
            payload["unexpected"] = True
        if failure == "array":
            payload = []
        if failure == "exit":
            code = 42
        if failure == "storage":
            Path(options["env"]["OPSKIT_DATA_DIR"]).mkdir(exist_ok=True)
        content = b"not JSON" if failure == "json" else json.dumps(payload).encode()
        return subprocess.CompletedProcess(command, code, content, b"")

    monkeypatch.setattr(gate.subprocess, "run", run)
    with pytest.raises((ValueError, RuntimeError, subprocess.TimeoutExpired)):
        gate.verify(binary, 7, output)
    assert not (output / "verification.json").exists()
    if failure == "exit":
        evidence = json.loads((output / "000.status.json").read_text())
        assert evidence["exit_code"] == 42
        assert evidence["expected_exit_code"] == 0


def test_gate_does_not_reuse_acceptance_directory(tmp_path, monkeypatch):
    binary = tmp_path / "binary"
    binary.write_bytes(b"test")
    output = tmp_path / "existing"
    output.mkdir()
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **kw: pytest.fail("reused evidence directory"))
    with pytest.raises(FileExistsError):
        gate.verify(binary, 7, output)


def test_gate_accepts_complete_success_and_failure_envelopes(tmp_path, monkeypatch):
    binary = tmp_path / "binary"
    binary.write_bytes(b"intercepted fixture")

    def run(command, **options):
        code = (2 if "repair-state" in command else
                1 if "inspect-native-profiles" in command and gate.os.name != "nt" else 0)
        data = {"version": 7} if "--version" in command else {}
        if command[-2:] == ["update", "reconcile"]:
            data = {"state": "no_job"}
        payload = {"schema_version": 1, "ok": code == 0, "data": data,
                   "error": None if code == 0 else {"code": code, "message":
                       "The AppContainer sandbox requires Windows." if code == 1 else "invalid input"}}
        return subprocess.CompletedProcess(command, code, json.dumps(payload).encode(), b"")

    monkeypatch.setattr(gate.subprocess, "run", run)
    output = tmp_path / "acceptance"
    assert gate.verify(binary, 7, output)["checks"] == 30
    assert (output / "verification.json").is_file()
    checks = json.loads((output / "verification.json").read_text())["checks"]
    assert not any("inspect-locks" in check["args"] for check in checks)
    assert not any("configure-creator" in check["args"] for check in checks)
    assert not any("creator-tools" in check["args"] for check in checks)
    assert any(check["args"] == ["plugin", "prepare", "--help"] for check in checks)
    assert any(check["args"] == ["plugin", "permissions", "--help"] for check in checks)
    assert any("inspect-updates" in check["args"] for check in checks)
    assert any("inspect-native-profiles" in check["args"] for check in checks)
    assert any("inspect-native-workspaces" in check["args"] for check in checks)
    assert any(check["args"] == ["plugin", "recover-native-profile", "--help"] for check in checks)


@pytest.mark.parametrize("platform", ["nt", "linux", "darwin"])
@pytest.mark.parametrize("broken", [False, True])
def test_profile_gate_checks_platform_contract_and_rejects_other_failures(tmp_path, monkeypatch, platform, broken):
    from types import SimpleNamespace
    monkeypatch.setattr(gate, "os", SimpleNamespace(name="nt" if platform == "nt" else "posix",
                                                  environ=os.environ))
    binary = tmp_path / "binary"
    binary.write_bytes(b"intercepted fixture")
    profile_calls = []
    def run(command, **options):
        profile = "inspect-native-profiles" in command
        code = (2 if "repair-state" in command else 1 if profile and platform != "nt" else 0)
        message = "The AppContainer sandbox requires Windows." if profile else "invalid input"
        if profile:
            profile_calls.append(command)
            assert command[1:3] == ["--lang", "en"]
            if broken:
                code, message = 1, "unrelated inspection failure"
        data = {"version": 7} if "--version" in command else {}
        if command[-2:] == ["update", "reconcile"]:
            data = {"state": "no_job"}
        payload = {"schema_version": 1, "ok": code == 0, "data": data,
                   "error": None if code == 0 else {"code": code, "message": message}}
        return subprocess.CompletedProcess(command, code, json.dumps(payload).encode(), b"")
    monkeypatch.setattr(gate.subprocess, "run", run)
    output = tmp_path / "acceptance"
    if broken:
        with pytest.raises((ValueError, RuntimeError)):
            gate.verify(binary, 7, output)
        assert not (output / "verification.json").exists()
    else:
        assert gate.verify(binary, 7, output)["checks"] == 30
        checks = json.loads((output / "verification.json").read_text())["checks"]
        profile = next(check for check in checks if "inspect-native-profiles" in check["args"])
        assert profile["exit_code"] == (0 if platform == "nt" else 1)
    assert len(profile_calls) == 1
