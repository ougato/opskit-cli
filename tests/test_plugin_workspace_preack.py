"""Allocation failures after journal binding remain recoverable without an ACK."""
import errno
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_resources as resources, plugin_workspace as workspace
from test_plugin_resource_journal import Daemon
from test_plugin_workspace import block


def failed_request_worker(connection, request, *arguments):
    original = os.open
    def open_file(path, *args, **kwargs):
        if Path(path).name == 'request.json':
            raise OSError(errno.ENOSPC, 'injected request disk full')
        return original(path, *args, **kwargs)
    os.open = open_file
    workspace._allocate_worker(connection, request, *arguments)


def unacknowledged_worker(connection, request, *arguments):
    from network import worker
    original = worker.send
    def send(channel, message):
        if message.get('ok') is True and 'identity' in message and 'root' in message:
            block()
        return original(channel, message)
    worker.send = send
    workspace._allocate_worker(connection, request, *arguments)


def unconfirmed_binding_worker(connection, request, *arguments):
    from core import secrets
    original = secrets.atomic_write
    def write(path, content):
        original(path, content)
        if json.loads(content)['schema'] == 2:
            raise OSError('binding written but confirmation lost')
    secrets.atomic_write = write
    workspace._allocate_worker(connection, request, *arguments)


def interrupted_after_mkdir_worker(connection, request, *arguments):
    original = Path.mkdir
    def mkdir(path, *args, **kwargs):
        if (path.name == 'opskit-sandbox-' + request
                and os.environ['OPSKIT_TEST_PREIDENTITY_STAGE'] == 'before'):
            block()
        result = original(path, *args, **kwargs)
        if path.name == 'opskit-sandbox-' + request:
            # Creation succeeded, but no inode identity or journal was recorded.
            (path / 'unbound-evidence').write_bytes(b'preserve unknown ownership')
            block()
        return result
    Path.mkdir = mkdir
    workspace._allocate_worker(connection, request, *arguments)


@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('stage', ['before', 'after'])
def test_preidentity_interruption_retains_unowned_directory(
        tmp_path, monkeypatch, expire_worker_after_marker, cancel, stage):
    """Absent attempts retire under admission locks; existing objects stay pending."""
    from network import worker
    daemon = Daemon()
    lease = resources.ResourceLease(['docker'], 'container', 'opskit-' + 'a' * 32, daemon)
    marker = tmp_path / 'preidentity.pid'
    monkeypatch.setenv('OPSKIT_TEST_WORKSPACE_PID', str(marker))
    monkeypatch.setenv('OPSKIT_TEST_PREIDENTITY_STAGE', stage)
    monkeypatch.setattr(workspace, '_allocate_worker', interrupted_after_mkdir_worker)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    try:
        with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
            lease.allocate_workspace(timeout=30, request_text='{}')
        monkeypatch.setattr(worker, 'time', time)
        assert resources.reconcile(['docker'], daemon) == {
            'recovered': 0, 'active': 1, 'pending': 0, 'invalid': 0}
    finally:
        monkeypatch.setattr(worker, 'time', time)
        if stage == 'after':
            with pytest.raises(RuntimeError, match='evidence retained'):
                lease.close(daemon)
        else:
            lease.close(daemon)
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    roots = list((tmp_path / 'plugin-runtimes' / 'workspaces').iterdir())
    assert len(roots) == int(stage == 'after')
    if stage == 'before':
        assert not lease.path.exists()
        assert resources.reconcile(['docker'], daemon) == {
            'recovered': 0, 'active': 0, 'pending': 0, 'invalid': 0}
        return
    record = resources._read(lease.path)
    assert record['schema'] == 3 and 'workspace' not in record
    assert lease.path.with_suffix('.lock').is_file()
    root, parent = workspace.recorded_workspace_intent(record['workspace_intent'])
    from core.plugin_state import _directory
    assert parent == _directory(root.parent)
    if stage == 'after':
        assert root == roots[0]
        evidence = root / 'unbound-evidence'
        assert evidence.read_bytes() == b'preserve unknown ownership'
        assert not (root / 'request.json').exists()
    assert resources.reconcile(['docker'], daemon) == {
        'recovered': 0, 'active': 0, 'pending': 1, 'invalid': 0}
    assert resources._read(lease.path) == record
    if stage == 'after':
        assert evidence.read_bytes() == b'preserve unknown ownership'


