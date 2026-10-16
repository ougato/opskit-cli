"""Resource journal reads are fresh, bounded, and do not run in the caller."""
from contextlib import closing
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_inspection as inspection, plugin_resources as resources, plugin_sandbox as sandbox
from worker_test_markers import publish_marker


def record_file(root):
    value = {'schema': 1, 'token': 'a' * 32, 'kind': 'container',
             'name': 'opskit-' + 'b' * 32, 'backend': ['docker'], 'daemon': 'owned-daemon'}
    path = root / ('a' * 32 + '.json')
    path.write_text(json.dumps(value), encoding='utf-8')
    return path, value


def blocked_journal_session(connection, *args):
    original_open = os.open
    def opened(path, *values, **kwargs):
        if Path(path).name == 'a' * 32 + '.json':
            read, write = os.pipe()
            publish_marker(os.environ['OPSKIT_TEST_RESOURCE_READ_PID'], str(os.getpid()))
            try:
                os.read(read, 1)
            finally:
                os.close(read)
                os.close(write)
        return original_open(path, *values, **kwargs)
    os.open = opened
    inspection._inspection_session(connection, *args)


@pytest.mark.parametrize('cancel', [False, True])
def test_blocked_record_open_is_reaped(tmp_path, monkeypatch, expire_worker_after_marker, cancel):
    from network import worker
    path, _ = record_file(tmp_path)
    marker = tmp_path / 'reader.pid'
    monkeypatch.setenv('OPSKIT_TEST_RESOURCE_READ_PID', str(marker))
    monkeypatch.setattr(inspection, '_inspection_session', blocked_journal_session)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
        resources._read_supervised(path)
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert path.exists()


def test_parent_never_reads_record_and_scoped_reader_is_reused(tmp_path, monkeypatch):
    path, value = record_file(tmp_path)
    monkeypatch.setattr(resources, '_read', lambda *args: pytest.fail('parent read resource journal'))
    with closing(inspection.ContentInspector()) as reader:
        monkeypatch.setattr(sandbox._invocations, 'inspection_scope', True, raising=False)
        monkeypatch.setattr(sandbox._invocations, 'inspector', reader, raising=False)
        assert resources._read_supervised(path) == value
        pid = reader.pid
        value['daemon'] = 'fresh-daemon'
        path.write_text(json.dumps(value), encoding='utf-8')
        assert resources._read_supervised(path) == value and reader.pid == pid
    assert not psutil.pid_exists(pid)


def test_invalid_record_does_not_poison_next_fresh_read(tmp_path):
    path, value = record_file(tmp_path)
    with closing(inspection.ContentInspector()) as reader:
        assert reader.resource_record(path, timeout=30) == value
        pid = reader.pid
        path.write_text(json.dumps(value)[:-1] + ', "schema": 1}', encoding='utf-8')
        with pytest.raises(ValueError, match='invalid resource journal'):
            reader.resource_record(path, timeout=30)
        path.write_text(json.dumps(value), encoding='utf-8')
        assert reader.resource_record(path, timeout=30) == value and reader.pid == pid


@pytest.mark.parametrize('response', [
    {}, {'ok': False}, {'ok': 1, 'record': {}}, {'ok': True, 'record': {}},
    {'ok': True, 'record': {}, 'extra': True}, {'ok': False, 'reason': 'unknown'},
])
def test_malformed_reader_response_is_not_a_record(response):
    with pytest.raises((RuntimeError, ValueError)):
        inspection._resource_record_value(response, Path('a' * 32 + '.json'))


def test_read_timeout_is_pending_and_never_calls_docker(tmp_path, monkeypatch):
    root = resources._root()
    path, _ = record_file(root)
    def timeout(*args, **kwargs):
        raise TimeoutError('synthetic filesystem timeout')
    monkeypatch.setattr(resources, '_read_supervised', timeout)
    calls = []
    assert resources.reconcile(['docker'], lambda *args: calls.append(args)) == {
        'recovered': 0, 'active': 0, 'pending': 1, 'invalid': 0}
    assert not calls and path.exists()
