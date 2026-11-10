"""Bind lock-directory permission changes to a verified directory handle."""
import os
import stat

from core.file_identity import same_file_identity


def restrict_lock_directory(path, previous, error_type):
    """Keep existing lock privacy without chmod/ACL changes through a swapped name."""
    if os.name == "nt":
        _restrict_windows(path, previous, error_type)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
        if (not stat.S_ISDIR(opened.st_mode) or not stat.S_ISDIR(current.st_mode)
                or not same_file_identity(opened, current)
                or previous is not None and not same_file_identity(opened, previous)):
            raise error_type("lock directory identity changed")
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)


def _restrict_windows(path, previous, error_type):
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    # Open the directory itself without following reparse points. Do not infer
    # that omitting delete sharing prevents a directory rename on all systems.
    handle = create(str(path), 0x00020080 | 0x00040000, 3, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    try:
        _restrict_open_windows_directory(path, descriptor, previous, error_type)
    finally:
        os.close(descriptor)


def create_windows_lock_directory(path, error_type):
    """Create the final directory and obtain its handle in one native operation."""
    descriptor = _create_windows_directory(path)
    try:
        _restrict_open_windows_directory(path, descriptor, None, error_type)
    finally:
        os.close(descriptor)


def _create_windows_directory(path):
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class UnicodeString(ctypes.Structure):
        _fields_ = [("length", wintypes.USHORT), ("maximum_length", wintypes.USHORT),
                    ("buffer", wintypes.LPWSTR)]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [("length", wintypes.ULONG), ("root", wintypes.HANDLE),
                    ("name", ctypes.POINTER(UnicodeString)), ("attributes", wintypes.ULONG),
                    ("security", ctypes.c_void_p), ("quality", ctypes.c_void_p)]

    class IoStatus(ctypes.Structure):
        _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]

    native = ctypes.WinDLL("ntdll", use_last_error=True)
    convert = native.RtlDosPathNameToNtPathName_U_WithStatus
    convert.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(UnicodeString), ctypes.c_void_p, ctypes.c_void_p]
    convert.restype = ctypes.c_long
    release = native.RtlFreeUnicodeString
    release.argtypes = [ctypes.POINTER(UnicodeString)]
    release.restype = None
    name = UnicodeString()
    if convert(str(path.absolute()), ctypes.byref(name), None, None) < 0:
        raise OSError("could not convert lock directory path")
    try:
        create = native.NtCreateFile
        create.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG,
                           ctypes.POINTER(ObjectAttributes), ctypes.POINTER(IoStatus),
                           ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
                           wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
        create.restype = ctypes.c_long
        attributes = ObjectAttributes(ctypes.sizeof(ObjectAttributes), None, ctypes.pointer(name),
                                      0x40, None, None)  # OBJ_CASE_INSENSITIVE
        handle = wintypes.HANDLE()
        status = IoStatus()
        # FILE_CREATE is exclusive and returns the new directory handle in the
        # same operation. Read/write sharing matches the existing-directory
        # path; the handle is closed after the ACL is applied.
        # Access: SYNCHRONIZE | READ_CONTROL | WRITE_DAC | FILE_READ_ATTRIBUTES.
        # Options: DIRECTORY_FILE | SYNCHRONOUS_IO_NONALERT | OPEN_REPARSE_POINT.
        result = create(ctypes.byref(handle), 0x00160080, ctypes.byref(attributes),
                        ctypes.byref(status), None, 0x10, 3, 2, 0x00200021, None, 0)
        if result < 0:
            translate = native.RtlNtStatusToDosError
            translate.argtypes = [wintypes.ULONG]
            translate.restype = wintypes.ULONG
            raise ctypes.WinError(translate(result & 0xffffffff))
    finally:
        release(ctypes.byref(name))
    try:
        return msvcrt.open_osfhandle(handle.value, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.CloseHandle(handle)
        raise


def _restrict_open_windows_directory(path, descriptor, previous, error_type):
    import ctypes
    from ctypes import wintypes
    import msvcrt

    opened = os.fstat(descriptor)
    current = path.lstat()
    if (not stat.S_ISDIR(opened.st_mode) or not stat.S_ISDIR(current.st_mode)
            or getattr(opened, "st_file_attributes", 0) & 0x400
            or getattr(current, "st_file_attributes", 0) & 0x400
            or not same_file_identity(opened, current)
            or previous is not None and not same_file_identity(opened, previous)):
        raise error_type("lock directory identity changed")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    convert = api.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                        ctypes.POINTER(wintypes.ULONG)]
    convert.restype = wintypes.BOOL
    apply = api.SetKernelObjectSecurity
    apply.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
    apply.restype = wintypes.BOOL
    security = ctypes.c_void_p()
    if not convert("D:P(A;OICI;FA;;;OW)", 1, ctypes.byref(security), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not apply(msvcrt.get_osfhandle(descriptor), 4 | 0x80000000, security):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        free = kernel.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(security)
