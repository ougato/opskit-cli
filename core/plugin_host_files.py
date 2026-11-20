"""Bounded host file reads behind explicit directory authorization."""
from pathlib import Path, PurePosixPath
import base64

from core.native_sandbox import CapabilityRequest
from core.host_file_access import read_bounded as _read_file

MAX_READ = 1024 * 1024
MAX_LIST_NAMES_BYTES = 1024 * 1024


def read_files(plugin, fingerprint, directory, relatives, *, authority, check_identity, checkpoint):
    """Read a bounded batch under one directory capability."""
    from core.host_file_access import source_path
    from core.native_task_control import periodic_checkpoint
    from core.plugin_host_tools import _grant

    if (not isinstance(relatives, list) or not 1 <= len(relatives) <= 64
            or any(not isinstance(name, str) for name in relatives)
            or len(set(relatives)) != len(relatives)):
        raise ValueError("invalid host file batch")
    root, _, identity = source_path(directory, relatives[0])
    scope = _grant(plugin, fingerprint, "directory.read", str(root), authority, check_identity, checkpoint)

    def check():
        checkpoint()
        authority.check(scope, check_identity=check_identity)
        if source_path(directory, relatives[0])[2] != identity:
            raise ValueError("host directory changed during batch read")

    check()
    poll = periodic_checkpoint(check)
    remaining = 8 * MAX_READ
    result = []
    for relative in relatives:
        poll()
        _, path, current = source_path(directory, relative)
        if current != identity:
            raise ValueError("host directory changed during batch read")
        body = _read_file(path, root, min(MAX_READ, remaining))
        remaining -= len(body)
        result.append(base64.b64encode(body).decode("ascii"))
    check()
    return result


def list_files(plugin, fingerprint, directory, *, authority, check_identity, checkpoint,
               missing_ok=False, subdirectories=None):
    """List ordinary files as relative paths after directory consent."""
    import stat
    from core.native_task_control import periodic_checkpoint
    from core.plugin_host_tools import MAX_OUTPUT_DEPTH, MAX_OUTPUT_ENTRIES, _grant, _identity, _ordinary
    from core.plugin_tree import bounded_paths

    if type(missing_ok) is not bool:
        raise ValueError("invalid missing-directory policy")
    if subdirectories is not None and (
        not isinstance(subdirectories, list) or not 1 <= len(subdirectories) <= 32
        or any(not isinstance(name, str) or not name or name in {".", ".."}
               or any(char in name for char in "/\\:") or name.endswith((".", " "))
               or any(ord(char) < 32 or ord(char) == 127 for char in name)
               for name in subdirectories)
        or len({name.casefold() for name in subdirectories}) != len(subdirectories)
    ):
        raise ValueError("invalid host subdirectory list")
    try:
        root = _ordinary(directory, directory=True)
    except FileNotFoundError:
        if missing_ok:
            return []
        raise
    identity = _identity(root)
    scope = _grant(plugin, fingerprint, "directory.read", str(root), authority, check_identity, checkpoint)

    def check_directory():
        authority.check(scope, check_identity=check_identity)
        if _ordinary(directory, directory=True) != root or _identity(root) != identity:
            raise ValueError("host directory changed during listing")

    check_directory()
    names = []
    names_bytes = 0
    entries = 0
    poll = periodic_checkpoint(checkpoint)
    for subtree in ([root] if subdirectories is None else [root / name for name in subdirectories]):
        try:
            subtree = _ordinary(str(subtree), directory=True)
        except FileNotFoundError:
            if missing_ok and subdirectories is not None:
                continue
            raise
        subtree_identity = _identity(subtree)
        for entry in bounded_paths(subtree, max_entries=MAX_OUTPUT_ENTRIES - entries,
                                   max_depth=MAX_OUTPUT_DEPTH, skip_dirs=frozenset(), checkpoint=poll):
            entries += 1
            info = entry.lstat()
            if getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("linked host path forbidden")
            if stat.S_ISDIR(info.st_mode):
                continue
            relative = entry.relative_to(root).as_posix()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or any(ord(char) < 32 or ord(char) == 127 for char in relative)
                    or any(":" in part or "\\" in part or part.endswith((".", " ")) for part in relative.split("/"))):
                raise ValueError("unsafe host file")
            names_bytes += len(relative.encode("utf-8"))
            if names_bytes > MAX_LIST_NAMES_BYTES:
                raise ValueError("host file list limit exceeded")
            names.append(relative)
        if _ordinary(str(subtree), directory=True) != subtree or _identity(subtree) != subtree_identity:
            raise ValueError("host subdirectory changed during listing")
    check_directory()
    return sorted(names)


def read_file(plugin, fingerprint, directory, relative, *, authority, check_identity, missing_ok=False):
    if (type(missing_ok) is not bool or not isinstance(directory, str) or not Path(directory).is_absolute()
            or not isinstance(relative, str) or not relative or "\\" in relative
            or any(ord(char) < 32 for char in directory + relative)):
        raise ValueError("invalid host file request")
    root = Path(directory).absolute()
    # Resolve no links: the displayed authorization must match the actual tree.
    for parent in (root, *root.parents):
        info = parent.lstat()
        if parent.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("linked host directory forbidden")
    if not root.is_dir():
        raise ValueError("host directory required")
    original = root.stat()
    root_identity = original.st_dev, original.st_ino
    name = PurePosixPath(relative)
    if (name.is_absolute() or ".." in name.parts or not name.parts
            or any(":" in part or part.endswith((".", " ")) for part in name.parts)):
        raise ValueError("invalid host relative path")
    scope = CapabilityRequest(plugin, fingerprint, "directory.read", str(root))
    authority.require(scope, check_identity=check_identity)
    authority.check(scope, check_identity=check_identity)
    current = root.lstat()
    if ((current.st_dev, current.st_ino) != root_identity or root.is_symlink()
            or getattr(current, "st_file_attributes", 0) & 0x400):
        raise ValueError("host directory changed during authorization")
    target = root.joinpath(*name.parts)
    try:
        body = _read_file(target, root, MAX_READ)
    except FileNotFoundError:
        if not missing_ok:
            raise
        body = None
    current = root.lstat()
    if (current.st_dev, current.st_ino) != root_identity:
        raise ValueError("host directory changed during read")
    authority.check(scope, check_identity=check_identity)
    return None if body is None else {"encoding": "base64", "content": base64.b64encode(body).decode("ascii")}


def file_status(plugin, fingerprint, directory, relative, *, authority, check_identity):
    """Report a bounded regular file's existence and digest after directory consent."""
    import hashlib
    import os
    from core.host_file_access import source_path
    if not isinstance(relative, str) or "/" in relative or "\\" in relative:
        raise ValueError("file status requires one filename")
    root, target, identity = source_path(directory, relative)
    scope = CapabilityRequest(plugin, fingerprint, "directory.read", str(root))
    authority.require(scope, check_identity=check_identity)
    authority.check(scope, check_identity=check_identity)
    if source_path(directory, relative) != (root, target, identity):
        raise ValueError("host directory changed during authorization")
    if not os.path.lexists(target):
        result = {"exists": False, "sha256": None}
    else:
        content = _read_file(target, root, 16 * 1024)
        result = {"exists": True, "sha256": hashlib.sha256(content).hexdigest()}
    if source_path(directory, relative) != (root, target, identity):
        raise ValueError("host directory changed during inspection")
    authority.check(scope, check_identity=check_identity)
    return result
