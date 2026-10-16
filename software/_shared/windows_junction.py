"""Owned Windows directory junctions using reparse APIs, without a shell."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import struct
import time
import uuid

from software._shared.mutation import mutation_lock, versioned_mutation
from software.base import InstallError

_MOUNT_POINT = 0xA0000003
_GET = 0x000900A8
_SET = 0x000900A4


@contextmanager
def _open(path, *, delete=False, security_write=False):
    if os.name != "nt":
        raise InstallError("junction operations require Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    access = 0x80000000 | 0x40000000
    if delete:
        access |= 0x10000
    if security_write:
        access |= 0x40000 | 0x80000
    # Read sharing preserves metadata queries but rejects competing writers
    # and deletion while a mutation or transaction guard holds the object.
    for attempt in range(10):
        handle = create(str(path), access, 1, None, 3, 0x02000000 | 0x00200000, None)
        if handle != ctypes.c_void_p(-1).value:
            break
        error = ctypes.get_last_error()
        # Command exit can precede release of another process's directory
        # handle. Keep exclusive sharing and revalidate identity after opening.
        if error != 32 or attempt == 9:
            raise ctypes.WinError(error)
        time.sleep(.05)
    try:
        yield handle
    finally:
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        close(handle)


def _control(handle, code, body=None):
    call = ctypes.WinDLL("kernel32", use_last_error=True).DeviceIoControl
    call.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                     ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    call.restype = wintypes.BOOL
    source = ctypes.create_string_buffer(body) if body is not None else None
    output = ctypes.create_string_buffer(16384) if code == _GET else None
    size = wintypes.DWORD()
    if not call(handle, code, source, len(body) if body is not None else 0, output,
                len(output) if output is not None else 0, ctypes.byref(size), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return output.raw[:size.value] if output is not None else None


def _target(body):
    if len(body) < 16:
        raise InstallError("invalid junction data")
    tag, length, _ = struct.unpack_from("<IHH", body)
    offset, size, _, _ = struct.unpack_from("<HHHH", body, 8)
    if tag != _MOUNT_POINT or length + 8 != len(body) or offset % 2 or size % 2 or 16 + offset + size > len(body):
        raise InstallError("refusing an unmanaged reparse point")
    value = body[16 + offset:16 + offset + size].decode("utf-16-le")
    for prefix in ("\\??\\", "\\\\?\\"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            if value.startswith("UNC\\"):
                value = "\\\\" + value[4:]
            break
    return Path(value).resolve()


def _body(target):
    printed = str(target).encode("utf-16-le")
    value = str(target)
    substituted = ("\\??\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\??\\" + value).encode("utf-16-le")
    names = substituted + b"\0\0" + printed + b"\0\0"
    payload = struct.pack("<HHHH", 0, len(substituted), len(substituted) + 2, len(printed)) + names
    if len(payload) + 8 > 16384:
        raise ValueError("junction target exceeds limit")
    return struct.pack("<IHH", _MOUNT_POINT, len(payload), 0) + payload


@dataclass(frozen=True)
class _TargetBoundary:
    """Exact targets supplied by a caller that owns the external route policy."""
    root: Path
    targets: tuple[Path, ...]

    def __fspath__(self):
        return str(self.root)


def _authorize_target(target, boundary):
    if isinstance(boundary, _TargetBoundary):
        if target not in boundary.targets:
            raise InstallError("junction target is not explicitly authorized")
        return
    try:
        target.relative_to(boundary)
    except ValueError:
        raise InstallError("junction target is outside its managed root") from None


def _root_and_entry(junction, managed_root, authorized_targets=None):
    root = Path(managed_root).resolve()
    entry = Path(junction).absolute()
    if entry.parent.resolve() != root:
        raise InstallError("junction must be directly inside its managed root")
    for parent in (entry.parent, *entry.parent.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_reparse_tag", 0):
            raise InstallError("junction parent must not be a link")
    if authorized_targets is not None:
        if not isinstance(authorized_targets, tuple) or not 1 <= len(authorized_targets) <= 8:
            raise InstallError("invalid explicit junction targets")
        targets = tuple(Path(target).resolve() for target in authorized_targets)
        if any(target in (root, entry) for target in targets):
            raise InstallError("invalid explicit junction targets")
        return _TargetBoundary(root, targets), entry
    return root, entry


class _FileIdInfo(ctypes.Structure):
    _fields_ = [("volume", ctypes.c_ulonglong), ("file_id", ctypes.c_ubyte * 16)]


def _identity(handle):
    call = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandleEx
    call.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    call.restype = wintypes.BOOL
    info = _FileIdInfo()
    if not call(handle, 18, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return info.volume, int.from_bytes(bytes(info.file_id), "little")


def _security(handle):
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    read = api.GetKernelObjectSecurity
    read.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD)]
    read.restype = wintypes.BOOL
    size = wintypes.DWORD()
    read(handle, 5, None, 0, ctypes.byref(size))
    if not 0 < size.value <= 65536:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(size.value)
    if not read(handle, 5, descriptor, size.value, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    control, revision = wintypes.WORD(), wintypes.DWORD()
    get_control = api.GetSecurityDescriptorControl
    get_control.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    get_control.restype = wintypes.BOOL
    if not get_control(descriptor, ctypes.byref(control), ctypes.byref(revision)):
        raise ctypes.WinError(ctypes.get_last_error())
    return descriptor.raw, bool(control.value & 0x1000)


def _restore_security(handle, descriptor, protected):
    apply = ctypes.WinDLL("advapi32", use_last_error=True).SetKernelObjectSecurity
    apply.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
    apply.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(descriptor)
    flags = 5 | (0x80000000 if protected else 0x20000000)
    if not apply(handle, flags, buffer):
        raise ctypes.WinError(ctypes.get_last_error())


@dataclass(frozen=True)
class _State:
    identity: tuple
    data: bytes
    security: bytes
    protected: bool


def _state(entry, root, handle=None):
    if handle is None:
        if not os.path.lexists(entry):
            return None
        if getattr(entry.lstat(), "st_reparse_tag", None) != _MOUNT_POINT:
            raise InstallError("refusing an unmanaged junction")
        with _open(entry) as opened:
            return _state(entry, root, opened)
    identity = _identity(handle)
    body = _control(handle, _GET)
    _authorize_target(_target(body), root)
    info = entry.lstat()
    if getattr(info, "st_reparse_tag", None) != _MOUNT_POINT or (info.st_dev, info.st_ino) != identity:
        raise InstallError("junction identity changed")
    descriptor, protected = _security(handle)
    return _State(identity, body, descriptor, protected)


def _delete_owned(entry, root, expected):
    with _open(entry, delete=True) as handle:
        if _state(entry, root, handle) != expected:
            raise InstallError("junction changed before deletion")
        call = ctypes.WinDLL("kernel32", use_last_error=True).SetFileInformationByHandle
        call.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        call.restype = wintypes.BOOL
        disposition = wintypes.BOOL(True)
        if not call(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
            raise ctypes.WinError(ctypes.get_last_error())


def _restore(entry, root, previous, expected):
    current = _state(entry, root)
    if current == previous:
        return
    if current != expected:
        raise InstallError("junction changed during rollback")
    if previous is None:
        _delete_owned(entry, root, expected)
    elif current is not None:
        with _open(entry) as handle:
            if _state(entry, root, handle) != expected:
                raise InstallError("junction changed during rollback")
            _control(handle, _SET, previous.data)
    else:
        temporary = entry.with_name(".opskit-junction-restore-" + uuid.uuid4().hex)
        temporary.mkdir()
        try:
            with _open(temporary, security_write=True) as handle:
                _control(handle, _SET, previous.data)
                _restore_security(handle, previous.security, previous.protected)
            temporary.rename(entry)
        finally:
            if os.path.lexists(temporary):
                temporary.rmdir()
    restored = _state(entry, root)
    if ((restored is None) != (previous is None) or
            (restored is not None and (restored.data, restored.security, restored.protected) !=
             (previous.data, previous.security, previous.protected))):
        raise InstallError("junction restoration did not persist")


class _Change:
    def __init__(self, entry, root, previous):
        self.entry, self.root, self.previous = entry, root, previous
        self.expected = previous
        self._guard = self._handle = None

    def close(self):
        if self._guard is not None:
            self._guard.__exit__(None, None, None)
            self._guard = self._handle = None

    def checkpoint(self, expected_target=None, *, removed=False, unchanged=False):
        self.close()
        if os.path.lexists(self.entry):
            self._guard = _open(self.entry)
            self._handle = self._guard.__enter__()
            current = _state(self.entry, self.root, self._handle)
        else:
            current = None
        if unchanged:
            if current != self.previous:
                raise InstallError("junction changed during fallback")
        elif removed:
            if current is not None:
                raise InstallError("junction removal did not persist")
        else:
            if current is None or expected_target is None or _target(current.data) != Path(expected_target).resolve():
                raise InstallError("junction does not match requested target")
            if self.previous is not None and (current.identity, current.security, current.protected) != (
                    self.previous.identity, self.previous.security, self.previous.protected):
                raise InstallError("junction identity or permissions changed during update")
        self.expected = current

    def verify(self):
        current = _state(self.entry, self.root, self._handle)
        if current != self.expected:
            raise InstallError("junction changed before commit")


@contextmanager
def junction_transaction(junction, managed_root, *, authorized_targets=None):
    """Compensate dependent writes; external targets require an exact allowlist."""
    with mutation_lock():
        root, entry = _root_and_entry(junction, managed_root, authorized_targets)
        previous = _state(entry, root)
        change = _Change(entry, root, previous)
        try:
            yield change
            change.verify()
        except BaseException:
            change.close()
            try:
                _restore(entry, root, previous, change.expected)
            except Exception as rollback_error:
                raise InstallError("junction operation failed; rollback incomplete") from rollback_error
            raise
        finally:
            change.close()


@versioned_mutation
def set_junction(junction, target, managed_root, *, authorized_targets=None):
    root, entry = _root_and_entry(junction, managed_root, authorized_targets)
    candidate = Path(target).absolute()
    if ".." in candidate.parts:
        raise InstallError("invalid junction target directory")
    _authorize_target(candidate, root)
    target = candidate.resolve(strict=True)
    _authorize_target(target, root)
    if not target.is_dir() or target in (Path(root), entry):
        raise InstallError("invalid junction target directory")
    replacement = _body(target)
    if os.path.lexists(entry):
        if getattr(entry.lstat(), "st_reparse_tag", None) != _MOUNT_POINT:
            raise InstallError("refusing to replace an unmanaged directory")
        with _open(entry) as handle:
            previous = _control(handle, _GET)
            _authorize_target(_target(previous), root)
            if _target(previous) != target:
                try:
                    _control(handle, _SET, replacement)
                except BaseException:
                    try:
                        current = _control(handle, _GET)
                        if current != previous:
                            if _target(current) != target:
                                raise InstallError("junction changed during rollback")
                            _control(handle, _SET, previous)
                    except Exception as rollback_error:
                        raise InstallError("junction operation failed; rollback incomplete") from rollback_error
                    raise
        return
    temporary = entry.with_name(".opskit-junction-" + uuid.uuid4().hex)
    temporary.mkdir()
    try:
        with _open(temporary) as handle:
            _control(handle, _SET, replacement)
        temporary.rename(entry)
    finally:
        if os.path.lexists(temporary):
            temporary.rmdir()


@versioned_mutation
def remove_junction(junction, managed_root, *, authorized_targets=None):
    root, entry = _root_and_entry(junction, managed_root, authorized_targets)
    previous = _state(entry, root)
    if previous is None:
        return
    _delete_owned(entry, root, previous)
