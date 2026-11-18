"""Consent-bound compare-and-write of an existing, exclusively opened host file."""
import base64
import hashlib
import json
import os
import re
import stat
import uuid

from core.host_file_access import _windows_final_path, _windows_long_path, source_path
from core.native_sandbox import CapabilityRequest

MAX_WRITE = 16 * 1024


def _exclusive_open(path):
    if os.name != "nt":
        raise ValueError("host file write backend unavailable")
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    # No sharing: independent readers, writers and renames cannot race the
    # content check and write while this handle is held.
    handle = create(str(path), 0x80000000 | 0x40000000, 0, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise


def compare_write(plugin, fingerprint, directory, relative, expected_sha256, encoded, *,
                  authority, check_identity, checkpoint):
    if (not isinstance(expected_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or not isinstance(encoded, str) or len(encoded) > (MAX_WRITE + 2) // 3 * 4):
        raise ValueError("invalid host file write request")
    content = base64.b64decode(encoded, validate=True)
    if len(content) > MAX_WRITE:
        raise ValueError("host file write limit exceeded")
    root, target, root_identity = source_path(directory, relative)
    for parent in (target, *target.parents):
        info = parent.lstat()
        if parent.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("linked host file path forbidden")
    before = target.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_WRITE:
        raise ValueError("host file must be bounded and ordinary")
    resource = json.dumps({"file": str(target), "before_sha256": expected_sha256,
                           "after_sha256": hashlib.sha256(content).hexdigest()}, sort_keys=True)
    scope = CapabilityRequest(plugin, fingerprint, "directory.write", resource)
    checkpoint()
    authority.require(scope, check_identity=check_identity)
    authority.check(scope, check_identity=check_identity)
    checkpoint()
    current_root, current_target, current_identity = source_path(directory, relative)
    if (current_root, current_target, current_identity) != (root, target, root_identity):
        raise ValueError("host directory changed during authorization")
    expected_path = _windows_long_path(target)
    fd = _exclusive_open(target)
    with os.fdopen(fd, "r+b", buffering=0) as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size > MAX_WRITE
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or _windows_final_path(stream.fileno()) != expected_path):
            raise ValueError("host file changed during authorization")
        original = stream.read(MAX_WRITE + 1)
        if hashlib.sha256(original).hexdigest() != expected_sha256:
            raise ValueError("host file contents changed")
        checkpoint()
        authority.check(scope, check_identity=check_identity)
        try:
            stream.seek(0)
            stream.write(content)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            if stream.read(MAX_WRITE + 1) != content:
                raise OSError("host file write verification failed")
            checkpoint()
            authority.check(scope, check_identity=check_identity)
        except BaseException:
            stream.seek(0)
            stream.write(original)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
            raise
    return {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}


def create_file(plugin, fingerprint, directory, relative, encoded, *, authority, check_identity, checkpoint):
    """Create a new bounded file via a same-directory temporary and no-overwrite rename."""
    if (os.name != "nt" or not isinstance(relative, str) or "/" in relative or "\\" in relative
            or not isinstance(encoded, str) or len(encoded) > (MAX_WRITE + 2) // 3 * 4):
        raise ValueError("invalid host file create request")
    content = base64.b64decode(encoded, validate=True)
    if len(content) > MAX_WRITE:
        raise ValueError("host file write limit exceeded")
    root, target, root_identity = source_path(directory, relative)
    if os.path.lexists(target):
        raise FileExistsError("host file already exists")
    digest = hashlib.sha256(content).hexdigest()
    resource = json.dumps({"file": str(target), "create_only": True, "after_sha256": digest}, sort_keys=True)
    scope = CapabilityRequest(plugin, fingerprint, "directory.write", resource)
    checkpoint()
    authority.require(scope, check_identity=check_identity)
    authority.check(scope, check_identity=check_identity)
    checkpoint()
    if source_path(directory, relative) != (root, target, root_identity) or os.path.lexists(target):
        raise ValueError("host file target changed during authorization")
    temporary = root / (".opskit-" + uuid.uuid4().hex + ".tmp")
    identity = None
    published = False
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
        with os.fdopen(fd, "wb", buffering=0) as stream:
            info = os.fstat(stream.fileno())
            identity = info.st_dev, info.st_ino
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or _windows_final_path(stream.fileno()) != _windows_long_path(temporary)):
                raise ValueError("temporary host file redirected")
            view = memoryview(content)
            while view:
                count = stream.write(view)
                if not count:
                    raise OSError("host file write incomplete")
                view = view[count:]
            stream.flush()
            os.fsync(stream.fileno())
        checkpoint()
        authority.check(scope, check_identity=check_identity)
        if source_path(directory, relative) != (root, target, root_identity) or os.path.lexists(target):
            raise ValueError("host file target changed before publish")
        before = temporary.lstat()
        if (before.st_dev, before.st_ino) != identity or before.st_nlink != 1:
            raise ValueError("temporary host file changed")
        # Windows rename fails if the destination exists. Never replace it.
        os.rename(temporary, target)
        published = True
        after = target.lstat()
        if (after.st_dev, after.st_ino) != identity or after.st_nlink != 1:
            raise ValueError("published host file changed")
        from core.host_file_access import read_bounded
        if read_bounded(target, root, MAX_WRITE) != content:
            raise OSError("host file write verification failed")
        checkpoint()
        authority.check(scope, check_identity=check_identity)
        return {"sha256": digest, "bytes": len(content)}
    except BaseException:
        candidate = target if published else temporary
        if identity is not None and os.path.lexists(candidate):
            info = candidate.lstat()
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and (info.st_dev, info.st_ino) == identity:
                candidate.unlink()
        raise
