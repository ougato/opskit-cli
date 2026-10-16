"""Owned process-group cleanup must retain permission failures and reap exits."""
import os
import sys
from types import SimpleNamespace

import psutil
import pytest

from core import process


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_exited_owned_group_is_reaped_without_permission_failure():
    child = process.start([sys.executable, "-c", "print('owned', flush=True)"])
    try:
        assert child.stdout.read().strip() == "owned"
        process.terminate_tree(child)
        assert child.returncode is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)
        child.stdout.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
@pytest.mark.parametrize("denied_first_signal", [False, True])
def test_exited_leader_does_not_leave_owned_descendant_running(tmp_path, monkeypatch, denied_first_signal):
    marker = tmp_path / "descendant.pid"
    program = '''
import subprocess, sys
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
Path(sys.argv[1]).write_text(str(child.pid))
print('owned', flush=True)
'''
    child = process.start([sys.executable, "-c", program, str(marker)])
    descendant = None
    try:
        assert child.stdout.read().strip() == "owned"
        descendant = psutil.Process(int(marker.read_text()))
        if denied_first_signal:
            native_killpg, native_poll = os.killpg, child.poll
            signals, polls = [], []

            def killpg(pid, sig):
                signals.append(pid)
                if len(signals) == 1:
                    raise PermissionError("injected first signal denial")
                return native_killpg(pid, sig)

            def poll():
                polls.append(None)
                return None if len(polls) == 1 else native_poll()

            monkeypatch.setattr(process.os, "killpg", killpg)
            child.poll = poll
        process.terminate_tree(child)
        if denied_first_signal:
            assert signals == [child.pid, child.pid]
        try:
            descendant.wait(timeout=3)
        except psutil.TimeoutExpired:
            assert descendant.status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            pass
        assert child.returncode is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)
        if descendant is not None:
            try:
                if descendant.is_running() and descendant.status() != psutil.STATUS_ZOMBIE:
                    descendant.kill()
                    descendant.wait(timeout=3)
            except psutil.NoSuchProcess:
                pass
        child.stdout.close()


def test_reap_then_retry_only_for_an_exited_leader(monkeypatch):
    calls = []
    def killpg(pid, signal):
        calls.append(pid)
        if len(calls) == 1:
            raise PermissionError("zombie group")
        raise ProcessLookupError("reaped group")
    monkeypatch.setattr(process, "os", SimpleNamespace(name="posix", killpg=killpg))
    monkeypatch.setattr(process, "signal", SimpleNamespace(SIGKILL=9))
    child = SimpleNamespace(pid=123, poll=lambda: 0, wait=lambda timeout: 0)
    process.terminate_tree(child)
    assert calls == [123, 123]


def test_permission_race_waits_bounded_exit_before_group_retry(monkeypatch):
    signals, waits = [], []

    def killpg(pid, sig):
        signals.append(pid)
        if len(signals) == 1:
            raise PermissionError("exit not yet waitable")
        raise ProcessLookupError("group now gone")

    def wait(timeout):
        waits.append(timeout)
        return 0

    monkeypatch.setattr(process, "os", SimpleNamespace(name="posix", killpg=killpg))
    child = SimpleNamespace(pid=123, poll=lambda: None, wait=wait)
    monkeypatch.setattr(process, "signal", SimpleNamespace(SIGKILL=9))
    process.terminate_tree(child)
    assert signals == [123, 123]
    assert waits == [0.1, 3]


@pytest.mark.parametrize("exited", [False, True], ids=["live-leader", "permission-still-denied"])
def test_real_permission_denial_is_never_suppressed(monkeypatch, exited):
    calls = []
    def killpg(pid, signal):
        calls.append(pid)
        raise PermissionError("must remain visible")
    monkeypatch.setattr(process, "os", SimpleNamespace(name="posix", killpg=killpg))
    monkeypatch.setattr(process, "signal", SimpleNamespace(SIGKILL=9))
    def wait(timeout):
        assert not exited and timeout == 0.1
        raise process.subprocess.TimeoutExpired("owned child", timeout)

    child = SimpleNamespace(pid=123, poll=lambda: 0 if exited else None, wait=wait)
    with pytest.raises(PermissionError, match="must remain visible"):
        process.terminate_tree(child)
    assert len(calls) == (2 if exited else 1)
