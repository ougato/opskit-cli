"""Content inspection must reclaim actual blocked I/O without granting authority."""
import os
import json
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_inspection as inspection
from core.plugin_integrity import write_checksums
from core.plugin_trust import compute_fingerprint


def _blocked_inspection(connection, source, verify):
    read, write = os.pipe()
    Path(source).write_text(str(os.getpid()))
    try:
        os.read(read, 1)
    finally:
        os.close(read)
        os.close(write)
        connection.close()


def _blocked_session(connection, buffer, length, requested):
    requested.wait()
    request = json.loads(bytes(memoryview(buffer).cast("B")[:length.value]))
    _blocked_inspection(connection, request["source"], request["verify"])


def test_supervised_content_matches_normalized_fingerprint(tmp_path):
    (tmp_path / "plugin.yaml").write_text("name: demo\nversion: 1\n")
    (tmp_path / "code.py").write_bytes(b"print('ok')\r\n")
    expected = compute_fingerprint(tmp_path)
    assert inspection.inspect_content(tmp_path, verify_checksums=False, timeout=30) == expected
    (tmp_path / "code.py").write_bytes(b"print('ok')\n")
    assert inspection.inspect_content(tmp_path, verify_checksums=True, timeout=30) == expected


def test_checksum_mismatch_cannot_return_authorization_fingerprint(tmp_path):
    (tmp_path / "plugin.yaml").write_text("name: demo\nversion: 1\n")
    (tmp_path / "code.py").write_text("original")
    write_checksums(tmp_path)
    assert inspection.inspect_content(tmp_path, verify_checksums=True, timeout=30) == compute_fingerprint(tmp_path)
    (tmp_path / "code.py").write_text("changed")
    with pytest.raises(RuntimeError, match="plugin content inspection failed"):
        inspection.inspect_content(tmp_path, verify_checksums=True, timeout=30)


def test_deadline_reclaims_worker_blocked_in_actual_read(tmp_path, monkeypatch, expire_worker_after_marker):
    marker = tmp_path / "pid"
    monkeypatch.setattr(inspection, "_inspect_worker", _blocked_inspection)
    timeout, observed = expire_worker_after_marker(marker)
    with pytest.raises(TimeoutError):
        inspection.inspect_content(marker, verify_checksums=True, timeout=timeout)
    assert observed
    assert not psutil.pid_exists(int(marker.read_text()))


def test_cancellation_reclaims_actual_blocked_worker(tmp_path, monkeypatch):
    from network import worker
    marker = tmp_path / "pid"
    monkeypatch.setattr(inspection, "_inspect_worker", _blocked_inspection)
    def cancelled_clock():
        if marker.exists():
            raise KeyboardInterrupt
        return time.monotonic()
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=cancelled_clock))
    with pytest.raises(KeyboardInterrupt):
        inspection.inspect_content(marker, verify_checksums=True, timeout=30)
    assert not psutil.pid_exists(int(marker.read_text()))


@pytest.mark.parametrize("response", [
    {}, {"ok": 1, "fingerprint": "a" * 64},
    {"ok": True, "fingerprint": "not-a-digest"},
    {"ok": True, "fingerprint": "a" * 64, "authority": True},
])
def test_malformed_worker_result_cannot_become_content_identity(tmp_path, monkeypatch, response):
    from network import worker
    monkeypatch.setattr(worker, "run", lambda *args, **kwargs: response)
    with pytest.raises(RuntimeError, match="plugin content inspection failed"):
        inspection.inspect_content(tmp_path, verify_checksums=True, timeout=30)


def test_error_does_not_echo_file_contents_or_path(tmp_path):
    source = tmp_path / "synthetic-private-marker"
    source.write_text("not a plugin directory")
    with pytest.raises(RuntimeError) as caught:
        inspection.inspect_content(source, verify_checksums=True, timeout=30)
    assert str(caught.value) == "plugin content inspection failed"


