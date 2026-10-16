"""Private, exclusive publication; no pathname-based permission mutation."""
import errno
import os
from pathlib import Path, PureWindowsPath
import secrets
import stat
import sys

from core.private_file import verify_private_file


def publish_private_file(target, payload):
    target = Path(target)
    if not target.name:
        raise ValueError("secret output requires a filename")
    if os.name == "nt":
        if (":" in target.name or target.name.endswith((".", " "))
                or PureWindowsPath(target.name).is_reserved()):
            raise ValueError("invalid secret output filename")
        _windows_publish(Path(os.path.abspath(target)), payload)
    elif sys.platform.startswith("linux") or sys.platform == "darwin":
        _posix_publish(Path(os.path.abspath(target)), payload)
    else:
        raise ValueError("private export unsupported on this platform")


def _write(descriptor, payload):
    with os.fdopen(descriptor, "wb", closefd=False) as output:
        output.write(payload)
        output.flush()
        os.fsync(descriptor)


def _same_object(left, right):
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _verify_posix_directory(descriptor):
    current = os.fstat(descriptor)
    if (not stat.S_ISDIR(current.st_mode) or current.st_uid != os.geteuid()
            or current.st_mode & 0o022):
        raise ValueError("secret output directory must be owned and not shared writable")
    if sys.platform == "darwin":
        from core.private_file import _darwin_permissions
        _darwin_permissions(descriptor)


def _posix_publish(target, payload):
    parent = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    temporary = ".opskit-secret-" + secrets.token_hex(16)
    published = False
    try:
        directory = os.fstat(parent)
        def check_parent():
            _verify_posix_directory(parent)
            if not _same_object(os.lstat(target.parent), directory):
                raise ValueError("secret output directory changed")

        check_parent()
        descriptor = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        identity = os.fstat(descriptor)
        verify_private_file(descriptor)
        _write(descriptor, payload)
        verify_private_file(descriptor)
        check_parent()
        # link is an atomic no-replace publication, unlike rename/replace.
        os.link(temporary, target.name, src_dir_fd=parent, dst_dir_fd=parent,
                follow_symlinks=False)
        published = True
        os.unlink(temporary, dir_fd=parent)
        check_parent()
        os.fsync(parent)
        check_parent()
    except BaseException:
        if published and _can_clean_posix_directory(parent):
            try:
                current = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if _same_object(current, identity):
                    os.unlink(target.name, dir_fd=parent)
                    os.fsync(parent)
        raise
    finally:
        try:
            if descriptor is not None and _can_clean_posix_directory(parent):
                try:
                    current = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if _same_object(current, os.fstat(descriptor)):
                        os.unlink(temporary, dir_fd=parent)
        finally:
            try:
                if descriptor is not None:
                    os.close(descriptor)
            finally:
                os.close(parent)


def _can_clean_posix_directory(descriptor):
    try:
        _verify_posix_directory(descriptor)
    except (OSError, ValueError):
        # POSIX has no portable delete-by-file-handle. Once foreign writers
        # gain access, a stat/unlink pair is unsafe even with an identity check.
        # Preserve the private object rather than risking deletion of a swap.
        return False
    return True


def _windows_publish(target, payload):
    import ctypes
    from ctypes import wintypes
    import msvcrt
    from core.private_file import _windows_permissions

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    native = ctypes.WinDLL("ntdll")
    pointer = ctypes.c_void_p
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, pointer,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    get_info = kernel.GetFileInformationByHandleEx
    get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD]
    get_info.restype = wintypes.BOOL
    set_info = kernel.SetFileInformationByHandle
    set_info.argtypes = [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD]
    set_info.restype = wintypes.BOOL
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(pointer), pointer]
    convert.restype = wintypes.BOOL
    free = kernel.LocalFree
    free.argtypes = [pointer]
    free.restype = pointer

    class IoStatus(ctypes.Structure):
        _fields_ = [("status", pointer), ("information", ctypes.c_size_t)]

    rename = native.NtSetInformationFile
    rename.argtypes = [wintypes.HANDLE, ctypes.POINTER(IoStatus), pointer,
                       wintypes.ULONG, ctypes.c_int]
    rename.restype = ctypes.c_long
    dos_error = native.RtlNtStatusToDosError
    dos_error.argtypes = [ctypes.c_long]
    dos_error.restype = wintypes.ULONG

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", pointer),
                    ("inherit", wintypes.BOOL)]

    class RenameInfo(ctypes.Structure):
        _fields_ = [("replace", ctypes.c_ubyte), ("root", wintypes.HANDLE),
                    ("length", wintypes.DWORD), ("name", wintypes.WCHAR * 1)]

    def checked(handle):
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def open_parent():
        # No delete sharing: the opened directory itself cannot be renamed.
        return checked(create(str(target.parent), 0x20083, 3, None, 3, 0x02200000, None))

    def parent_identity(handle):
        attributes = ctypes.create_string_buffer(8)
        identity = ctypes.create_string_buffer(24)
        if (not get_info(handle, 9, attributes, len(attributes))
                or not get_info(handle, 18, identity, len(identity))):
            raise ctypes.WinError(ctypes.get_last_error())
        flags = ctypes.c_uint32.from_buffer(attributes).value
        if not flags & 0x10 or flags & 0x400:
            raise ValueError("secret output directory must not be a reparse point")
        return identity.raw

    parent = open_parent()
    handle = None
    descriptor = None
    committed = False
    try:
        identity = parent_identity(parent)
        def check_parent():
            try:
                _windows_permissions(None, handle=parent, directory=True)
            except ValueError:
                raise ValueError("secret output directory permissions are not protected") from None
            current = open_parent()
            try:
                if parent_identity(current) != identity:
                    raise ValueError("secret output directory changed")
            finally:
                close(current)

        check_parent()
        security = pointer()
        if not convert("D:P(A;;FA;;;OW)(A;;FA;;;SY)(A;;FA;;;BA)", 1,
                       ctypes.byref(security), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), security, False)
            temporary = target.parent / (".opskit-secret-" + secrets.token_hex(16))
            # ACL applies at creation, before the first secret byte. DELETE is
            # needed for handle-bound rename and cleanup; no sharing is allowed.
            handle = checked(create(str(temporary), 0xC0030000, 0, ctypes.byref(attributes),
                                    1, 0x00200000, None))
        finally:
            free(security)
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        check_parent()
        verify_private_file(descriptor)
        _write(descriptor, payload)
        verify_private_file(descriptor)
        check_parent()
        name = target.name.encode("utf-16-le")
        buffer = ctypes.create_string_buffer(max(ctypes.sizeof(RenameInfo), RenameInfo.name.offset + len(name)))
        info = RenameInfo.from_buffer(buffer)
        info.replace, info.root, info.length = False, parent, len(name)
        ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.name.offset, name, len(name))
        # The native rename API supports a directory handle and a relative
        # name. Win32 SetFileInformationByHandle requires RootDirectory=NULL.
        status = rename(handle, ctypes.byref(IoStatus()), buffer, len(buffer), 10)
        if status < 0:
            code = dos_error(status)
            if code in (80, 183):
                raise FileExistsError(errno.EEXIST, "secret output already exists")
            raise ctypes.WinError(code)
        check_parent()
        os.fsync(descriptor)
        check_parent()
        committed = True
    finally:
        try:
            if handle is not None and not committed:
                disposition = wintypes.BOOL(True)
                if not set_info(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
                    raise ctypes.WinError(ctypes.get_last_error())
        finally:
            try:
                if descriptor is not None:
                    os.close(descriptor)
                elif handle is not None:
                    close(handle)
            finally:
                close(parent)
