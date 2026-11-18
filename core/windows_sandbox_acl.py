"""Grant one AppContainer SID access to an exclusively owned staging tree.

Never apply this helper to a user's project or shared interpreter. Host resources
are brokered separately. The staging tree must remain host-owned while in use.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import stat


@contextmanager
def staged_access(root: Path, sid, *, writable: bool = False):
    if os.name != "nt":
        raise NotImplementedError("AppContainer ACLs require Windows")
    root = Path(root).absolute()
    from core.windows_permissions import _descriptor
    from core.plugin_tree import bounded_paths
    # Validate the complete bounded tree before modifying any DACL.
    for parent in (root, *root.parents):
        info = parent.lstat()
        if getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("sandbox staging ancestors must not be reparse points")
    paths = [root, *bounded_paths(root, max_entries=8192, skip_dirs=frozenset())]
    snapshots = []
    for path in paths:
        info = path.lstat()
        if (getattr(info, "st_file_attributes", 0) & 0x400
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
            raise ValueError("unsafe sandbox staging entry")
        snapshots.append((path, _descriptor(path), (info.st_dev, info.st_ino)))
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    class Trustee(ctypes.Structure):
        _fields_ = [("multiple", ctypes.c_void_p), ("multiple_operation", ctypes.c_int),
                    ("form", ctypes.c_int), ("kind", ctypes.c_int), ("name", ctypes.c_void_p)]
    class ExplicitAccess(ctypes.Structure):
        _fields_ = [("permissions", wintypes.DWORD), ("mode", ctypes.c_int),
                    ("inheritance", wintypes.DWORD), ("trustee", Trustee)]
    api.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                                             ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    api.SetEntriesInAclW.argtypes = [wintypes.ULONG, ctypes.POINTER(ExplicitAccess),
                                     ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    api.InitializeSecurityDescriptor.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    api.SetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                             ctypes.c_void_p, wintypes.BOOL]
    apply = api.SetFileSecurityW
    apply.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    changed = []
    try:
        for path, original, file_id in snapshots:
            current = path.lstat()
            if (current.st_dev, current.st_ino) != file_id:
                raise ValueError("sandbox staging changed during ACL preparation")
            original_descriptor, _ = original
            present, defaulted = wintypes.BOOL(), wintypes.BOOL()
            old_acl, new_acl = ctypes.c_void_p(), ctypes.c_void_p()
            if not api.GetSecurityDescriptorDacl(original_descriptor, ctypes.byref(present),
                                                  ctypes.byref(old_acl), ctypes.byref(defaulted)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not present or not old_acl.value:
                raise ValueError("sandbox staging must have an explicit DACL")
            entry = ExplicitAccess(0x1f01ff if writable else 0x1200a9, 1,
                                   3 if path.is_dir() else 0,
                                   Trustee(None, 0, 0, 0, sid))
            status = api.SetEntriesInAclW(1, ctypes.byref(entry), old_acl, ctypes.byref(new_acl))
            if status:
                raise ctypes.WinError(status)
            try:
                descriptor = ctypes.create_string_buffer(64)
                if not api.InitializeSecurityDescriptor(descriptor, 1) or not api.SetSecurityDescriptorDacl(
                        descriptor, True, new_acl, False):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not apply(str(path), 4 | 0x80000000, descriptor):
                    raise ctypes.WinError(ctypes.get_last_error())
                changed.append((path, original, file_id))
            finally:
                kernel.LocalFree(new_acl)
        yield
    finally:
        first_error = None
        for path, (descriptor, protected), file_id in reversed(changed):
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) != file_id:
                    raise ValueError("sandbox staging replaced before ACL restoration")
                if not apply(str(path), 4 | (0x80000000 if protected else 0x20000000), descriptor):
                    raise ctypes.WinError(ctypes.get_last_error())
            except Exception as error:
                first_error = first_error or error
        if first_error is not None:
            raise first_error
