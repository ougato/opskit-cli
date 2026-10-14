"""Bound directory discovery before snapshotting untrusted plugin trees."""
import os
from pathlib import Path

SKIP_DIRS = frozenset({".git", "__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache"})


def bounded_paths(root, *, max_entries=8192, max_depth=64, skip_dirs=SKIP_DIRS, checkpoint=None):
    count = 0
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise ValueError("linked plugin roots forbidden")

    def walk(directory, depth):
        nonlocal count
        if checkpoint:
            checkpoint()
        with os.scandir(directory) as entries:
            for entry in entries:
                if checkpoint:
                    checkpoint()
                count += 1
                if count > max_entries or depth > max_depth:
                    raise ValueError("plugin tree limits exceeded")
                path = Path(entry.path)
                if entry.is_symlink() or getattr(path, "is_junction", lambda: False)():
                    raise ValueError("plugin links forbidden")
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in skip_dirs:
                        continue
                    yield path
                    yield from walk(path, depth + 1)
                elif entry.is_file(follow_symlinks=False):
                    yield path
                else:
                    raise ValueError("plugin special files forbidden")

    yield from walk(root, 1)
