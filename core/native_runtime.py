"""Private native runtime snapshots: executable data, never host plugin imports.

The operator supplies an already prepared Python directory. Dependency installation
and its hashed wheel lock are handled separately; this module binds the final bytes.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import stat

from core.plugin_tree import bounded_paths
from core.paths import absolute_path

MAX_BYTES = 512 * 1024**2
MAX_ENTRIES = 16384


def inventory(root: Path, *, allow_empty=False) -> dict[str, str]:
    """Hash ordinary bounded files; reject reparse points and changing input."""
    root = absolute_path(root)
    for parent in (root, *root.parents):
        info = parent.lstat()
        if getattr(info, "st_file_attributes", 0) & 0x400 or parent.is_symlink():
            raise ValueError("linked native runtime path")
    if not root.is_dir():
        raise ValueError("native runtime must be a directory")
    files = {}
    total = 0
    for path in bounded_paths(root, max_entries=MAX_ENTRIES, skip_dirs=frozenset()):
        before = path.lstat()
        if stat.S_ISDIR(before.st_mode):
            continue
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or getattr(before, "st_file_attributes", 0) & 0x400):
            raise ValueError("unsafe native runtime entry")
        total += before.st_size
        if total > MAX_BYTES:
            raise ValueError("native runtime exceeds size limit")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            import os
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError("native runtime changed while opening")
            consumed = 0
            while chunk := stream.read(min(1024**2, before.st_size - consumed + 1)):
                consumed += len(chunk)
                if consumed > before.st_size:
                    raise ValueError("native runtime grew during hashing")
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        if (consumed != before.st_size or (after.st_size, after.st_mtime_ns) !=
                (before.st_size, before.st_mtime_ns)):
            raise ValueError("native runtime changed while hashing")
        files[path.relative_to(root).as_posix()] = digest.hexdigest()
    if not files and not allow_empty:
        raise ValueError("empty native runtime")
    return files


def verify(root: Path, expected: dict[str, str], *, allow_empty=False) -> None:
    """Must be called on the private execution snapshot, not just its source."""
    if (not isinstance(expected, dict) or (not expected and not allow_empty)
            or inventory(root, allow_empty=allow_empty) != expected):
        raise ValueError("native runtime content does not match prepared inventory")


def stage(source: Path, destination: Path, expected: dict[str, str], *, allow_empty=False) -> None:
    """Create a fresh owned snapshot and verify the copied bytes before use."""
    import shutil
    source, destination = absolute_path(source), absolute_path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("native runtime destination must not exist")
    if source == destination or source in destination.parents:
        raise ValueError("native runtime destination overlaps source")
    verify(source, expected, allow_empty=allow_empty)
    destination.mkdir(parents=False)
    for path in bounded_paths(source, max_entries=MAX_ENTRIES, skip_dirs=frozenset()):
        if path.is_dir():
            (destination / path.relative_to(source)).mkdir(parents=True, exist_ok=True)
    for relative in expected:
        original = source / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # Copy only names authenticated by the complete ordinary-file inventory.
        shutil.copyfile(original, target, follow_symlinks=False)
    verify(destination, expected, allow_empty=allow_empty)
