"""Windows account directories resolved by the OS rather than environment hints."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import uuid

from software.base import InstallError


def local_app_data():
    if os.name != "nt":
        raise InstallError("Windows account directory requires Windows")
    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]
    folder = GUID.from_buffer_copy(uuid.UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le)
    shell = ctypes.WinDLL("shell32")
    resolve = shell.SHGetKnownFolderPath
    resolve.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(wintypes.LPWSTR)]
    resolve.restype = ctypes.c_long
    token = wintypes.HANDLE()
    open_token = ctypes.WinDLL("advapi32", use_last_error=True).OpenProcessToken
    open_token.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    open_token.restype = wintypes.BOOL
    if not open_token(wintypes.HANDLE(-1), 0x0008 | 0x0004, ctypes.byref(token)):
        raise InstallError("Windows account token could not be opened")
    result = wintypes.LPWSTR()
    try:
        status = resolve(ctypes.byref(folder), 0, token, ctypes.byref(result))
        if status != 0 or not result:
            raise InstallError("Windows account directory could not be resolved")
        value = result.value
        if not value or len(value) > 32767 or not Path(value).is_absolute():
            raise InstallError("invalid Windows account directory")
        return Path(value)
    finally:
        free = ctypes.WinDLL("ole32").CoTaskMemFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = None
        free(ctypes.cast(result, ctypes.c_void_p))
        close = ctypes.WinDLL("kernel32").CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        close(token)
