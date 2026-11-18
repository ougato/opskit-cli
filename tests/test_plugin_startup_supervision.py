"""Startup owns its reader before yield and cannot restart the policy clock."""
import json
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from worker_test_markers import publish_marker

from core import config, plugin_inspection as inspection, plugin_sandbox as sandbox


def _blocked_timeout(connection, buffer, length, requested):
    requested.wait()
    request = json.loads(bytes(memoryview(buffer).cast("B")[:length.value]))
    read, write = os.pipe()
    publish_marker(Path(request["config"]), str(os.getpid()))
    try:
        os.read(read, 1)
    finally:
        os.close(read)
        os.close(write)
        connection.close()


def test_real_config_reread_shares_reader_and_restores_outer_budget(tmp_path):
    config.save_config({"plugin": {"sandbox": {"timeout": 10}}})
    with sandbox._execution_budget():
        pid = sandbox._invocations.inspector.pid
        parent = sandbox._invocations.deadline
        config.save_config({"plugin": {"sandbox": {"timeout": 1}}})
        with sandbox._execution_budget():
            assert sandbox._invocations.inspector.pid == pid
            assert sandbox._invocations.deadline < parent
        assert sandbox._invocations.deadline == parent
    assert sandbox._invocations.deadline is None
    assert sandbox._invocations.inspector is None
    assert not psutil.pid_exists(pid)


def test_blocked_startup_read_reaped_before_yield(tmp_path, monkeypatch, expire_worker_after_marker):
    marker = tmp_path / "worker-pid"
    monkeypatch.setattr(config, "get_config_path", lambda: marker)
    monkeypatch.setattr(inspection, "_inspection_session", _blocked_timeout)
    _, observed = expire_worker_after_marker(marker, startup_budget=sandbox.CONFIG_READ_TIMEOUT)
    with pytest.raises(sandbox.SandboxError, match="execution deadline reading sandbox settings"):
        with sandbox._execution_budget():
            pytest.fail("blocked configuration yielded execution")
    assert observed
    assert not psutil.pid_exists(int(marker.read_text()))
    assert sandbox._invocations.deadline is None
    assert sandbox._invocations.inspector is None
    assert not sandbox._invocations.inspection_scope


def test_cancel_during_startup_reaps_and_restores_state(tmp_path, monkeypatch):
    import time
    from network import worker
    marker = tmp_path / "worker-pid"
    monkeypatch.setattr(config, "get_config_path", lambda: marker)
    monkeypatch.setattr(inspection, "_inspection_session", _blocked_timeout)
    def clock():
        if marker.exists():
            raise KeyboardInterrupt
        return time.monotonic()
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
    with pytest.raises(KeyboardInterrupt):
        with sandbox._execution_budget():
            pytest.fail("cancelled startup yielded execution")
    assert not psutil.pid_exists(int(marker.read_text()))
    assert sandbox._invocations.deadline is None
    assert sandbox._invocations.inspector is None
    assert not sandbox._invocations.inspection_scope


def test_actual_config_result_cannot_restart_clock(tmp_path, monkeypatch):
    config.save_config({"plugin": {"sandbox": {"timeout": 1}}})
    clock = [100.0]
    pids = []
    real_read = inspection.ContentInspector.execution_timeout
    def delayed(self, path, *, timeout):
        value = real_read(self, path, timeout=timeout)
        pids.append(self.pid)
        clock[0] += 3
        return value
    monkeypatch.setattr(sandbox, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(inspection.ContentInspector, "execution_timeout", delayed)
    with pytest.raises(sandbox.SandboxError, match="execution deadline"):
        with sandbox._execution_budget():
            pytest.fail("late real policy result restarted the clock")
    assert pids and not psutil.pid_exists(pids[0])
    assert sandbox._invocations.deadline is None


def test_bad_real_config_denies_before_yield_without_private_output(tmp_path):
    path = config.get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "private-marker: [invalid yaml"
    path.write_text(body)
    before = {child.pid for child in multiprocessing.active_children()}
    with pytest.raises(sandbox.SandboxError) as caught:
        with sandbox._execution_budget():
            pytest.fail("invalid config yielded execution")
    assert str(caught.value) == "invalid sandbox settings"
    assert path.read_text() == body
    assert {child.pid for child in multiprocessing.active_children()} <= before
    assert sandbox._invocations.deadline is None
    assert sandbox._invocations.inspector is None


@pytest.mark.parametrize("result", [{}, {"ok": 1, "timeout": 10}, {"ok": True, "timeout": True},
                                    {"ok": True, "timeout": 10, "config": {}},
                                    {"ok": True, "timeout": float("nan")}])
def test_timeout_result_cannot_grant_budget_from_bad_response(result):
    with pytest.raises(RuntimeError):
        inspection._timeout_value(result)
