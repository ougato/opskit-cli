"""Abrupt parent termination must reclaim a worker stuck in actual OS I/O."""
import json
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest


def alive(process):
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def marking_worker(connection, marker):
    Path(marker).write_text("started")
    from network.worker import send
    send(connection, {"ok": True})


@pytest.mark.parametrize("mode", ["disposable", "session"])
def test_worker_exits_when_parent_is_killed(tmp_path, mode):
    marker = tmp_path / "child.json"
    # Do not use core.process.start: its existing Windows Job would mask whether
    # the multiprocessing worker has its own parent-lifetime protection.
    parent = subprocess.Popen([sys.executable, str(Path(__file__).with_name("worker_parent_crash_fixture.py")), str(marker), mode],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    child = None
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                identity = json.loads(marker.read_text())
                child = psutil.Process(identity["pid"])
                assert child.create_time() == identity["created"]
                break
            except (FileNotFoundError, json.JSONDecodeError):
                assert parent.poll() is None, "fixture parent failed during startup"
                assert time.monotonic() < deadline, "worker did not enter blocking I/O"
                time.sleep(.02)
        parent.kill()
        parent.wait(timeout=5)
        deadline = time.monotonic() + 5
        while alive(child):
            assert time.monotonic() < deadline, "worker survived abrupt parent termination"
            time.sleep(.02)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if child is not None and alive(child):
            try:
                child.kill()
                child.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass
        parent.stdout.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job assignment gate")
def test_job_assignment_failure_never_starts_work(tmp_path, monkeypatch):
    import multiprocessing
    from core import windows_job
    from network.worker import run
    marker = tmp_path / "must-not-start"
    before = {child.pid for child in multiprocessing.active_children()}
    def refused(handle):
        raise OSError("synthetic assignment refusal")
    monkeypatch.setattr(windows_job, "attach_running", refused)
    with pytest.raises(OSError, match="assignment refusal"):
        run(marking_worker, (str(marker),), timeout=30, label="assignment gate")
    assert not marker.exists()
    assert {child.pid for child in multiprocessing.active_children()} == before
