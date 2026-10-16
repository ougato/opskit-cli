"""Build helpers must not silently fetch additional executable tools."""
import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location("opskit_build", Path(__file__).resolve().parents[1] / "build.py")
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)

INSTALL_SPEC = importlib.util.spec_from_file_location(
    "install_release_build", Path(__file__).resolve().parents[1] / "scripts/install_release_build.py")
installer = importlib.util.module_from_spec(INSTALL_SPEC)
INSTALL_SPEC.loader.exec_module(installer)


def test_nuitka_refuses_interactive_downloads_and_update_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "DIST_DIR", tmp_path / "dist")
    monkeypatch.setattr(build, "BUILD_DIR", tmp_path / "build")
    monkeypatch.setattr(build, "_find_tool", lambda name: "installed-nuitka")
    monkeypatch.setattr(build, "_plugin_runtime_bundle", lambda: tmp_path / "sdk.zip")
    captured = []

    def run(command, **kwargs):
        captured.append((command, kwargs))
        (build.DIST_DIR / "opskit-test").write_bytes(b"compiled")
        return 0

    monkeypatch.setattr(build, "_run", run)
    assert build.build_nuitka("opskit-test", False, jobs=2) == build.DIST_DIR / "opskit-test"
    command, kwargs = captured[0]
    assert "--assume-yes-for-downloads" not in command
    assert "--update-check=never" in command
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert "--jobs=2" in command


@pytest.mark.parametrize("value", ["0", "-1"])
def test_invalid_compile_parallelism_rejected(value):
    with pytest.raises(build.argparse.ArgumentTypeError):
        build.positive_jobs(value)


def test_nuitka_failure_does_not_report_existing_output_as_success(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "DIST_DIR", tmp_path / "dist")
    monkeypatch.setattr(build, "BUILD_DIR", tmp_path / "build")
    monkeypatch.setattr(build, "_find_tool", lambda name: "installed-nuitka")
    monkeypatch.setattr(build, "_plugin_runtime_bundle", lambda: tmp_path / "sdk.zip")
    build.DIST_DIR.mkdir()
    (build.DIST_DIR / "opskit-test").write_bytes(b"old compiled file")
    monkeypatch.setattr(build, "_run", lambda *args, **kwargs: 1)
    assert build.build_nuitka("opskit-test", False) is None


def test_installer_refuses_existing_environment_before_executing_packages(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "sys", SimpleNamespace(
        implementation=SimpleNamespace(name="cpython"), version_info=(3, 12)))
    marker = tmp_path / "unreviewed-package.py"
    marker.write_text("must not execute")
    monkeypatch.setattr(installer.venv, "EnvBuilder", lambda **kwargs: pytest.fail("reused environment"))
    with pytest.raises(ValueError, match="must not already exist"):
        installer.install(tmp_path)
    assert marker.read_text() == "must not execute"


@pytest.mark.parametrize("failed_lock,expected_calls", [
    ("requirements-installer.txt", 2), ("requirements-release-bootstrap.txt", 3),
])
def test_installer_propagates_failed_hash_check_before_build_stage(tmp_path, monkeypatch, failed_lock, expected_calls):
    monkeypatch.setattr(installer, "sys", SimpleNamespace(
        implementation=SimpleNamespace(name="cpython"), version_info=(3, 12)))
    monkeypatch.setattr(installer.venv, "EnvBuilder", lambda **kwargs: SimpleNamespace(create=lambda target: None))
    calls = []

    def refuse(command, **kwargs):
        calls.append((command, kwargs))
        if "--require-hashes" in command and Path(command[-1]).name == failed_lock:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(installer.subprocess, "run", refuse)
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(tmp_path / "new")
    assert len(calls) == expected_calls
    command, options = calls[-1]
    assert Path(command[-1]).name == failed_lock
    assert Path(calls[1][0][-1]).name == "requirements-installer.txt"
    assert "--require-hashes" in command
    assert "--no-cache-dir" in command
    assert "--disable-pip-version-check" in command
    for _, options in calls:
        for name in ("TMPDIR", "TMP", "TEMP"):
            assert Path(options["env"][name]) == tmp_path / "new/tmp"
