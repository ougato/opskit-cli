"""Supervised invocation lock opening; the caller owns the actual OS lock."""
from array import array
from contextlib import contextmanager
import errno
import math
import multiprocessing
import os
from pathlib import Path
import socket
import re
import stat
import time
import uuid

from core.resource_lock import ResourceBusyError


def _file_identity(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_ino <= 0
            or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise ValueError('unsafe invocation lock file')
    return info.st_dev, info.st_ino


def _parents(path):
    identities = []
    for parent in path.parents:
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or parent.is_symlink()
                or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise ValueError('unsafe invocation lock parent')
        identities.append((info.st_dev, info.st_ino))
    return identities


def _confirm(path, descriptor, identity, parents):
    if (_parents(path) != parents or _file_identity(path.lstat()) != identity
            or _file_identity(os.fstat(descriptor)) != identity):
        raise ValueError('invocation lock identity changed')


def _lock_worker(connection, source, request, confirmed, transport):
    from core.secrets import restrict_permissions
    from network.worker import send
    descriptor = None
    try:
        if 'plugin' in source:
            from core.paths import data_dir
            path = data_dir() / 'plugin-runtimes' / (source['plugin'] + '.lock')
            # Absolute conversion stays in this worker as well.
            path = path.absolute()
        elif 'resource' in source:
            from core.plugin_resources import _root
            path = _root() / (source['resource'] + '.lock')
        else:
            path = Path(source['path'])
        # Reject existing links before creation or permission changes.
        for parent in path.parents:
            if parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)():
                raise ValueError('unsafe invocation lock parent')
        path.parent.mkdir(parents=True, exist_ok=True)
        parents = _parents(path)
        restrict_permissions(path.parent)
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('unsafe invocation lock path')
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
                             | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0), 0o600)
        identity = _file_identity(os.fstat(descriptor))
        _confirm(path, descriptor, identity, parents)
        handle = 0
        if os.name == 'nt':
            import msvcrt
            handle = msvcrt.get_osfhandle(descriptor)
        else:
            transport.sendmsg([b'L'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array('i', [descriptor]))])
        result = {'ok': True, 'request': request, 'handle': handle}
        if 'resource' in source:
            result['path'] = str(path)
        send(connection, result)
        confirmed.wait()
        # Caller has acquired the nonblocking lock; check the name again now.
        _confirm(path, descriptor, identity, parents)
        send(connection, {'ok': True, 'request': request})
    except (OSError, ValueError, RuntimeError):
        send(connection, {'ok': False, 'request': request})
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if transport is not None:
            transport.close()
        connection.close()


def _receive_descriptor(transport, deadline):
    descriptors = []
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('invocation lock transfer deadline exceeded')
        transport.settimeout(remaining)
        # Receive surplus rights before rejecting the frame. Darwin can install
        # rights omitted by a truncated ancillary buffer; a two-FD buffer is
        # insufficient even for a malformed three-FD frame. Our private, owned
        # helper sends exactly one; keep room for a full surplus-rights batch.
        content, ancillary, flags, _ = transport.recvmsg(1, socket.CMSG_SPACE(256 * array('i').itemsize))
        unexpected = False
        for level, kind, payload in ancillary:
            if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS:
                unexpected = True
                continue
            values = array('i')
            values.frombytes(payload[:len(payload) - len(payload) % values.itemsize])
            descriptors.extend(values)
        if (unexpected or content != b'L' or flags & (socket.MSG_CTRUNC | socket.MSG_TRUNC)
                or len(descriptors) != 1):
            raise RuntimeError('invalid invocation lock transfer')
        result = descriptors.pop()
        try:
            os.set_inheritable(result, False)
        except BaseException:
            os.close(result)
            raise
        return result
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def _duplicate_windows_handle(process, value):
    import _winapi
    import msvcrt
    handle = _winapi.DuplicateHandle(process.sentinel, value, _winapi.GetCurrentProcess(),
                                     0, False, _winapi.DUPLICATE_SAME_ACCESS)
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        _winapi.CloseHandle(handle)
        raise


def _acquire(descriptor):
    if os.name == 'nt':
        import msvcrt
        os.lseek(descriptor, 0, 0)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            raise ResourceBusyError('plugin is busy; retry later') from None
    else:
        import fcntl
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ResourceBusyError('plugin is busy; retry later') from None


@contextmanager
def hold(path=None, *, timeout, plugin=None, resource=None):
    """Bound filesystem preparation and confirm identity while caller holds lock.

    Native nonblocking lock/close calls still run in the caller. This is not a
    hard deadline guarantee for arbitrary faulty network filesystems.
    """
    from network.worker import owned_process, wait_result
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('invalid invocation lock allowance')
    if plugin is not None:
        if (path is not None or resource is not None or not isinstance(plugin, str)
                or re.fullmatch(r'[a-z][a-z0-9_]{0,127}', plugin) is None):
            raise ValueError('invalid invocation lock identity')
        source = {'plugin': plugin}
    elif resource is not None:
        if path is not None or not isinstance(resource, str) or re.fullmatch(r'[0-9a-f]{32}', resource) is None:
            raise ValueError('invalid resource lease identity')
        source = {'resource': resource}
    else:
        source = str(path)
        if (not 0 < len(source) <= 65536 or '\0' in source or not Path(source).is_absolute()
                or '..' in Path(source).parts):
            raise ValueError('invalid invocation lock request')
        source = {'path': source}
    deadline = time.monotonic() + timeout
    request = uuid.uuid4().hex
    confirmed = multiprocessing.get_context('spawn').Event()
    receiver = sender = None
    descriptor = None
    transferred_path = None
    try:
        if os.name != 'nt':
            receiver, sender = socket.socketpair()
        with owned_process(_lock_worker, (source, request, confirmed, sender),
                           label='plugin invocation lock') as (connection, process):
            if sender is not None:
                sender.close()
            result = wait_result(connection, deadline=deadline, label='plugin invocation lock')
            fields = {'ok', 'request', 'handle'} | ({'path'} if resource is not None else set())
            if (set(result) != fields or result['ok'] is not True
                    or result['request'] != request or type(result['handle']) is not int
                    or not 0 <= result['handle'] < 2**64):
                raise RuntimeError('invocation lock opening unconfirmed')
            if resource is not None:
                value = result['path']
                if (not isinstance(value, str) or not 0 < len(value) <= 65536 or '\0' in value
                        or not Path(value).is_absolute() or '..' in Path(value).parts
                        or Path(value).name != resource + '.lock'
                        or Path(value).parent.name != 'plugin-resources'):
                    raise RuntimeError('resource lease path unconfirmed')
                transferred_path = Path(value)
            if os.name == 'nt':
                descriptor = _duplicate_windows_handle(process, result['handle'])
            else:
                if result['handle'] != 0:
                    raise RuntimeError('invalid invocation lock handle')
                descriptor = _receive_descriptor(receiver, deadline)
            _acquire(descriptor)
            confirmed.set()
            result = wait_result(connection, deadline=deadline, label='plugin invocation lock confirmation')
            if (set(result) != {'ok', 'request'} or result['ok'] is not True
                    or result['request'] != request):
                raise RuntimeError('invocation lock confirmation failed')
        if time.monotonic() >= deadline:
            raise TimeoutError('invocation lock acquisition deadline exceeded')
        yield transferred_path
    finally:
        # Do not retain semaphore finalizers in a caught exception traceback.
        confirmed = None
        # Never explicitly unlock from the helper or delete a permanent lock.
        if descriptor is not None:
            os.close(descriptor)
        for transport in (receiver, sender):
            if transport is not None:
                transport.close()
