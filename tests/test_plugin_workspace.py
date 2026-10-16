"""Workspace I/O cannot block supervision or delete replacement/unowned data."""
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_workspace as workspace
from worker_test_markers import publish_marker


def blocked_allocate(connection, request, *arguments):
    original = Path.mkdir
    def mkdir(path, *args, **kwargs):
        if path.name.startswith("opskit-sandbox-"):
            block()
        return original(path, *args, **kwargs)
    Path.mkdir = mkdir
    workspace._allocate_worker(connection, request, *arguments)


def block():
    read, write = os.pipe()
    publish_marker(os.environ["OPSKIT_TEST_WORKSPACE_PID"], str(os.getpid()))
    try:
        os.read(read, 1)
    finally:
        os.close(read)
        os.close(write)


def blocked_cleanup(connection, *args):
    original = Path.unlink
    def unlink(path, *positional, **keywords):
        if path.name == "value":
            block()
        return original(path, *positional, **keywords)
    Path.unlink = unlink
    workspace._cleanup_worker(connection, *args)


def inaccessible_cleanup(connection, root, *args):
    def deny_target(original):
        def denied(path, *positional, **keywords):
            if os.fspath(path) == root:
                raise PermissionError("owned workspace metadata unavailable")
            return original(path, *positional, **keywords)
        return denied
    # lexists uses lstat; pathlib.lstat uses stat(follow_symlinks=False).
    # Inject the same filesystem failure for both implementations.
    os.lstat = deny_target(os.lstat)
    os.stat = deny_target(os.stat)
    # Cover the cached pathlib accessors used by Python 3.10 as well.
    Path.lstat = deny_target(Path.lstat)
    Path.stat = deny_target(Path.stat)
    workspace._cleanup_worker(connection, root, *args)


def test_permission_error_does_not_report_cleanup_success(monkeypatch):
    lease = workspace.allocate(timeout=30)
    (lease.root / "evidence").write_bytes(b"keep")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(workspace, "_cleanup_worker", inaccessible_cleanup)
            with pytest.raises(RuntimeError, match="cleanup unconfirmed"):
                workspace.cleanup(lease, timeout=30)
        assert (lease.root / "evidence").read_bytes() == b"keep"
    finally:
        workspace.cleanup(lease, timeout=30)


def test_creation_and_removal_do_not_touch_parent_filesystem(tmp_path, monkeypatch):
    for variable in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(variable, str(tmp_path.resolve()))
    original = Path.mkdir
    def forbidden(path, *args, **kwargs):
        if path.name.startswith("opskit-sandbox-"):
            pytest.fail("workspace created in parent")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    lease = workspace.allocate(timeout=30)
    assert lease.root.parent == tmp_path.resolve() / "plugin-runtimes/workspaces"
    (lease.root / "value").write_bytes(b"owned")
    monkeypatch.setattr(Path, "unlink", lambda *args, **kwargs: pytest.fail("workspace deleted in parent"))
    workspace.cleanup(lease, timeout=30)
    assert not lease.root.exists()


@pytest.mark.parametrize("operation", ["allocate", "cleanup"])
@pytest.mark.parametrize("cancel", [False, True])
def test_blocked_io_is_reaped(tmp_path, monkeypatch, expire_worker_after_marker, operation, cancel):
    from network import worker
    marker = tmp_path / "pid"
    monkeypatch.setenv("OPSKIT_TEST_WORKSPACE_PID", str(marker))
    lease = workspace.allocate(timeout=30) if operation == "cleanup" else None
    if lease:
        (lease.root / "value").write_bytes(b"preserved")
    with monkeypatch.context() as patch:
        patch.setattr(workspace, "_allocate_worker" if operation == "allocate" else "_cleanup_worker",
                      blocked_allocate if operation == "allocate" else blocked_cleanup)
        if cancel:
            def clock():
                if marker.exists():
                    raise KeyboardInterrupt
                return time.monotonic()
            patch.setattr(worker, "time", SimpleNamespace(monotonic=clock))
        else:
            expire_worker_after_marker(marker)
        with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
            if lease:
                workspace.cleanup(lease, timeout=30)
            else:
                workspace.allocate(timeout=30)
    monkeypatch.setattr(worker, "time", time)
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    if lease:
        assert (lease.root / "value").read_bytes() == b"preserved"
        workspace.cleanup(lease, timeout=30)
        assert not lease.root.exists()


