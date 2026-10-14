"""Bounded explicit secret inputs; do not include secret values in errors."""
import sys
from pathlib import Path

MAX_SECRET_BYTES = 65536


def export_secret(filename: str, data) -> None:
    """Create an exclusive private file; never overwrite an existing destination."""
    import json
    import os
    from core.secrets import restrict_permissions
    target = Path(filename)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        restrict_permissions(target)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            fd = -1
            json.dump(data, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        if fd != -1:
            os.close(fd)
        target.unlink(missing_ok=True)
        raise


def read_secret_file(path: str) -> str:
    if path == "-":
        value = sys.stdin.read(MAX_SECRET_BYTES + 1)
    else:
        target = Path(path)
        if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()) or not target.is_file():
            raise ValueError("secret input must be a regular file")
        with target.open("rb") as source:
            raw = source.read(MAX_SECRET_BYTES + 1)
        if len(raw) > MAX_SECRET_BYTES:
            raise ValueError("secret input too large")
        value = raw.decode("utf-8")
    if len(value.encode("utf-8")) > MAX_SECRET_BYTES:
        raise ValueError("secret input too large")
    value = value.strip()
    if not value:
        raise ValueError("secret input is empty")
    return value
