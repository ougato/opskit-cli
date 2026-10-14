"""Owned archive staging with atomic publication that cannot replace a version."""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time


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
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        rename = getattr(library, "renameat2", None)
        if rename is None:
            raise OSError("atomic non-replacing directory publication is unavailable")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(-100, encode(source), -100, encode(destination), 1)
    elif sys.platform == "darwin":
        rename = getattr(library, "renamex_np", None)
        if rename is None:
            raise OSError("atomic non-replacing directory publication is unavailable")
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(encode(source), encode(destination), 4)
    else:
        raise OSError("atomic non-replacing directory publication is unsupported")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


@contextmanager
def staged_directory(destination: Path, *, prefix, utf8_paths=False):
    destination = Path(destination)
    if os.path.lexists(destination):
        raise FileExistsError("version directory already exists; refusing to overwrite")
    for parent in destination.parents:
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ValueError("version directory parent must not be a link")
    def filesystem_path(path):
        return str(path).encode("utf-8") if utf8_paths else path

    os.makedirs(filesystem_path(destination.parent), exist_ok=True)
    work = tempfile.mkdtemp(
        prefix=prefix.encode("utf-8") if utf8_paths else prefix,
        dir=filesystem_path(destination.parent),
    )
    payload = Path(work.decode("utf-8") if isinstance(work, bytes) else work) / "payload"
    try:
        yield payload
        publish_new_directory(payload, destination, utf8_paths=utf8_paths)
    finally:
        shutil.rmtree(work)
