"""Use an existing NTFS short name without changing workspace ownership.

The handles protect the path mapping until the isolated worker and its ACL
cleanup finish. Runtime contents retain the ordinary snapshot verification
rules; pinning a path does not freeze file contents.
"""
from contextlib import contextmanager, ExitStack
import os
from pathlib import Path


def _short_path(path):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetShortPathNameW.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetShortPathNameW(str(path), buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError("short path unavailable")
    return Path(buffer.value)


def _local_ntfs(path):
    import ctypes
    from ctypes import wintypes
    anchor = path.anchor
    # Do not infer alias stability on network paths or other filesystems.
    if (len(anchor) != 7 or not anchor.startswith("\\\\?\\")
            or not anchor[4].isascii() or not anchor[4].isalpha() or anchor[5:] != ":\\"):
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumeInformationW.restype = wintypes.BOOL
    fs = ctypes.create_unicode_buffer(64)
    return (kernel.GetDriveTypeW(anchor) == 3 and
            bool(kernel.GetVolumeInformationW(anchor, None, 0, None, None, None, fs, len(fs))) and
            fs.value.upper() == "NTFS")


def _checked_identity(info, *, directory):
    if info.attributes & 0x400:
        raise ValueError("short execution path contains a reparse point")
    if (bool(info.attributes & 0x10) != directory or not info.volume
            or not (info.index_high or info.index_low) or (not directory and info.links != 1)):
        raise ValueError("short execution path has invalid file identity or type")
    return info.volume, info.index_high, info.index_low


def _pin_identity(path, stack, *, directory):
    import ctypes
    from ctypes import wintypes
    class Information(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
                    ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME),
                    ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                    ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(Information)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    # Attribute-only access does not enforce delete sharing on Windows.
    handle = kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    stack.callback(kernel.CloseHandle, handle)
    info = Information()
    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return _checked_identity(info, directory=directory)


@contextmanager
def short_execution_root(root, executable_relative):
    """Yield a shorter equivalent execution root, or the unchanged root.

    This only optimizes existing local NTFS 8.3 names. The caller must keep the
    context open through process exit and ACL restoration.
    """
    root = Path(root)
    relative = Path(executable_relative)
    if relative.anchor or relative.drive or not relative.parts or ".." in relative.parts:
        raise ValueError("invalid executable relative path")
    if (os.name != "nt" or len(str(root / relative).encode("utf-16-le")) // 2 < 200
            or not _local_ntfs(root)):
        yield root
        return
    try:
        short = _short_path(root)
    except OSError:
        yield root
        return
    if (not short.is_absolute() or short.anchor.casefold() != root.anchor.casefold()
            or ".." in short.parts):
        raise ValueError("invalid short execution path")
    if len(str(short).encode("utf-16-le")) >= len(str(root).encode("utf-16-le")):
        yield root
        return
    original = root / relative
    alias = short / relative
    originals = list(reversed((original, *original.parents)))
    aliases = list(reversed((alias, *alias.parents)))
    if len(originals) != len(aliases):
        raise ValueError("short execution path has different ancestry")
    stack = ExitStack()
    try:
        try:
            for index, (long_part, short_part) in enumerate(zip(originals, aliases)):
                is_directory = index < len(originals) - 1
                if (_pin_identity(long_part, stack, directory=is_directory) !=
                        _pin_identity(short_part, stack, directory=is_directory)):
                    raise ValueError("short execution path identity mismatch")
        except OSError:
            # Failure to acquire all pins means this optimization is unavailable.
            stack.close()
            yield root
            return
        with stack:
            yield short
    finally:
        stack.close()
