"""Bounded logical workspace inventory; callers hold the platform budget lock."""
from dataclasses import dataclass
import os
from pathlib import Path
import stat

from core.plugin_state import _plain, _directory

MAX_WORKSPACES = 8
WORKSPACE_BYTES = 192 * 1024**2
WORKSPACE_ENTRIES = 16388
MAX_WORKSPACE_BYTES = MAX_WORKSPACES * WORKSPACE_BYTES
MAX_WORKSPACE_ENTRIES = MAX_WORKSPACES * WORKSPACE_ENTRIES
MAX_PARENT_ENTRIES = 16384
MAX_DEPTH = 66


@dataclass(frozen=True)
class WorkspaceBudget:
    payload_bytes: int
    charged_bytes: int
    charged_entries: int
    workspaces: int


def _version(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def reserve_workspace(parent):
    """Reserve one full snapshot, including every retained/unknown entry.

    O(entries) time and O(depth) traversal space, both capped. Existing direct
    directories each reserve a full growing workspace. This is logical size,
    not filesystem blocks, free-space reservation or a machine-wide quota.
    """
    parent = Path(parent).absolute()
    if parent.resolve() != parent:
        raise ValueError("workspace budget parent must be canonical")
    for ancestor in (parent, *parent.parents):
        _directory(ancestor)
    visited = 0

    def scan(directory, depth):
        nonlocal visited
        if depth > MAX_DEPTH:
            raise ValueError("workspace depth quota exhausted")
        before = directory.lstat()
        if not _plain(before, stat.S_ISDIR):
            raise ValueError("unsafe retained workspace")
        payload = entries = 0
        with os.scandir(directory) as children:
            for child in children:
                visited += 1
                entries += 1
                if visited + WORKSPACE_ENTRIES > MAX_WORKSPACE_ENTRIES:
                    raise ValueError("workspace entry quota exhausted")
                path = directory / child.name
                info = path.lstat()
                if _plain(info, stat.S_ISDIR):
                    size, count = scan(path, depth + 1)
                    payload += size
                    entries += count
                elif _plain(info, stat.S_ISREG) and info.st_nlink == 1:
                    payload += info.st_size
                    if _version(info) != _version(path.lstat()):
                        raise ValueError("workspace file changed")
                else:
                    raise ValueError("unsafe retained workspace")
                if payload + WORKSPACE_BYTES > MAX_WORKSPACE_BYTES:
                    raise ValueError("workspace byte quota exhausted")
        if _version(before) != _version(directory.lstat()):
            raise ValueError("workspace directory changed")
        return payload, entries

    before = parent.lstat()
    payload = charged = entries = count = 0
    with os.scandir(parent) as children:
        for index, child in enumerate(children, 1):
            if index > MAX_PARENT_ENTRIES:
                raise ValueError("workspace parent inventory exceeds limit")
            visited += 1
            path = parent / child.name
            info = path.lstat()
            if _plain(info, stat.S_ISDIR):
                count += 1
                if count >= MAX_WORKSPACES:
                    raise ValueError("workspace slots exhausted; retained evidence preserved")
                size, child_entries = scan(path, 0)
                payload += size
                charged += max(size, WORKSPACE_BYTES)
                entries += max(child_entries + 1, WORKSPACE_ENTRIES)
            elif (_plain(info, stat.S_ISREG) and info.st_nlink == 1
                  and not child.name.startswith("opskit-sandbox-")):
                payload += info.st_size
                charged += info.st_size
                entries += 1
                if _version(info) != _version(path.lstat()):
                    raise ValueError("workspace file changed")
            else:
                raise ValueError("unsafe retained workspace")
            if charged + WORKSPACE_BYTES > MAX_WORKSPACE_BYTES:
                raise ValueError("workspace byte quota exhausted")
            if entries + WORKSPACE_ENTRIES > MAX_WORKSPACE_ENTRIES:
                raise ValueError("workspace entry quota exhausted")
    if _version(before) != _version(parent.lstat()):
        raise ValueError("workspace parent changed")
    if charged + WORKSPACE_BYTES > MAX_WORKSPACE_BYTES:
        raise ValueError("workspace byte quota exhausted")
    if entries + WORKSPACE_ENTRIES > MAX_WORKSPACE_ENTRIES:
        raise ValueError("workspace entry quota exhausted")
    return WorkspaceBudget(payload, charged, entries, count)
