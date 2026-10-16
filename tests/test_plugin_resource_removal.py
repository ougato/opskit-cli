"""Journal retirement is bounded and refuses changed or foreign evidence."""
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_resource_io as io, plugin_resources as resources
from test_plugin_resource_inspection import record_file
from worker_test_markers import publish_marker


def blocked_remove_worker(connection, *args):
    from network import worker
    stage = os.environ['OPSKIT_TEST_RESOURCE_REMOVE_STAGE']
    original_read, original_unlink, original_send = resources._read, Path.unlink, worker.send
    def block():
        read, write = os.pipe()
        publish_marker(os.environ['OPSKIT_TEST_RESOURCE_REMOVE_PID'], str(os.getpid()))
        try:
            os.read(read, 1)
        finally:
            os.close(read)
            os.close(write)
    def read(path):
        if stage == 'read':
            block()
        return original_read(path)
    def unlink(path, *values, **kwargs):
        if stage == 'unlink' and path.name == 'a' * 32 + '.json':
            block()
        return original_unlink(path, *values, **kwargs)
    def send(target, result):
        if stage == 'response' and result.get('ok') is True:
            block()
        return original_send(target, result)
    resources._read, Path.unlink, worker.send = read, unlink, send
    io._remove_worker(connection, *args)


def changed_remove_worker(connection, *args):
    original = resources._read
    def changed(path):
        value = original(path)
        raw = path.read_bytes()
        if os.environ['OPSKIT_TEST_RESOURCE_CHANGE'] == 'metadata':
            path.write_bytes(raw + b' ')
        else:
            replacement = path.with_suffix('.replacement')
            replacement.write_bytes(raw)
            os.replace(replacement, path)
        return value
    resources._read = changed
    io._remove_worker(connection, *args)


@pytest.mark.parametrize('stage', ['read', 'unlink'])
@pytest.mark.parametrize('cancel', [False, True])
def test_blocked_removal_reaped_and_record_retained(tmp_path, monkeypatch, expire_worker_after_marker, stage, cancel):
    from network import worker
    path, record = record_file(resources._root())
    marker = tmp_path / 'worker.pid'
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_REMOVE_PID', str(marker))
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_REMOVE_STAGE', stage)
    monkeypatch.setattr(io, '_remove_worker', blocked_remove_worker)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
        io.remove_record(path, record, timeout=30)
    assert path.exists() and marker.exists()
    assert not psutil.pid_exists(int(marker.read_text()))


def test_lost_response_is_not_confirmed_success(tmp_path, monkeypatch, expire_worker_after_marker):
    path, record = record_file(resources._root())
    marker = tmp_path / 'worker.pid'
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_REMOVE_PID', str(marker))
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_REMOVE_STAGE', 'response')
    monkeypatch.setattr(io, '_remove_worker', blocked_remove_worker)
    expire_worker_after_marker(marker)
    with pytest.raises(TimeoutError):
        io.remove_record(path, record, timeout=30)
    assert not path.exists() and not psutil.pid_exists(int(marker.read_text()))


@pytest.mark.parametrize('change', ['metadata', 'identity'])
def test_changed_file_after_read_is_not_removed(monkeypatch, change):
    path, record = record_file(resources._root())
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_CHANGE', change)
    monkeypatch.setattr(io, '_remove_worker', changed_remove_worker)
    with pytest.raises(ValueError, match='changed during cleanup'):
        io.remove_record(path, record, timeout=30)
    assert path.exists()


def test_parent_does_not_unlink_record(monkeypatch):
    path, record = record_file(resources._root())
    original = Path.unlink
    def unlink(target, *args, **kwargs):
        if target == path:
            pytest.fail('journal unlinked by parent')
        return original(target, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', unlink)
    io.remove_record(path, record, timeout=30)
    assert not path.exists()


def test_foreign_directory_record_is_retained(tmp_path):
    path, record = record_file(tmp_path)
    with pytest.raises(ValueError, match='changed during cleanup'):
        io.remove_record(path, record, timeout=30)
    assert path.exists()


@pytest.mark.parametrize('kind', ['hardlink', 'symlink'])
def test_linked_evidence_is_not_removed(tmp_path, kind):
    outside = tmp_path / 'foreign'
    outside.mkdir()
    foreign, record = record_file(outside)
    root = resources._root()
    path = root / foreign.name
    if kind == 'hardlink':
        os.link(foreign, path)
    else:
        try:
            path.symlink_to(foreign)
        except OSError:
            pytest.skip('symlinks unavailable')
    with pytest.raises(ValueError):
        io.remove_record(path, record, timeout=30)
    assert foreign.exists() and os.path.lexists(path)


@pytest.mark.parametrize('timeout', [0, -1, float('inf'), float('nan'), True, None])
def test_invalid_allowance_cannot_remove_record(timeout):
    path, record = record_file(resources._root())
    with pytest.raises(ValueError):
        io.remove_record(path, record, timeout=timeout)
    assert path.exists()
