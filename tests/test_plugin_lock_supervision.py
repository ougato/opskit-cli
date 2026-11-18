"""Blocking lock preparation cannot hang or outlive its parent invocation."""
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psutil
import pytest

from core import plugin_invocation_lock as plugin_lock, plugin_sandbox as sandbox
from worker_test_markers import publish_marker


def blocked_lock_worker(connection, source, request, confirmed, transport):
    from core import secrets
    stage = os.environ['OPSKIT_TEST_LOCK_STAGE']
    original_open, original_parents = os.open, plugin_lock._parents
    original_confirm, original_permissions = plugin_lock._confirm, secrets.restrict_permissions
    def block():
        read, write = os.pipe()
        publish_marker(os.environ['OPSKIT_TEST_LOCK_PID'], str(os.getpid()))
        try:
            os.read(read, 1)
        finally:
            os.close(read)
            os.close(write)
    def opened(path, *args, **kwargs):
        if stage == 'open' and Path(path).name == 'demo.lock':
            block()
        return original_open(path, *args, **kwargs)
    def parents(path):
        if stage == 'parents':
            block()
        return original_parents(path)
    def checked(*args):
        if stage == 'confirm' and confirmed.is_set():
            block()
        return original_confirm(*args)
    def permissions(path):
        if stage == 'permissions':
            block()
        return original_permissions(path)
    os.open, plugin_lock._parents = opened, parents
    plugin_lock._confirm, secrets.restrict_permissions = checked, permissions
    plugin_lock._lock_worker(connection, source, request, confirmed, transport)


@pytest.mark.parametrize('stage', ['parents', 'permissions', 'open', 'confirm'])
@pytest.mark.parametrize('cancel', [False, True])
def test_blocked_lock_stage_reaped_and_retryable(tmp_path, monkeypatch, expire_worker_after_marker, stage, cancel):
    from network import worker
    marker = tmp_path / 'worker.pid'
    monkeypatch.setenv('OPSKIT_TEST_LOCK_STAGE', stage)
    monkeypatch.setenv('OPSKIT_TEST_LOCK_PID', str(marker))
    original_worker = plugin_lock._lock_worker
    monkeypatch.setattr(plugin_lock, '_lock_worker', blocked_lock_worker)
    if cancel:
        def clock():
            if marker.exists():
                raise KeyboardInterrupt
            return time.monotonic()
        monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=clock))
    else:
        expire_worker_after_marker(marker)
    with pytest.raises(KeyboardInterrupt if cancel else sandbox.SandboxError):
        with sandbox._state_lock('demo'):
            pytest.fail('blocked lock preparation entered business')
    assert marker.exists()
    assert not psutil.pid_exists(int(marker.read_text()))
    monkeypatch.setattr(plugin_lock, '_lock_worker', original_worker)
    monkeypatch.setattr(worker, 'time', time)
    with sandbox._state_lock('demo'):
        pass


def test_parent_does_not_prepare_or_inspect_lock_files(tmp_path, monkeypatch):
    original_lstat, original_mkdir = Path.lstat, Path.mkdir
    def lstat(path, *args, **kwargs):
        if path == tmp_path / 'plugin-runtimes/demo.lock':
            pytest.fail('parent inspected invocation lock path')
        return original_lstat(path, *args, **kwargs)
    def mkdir(path, *args, **kwargs):
        if path == tmp_path / 'plugin-runtimes':
            pytest.fail('parent created invocation lock directory')
        return original_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    with sandbox._state_lock('demo'):
        pass


@pytest.mark.parametrize('timeout', [0, -1, float('nan'), float('inf'), True, None])
def test_bad_allowance_has_no_filesystem_side_effect(tmp_path, timeout):
    path = tmp_path / 'absent/demo.lock'
    with pytest.raises(ValueError):
        with plugin_lock.hold(path, timeout=timeout):
            pytest.fail('invalid lock allowance accepted')
    assert not path.parent.exists()


def test_helper_is_gone_before_business(tmp_path, monkeypatch):
    original = plugin_lock._lock_worker
    monkeypatch.setenv('OPSKIT_TEST_LOCK_PID', str(tmp_path / 'helper.pid'))
    monkeypatch.setattr(plugin_lock, '_lock_worker', marked_lock_worker)
    with sandbox._state_lock('demo'):
        assert not psutil.pid_exists(int((tmp_path / 'helper.pid').read_text()))
    monkeypatch.setattr(plugin_lock, '_lock_worker', original)


def marked_lock_worker(connection, *args):
    publish_marker(os.environ['OPSKIT_TEST_LOCK_PID'], str(os.getpid()))
    plugin_lock._lock_worker(connection, *args)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX descriptor transport')
@pytest.mark.parametrize('count,content', [(0, b'L'), (1, b'X'), (2, b'L'), (3, b'L')])
def test_malformed_descriptor_transfer_closes_received_fds(count, content):
    from array import array
    import socket
    receiver, sender = socket.socketpair()
    read, write = os.pipe()
    try:
        ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array('i', [read] * count))] if count else []
        sender.sendmsg([content], ancillary)
        before = psutil.Process().num_fds()
        with pytest.raises(RuntimeError):
            plugin_lock._receive_descriptor(receiver, time.monotonic() + 5)
        assert psutil.Process().num_fds() == before
    finally:
        receiver.close()
        sender.close()
        os.close(read)
        os.close(write)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX descriptor transport')
def test_absent_descriptor_has_bounded_wait():
    import socket
    receiver, sender = socket.socketpair()
    try:
        with pytest.raises(TimeoutError):
            plugin_lock._receive_descriptor(receiver, time.monotonic() + 0.05)
    finally:
        receiver.close()
        sender.close()


def malformed_confirmation_worker(connection, *args):
    from network import worker
    original = worker.send
    def send(target, result):
        if set(result) == {'ok', 'request'} and result['ok'] is True:
            result = dict(result, ok=1)
        original(target, result)
    worker.send = send
    plugin_lock._lock_worker(connection, *args)


def test_malformed_confirmation_never_enters_business(tmp_path, monkeypatch):
    original = plugin_lock._lock_worker
    monkeypatch.setattr(plugin_lock, '_lock_worker', malformed_confirmation_worker)
    with pytest.raises(sandbox.SandboxError, match='acquisition failed'):
        with sandbox._state_lock('demo'):
            pytest.fail('nonboolean confirmation accepted')
    monkeypatch.setattr(plugin_lock, '_lock_worker', original)
    with sandbox._state_lock('demo'):
        pass
