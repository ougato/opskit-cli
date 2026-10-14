"""Fail-fast, reentrant OS locks for an explicitly identified private resource."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat
import threading


class ResourceBusyError(RuntimeError):
    """Transient contention, distinct from unsafe paths or invalid lock files."""


class ResourceLock:
    def __init__(self, path_provider, *, label, error_type=RuntimeError):
        self.path_provider = path_provider
        self.label = label
        self.error_type = error_type
        self.busy_error_type = ResourceBusyError if error_type is RuntimeError else error_type
        self._thread_lock = threading.RLock()
        self._active = threading.local()

    def _reject_link(self, path):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise self.error_type(self.label + " lock path must not be a link")

    @contextmanager
    def hold(self):
        if not self._thread_lock.acquire(blocking=False):
            raise self.busy_error_type(self.label + " is busy; retry later")
        try:
            path = Path(self.path_provider())
            identity = str(path.absolute())
            if getattr(self._active, "identity", None) is not None:
                if self._active.identity != identity:
                    raise self.error_type(self.label + " resource changed during operation")
                yield
                return
            for parent in path.parents:
                self._reject_link(parent)
            path.parent.mkdir(parents=True, exist_ok=True)
            from core.secrets import restrict_permissions
            restrict_permissions(path.parent)
            self._reject_link(path)
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(descriptor, "r+b") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise self.error_type(self.label + " lock must be a regular file")
                if info.st_nlink != 1:
                    raise self.error_type(self.label + " lock must not have hard links")
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError as exc:
                        if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                            raise
                        raise self.busy_error_type(self.label + " is busy; retry later") from None
                else:
                    import fcntl
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise self.busy_error_type(self.label + " is busy; retry later") from None
                self._active.identity = identity
                try:
                    yield
                finally:
                    self._active.identity = None
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            self._thread_lock.release()
