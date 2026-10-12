"""Bounded archive extraction, without links or directory traversal."""
from __future__ import annotations

import shutil
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

MAX_EXTRACT_BYTES = 2 * 1024**3
MAX_EXTRACT_FILES = 100_000


def _target(root: Path, name: str) -> Path:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or any(":" in p for p in path.parts):
        raise ValueError("unsafe archive path")
    target = root.joinpath(*path.parts).resolve()
    target.relative_to(root)
    return target


def extract_tar(archive: tarfile.TarFile, dest: str | Path) -> None:
    root = Path(dest).resolve()
    members = archive.getmembers()
    if len(members) > MAX_EXTRACT_FILES or sum(m.size for m in members) > MAX_EXTRACT_BYTES:
        raise ValueError("archive exceeds extraction limits")
    for member in members:
        _target(root, member.name)
        if not (member.isfile() or member.isdir()):
            raise ValueError("archive links and special files are forbidden")
    root.mkdir(parents=True, exist_ok=True)
    for member in members:
        target = _target(root, member.name)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("archive file missing")
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)


def extract_zip(archive: zipfile.ZipFile, dest: str | Path) -> None:
    root = Path(dest).resolve()
    members = archive.infolist()
    if len(members) > MAX_EXTRACT_FILES or sum(m.file_size for m in members) > MAX_EXTRACT_BYTES:
        raise ValueError("archive exceeds extraction limits")
    for member in members:
        _target(root, member.filename)
        mode = member.external_attr >> 16
        if mode & 0o170000 not in (0, 0o100000, 0o040000):
            raise ValueError("archive links and special files are forbidden")
    root.mkdir(parents=True, exist_ok=True)
    for member in members:
        target = _target(root, member.filename)
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
