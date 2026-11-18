"""Quota metadata reads remain fresh and blocked readers are reaped."""
from contextlib import closing
import os
import time
from types import SimpleNamespace

import psutil
import pytest

from worker_test_markers import publish_marker

from core import plugin_inspection as inspection, plugin_state as state


def blocked_quota_session(connection, buffer, length, requested):
    def blocked(root):
        publish_marker(os.environ["OPSKIT_TEST_QUOTA_PID"], str(os.getpid()))
        read, write = os.pipe()
        try:
            os.read(read, 1)
        finally:
            os.close(read)
            os.close(write)
    state.reserve_staging = blocked
    inspection._inspection_session(connection, buffer, length, requested)


def test_reader_is_fresh_and_parent_does_not_scan(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "self"
    root.mkdir()
    monkeypatch.setattr(state, "reserve_staging", lambda *a, **k: pytest.fail("parent scanned"))
    with closing(inspection.ContentInspector()) as reader:
        assert reader.state_quota(root, timeout=30) == (0, 0)
        pid = reader.pid
        wrapper = root.parent / ".state-retained"
        wrapper.mkdir()
        (wrapper / "data").write_bytes(b"data")
        assert reader.state_quota(root, timeout=30) == (1, 4)
        assert reader.pid == pid
    assert not psutil.pid_exists(pid)


@pytest.mark.parametrize("cancel", [False, True])
def test_blocked_system_read_times_out_and_reaps(tmp_path, monkeypatch, expire_worker_after_marker, cancel):
    from network import worker
    marker = tmp_path / "reader-pid"
    monkeypatch.setenv("OPSKIT_TEST_QUOTA_PID", str(marker))
    monkeypatch.setattr(inspection, "_inspection_session", blocked_quota_session)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    reader = inspection.ContentInspector()
    with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
        reader.state_quota(tmp_path.resolve() / "self", timeout=30)
    assert marker.exists(), "worker must reach blocking read before timeout"
    assert not psutil.pid_exists(int(marker.read_text()))
    assert reader.pid is None


@pytest.mark.parametrize("result", [
    {"ok": False}, {"ok": True, "count": True, "size": 0},
    {"ok": True, "count": 8, "size": 0},
    {"ok": True, "count": 0, "size": state.MAX_STAGING_BYTES},
    {"ok": True, "count": 0, "size": 0, "extra": 1},
])
def test_invalid_response_refused(result):
    with pytest.raises(RuntimeError):
        inspection._state_quota_value(result)
