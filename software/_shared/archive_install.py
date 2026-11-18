"""Owned archive staging with atomic publication that cannot replace a version."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import tempfile

from core.directory_publish import publish_new_directory


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
