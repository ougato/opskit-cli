"""Host Path conversion is fresh, partition-bound, and interruptible."""
from contextlib import closing
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_inspection as inspection, plugin_sandbox as sandbox
from worker_test_markers import publish_marker


def blocked_path_session(connection, *arguments):
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        if path.name == "slow":
            read, write = os.pipe()
            publish_marker(os.environ["OPSKIT_TEST_STATE_PATH_PID"], str(os.getpid()))
            try:
                os.read(read, 1)
            finally:
                os.close(read)
                os.close(write)
        return original(path, *args, **kwargs)
    Path.resolve = resolve
    inspection._inspection_session(connection, *arguments)


def test_nested_paths_encode_without_parent_resolution(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        if path == root or path.is_relative_to(root):
            pytest.fail("host state path resolved in parent")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve)
    value = {"files": [root / "a", (root / "directory" / "中文",)]}
    assert sandbox._encode(value, root) == {"files": [
        {"__opskit_path__": "/data/a"}, [{"__opskit_path__": "/data/directory/中文"}],
    ]}


def test_scoped_paths_reuse_existing_inspector(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    with closing(inspection.ContentInspector()) as reader:
        reader.state_path(root, root / "first", timeout=30)
        pid = reader.pid
        monkeypatch.setattr(sandbox._invocations, "inspection_scope", True, raising=False)
        monkeypatch.setattr(sandbox._invocations, "inspector", reader, raising=False)
        assert sandbox._encode(root / "second", root) == {"__opskit_path__": "/data/second"}
        assert reader.pid == pid
    assert not psutil.pid_exists(pid)


@pytest.mark.parametrize("cancel", [False, True])
def test_blocked_path_resolution_reaped(tmp_path, monkeypatch, expire_worker_after_marker, cancel):
    from network import worker
    root = tmp_path.resolve()
    marker = root / "pid"
    monkeypatch.setenv("OPSKIT_TEST_STATE_PATH_PID", str(marker))
    monkeypatch.setattr(inspection, "_inspection_session", blocked_path_session)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    with pytest.raises(KeyboardInterrupt if cancel else sandbox.SandboxError,
                       match=None if cancel else "deadline resolving file path"):
        sandbox._encode(root / "slow", root)
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))


def test_outside_partition_is_rejected_without_exposing_path(tmp_path):
    root = tmp_path.resolve() / "state"
    with pytest.raises(sandbox.SandboxError, match="outside the authorized plugin state") as caught:
        sandbox._encode(root.parent / "private-secret", root)
    assert "private-secret" not in str(caught.value)


def test_symlink_target_is_checked_fresh(tmp_path):
    root = tmp_path.resolve() / "state"
    root.mkdir()
    child = root / "inside"
    child.mkdir()
    link = root / "link"
    try:
        link.symlink_to(child, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    with closing(inspection.ContentInspector()) as reader:
        assert reader.state_path(root, link / "value", timeout=30) == "/data/inside/value"
        link.unlink()
        link.symlink_to(tmp_path.resolve(), target_is_directory=True)
        with pytest.raises(inspection.StatePathError):
            reader.state_path(root, link / "value", timeout=30)


@pytest.mark.parametrize("path", ["/data/../etc/passwd", "/data/a/../../secret"])
def test_container_path_traversal_rejected_before_inspection(tmp_path, monkeypatch, path):
    monkeypatch.setattr(sandbox, "_encode_host_path", lambda *args: pytest.fail("container path performed host I/O"))
    with pytest.raises(sandbox.SandboxError, match="outside the authorized plugin state"):
        sandbox._encode(Path(path), tmp_path)


@pytest.mark.parametrize("response", [
    {}, {"ok": False}, {"ok": 1, "path": "/data/value"},
    {"ok": True, "path": "/etc/passwd"}, {"ok": True, "path": "/data/../secret"},
    {"ok": True, "path": "/data/value", "extra": 1}, {"ok": True, "path": "/data/\0"},
])
def test_malformed_result_never_becomes_file_capability(response):
    with pytest.raises(RuntimeError):
        inspection._state_path_value(response)


def test_mount_path_resolution_stays_in_reader(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        if path == root or path.is_relative_to(root):
            pytest.fail("mount path resolved in parent")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve)
    with closing(inspection.ContentInspector()) as reader:
        monkeypatch.setattr(sandbox._invocations, "inspection_scope", True, raising=False)
        monkeypatch.setattr(sandbox._invocations, "inspector", reader, raising=False)
        assert sandbox.mount_path(root / "a" / ".." / "中文", ["docker"]) == str(root / "中文")
        pid = reader.pid
        assert sandbox.mount_path(root / "second", ["docker"]) == str(root / "second")
        assert reader.pid == pid
    assert not psutil.pid_exists(pid)


@pytest.mark.parametrize("cancel", [False, True])
def test_blocked_mount_resolution_reaped(tmp_path, monkeypatch, expire_worker_after_marker, cancel):
    from network import worker
    root = tmp_path.resolve()
    marker = root / "pid"
    monkeypatch.setenv("OPSKIT_TEST_STATE_PATH_PID", str(marker))
    monkeypatch.setattr(inspection, "_inspection_session", blocked_path_session)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    with pytest.raises(KeyboardInterrupt if cancel else sandbox.SandboxError,
                       match=None if cancel else "deadline resolving mount path"):
        sandbox.mount_path(root / "slow", ["docker"])
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))


@pytest.mark.parametrize("response", [
    {}, {"ok": False}, {"ok": 1, "path": "/absolute"},
    {"ok": True, "path": "relative"}, {"ok": True, "path": "/data/../secret"},
    {"ok": True, "path": "/absolute", "extra": 1}, {"ok": True, "path": "/data/\0"},
])
def test_malformed_mount_resolution_rejected(response):
    with pytest.raises(RuntimeError):
        inspection._mount_path_value(response)


@pytest.mark.parametrize("separator", [",", "\n", "\r"])
def test_mount_syntax_rejected_after_resolution(tmp_path, separator):
    with pytest.raises(sandbox.SandboxError, match="unsupported mount path"):
        sandbox.mount_path(tmp_path.resolve() / ("unsafe" + separator + "name"), ["docker"])
