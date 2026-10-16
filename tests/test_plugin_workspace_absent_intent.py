"""Only locked, still-absent attempts may retire without directory ownership."""
import json
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_resource_io as io, plugin_resources as resources
from core.paths import data_dir
from core.plugin_state import _directory
from core.plugin_state_budget import platform_state_guard
from core.resource_lock import ResourceLock
from test_plugin_resource_journal import Daemon


def attempt():
    daemon = Daemon()
    lease = resources.ResourceLease(['docker'], 'container', 'opskit-' + 'a' * 32, daemon)
    parent = data_dir() / 'plugin-runtimes/workspaces'
    parent.mkdir(parents=True, exist_ok=True)
    record = io.record_workspace_intent(lease.record, 'opskit-sandbox-' + 'b' * 32,
                                        _directory(parent))
    return daemon, lease, record, parent / record['workspace_intent']['name']


def test_close_retires_attempt_only_after_locked_absence_confirmation():
    daemon, lease, _, root = attempt()
    assert resources.reconcile(['docker'], daemon)['active'] == 1
    lease.close(daemon)
    assert not root.exists() and not lease.path.exists()
    assert resources.reconcile(['docker'], daemon) == {
        'recovered': 0, 'active': 0, 'pending': 0, 'invalid': 0}


def test_restart_reconciles_absent_attempt():
    daemon, lease, _, root = attempt()
    lease.lock.__exit__(None, None, None)
    lease.closed = True
    assert resources.reconcile(['docker'], daemon) == {
        'recovered': 1, 'active': 0, 'pending': 0, 'invalid': 0}
    assert not root.exists() and not lease.path.exists()


@pytest.mark.parametrize('kind', ['directory', 'file'])
def test_existing_unbound_object_is_never_removed(kind):
    daemon, lease, _, root = attempt()
    if kind == 'directory':
        root.mkdir()
        (root / 'foreign').write_text('retain')
    else:
        root.write_text('retain')
    before = lease.path.read_bytes()
    with pytest.raises(RuntimeError):
        lease.close(daemon)
    assert lease.path.read_bytes() == before
    assert (root / 'foreign' if kind == 'directory' else root).read_text() == 'retain'
    assert resources.reconcile(['docker'], daemon)['pending'] == 1


@pytest.mark.parametrize('lock', ['allocation', 'index'])
def test_busy_admission_or_index_retains_record_then_retries(lock):
    daemon, lease, _, root = attempt()
    before = lease.path.read_bytes()
    guard = platform_state_guard() if lock == 'allocation' else ResourceLock(
        lambda: lease.path.parent / '.index.lock', label='test resource index').hold()
    with guard:
        with pytest.raises(RuntimeError):
            lease.close(daemon)
        assert lease.path.read_bytes() == before and not root.exists()
    assert resources.reconcile(['docker'], daemon)['recovered'] == 1


def test_changed_parent_identity_retains_absent_attempt():
    daemon, lease, record, root = attempt()
    record['workspace_intent']['parent'][1] += 1
    lease.path.write_text(json.dumps(record))
    before = lease.path.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        lease.close(daemon)
    assert lease.path.read_bytes() == before and not root.exists()


def test_plain_record_removal_still_cannot_bypass_absence_protocol():
    daemon, lease, record, _ = attempt()
    before = lease.path.read_bytes()
    try:
        with pytest.raises(ValueError):
            io.remove_record(lease.path, record, timeout=30)
        assert lease.path.read_bytes() == before
    finally:
        lease.lock.__exit__(None, None, None)
        lease.closed = True


def object_appears_worker(connection, *arguments):
    from core.plugin_workspace import recorded_workspace_intent
    original = resources._read
    def read(path):
        record = original(path)
        root, _ = recorded_workspace_intent(record['workspace_intent'])
        root.mkdir()
        (root / 'foreign').write_text('retain raced object')
        resources._read = original
        return record
    resources._read = read
    io._retire_absent_intent_worker(connection, *arguments)


def test_object_appearing_after_initial_check_keeps_record(monkeypatch):
    daemon, lease, _, root = attempt()
    before = lease.path.read_bytes()
    monkeypatch.setattr(io, '_retire_absent_intent_worker', object_appears_worker)
    with pytest.raises(RuntimeError, match='evidence retained'):
        lease.close(daemon)
    assert lease.path.read_bytes() == before
    assert (root / 'foreign').read_text() == 'retain raced object'


def inaccessible_absence_worker(connection, *arguments):
    original = io._present
    def present(path):
        if path.name.startswith('opskit-sandbox-'):
            raise PermissionError('injected workspace metadata denial')
        return original(path)
    io._present = present
    io._retire_absent_intent_worker(connection, *arguments)


def test_permission_error_is_not_absence(monkeypatch):
    daemon, lease, _, root = attempt()
    before = lease.path.read_bytes()
    monkeypatch.setattr(io, '_retire_absent_intent_worker', inaccessible_absence_worker)
    with pytest.raises(OSError):
        lease.close(daemon)
    assert lease.path.read_bytes() == before and not root.exists()


def blocked_absence_worker(connection, *arguments):
    from test_plugin_workspace import block
    io._confirm_absent_workspace = lambda record: block()
    io._retire_absent_intent_worker(connection, *arguments)


@pytest.mark.parametrize('cancel', [False, True])
def test_blocked_absence_is_reaped_and_both_locks_release(
        tmp_path, monkeypatch, expire_worker_after_marker, cancel):
    from network import worker
    daemon, lease, _, root = attempt()
    before = lease.path.read_bytes()
    marker = tmp_path / 'absence-worker.pid'
    monkeypatch.setenv('OPSKIT_TEST_WORKSPACE_PID', str(marker))
    with monkeypatch.context() as patch:
        patch.setattr(io, '_retire_absent_intent_worker', blocked_absence_worker)
        if cancel:
            def clock():
                if marker.exists():
                    raise KeyboardInterrupt
                return time.monotonic()
            patch.setattr(worker, 'time', SimpleNamespace(monotonic=clock))
        else:
            expire_worker_after_marker(marker)
        with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
            lease.close(daemon)
    monkeypatch.setattr(worker, 'time', time)
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert lease.path.read_bytes() == before and not root.exists()
    assert resources.reconcile(['docker'], daemon)['recovered'] == 1
