"""Intent creation owns its index transaction and refuses unconfirmed launch."""
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_resource_io as io, plugin_resources as resources
from test_plugin_resource_journal import Daemon
from worker_test_markers import publish_marker


def creation_worker(connection, *args):
    from core import secrets
    from network import worker
    stage = os.environ['OPSKIT_TEST_RESOURCE_CREATE_STAGE']
    original_glob, original_write, original_read, original_send = Path.glob, secrets.atomic_write, resources._read, worker.send
    def block():
        read, write = os.pipe()
        publish_marker(os.environ['OPSKIT_TEST_RESOURCE_CREATE_PID'], str(os.getpid()))
        try:
            os.read(read, 1)
        finally:
            os.close(read)
            os.close(write)
    def glob(path, *values, **kwargs):
        if stage == 'scan':
            block()
        return original_glob(path, *values, **kwargs)
    def write(path, text):
        if stage == 'write':
            block()
        if stage == 'diskfull':
            raise OSError('owned injected disk full')
        original_write(path, text)
    def read(path):
        if stage == 'verify':
            block()
        return original_read(path)
    def send(target, value):
        if stage == 'response' and value.get('ok') is True:
            block()
        return original_send(target, value)
    Path.glob, secrets.atomic_write, resources._read, worker.send = glob, write, read, send
    io._create_worker(connection, *args)


@pytest.mark.parametrize('stage', ['scan', 'write', 'verify', 'response'])
@pytest.mark.parametrize('cancel', [False, True])
def test_creation_timeout_cancel_never_launch_and_preserve_written_intent(tmp_path, monkeypatch, expire_worker_after_marker, stage, cancel):
    from network import worker
    marker = tmp_path / 'worker.pid'
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_CREATE_PID', str(marker))
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_CREATE_STAGE', stage)
    monkeypatch.setattr(io, '_create_worker', creation_worker)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    daemon = Daemon()
    with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
        resources.ResourceLease(['docker'], 'container', 'opskit-' + 'b' * 32, daemon)
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert all(call[0] == 'info' for call in daemon.calls)
    paths = list(resources._root().glob('*.json'))
    assert len(paths) == (1 if stage in ('verify', 'response') else 0)
    monkeypatch.setattr(worker, 'time', time)
    # The parent token and worker index locks must both have been released.
    from core.resource_lock import ResourceLock
    for path in resources._root().glob('*.lock'):
        with ResourceLock(lambda: path, label='old observer').hold():
            pass
    if paths:
        assert resources.reconcile(['docker'], daemon)['recovered'] == 1


def test_real_worker_disk_full_refuses_launch(monkeypatch):
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_CREATE_STAGE', 'diskfull')
    monkeypatch.setattr(io, '_create_worker', creation_worker)
    daemon = Daemon()
    with pytest.raises(OSError, match='creation failed'):
        resources.ResourceLease(['docker'], 'container', 'opskit-' + 'b' * 32, daemon)
    assert not list(resources._root().glob('*.json'))
    assert all(call[0] == 'info' for call in daemon.calls)


def test_parent_does_not_scan_or_write_creation(tmp_path, monkeypatch):
    from core import secrets
    original = Path.glob
    def glob(path, *args, **kwargs):
        if path == tmp_path / 'plugin-resources':
            pytest.fail('parent scanned creation capacity')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'glob', glob)
    monkeypatch.setattr(secrets, 'atomic_write', lambda *args: pytest.fail('parent wrote intent'))
    daemon = Daemon()
    lease = resources.ResourceLease(['docker'], 'container', 'opskit-' + 'b' * 32, daemon)
    lease.close(daemon)


def value(token='a'):
    return {'schema':1, 'token':token * 32, 'kind':'container', 'name':'opskit-' + 'b' * 32, 'backend':['docker'], 'daemon':'daemon-a'}


def test_existing_token_not_overwritten():
    import json
    record=value()
    path=resources._root() / (record['token'] + '.json')
    path.write_text(json.dumps(record))
    raw=path.read_bytes()
    with pytest.raises(ValueError, match='changed'):
        io.create_record(path, record, timeout=30, limit=256)
    assert path.read_bytes()==raw


def test_foreign_directory_refused(tmp_path):
    record=value()
    path=tmp_path / (record['token'] + '.json')
    with pytest.raises(ValueError, match='changed'):
        io.create_record(path, record, timeout=30, limit=256)
    assert not path.exists()


@pytest.mark.parametrize('limit', [0,257,True,None])
def test_invalid_capacity_has_no_write(tmp_path, limit):
    record=value()
    path=tmp_path / (record['token'] + '.json')
    with pytest.raises(ValueError, match='capacity'):
        io.create_record(path, record, timeout=30, limit=limit)
    assert not path.exists()


def test_legacy_index_lock_prevents_any_write():
    from core.resource_lock import ResourceLock, ResourceBusyError
    root = resources._root()
    record = value()
    path = root / (record['token'] + '.json')
    with ResourceLock(lambda: root / '.index.lock', label='legacy writer').hold():
        with pytest.raises(ResourceBusyError):
            io.create_record(path, record, timeout=30, limit=256)
        assert not path.exists()
    io.create_record(path, record, timeout=30, limit=256)
    assert resources._read(path) == record


@pytest.mark.parametrize('timeout', [0, -1, float('nan'), float('inf'), True, None])
def test_invalid_creation_allowance_has_no_write(tmp_path, timeout):
    record = value()
    path = tmp_path / (record['token'] + '.json')
    with pytest.raises(ValueError, match='allowance'):
        io.create_record(path, record, timeout=timeout, limit=256)
    assert not path.exists()
