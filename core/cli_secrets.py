"""Bounded explicit secret inputs; do not include secret values in errors."""
import os
import stat
import sys
from pathlib import Path

from core.private_file import verify_private_file

MAX_SECRET_BYTES = 65536


def export_secret(filename: str, data) -> None:
    """Serialize first, then publish a complete private file without replacement."""
    import json
    from core.private_export import publish_private_file
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    publish_private_file(Path(filename), payload)


def _input_identity(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_ino <= 0
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise ValueError("secret input must be a single-link regular file")
    if info.st_size > MAX_SECRET_BYTES:
        raise ValueError("secret input too large")
    # Windows path stat and descriptor stat can expose different legacy ctime
    # meanings. Use the explicit creation field there, not unlike timestamps.
    change_time = (getattr(info, "st_birthtime_ns", None)
                   if os.name == "nt" else info.st_ctime_ns)
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            change_time, info.st_mode)


def _read_secret_bytes(target):
    before = _input_identity(target.lstat())
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    descriptor = os.open(target, flags)
    try:
        if _input_identity(os.fstat(descriptor)) != before:
            raise ValueError("secret input changed while opening")
        verify_private_file(descriptor)
        # Keep ownership of the descriptor until every check completes, including
        # the pathname recheck. Nonblocking open prevents a replaced POSIX FIFO
        # from hanging before its type is rejected.
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            raw = source.read(MAX_SECRET_BYTES + 1)
        if (_input_identity(os.fstat(descriptor)) != before
                or _input_identity(target.lstat()) != before
                or len(raw) != before[2]):
            raise ValueError("secret input changed while reading")
        verify_private_file(descriptor)
        return raw
    finally:
        os.close(descriptor)


def read_secret_file(path: str) -> str:
    if path == "-":
        value = sys.stdin.read(MAX_SECRET_BYTES + 1)
    else:
        raw = _read_secret_bytes(Path(path))
        try:
            value = raw.decode("utf-8")
        except UnicodeError:
            raise ValueError("secret input must be UTF-8 text") from None
    if len(value.encode("utf-8")) > MAX_SECRET_BYTES:
        raise ValueError("secret input too large")
    value = value.strip()
    if not value:
        raise ValueError("secret input is empty")
    return value
