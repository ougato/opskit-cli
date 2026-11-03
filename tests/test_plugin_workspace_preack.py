"""Unacknowledged native workspaces never grant name-based deletion authority."""
import errno
import os
from pathlib import Path
import time
from types import SimpleNamespace
import psutil
import pytest
from core import plugin_workspace as workspace
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
@pytest.mark.parametrize('stage', ['before', 'after', 'ack'])
def test_unconfirmed_native_allocation_retains_unknown_content(tmp_path, monkeypatch, expire_worker_after_marker, cancel, stage):
    from network import worker
    marker = tmp_path / 'allocation.pid'
    monkeypatch.setenv('OPSKIT_TEST_WORKSPACE_PID', str(marker))
    monkeypatch.setenv('OPSKIT_TEST_PREIDENTITY_STAGE', stage)
    target = unacknowledged_worker if stage == 'ack' else interrupted_after_mkdir_worker
    monkeypatch.setattr(workspace, '_allocate_worker', target)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    with pytest.raises(KeyboardInterrupt if cancel else TimeoutError):
        workspace.allocate(timeout=30, request_text='{}')
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    roots = list((tmp_path / 'plugin-runtimes/workspaces').iterdir())
    assert len(roots) == int(stage != 'before')
    if stage == 'after':
        assert (roots[0] / 'unbound-evidence').read_bytes() == b'preserve unknown ownership'
    if stage == 'ack':
        assert (roots[0] / 'request.json').read_bytes() == b'{}'


def test_request_write_failure_preserves_unknown_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, '_allocate_worker', failed_request_worker)
    with pytest.raises(RuntimeError, match='allocation unconfirmed'):
        workspace.allocate(timeout=30, request_text='{}')
    roots = list((tmp_path / 'plugin-runtimes/workspaces').iterdir())
    assert len(roots) == 1 and not (roots[0] / 'request.json').exists()
