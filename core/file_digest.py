"""Streaming SHA256 helpers for downloaded and published artifacts."""
import hashlib
from pathlib import Path


def file_digest(path: Path) -> str:
    with path.open("rb") as handle:
        return stream_digest(handle)


def stream_digest(handle) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(65536), b""):
        digest.update(chunk)
    return digest.hexdigest()
