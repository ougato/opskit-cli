"""The temporary compiler workaround must fail closed on dependency drift."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "signal_build_installer", Path(__file__).resolve().parents[1] / "scripts/install_release_build.py"
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.mark.parametrize("version,source", [("4.4", b""), ("4.3", b"modified")])
def test_reject_unreviewed_compiler(version, source):
    with pytest.raises(ValueError, match="unreviewed"):
        installer.patch_bootstrap(source, version)


def test_only_handler_changes_and_second_application_is_refused(monkeypatch):
    original = b"before\n    cleanupChildProcess(!signal_from_terminal);\n}\nafter\n"
    monkeypatch.setattr(installer, "BOOTSTRAP_SHA256", hashlib.sha256(original).hexdigest())
    patched = installer.patch_bootstrap(original, "4.3")
    assert patched == b"before\n    cleanupChildProcess(!signal_from_terminal);\n    _exit(128 + sig);\n}\nafter\n"
    with pytest.raises(ValueError, match="unreviewed"):
        installer.patch_bootstrap(patched, "4.3")


def test_refuse_bootstrap_outside_environment(tmp_path, monkeypatch):
    if installer.os.name != "posix":
        pytest.skip("POSIX build workaround")
    outside = tmp_path / "outside.c"
    outside.write_bytes(b"untouched")
    monkeypatch.setattr(installer.subprocess, "check_output", lambda *a, **kw: json.dumps(["4.3", str(outside)]))
    with pytest.raises(ValueError, match="outside"):
        installer.apply_signal_workaround(tmp_path / "python", tmp_path / "environment")
    assert outside.read_bytes() == b"untouched"


def test_patch_receipt_matches_actual_environment_file(tmp_path, monkeypatch):
    if installer.os.name != "posix":
        pytest.skip("POSIX build workaround")
    target = tmp_path / "environment"
    target.mkdir()
    source = target / "OnefileBootstrap.c"
    original = b"    cleanupChildProcess(!signal_from_terminal);\n}"
    source.write_bytes(original)
    monitor = target / "MainProgram.c"
    monitor_original = b"if (kill(onefile_ppid, 0) == -1 && errno == ESRCH)"
    monitor.write_bytes(monitor_original)
    monkeypatch.setattr(installer, "PARENT_MONITOR_SHA256", hashlib.sha256(monitor_original).hexdigest())
    digest = hashlib.sha256(original).hexdigest()
    monkeypatch.setattr(installer, "BOOTSTRAP_SHA256", digest)
    monkeypatch.setattr(installer.subprocess, "check_output", lambda *a, **kw: json.dumps(["4.3", str(source)]))
    installer.apply_signal_workaround(target / "bin/python", target)
    receipt = json.loads((target / "nuitka-signal-workaround.json").read_text())
    assert receipt["original_sha256"] == digest
    assert receipt["patched_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert b"_exit(128 + sig);" in source.read_bytes()
    assert receipt["parent_monitor_patched_sha256"] == hashlib.sha256(monitor.read_bytes()).hexdigest()
    assert monitor.read_bytes() == b"if (getppid() != (pid_t)onefile_ppid)"
    with pytest.raises(ValueError, match="unreviewed"):
        installer.apply_signal_workaround(target / "bin/python", target)


@pytest.mark.parametrize("version,source", [("4.4", b""), ("4.3", b"modified")])
def test_reject_unreviewed_parent_monitor(version, source):
    with pytest.raises(ValueError, match="unreviewed"):
        installer.patch_parent_monitor(source, version)


def test_monitor_drift_does_not_partially_patch_bootstrap(tmp_path, monkeypatch):
    if installer.os.name != "posix":
        pytest.skip("POSIX build workaround")
    source = tmp_path / "OnefileBootstrap.c"
    original = b"    cleanupChildProcess(!signal_from_terminal);\n}"
    source.write_bytes(original)
    (tmp_path / "MainProgram.c").write_bytes(b"unreviewed")
    monkeypatch.setattr(installer, "BOOTSTRAP_SHA256", hashlib.sha256(original).hexdigest())
    monkeypatch.setattr(installer.subprocess, "check_output", lambda *a, **kw: json.dumps(["4.3", str(source)]))
    with pytest.raises(ValueError, match="unreviewed"):
        installer.apply_signal_workaround(tmp_path / "bin/python", tmp_path)
    assert source.read_bytes() == original
    assert not (tmp_path / "nuitka-signal-workaround.json").exists()
