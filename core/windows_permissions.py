"""Preserve Windows file DACLs without changing ownership or widening access."""
import os


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
