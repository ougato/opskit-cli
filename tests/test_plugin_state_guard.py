"""Independent processes must share the same permanent transaction guard."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from core import plugin_sandbox as sandbox, plugin_state as state
from core.resource_lock import ResourceBusyError


@contextmanager
def held_by_child(root):
    marker = root.parent / "guard-ready"
    release = root.parent / "guard-release"
    script = '''
from pathlib import Path
import sys,time
from core.plugin_state import transaction_guard
root,marker,release=map(Path,sys.argv[1:])
with transaction_guard(root):
    marker.write_text('ready')
    until=time.monotonic()+30
    while not release.exists():
        if time.monotonic()>=until: raise TimeoutError('guard owner not released')
        time.sleep(.01)
'''
    child = subprocess.Popen([sys.executable, "-c", script, str(root), str(marker), str(release)],
                             cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        until = time.monotonic() + 20
        while not marker.exists():
            assert child.poll() is None, child.communicate()[1]
            assert time.monotonic() < until, "child did not acquire guard"
            time.sleep(.01)
        yield child
    finally:
        release.write_text("release")
        try:
            _, stderr = child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=5)
            raise
        assert child.returncode == 0, stderr


def test_same_target_refuses_while_distinct_target_is_independent(tmp_path):
    root = tmp_path.resolve() / "self"
    with held_by_child(root):
        with pytest.raises(ResourceBusyError):
            with state.transaction_guard(root):
                pytest.fail("same target acquired")
        with state.transaction_guard(tmp_path.resolve() / "other"):
            pass
    lock = state.transaction_lock_path(root)
    identity = lock.stat().st_ino
    with state.transaction_guard(root):
        assert lock.stat().st_ino == identity
    assert lock.is_file() and lock.stat().st_ino == identity


def test_no_record_recovery_still_refuses_active_writer(tmp_path):
    root = tmp_path.resolve() / "self"
    with held_by_child(root):
        with pytest.raises(sandbox.SandboxError, match="transaction is busy"):
            sandbox._recover_state(root, plugin="owner", caller="platform")
    sandbox._recover_state(root, plugin="owner", caller="platform")


def test_publish_refuses_before_creating_journal_or_renaming(tmp_path):
    root = tmp_path.resolve() / "self"
    root.mkdir()
    (root / "value").write_bytes(b"old")
    staging = tmp_path / ".state-owned" / "new"
    staging.mkdir(parents=True)
    (staging / "value").write_bytes(b"new")
    from core.config import get_config_path
    from core.plugin_trust import grant, _trust_file
    from core.plugin_state_io import OwnedStaging
    from core.plugin_state_publish import publish_supervised
    grant("owner", "a" * 64, "1")
    lease = OwnedStaging(root, staging.parent, state._directory(root.parent), state._directory(staging.parent))
    requirements = {"operation": "authorize", "config": str(get_config_path()), "trust": str(_trust_file()),
                    "identities": (("owner", "a" * 64),), "edges": ()}
    with held_by_child(root):
        with pytest.raises(sandbox.SandboxError, match="state not committed"):
            publish_supervised(lease, state._directory(staging), plugin="owner", caller="platform",
                               requirements=requirements, timeout=30)
    assert (root / "value").read_bytes() == b"old"
    assert (staging / "value").read_bytes() == b"new"
    assert not state.record_path(root).exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory"])
def test_unsafe_guard_file_refused(tmp_path, kind):
    root = tmp_path.resolve() / "self"
    lock = state.transaction_lock_path(root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "target"
    target.write_bytes(b"keep")
    if kind == "symlink":
        try:
            lock.symlink_to(target)
        except OSError:
            pytest.skip("symlink creation unavailable")
    elif kind == "hardlink":
        os.link(target, lock)
    else:
        lock.mkdir()
    with pytest.raises((RuntimeError, OSError)):
        with state.transaction_guard(root):
            pytest.fail("unsafe guard admitted")
    assert target.read_bytes() == b"keep"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits replacement of an open locked inode")
def test_lock_replacement_during_acquisition_is_refused(tmp_path, monkeypatch):
    import fcntl
    original = fcntl.flock
    root = tmp_path.resolve() / "self"
    lock = state.transaction_lock_path(root)
    def changed(fd, operation):
        original(fd, operation)
        if operation == fcntl.LOCK_EX | fcntl.LOCK_NB:
            lock.unlink()
            lock.write_bytes(b"replacement")
    monkeypatch.setattr(fcntl, "flock", changed)
    with pytest.raises(RuntimeError, match="identity changed"):
        with state.transaction_guard(root):
            pytest.fail("replaced lock admitted")
    assert lock.read_bytes() == b"replacement"
