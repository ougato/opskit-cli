"""Directory barriers must not confirm changed records or failed retirement."""
import errno
import os
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_metadata as metadata, plugin_resource_io as io
from core import plugin_resources as resources, plugin_workspace as workspace
from core.plugin_state import _directory
from test_plugin_resource_inspection import record_file
from test_plugin_resource_journal import Daemon
from test_plugin_workspace import block


@pytest.mark.skipif(os.name == 'nt', reason='POSIX directory fsync contract')
def test_failed_fsync_closes_real_directory_handle(tmp_path, monkeypatch):
    observed = []
    def failed(descriptor):
        observed.append(descriptor)
        raise OSError(errno.EIO, 'injected directory sync failure')
    monkeypatch.setattr(os, 'fsync', failed)
    with pytest.raises(OSError) as failure:
        metadata.sync_directory(tmp_path, _directory(tmp_path))
    assert failure.value.errno == errno.EIO and len(observed) == 1
    with pytest.raises(OSError) as closed:
        os.fstat(observed[0])
    assert closed.value.errno == errno.EBADF


def test_sync_rejects_wrong_identity_before_open(tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'open', lambda *a, **k: pytest.fail('opened wrong directory'))
    identity = _directory(tmp_path)
    with pytest.raises(ValueError, match='before sync'):
        metadata.sync_directory(tmp_path, [identity[0], identity[1] + 1])


@pytest.mark.skipif(os.name == 'nt', reason='POSIX directory fsync contract')
def test_directory_replaced_during_fsync_is_not_confirmed(tmp_path, monkeypatch):
    target = tmp_path / 'directory'
    target.mkdir()
    original_identity = _directory(target)
    old = tmp_path / 'original'
    original = os.fsync
    def changed(descriptor):
        original(descriptor)
        target.rename(old)
        target.mkdir()
    monkeypatch.setattr(os, 'fsync', changed)
    with pytest.raises(ValueError, match='during sync'):
        metadata.sync_directory(target, original_identity)
    assert _directory(old) == original_identity
    assert _directory(target) != original_identity


def test_identical_record_replaced_during_directory_sync_is_not_confirmation(monkeypatch):
    root = resources._root()
    path, record = record_file(root)
    raw = path.read_bytes()
    original = metadata.sync_directory
    def changed(directory, identity):
        result = original(directory, identity)
        replacement = path.with_suffix('.replacement')
        replacement.write_bytes(raw)
        os.replace(replacement, path)
        return result
    monkeypatch.setattr(metadata, 'sync_directory', changed)
    with pytest.raises(ValueError, match='during directory sync'):
        io._confirm_record(path, record, _directory(root))
    assert path.read_bytes() == raw


def _fault_sync():
    original = metadata.sync_directory
    def sync(path, identity):
        expected = 'workspaces' if os.environ['OPSKIT_TEST_SYNC_OPERATION'] == 'cleanup' else 'plugin-resources'
        if path.name == expected:
            if os.environ['OPSKIT_TEST_SYNC_OPERATION'] == 'bind':
                import json
                # Preserve the bind-stage fault after the preceding intent
                # barrier; intent-stage failures have their own regression.
                if not any(json.loads(p.read_bytes())['schema'] == 2 for p in path.glob('*.json')):
                    return original(path, identity)
            if os.environ['OPSKIT_TEST_SYNC_FAILURE'] == 'block':
                block()
            raise OSError(errno.EIO, 'injected directory sync failure')
        return original(path, identity)
    metadata.sync_directory = sync


def failed_create(connection, *args):
    _fault_sync()
    io._create_worker(connection, *args)


def failed_bind(connection, *args):
    _fault_sync()
    workspace._allocate_worker(connection, *args)


def failed_remove(connection, *args):
    _fault_sync()
    io._remove_worker(connection, *args)


def failed_cleanup(connection, *args):
    _fault_sync()
    workspace._cleanup_worker(connection, *args)


@pytest.mark.parametrize('operation', ['create', 'bind', 'remove', 'cleanup'])
@pytest.mark.parametrize('fault', ['eio', 'timeout', 'cancel'])
def test_sync_failure_does_not_report_success(tmp_path, monkeypatch, expire_worker_after_marker,
                                            operation, fault):
    from network import worker
    daemon = Daemon()
    lease = None
    if operation != 'create':
        lease = resources.ResourceLease(['docker'], 'container', 'opskit-' + 'a' * 32, daemon)
    owned = lease.allocate_workspace(timeout=30) if operation == 'cleanup' else None
    marker = tmp_path / 'sync.pid'
    monkeypatch.setenv('OPSKIT_TEST_SYNC_OPERATION', operation)
    monkeypatch.setenv('OPSKIT_TEST_SYNC_FAILURE', 'eio' if fault == 'eio' else 'block')
    monkeypatch.setenv('OPSKIT_TEST_WORKSPACE_PID', str(marker))
    module, name, target = {
        'create': (io, '_create_worker', failed_create),
        'bind': (workspace, '_allocate_worker', failed_bind),
        'remove': (io, '_remove_worker', failed_remove),
        'cleanup': (workspace, '_cleanup_worker', failed_cleanup),
    }[operation]
    try:
        with monkeypatch.context() as patch:
            patch.setattr(module, name, target)
            if fault == 'cancel':
                def clock():
                    if marker.exists():
                        raise KeyboardInterrupt
                    return time.monotonic()
                patch.setattr(worker, 'time', SimpleNamespace(monotonic=clock))
            elif fault == 'timeout':
                expire_worker_after_marker(marker)
            error = (KeyboardInterrupt if fault == 'cancel' else TimeoutError if fault == 'timeout'
                     else RuntimeError if operation in ('bind', 'cleanup') else OSError)
            with pytest.raises(error):
                if operation == 'create':
                    resources.ResourceLease(['docker'], 'container', 'opskit-' + 'a' * 32, daemon)
                elif operation == 'bind':
                    lease.allocate_workspace(timeout=30, request_text='{}')
                else:
                    lease.close(daemon)
        monkeypatch.setattr(worker, 'time', time)
        if fault != 'eio':
            assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
        paths = list(resources._root().glob('*.json'))
        if operation == 'remove':
            assert not paths, 'unlink may have executed despite unconfirmed sync'
        else:
            assert len(paths) == 1
            record = resources._read(paths[0])
            assert record['schema'] == (1 if operation == 'create' else 2)
            if operation == 'bind':
                bound = workspace.recorded_workspace(record['workspace'])
                assert bound.root.exists() and not (bound.root / 'request.json').exists()
            if operation == 'cleanup':
                assert not owned.root.exists(), 'directory removed before failed parent sync'
    finally:
        monkeypatch.setattr(worker, 'time', time)
        if lease is not None and not lease.closed:
            lease.close(daemon)
        result = resources.reconcile(['docker'], daemon)
        assert not any(result[key] for key in ('active', 'pending', 'invalid'))
        assert not list(resources._root().glob('*.json'))