def test_sandbox_inspection_uses_parent_budget_and_checks_return_deadline(tmp_path, monkeypatch):
    from core import plugin_sandbox as sandbox
    clock = [10.0]
    observed = []
    monkeypatch.setattr(sandbox, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(sandbox._invocations, "deadline", 12.0, raising=False)
    def inspect(root, *, verify_checksums, timeout):
        observed.append((verify_checksums, timeout))
        clock[0] = 12.0
        return "a" * 64
    monkeypatch.setattr(inspection, "inspect_content", inspect)
    with pytest.raises(sandbox.SandboxError, match="execution deadline"):
        sandbox._inspect_content(tmp_path, verify=True)
    assert observed == [(True, 2.0)]


def test_expired_parent_does_not_start_content_worker(tmp_path, monkeypatch):
    from core import plugin_sandbox as sandbox
    monkeypatch.setattr(sandbox, "time", SimpleNamespace(monotonic=lambda: 12.0))
    monkeypatch.setattr(sandbox._invocations, "deadline", 12.0, raising=False)
    monkeypatch.setattr(inspection, "inspect_content", lambda *args, **kwargs: pytest.fail("started after expiry"))
    with pytest.raises(sandbox.SandboxError, match="execution deadline"):
        sandbox.compute_fingerprint(tmp_path)


def test_session_reuses_pid_but_rereads_changed_content(tmp_path):
    source = tmp_path / "code.py"
    source.write_text("original")
    reader = inspection.ContentInspector()
    try:
        first = reader.inspect(tmp_path, verify_checksums=True, timeout=30)
        pid = reader.pid
        source.write_text("changed")
        second = reader.inspect(tmp_path, verify_checksums=True, timeout=30)
        assert reader.pid == pid
        assert first != second == compute_fingerprint(tmp_path)
    finally:
        reader.close()
    assert not psutil.pid_exists(pid)
    with pytest.raises(RuntimeError, match="closed"):
        reader.inspect(tmp_path, verify_checksums=False, timeout=30)


@pytest.mark.parametrize("cancel", [False, True])
def test_session_reclaims_actual_blocked_read(tmp_path, monkeypatch, expire_worker_after_marker, cancel):
    from network import worker
    marker = tmp_path / "pid"
    monkeypatch.setattr(inspection, "_inspection_session", _blocked_session)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    reader = inspection.ContentInspector()
    try:
        with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
            reader.inspect(marker, verify_checksums=False, timeout=30)
        assert reader.pid is None
        assert not psutil.pid_exists(int(marker.read_text()))
        with pytest.raises(RuntimeError, match="closed"):
            reader.inspect(marker, verify_checksums=False, timeout=30)
    finally:
        reader.close()


def test_nested_budgets_share_reader_and_outer_exit_reclaims_it(tmp_path):
    from core import plugin_sandbox as sandbox
    (tmp_path / "code.py").write_text("content")
    with sandbox._execution_budget():
        sandbox.compute_fingerprint(tmp_path)
        first = sandbox._invocations.inspector
        pid = first.pid
        with sandbox._execution_budget():
            sandbox.compute_fingerprint(tmp_path)
            assert sandbox._invocations.inspector is first
            assert first.pid == pid
        assert psutil.pid_exists(pid)
    assert sandbox._invocations.inspector is None
    assert not psutil.pid_exists(pid)
    with sandbox._execution_budget():
        sandbox.compute_fingerprint(tmp_path)
        assert sandbox._invocations.inspector is not first


def test_invalid_session_request_cannot_start_worker(tmp_path):
    reader = inspection.ContentInspector()
    try:
        with pytest.raises(ValueError, match="path"):
            reader.inspect("x" * 65537, verify_checksums=False, timeout=30)
        assert reader.pid is None
    finally:
        reader.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job assignment gate")
def test_session_job_refusal_reclaims_without_entering_request_loop(tmp_path, monkeypatch):
    import multiprocessing
    from core import windows_job
    marker = tmp_path / "pid"
    monkeypatch.setattr(inspection, "_inspection_session", _blocked_session)
    before = {child.pid for child in multiprocessing.active_children()}
    def refuse(handle):
        raise OSError("synthetic assignment refusal")
    monkeypatch.setattr(windows_job, "attach_running", refuse)
    reader = inspection.ContentInspector()
    with pytest.raises(OSError, match="assignment refusal"):
        reader.inspect(marker, verify_checksums=False, timeout=30)
    assert reader.pid is None
    assert not marker.exists()
    assert {child.pid for child in multiprocessing.active_children()} == before
