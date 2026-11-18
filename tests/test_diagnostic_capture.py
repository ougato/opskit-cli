"""Real command trees and bounded diagnostic capture, not mocked subprocesses."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest

from core import process, privilege


def test_capture_preserves_exit_status_and_merged_output():
    result = process.capture([sys.executable, "-c",
        "import sys; print('out', flush=True); print('err', file=sys.stderr); sys.exit(7)"], timeout=5)
    assert result.returncode == 7
    assert "out" in result.stdout and "err" in result.stdout


def test_timeout_reaps_actual_descendant(tmp_path):
    marker = tmp_path / "child.json"
    code = ("import subprocess,sys,time,json; from pathlib import Path; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
            "Path(sys.argv[1]).write_text(json.dumps(child.pid)); time.sleep(30)")
    with pytest.raises(subprocess.TimeoutExpired):
        # Include two Python launches and Windows Job attachment under load;
        # the marker proves the descendant existed before timeout cleanup.
        process.capture([sys.executable, "-c", code, str(marker)], timeout=8)
    pid = json.loads(marker.read_text())
    deadline = time.monotonic() + 3
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
            break
        time.sleep(.05)
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_output_limit_closes_generator_and_reaps_command(monkeypatch):
    original = process.stream
    refs = []
    def tracked(cmd, **kwargs):
        return original(cmd, process_ref=refs, **kwargs)
    monkeypatch.setattr(process, "stream", tracked)
    with pytest.raises(RuntimeError, match="output exceeds limit"):
        process.capture([sys.executable, "-u", "-c",
            "import time; print('x'*2000); time.sleep(30)"], timeout=5, max_bytes=1000)
    assert len(refs) == 1 and refs[0].poll() is not None


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_budget_never_launches_command(monkeypatch, timeout):
    monkeypatch.setattr(process.subprocess, "Popen", lambda *a, **k: pytest.fail("invalid budget launched command"))
    with pytest.raises(ValueError):
        process.capture(["invalid"], timeout=timeout)


def test_privileged_diagnostic_always_uses_noninteractive_sudo(monkeypatch):
    monkeypatch.setattr(privilege, "is_root", lambda: False)
    monkeypatch.setattr(privilege.sys, "platform", "linux")
    monkeypatch.setattr(privilege, "_has_sudo", lambda: True)
    calls = []
    monkeypatch.setattr(process, "capture", lambda command, **kwargs: calls.append((command, kwargs)))
    privilege.capture_as_root(["fixed-command", "setting"], timeout=2)
    assert calls == [(["sudo", "-n", "fixed-command", "setting"], {"timeout": 2, "max_bytes": 65536})]


def test_reader_start_failure_reclaims_already_started_client(monkeypatch):
    refs = []
    original = process.start
    def tracked(*args, **kwargs):
        child = original(*args, **kwargs)
        refs.append(child)
        return child
    def failed_start(self):
        raise RuntimeError("cannot start reader")
    monkeypatch.setattr(process, "start", tracked)
    monkeypatch.setattr(process.threading.Thread, "start", failed_start)
    with pytest.raises(RuntimeError, match="cannot start reader"):
        process.capture([sys.executable, "-c", "import time; time.sleep(60)"], timeout=5)
    assert len(refs) == 1 and refs[0].poll() is not None
    assert refs[0].stdout.closed
def test_native_capture_uses_supplied_working_directory(tmp_path):
    import sys
    from core.process import capture
    result = capture([sys.executable, "-c", "from pathlib import Path; print(Path.cwd())"],
                     timeout=5, cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == str(tmp_path)

