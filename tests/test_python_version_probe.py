"""Detection verifies bounded live command output rather than snapshot claims."""
import shutil
import subprocess
import sys

import pytest

from core import process
from software.recipes.python import common, linux, windows, version_probe


@pytest.mark.parametrize("stdout,stderr,code,expected", [
    ("Python 3.12.13\n", "", 0, "3.12.13"),
    ("", "Python 3.10.20\n", 0, "3.10.20"),
    ("Python 3.12.13\n", "", 1, None),
    ("prefix Python 3.12.13", "", 0, None),
    ("Python 3.12.13rc1", "", 0, None),
    ("Python 3.12.13\nextra", "", 0, None),
])
def test_probe_requires_exact_successful_identity(monkeypatch, stdout, stderr, code, expected):
    def capture(command, **options):
        assert command == ["fixture-python", "--version"]
        assert options == dict(timeout=5, max_bytes=4096, stdin=subprocess.DEVNULL)
        return subprocess.CompletedProcess(command, code, stdout, stderr)
    monkeypatch.setattr(process, "capture", capture)
    assert version_probe.interpreter_version("fixture-python") == expected


@pytest.mark.parametrize("error", [OSError(), RuntimeError(), subprocess.TimeoutExpired("fixture", 5)])
def test_probe_resource_failures_are_unavailable(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(process, "capture", fail)
    assert version_probe.interpreter_version("fixture") is None


def test_probe_cancellation_propagates(monkeypatch):
    def cancel(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(process, "capture", cancel)
    with pytest.raises(KeyboardInterrupt):
        version_probe.interpreter_version("fixture")


@pytest.mark.parametrize("driver", [linux.LinuxDriver, windows.WindowsDriver])
def test_driver_uses_resolved_command_not_stale_snapshot(monkeypatch, driver):
    monkeypatch.setattr(common, "load_snapshot", lambda: {"active_version": "3.12.13", "uv_python_path": sys.executable})
    monkeypatch.setattr(shutil, "which", lambda command: "selected-python")
    monkeypatch.setattr(process, "capture", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, "Python 3.11.9\n", ""))
    assert driver().detect_active() == "3.11.9"


def test_real_interpreter_probe():
    assert version_probe.interpreter_version(sys.executable) == ".".join(map(str, sys.version_info[:3]))
