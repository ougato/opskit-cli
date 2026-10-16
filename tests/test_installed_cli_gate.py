"""Installed acceptance must reject inconsistent output and unexpected jobs."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location(
    "installed_gate", Path(__file__).resolve().parents[1] / "scripts/verify_installed_cli.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


@pytest.mark.parametrize("failure", [None, "missing_error", "success_error", "failure_error",
                                     "schema_bool", "error_code", "packaged_only"])
def test_installed_gate_output_and_reconcile_contract(tmp_path, monkeypatch, failure):
    environment = tmp_path / "environment"
    environment.mkdir()
    output = tmp_path / "report"

    def run(command, **options):
        assert options["stdin"] is subprocess.DEVNULL
        if "-c" in command:
            value = {"main": str(environment / "main.py"), "core": str(environment / "core/__init__.py"),
                     "commands": [[]], "skipped_help": [], "absent_pid": 2147483647}
            return subprocess.CompletedProcess(command, 0, json.dumps(value), "")
        code = 0
        if "repair-state" in command or "--limit" in command or "--family" in command:
            code = 2
        elif str(2147483647) in command:
            code = 1
        if command[-2:] == ["update", "reconcile"]:
            code = 0 if failure == "packaged_only" else 2
        data = {}
        if "processes" in command and "--once" in command:
            data = [{"rate_schema_version": 2, "cpu_percent": None,
                     "rate_status": "initial" if code == 0 else "gone"}]
        if command[-2:] == ["update", "reconcile"]:
            data = {"state": "no_job"}
        value = {"schema_version": 1, "ok": code == 0, "data": data,
                 "error": None if code == 0 else {"code": code, "message": "rejected"}}
        if failure == "missing_error":
            value.pop("error")
        if failure == "schema_bool":
            value["schema_version"] = True
        if failure == "success_error" and code == 0:
            value["error"] = {"code": 1, "message": "failed"}
        if failure == "failure_error" and code:
            value["error"] = None
        if failure == "error_code" and code:
            value["error"]["code"] = 99
        return subprocess.CompletedProcess(command, code, json.dumps(value), "")

    monkeypatch.setattr(gate.subprocess, "run", run)
    if os.name == "nt":
        monkeypatch.setattr(gate, "verify_windows_python_routes", lambda *a: {"fixture": True})
    if failure is None:
        assert gate.verify(environment, output)["checks"] == 15
        assert (output / "verification.json").is_file()
    else:
        with pytest.raises((ValueError, RuntimeError)):
            gate.verify(environment, output)
        assert not (output / "verification.json").exists()
