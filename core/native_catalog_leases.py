"""Per-reader catalog leases; callers serialize entry/inspection with catalog lock."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import uuid

from core.plugin_state import _directory
from core.resource_lock import ResourceBusyError, ResourceLock

MAX_READERS = 256


def _root(catalog, identity):
    if not isinstance(identity, str) or re.fullmatch(r"[0-9a-f]{32}", identity) is None:
        raise ValueError("invalid native catalog object")
    return Path(catalog) / "readers" / identity


def _plain_lease(path):
    for parent in path.parents:
        _directory(parent)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 0
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise ValueError("unsafe native catalog reader lease")


def require_inactive(catalog, identity):
    """Refuse active/unknown leases; retire only abandoned ordinary lease files.

    The caller holds the catalog lock, preventing new readers during this check
    and the subsequent deletion. Reader exit only removes its own lease file.
    """
    root = _root(catalog, identity)
    try:
        _directory(root)
    except FileNotFoundError:
        return
    with os.scandir(root) as entries:
        names = []
        for index, entry in enumerate(entries):
            if index >= MAX_READERS or re.fullmatch(r"[0-9a-f]{32}\.lock", entry.name) is None:
                raise ValueError("invalid native catalog reader inventory")
            names.append(entry.name)
    for name in names:
        path = root / name
        try:
            _plain_lease(path)
        except FileNotFoundError:
            continue  # A reader exited while inventory was being collected.
        with ResourceLock(lambda: path, label="native catalog reader").hold():
            _plain_lease(path)
        path.unlink(missing_ok=True)


@contextmanager
def reader(catalog, identity):
    """Enter under the catalog lock; release it before yielding to the reader."""
    root = _root(catalog, identity)
    # Bound active and abandoned lease count without treating a busy reader as
    # an error: many concurrent readers of the same immutable object are valid.
    if root.exists():
        for parent in (root, *root.parents):
            _directory(parent)
        names = []
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_READERS:
                    raise ValueError("native catalog reader capacity exhausted")
                if re.fullmatch(r"[0-9a-f]{32}\.lock", entry.name) is None:
                    raise ValueError("invalid native catalog reader inventory")
                try:
                    _plain_lease(root / entry.name)
                except FileNotFoundError:
                    continue
                names.append(entry.name)
        active = 0
        for name in names:
            previous = root / name
            try:
                _plain_lease(previous)
                with ResourceLock(lambda: previous, label="native catalog reader").hold():
                    _plain_lease(previous)
                previous.unlink(missing_ok=True)
            except FileNotFoundError:
                continue
            except ResourceBusyError:
                active += 1
        if active >= MAX_READERS:
            raise ValueError("native catalog reader capacity exhausted")
    path = root / (uuid.uuid4().hex + ".lock")
    try:
        with ResourceLock(lambda: path, label="native catalog reader").hold():
            yield
    finally:
        try:
            _plain_lease(path)
            path.unlink()
        except FileNotFoundError:
            pass
