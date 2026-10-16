"""Resource leases keep parent-owned locks after bounded helper preparation."""
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_invocation_lock as locks, plugin_resources as resources, plugin_resource_io as resource_io
from test_plugin_resource_inspection import record_file
from test_plugin_resource_journal import Daemon
from worker_test_markers import publish_marker


def blocked_resource_lock(connection, source, *args):
    original = os.open
    def opened(path, *values, **kwargs):
        target = Path(path)
        index = target.name == '.index.lock'
        wanted = os.environ['OPSKIT_TEST_RESOURCE_LOCK_STAGE'] == 'index'
        if target.suffix == '.lock' and index == wanted:
            read, write = os.pipe()
            publish_marker(os.environ['OPSKIT_TEST_RESOURCE_LOCK_PID'], str(os.getpid()))
            try:
                os.read(read, 1)
            finally:
                os.close(read)
                os.close(write)
        return original(path, *values, **kwargs)
    os.open = opened
    target = resource_io._create_worker if os.environ["OPSKIT_TEST_RESOURCE_LOCK_STAGE"] == "index" else locks._lock_worker
    target(connection, source, *args)


@pytest.mark.parametrize('stage', ['lease', 'index', 'reconcile'])
@pytest.mark.parametrize('cancel', [False, True])
def test_blocked_resource_lock_reaped_without_launch(tmp_path, monkeypatch, expire_worker_after_marker, stage, cancel):
    from network import worker
    monkeypatch.setenv('OPSKIT_DATA_DIR', str(tmp_path))
    marker = tmp_path / 'worker.pid'
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_LOCK_STAGE', stage)
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_LOCK_PID', str(marker))
    original = locks._lock_worker
    if stage == 'index':
        monkeypatch.setattr(resource_io, '_create_worker', blocked_resource_lock)
    else:
        monkeypatch.setattr(locks, '_lock_worker', blocked_resource_lock)
    daemon = Daemon()
    if stage == 'reconcile':
        record_file(resources._root())
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    if stage == 'reconcile' and not cancel:
        assert resources.reconcile(['docker'], daemon) == {'recovered': 0, 'active': 0, 'pending': 1, 'invalid': 0}
    else:
        with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
            if stage == 'reconcile':
                resources.reconcile(['docker'], daemon)
            else:
                resources.ResourceLease(['docker'], 'container', 'opskit-' + 'a' * 32, daemon)
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert all(call[0] == 'info' for call in daemon.calls)
    assert len(list(resources._root().glob('*.json'))) == (1 if stage == 'reconcile' else 0)
    monkeypatch.setattr(locks, '_lock_worker', original)
    monkeypatch.setattr(worker, 'time', time)
    # Failure at either lock must release any earlier token lease.
    for path in resources._root().glob('*.lock'):
        with locks.hold(path, timeout=30):
            pass


def test_resource_lock_files_are_not_opened_by_parent(tmp_path, monkeypatch):
    monkeypatch.setenv('OPSKIT_DATA_DIR', str(tmp_path))
    original = os.open
    def opened(path, *args, **kwargs):
        if Path(path).suffix == '.lock':
            pytest.fail('caller opened resource lock file')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, 'open', opened)
    daemon = Daemon()
    lease = resources.ResourceLease(['docker'], 'container', 'opskit-' + 'a' * 32, daemon)
    try:
        # An independent opener observes the real parent-owned lock, despite
        # the opening helper already having exited.
        from core.resource_lock import ResourceLock, ResourceBusyError
        monkeypatch.setattr(os, 'open', original)
        with pytest.raises(ResourceBusyError):
            with ResourceLock(lambda: lease.path.with_suffix('.lock'), label='legacy observer').hold():
                pytest.fail('parent lease lost mutual exclusion')
        monkeypatch.setattr(os, 'open', opened)
        assert resources.reconcile(['docker'], daemon)['active'] == 1
    finally:
        lease.close(daemon)


def test_relative_data_root_still_supported(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('OPSKIT_DATA_DIR', 'relative-data')
    daemon = Daemon()
    lease = resources.ResourceLease(['docker'], 'container', 'opskit-' + 'a' * 32, daemon)
    assert lease.path.is_absolute()
    lease.close(daemon)
