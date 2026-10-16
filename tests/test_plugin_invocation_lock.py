"""Invocation locks share hardened file checks and the legacy lock namespace."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from core import plugin_sandbox as sandbox
from core.resource_lock import ResourceBusyError


@contextmanager
def legacy_lock(path):
    with path.open('a+b') as stream:
        stream.write(b'0')
        stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def test_legacy_owner_blocks_new_lock(tmp_path):
    path = tmp_path / 'plugin-runtimes/demo.lock'
    path.parent.mkdir()
    with legacy_lock(path), pytest.raises(sandbox.SandboxError, match='busy in another process'):
        with sandbox._state_lock('demo'):
            pytest.fail('new lock bypassed legacy owner')
    with sandbox._state_lock('demo'):
        pass
    assert path.exists()


def test_new_owner_blocks_legacy_process(tmp_path):
    script = '''import os,sys
with open(sys.argv[1], 'a+b') as stream:
    stream.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print('busy')
    else:
        raise AssertionError('legacy lock bypassed new owner')
'''
    with sandbox._state_lock('demo'):
        result = subprocess.run([sys.executable, '-c', script, str(tmp_path / 'plugin-runtimes/demo.lock')],
                                text=True, capture_output=True, timeout=10)
    assert result.returncode == 0 and result.stdout.strip() == 'busy', result.stderr


@pytest.mark.parametrize('name', ['', '../other', 'Upper', 'a/b', 'a' * 129, None])
def test_invalid_identity_has_no_filesystem_side_effect(tmp_path, name):
    with pytest.raises(sandbox.SandboxError, match='invalid plugin state lock identity'):
        with sandbox._state_lock(name):
            pytest.fail('invalid identity acquired lock')
    assert not (tmp_path / 'plugin-runtimes').exists()


@pytest.mark.parametrize('kind', ['hardlink', 'symlink', 'parent_symlink'])
def test_foreign_file_is_never_modified(tmp_path, kind):
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    target = foreign / 'demo.lock'
    target.write_bytes(b'')
    root = tmp_path / 'plugin-runtimes'
    if kind == 'parent_symlink':
        try:
            root.symlink_to(foreign, target_is_directory=True)
        except OSError:
            pytest.skip('directory symlinks unavailable')
    else:
        root.mkdir()
        if kind == 'hardlink':
            os.link(target, root / 'demo.lock')
        else:
            try:
                (root / 'demo.lock').symlink_to(target)
            except OSError:
                pytest.skip('symlinks unavailable')
    with pytest.raises(sandbox.SandboxError, match='lock acquisition failed'):
        with sandbox._state_lock('demo'):
            pytest.fail('foreign lock accepted')
    assert target.read_bytes() == b''


@pytest.mark.skipif(os.name == 'nt', reason='POSIX FIFO')
def test_special_file_refused(tmp_path):
    path = tmp_path / 'plugin-runtimes/demo.lock'
    path.parent.mkdir()
    os.mkfifo(path)
    with pytest.raises(sandbox.SandboxError, match='lock acquisition failed'):
        with sandbox._state_lock('demo'):
            pytest.fail('special lock accepted')


def changed_lock_worker(connection, source, request, confirmed, transport):
    from core import plugin_invocation_lock as plugin_lock
    original = Path.lstat
    def changed(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path.name == 'demo.lock' and confirmed.is_set():
            return SimpleNamespace(st_mode=info.st_mode, st_nlink=1, st_file_attributes=0,
                                   st_dev=info.st_dev, st_ino=info.st_ino + 1)
        return info
    Path.lstat = changed
    plugin_lock._lock_worker(connection, source, request, confirmed, transport)


def test_post_acquisition_identity_is_checked(tmp_path, monkeypatch):
    from core import plugin_invocation_lock as plugin_lock
    monkeypatch.setattr(plugin_lock, '_lock_worker', changed_lock_worker)
    with pytest.raises(sandbox.SandboxError, match='lock acquisition failed'):
        with sandbox._state_lock('demo'):
            pytest.fail('changed lock identity accepted')


@pytest.mark.parametrize('error', [ValueError('business operation failed'),
                                 ResourceBusyError('business resource busy'),
                                 OSError('business I/O failed'), KeyboardInterrupt()])
def test_business_error_remains_primary_and_lock_released(error):
    with pytest.raises(type(error)) as caught:
        with sandbox._state_lock('demo'):
            raise error
    assert caught.value is error
    with sandbox._state_lock('demo'):
        pass
