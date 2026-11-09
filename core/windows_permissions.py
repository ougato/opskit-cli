"""Preserve Windows file DACLs without changing ownership or widening access."""
import os

from core.file_identity import same_file_identity


def _descriptor(path):
    if os.name != "nt":
        raise NotImplementedError("Windows security descriptors require Windows")
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    read = api.GetFileSecurityW
    read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD)]
    read.restype = wintypes.BOOL
    size = wintypes.DWORD()
    read(str(path), 4, None, 0, ctypes.byref(size))
    if not 0 < size.value <= 65536:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(size.value)
    if not read(str(path), 4, descriptor, size.value, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    control = wintypes.WORD()
    revision = wintypes.DWORD()
    get_control = api.GetSecurityDescriptorControl
    get_control.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    get_control.restype = wintypes.BOOL
    if not get_control(descriptor, ctypes.byref(control), ctypes.byref(revision)):
        raise ctypes.WinError(ctypes.get_last_error())
    return descriptor, bool(control.value & 0x1000)


def copy_dacl(source, target):
    """Copy access rules and their inheritance protection; retain target owner."""
    import ctypes
    from ctypes import wintypes
    descriptor, protected = _descriptor(source)
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    apply = api.SetFileSecurityW
    apply.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    apply.restype = wintypes.BOOL
    flags = 4 | (0x80000000 if protected else 0x20000000)
    if not apply(str(target), flags, descriptor):
        raise ctypes.WinError(ctypes.get_last_error())


def copy_dacl_to_verified_file(source, target, expected):
    """Apply access rules through a handle to the verified staged file."""
    import ctypes
    import msvcrt
    import stat
    from ctypes import wintypes

    descriptor, protected = _descriptor(source)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(target), 0x00020000 | 0x00040000, 3, None, 3,
                    0x00200000 | 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    try:
        actual = os.fstat(fd)
        if (not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1
                or getattr(actual, "st_file_attributes", 0) & 0x400
                or not same_file_identity(actual, expected)):
            raise RuntimeError("replacement staging identity changed")
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        apply = api.SetKernelObjectSecurity
        apply.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
        apply.restype = wintypes.BOOL
        flags = 4 | (0x80000000 if protected else 0x20000000)
        if not apply(msvcrt.get_osfhandle(fd), flags, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        os.close(fd)
