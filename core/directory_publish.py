"""Native atomic directory publication without replacing any existing target."""
import ctypes
import os
from pathlib import Path
import platform
import sys
import time


def _linux_publish(library, source: bytes, destination: bytes):
    rename = getattr(library, "renameat2", None)
    if rename is not None:
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        return rename(-100, source, -100, destination, 1)
    # Older glibc lacks the wrapper even when the kernel supports RENAME_NOREPLACE.
    # Limit syscall numbers to the two supported native 64-bit Linux ABIs.
    syscall = getattr(library, "syscall", None)
    if syscall is None or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise OSError("atomic non-replacing directory publication is unavailable")
    number = {"x86_64": 316, "aarch64": 276}.get(platform.machine())
    if number is None:
        raise OSError("atomic non-replacing directory publication is unavailable")
    syscall.argtypes = [ctypes.c_long, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    syscall.restype = ctypes.c_long
    return syscall(number, -100, source, -100, destination, 1)


def publish_new_directory(source: Path, destination: Path, *, utf8_paths=False):
    """No check-then-replacing-rename fallback on POSIX."""
    if os.name == "nt":
        for attempt in range(5):
            try:
                source.rename(destination)
                break
            except PermissionError as exc:
                # Every attempt uses the native non-replacing operation.
                # File scanners can briefly hold a child without delete sharing.
                if os.path.lexists(destination):
                    raise FileExistsError("version directory already exists; refusing to overwrite") from exc
                if getattr(exc, "winerror", None) not in (5, 32) or attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
        return
    encode = (lambda path: str(path).encode("utf-8")) if utf8_paths else os.fsencode
    source_bytes, destination_bytes = encode(source), encode(destination)
    if b"\0" in source_bytes or b"\0" in destination_bytes:
        raise ValueError("directory publication paths cannot contain null bytes")
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        result = _linux_publish(library, source_bytes, destination_bytes)
    elif sys.platform == "darwin":
        rename = getattr(library, "renamex_np", None)
        if rename is None:
            raise OSError("atomic non-replacing directory publication is unavailable")
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(source_bytes, destination_bytes, 4)
    else:
        raise OSError("atomic non-replacing directory publication is unsupported")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


