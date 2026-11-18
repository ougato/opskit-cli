"""Python installation delegates bounded execution to the tree supervisor."""
import subprocess
import sys
import time

import pytest

from core import process
from software.base import InstallError
from software.recipes.python import recipe


def test_uv_install_uses_private_directory_budget_and_closed_stdin(tmp_path, monkeypatch):
    monkeypatch.setattr(recipe.Path, "home", lambda: tmp_path)
    observed = []
    def capture(command, **kwargs):
        observed.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "installed", "")
    monkeypatch.setattr(process, "capture", capture)
    monkeypatch.setattr(recipe, "find_uv_python", lambda version: "managed-python")
    progress = []
    assert recipe.PythonRecipe()._install_with_uv("3.12.13", "private-uv", progress.append) == "managed-python"
    command, options = observed[0]
    assert command == ["private-uv", "python", "install", "3.12.13"]
    assert options["stdin"] == subprocess.DEVNULL
    assert options["max_bytes"] == 1024 * 1024
    assert options["timeout"] == 1800
    assert options["env"]["UV_PYTHON_INSTALL_DIR"].startswith(str(tmp_path))
    assert progress == [25, 75]


@pytest.mark.parametrize("failure", ["timeout", "output", "cancel", "spawn", "exit"])
def test_uv_failure_never_looks_up_or_activates_interpreter(monkeypatch, failure, caplog):
    private_output = "https://user:private-token@example.invalid/download"
    def capture(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1800, output=private_output)
        if failure == "output":
            raise RuntimeError("diagnostic command output exceeds limit")
        if failure == "cancel":
            raise KeyboardInterrupt()
        if failure == "spawn":
            raise FileNotFoundError("uv unavailable")
        return subprocess.CompletedProcess(command, 7, private_output, "")
    monkeypatch.setattr(process, "capture", capture)
    monkeypatch.setattr(recipe, "find_uv_python", lambda version: pytest.fail("failed installer queried interpreter"))
    installer = recipe.PythonRecipe()
    if failure == "spawn":
        assert installer._install_with_uv("3.12.13", "uv", lambda pct: None) is None
    else:
        with pytest.raises(KeyboardInterrupt if failure == "cancel" else InstallError) as caught:
            installer._install_with_uv("3.12.13", "uv", lambda pct: None)
        assert private_output not in str(caught.value)
    assert private_output not in caplog.text


@pytest.mark.parametrize("failure", ["timeout", "output"])
def test_real_uv_supervision_reclaims_child_tree(tmp_path, monkeypatch, failure):
    import psutil
    from software.recipes.python import constants
    marker = tmp_path / "child-pid"
    child_code = (
        "import os,time; from pathlib import Path; "
        f"Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    script = (
        "import subprocess,sys,time; from pathlib import Path\n"
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}])\n"
        f"marker=Path({str(marker)!r})\n"
        "deadline=time.monotonic()+5\n"
        "while not marker.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
        "assert marker.exists(), 'child never reached injection point'\n"
        + ("time.sleep(60)\n" if failure == "timeout" else
           "sys.stdout.write('x'*2097152); sys.stdout.flush(); time.sleep(60)\n")
    )
    actual_capture, actual_start = process.capture, process.start
    parents = []
    def start(command, **options):
        parent = actual_start(command, **options)
        parents.append(parent)
        return parent
    def capture(command, **options):
        return actual_capture([sys.executable, "-c", script], **options)
    monkeypatch.setattr(process, "start", start)
    monkeypatch.setattr(process, "capture", capture)
    monkeypatch.setattr(constants, "UV_PYTHON_TIMEOUT", 10)
    monkeypatch.setattr(recipe.Path, "home", lambda: tmp_path)
    try:
        with pytest.raises(InstallError):
            recipe.PythonRecipe()._install_with_uv("3.12.13", "uv", lambda pct: None)
        assert marker.exists(), "test never reached the child-process failure stage"
        assert len(parents) == 1 and parents[0].poll() is not None
        child_pid = int(marker.read_text())
        deadline = time.monotonic() + 3
        while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
            if psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE:
                break
            time.sleep(.02)
        assert not psutil.pid_exists(child_pid) or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
    finally:
        for parent in parents:
            process.terminate_tree(parent)
        if marker.exists():
            try:
                child = psutil.Process(int(marker.read_text()))
                child.kill()
                child.wait(timeout=3)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                pass