def failed_intent_worker(connection, request, *arguments):
    from core import secrets, plugin_metadata
    original_write = secrets.atomic_write
    original_sync = plugin_metadata.sync_directory
    stage = os.environ['OPSKIT_TEST_INTENT_FAILURE']
    def write(path, content):
        if json.loads(content)['schema'] == 3:
            if stage == 'before_write':
                raise OSError('intent write refused')
            original_write(path, content)
            if stage == 'after_write':
                raise OSError('intent confirmation lost')
        else:
            original_write(path, content)
    def sync(path, identity):
        if stage == 'during_sync' and path.name == 'plugin-resources':
            raise OSError('intent directory sync failed')
        return original_sync(path, identity)
    secrets.atomic_write = write
    plugin_metadata.sync_directory = sync
    workspace._allocate_worker(connection, request, *arguments)


@pytest.mark.parametrize('stage', ['before_write', 'after_write', 'during_sync'])
def test_unconfirmed_intent_never_creates_directory(tmp_path, monkeypatch, stage):
    daemon = Daemon()
    lease = resources.ResourceLease(['docker'], 'container', 'opskit-' + 'a' * 32, daemon)
    original = lease.path.read_bytes()
    monkeypatch.setenv('OPSKIT_TEST_INTENT_FAILURE', stage)
    monkeypatch.setattr(workspace, '_allocate_worker', failed_intent_worker)
    try:
        with pytest.raises(RuntimeError, match='allocation unconfirmed'):
            lease.allocate_workspace(timeout=30)
        assert not list((tmp_path / 'plugin-runtimes' / 'workspaces').iterdir())
        record = resources._read(lease.path)
        if stage == 'before_write':
            assert lease.path.read_bytes() == original and record['schema'] == 1
        else:
            assert record['schema'] == 3 and 'workspace' not in record
            from core.plugin_resource_io import remove_record
            with pytest.raises(ValueError, match='changed during cleanup'):
                remove_record(lease.path, record, timeout=30)
            assert resources._read(lease.path) == record
        with pytest.raises(ValueError, match='already requested'):
            lease.allocate_workspace(timeout=30)
    finally:
        lease.close(daemon)
    assert not lease.path.exists()
    assert resources.reconcile(['docker'], daemon) == {
        'recovered': 0, 'active': 0, 'pending': 0, 'invalid': 0}


@pytest.mark.parametrize('failure_worker', [failed_request_worker, unconfirmed_binding_worker])
def test_request_failure_is_bound_before_request_open(monkeypatch, failure_worker):
    daemon = Daemon()
    lease = resources.ResourceLease(['docker'], 'container', 'opskit-' + 'a' * 32, daemon)
    try:
        monkeypatch.setattr(workspace, '_allocate_worker', failure_worker)
        with pytest.raises(RuntimeError, match='allocation unconfirmed'):
            lease.allocate_workspace(timeout=30, request_text='{}')
        record = resources._read(lease.path)
        assert record['schema'] == 2 and lease.record['schema'] == 1
        owned = workspace.recorded_workspace(record['workspace'])
        assert owned.root.is_dir() and not (owned.root / 'request.json').exists()
        assert resources.reconcile(['docker'], daemon)['active'] == 1
        with pytest.raises(ValueError, match='already requested'):
            lease.allocate_workspace(timeout=30)
    finally:
        lease.close(daemon)
    assert not owned.root.exists() and not lease.path.exists()


@pytest.mark.parametrize('cancel', [False, True])
def test_unacknowledged_workspace_has_durable_binding(tmp_path, monkeypatch,
                                                      expire_worker_after_marker, cancel):
    from network import worker
    daemon = Daemon()
    lease = resources.ResourceLease(['docker'], 'container', 'opskit-' + 'a' * 32, daemon)
    marker = tmp_path / 'allocation.pid'
    monkeypatch.setenv('OPSKIT_TEST_WORKSPACE_PID', str(marker))
    monkeypatch.setattr(workspace, '_allocate_worker', unacknowledged_worker)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    try:
        with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
            lease.allocate_workspace(timeout=30, request_text='{}')
        monkeypatch.setattr(worker, 'time', time)
        assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
        record = resources._read(lease.path)
        assert record['schema'] == 2 and lease.record['schema'] == 1
        owned = workspace.recorded_workspace(record['workspace'])
        assert (owned.root / 'request.json').read_bytes() == b'{}'
        assert resources.reconcile(['docker'], daemon)['active'] == 1
    finally:
        monkeypatch.setattr(worker, 'time', time)
        lease.close(daemon)
    assert not owned.root.exists() and not lease.path.exists()


def test_close_rejects_changed_immutable_resource_binding():
    daemon = Daemon()
    lease = resources.ResourceLease(['docker'], 'container', 'opskit-' + 'a' * 32, daemon)
    original = lease.path.read_bytes()
    import json
    lease.path.write_text(json.dumps(dict(lease.record, daemon='daemon-b')))
    with pytest.raises(ValueError, match='binding changed'):
        lease.close(daemon)
    assert lease.path.exists()
    lease.path.write_bytes(original)
    assert resources.reconcile(['docker'], daemon)['recovered'] == 1
