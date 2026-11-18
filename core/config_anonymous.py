"""Publish initial configuration from an unnamed Linux file when supported."""
import ctypes
import errno
import os
from pathlib import Path
import stat
import sys

from core.file_identity import same_file_identity


_UNSUPPORTED = {errno.EOPNOTSUPP, errno.EINVAL, errno.ENOSYS, errno.EISDIR}


def _link_descriptor(source: int, directory: int, name: str) -> None:
    """Link the open unnamed file into the open directory without replacement."""
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        linkat = libc.linkat
    except AttributeError:
        raise OSError(errno.ENOSYS, "linkat unavailable") from None
    linkat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    linkat.restype = ctypes.c_int
    if linkat(source, b"", directory, os.fsencode(name), 0x1000) != 0:  # AT_EMPTY_PATH
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def publish_if_supported(path: Path, payload: bytes) -> bool:
    """Atomically publish a complete file, or return False if unsupported.

    Existing destinations are never replaced. Errors after publication retain
    the new file: its pathname may no longer identify our own object.
    """
    if not sys.platform.startswith("linux") or not hasattr(os, "O_TMPFILE"):
        return False
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    directory = os.open(path.parent, flags)
    try:
        info = os.fstat(directory)
        if not stat.S_ISDIR(info.st_mode) or not same_file_identity(info, path.parent.lstat()):
            raise ValueError("configuration directory changed")
        try:
            descriptor = os.open(".", os.O_TMPFILE | os.O_RDWR, 0o600, dir_fd=directory)
        except OSError as exc:
            if exc.errno in _UNSUPPORTED:
                return False
            raise
        try:
            file_info = os.fstat(descriptor)
            if (not stat.S_ISREG(file_info.st_mode) or file_info.st_nlink != 0
                    or not same_file_identity(file_info, file_info)):
                raise ValueError("configuration staging identity unavailable")
            # Creation mode is filtered by umask. Set it on the still-unnamed
            # inode so restrictive umasks cannot make the config unusable.
            os.fchmod(descriptor, 0o600)
            if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
                raise ValueError("configuration permissions unavailable")
            with os.fdopen(descriptor, "wb", closefd=False) as output:
                output.write(payload)
                output.flush()
                os.fsync(descriptor)
            if not same_file_identity(info, path.parent.lstat()):
                raise ValueError("configuration directory changed")
            try:
                _link_descriptor(descriptor, directory, path.name)
            except OSError as exc:
                if exc.errno in _UNSUPPORTED:
                    return False
                raise
            # A successful link operation is not itself proof that the name
            # still refers to our open inode. A concurrent rename or unusual
            # filesystem semantics can expose a different object instead.
            published = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            if (not stat.S_ISREG(published.st_mode)
                    or not same_file_identity(os.fstat(descriptor), published)):
                raise ValueError("published configuration identity changed")
            os.fsync(directory)
            if not same_file_identity(info, path.parent.lstat()):
                raise ValueError("configuration directory changed")
            return True
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)
