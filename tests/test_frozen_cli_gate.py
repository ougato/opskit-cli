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
        code = 2 if "repair-state" in command else 0
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
        code = 2 if "repair-state" in command else 0
        data = {"version": 7} if "--version" in command else {}
        if command[-2:] == ["update", "reconcile"]:
            data = {"state": "no_job"}
        payload = {"schema_version": 1, "ok": code == 0, "data": data,
                   "error": None if code == 0 else {"code": 2, "message": "invalid input"}}
        return subprocess.CompletedProcess(command, code, json.dumps(payload).encode(), b"")

    monkeypatch.setattr(gate.subprocess, "run", run)
    output = tmp_path / "acceptance"
    assert gate.verify(binary, 7, output)["checks"] == 20
    assert (output / "verification.json").is_file()