@pytest.mark.parametrize("change", ["identity", "parent", "hardlink", "symlink"])
def test_unowned_content_is_retained(tmp_path, change):
    lease = workspace.allocate(timeout=30)
    value = lease.root / "value"
    value.write_bytes(b"preserved")
    owned = lease
    try:
        if change == "identity":
            lease = workspace.Workspace(lease.root, lease.parent_identity, [0, 1])
        elif change == "parent":
            lease = workspace.Workspace(lease.root, [0, 1], lease.identity)
        elif change == "hardlink":
            os.link(value, lease.root / "other")
        else:
            try:
                (lease.root / "other").symlink_to(value)
            except OSError:
                pytest.skip("symlinks unavailable")
        with pytest.raises(RuntimeError, match="cleanup unconfirmed"):
            workspace.cleanup(lease, timeout=30)
        assert value.read_bytes() == b"preserved"
    finally:
        other = owned.root / "other"
        if os.path.lexists(other):
            other.unlink()
        workspace.cleanup(owned, timeout=30)


@pytest.mark.parametrize("primary", [ValueError("business error"), KeyboardInterrupt()])
def test_cleanup_failure_preserves_primary_error(monkeypatch, primary):
    lease = workspace.allocate(timeout=30)
    original = workspace.cleanup
    def failed(*args, **kwargs):
        raise TimeoutError("blocked cleanup")
    monkeypatch.setattr(workspace, "cleanup", failed)
    monkeypatch.setattr(workspace, "allocate", lambda **kwargs: lease)
    try:
        with pytest.raises(type(primary)) as caught:
            with workspace.owned_workspace(timeout=30, cleanup_timeout=30):
                raise primary
        assert caught.value is primary
        assert any("cleanup incomplete" in note for note in primary.__notes__)
    finally:
        original(lease, timeout=30)


@pytest.mark.parametrize("kind", [ValueError, KeyboardInterrupt])
def test_legacy_exception_notes_preserve_primary_metadata(kind):
    from core.cleanup import add_exception_note
    legacy = type("LegacyError", (kind,), {"add_note": None})
    primary = legacy("original business failure")
    cause = OSError("original cause")
    primary.__cause__ = cause
    primary.__notes__ = ["earlier evidence"]
    add_exception_note(primary, "cleanup incomplete; evidence retained")
    assert type(primary) is legacy
    assert primary.args == ("original business failure",)
    assert primary.__cause__ is cause
    assert primary.__notes__ == ["earlier evidence", "cleanup incomplete; evidence retained"]


@pytest.mark.parametrize("result", [{}, {"ok": False}, {"ok": True, "root": "/"}])
def test_invalid_allocation_never_triggers_name_based_cleanup(monkeypatch, result):
    from network import worker
    monkeypatch.setattr(worker, "run", lambda *args, **kwargs: result)
    with pytest.raises(RuntimeError, match="allocation unconfirmed"):
        workspace.allocate(timeout=30)


def test_workspace_cleanup_allows_snapshot_larger_than_persistent_state():
    lease = workspace.allocate(timeout=30)
    with (lease.root / "large").open("wb") as stream:
        stream.truncate(20 * 1024**2)
    workspace.cleanup(lease, timeout=30)
    assert not lease.root.exists()


@pytest.mark.parametrize("change", ["request", "root", "extra", "boolean"])
def test_cleanup_response_must_bind_request_and_root(monkeypatch, change):
    from network import worker
    lease = workspace.allocate(timeout=30)
    original = worker.run
    def response(target, arguments, **kwargs):
        result = {"ok": True, "request": arguments[-1], "root": arguments[0]}
        if change == "extra":
            result["extra"] = 1
        elif change == "boolean":
            result["ok"] = 1
        else:
            result[change] = "unbound"
        return result
    monkeypatch.setattr(worker, "run", response)
    try:
        with pytest.raises(RuntimeError, match="cleanup unconfirmed"):
            workspace.cleanup(lease, timeout=30)
        assert lease.root.is_dir()
    finally:
        monkeypatch.setattr(worker, "run", original)
        workspace.cleanup(lease, timeout=30)
