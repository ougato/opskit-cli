"""Bounded, nonrecursive filesystem primitives for private update caches."""
import os
from pathlib import Path
import stat


def entries(directory, limit=16):
    result = []
    with os.scandir(directory) as iterator:
        for entry in iterator:
            if len(result) >= limit:
                raise ValueError("update cache inventory exceeds limit")
            result.append(Path(entry.path))
    return result


def info(path):
    from core import updater
    updater._update_record_path(path)
    result = path.stat()
    if not stat.S_ISDIR(result.st_mode) and (not stat.S_ISREG(result.st_mode) or result.st_nlink != 1):
        raise ValueError("unsafe update cache file")
    return result


def unlink_owned(path, previous):
    current = info(path)
    if (current.st_dev, current.st_ino, current.st_size) != (previous.st_dev, previous.st_ino, previous.st_size):
        raise ValueError("update cache file changed during cleanup")
    path.unlink()
