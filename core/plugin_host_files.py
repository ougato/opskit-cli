"""Bounded host file reads behind explicit directory authorization."""
from pathlib import Path, PurePosixPath
import base64

from core.native_sandbox import CapabilityRequest
from core.host_file_access import read_bounded as _read_file

MAX_READ = 1024 * 1024
MAX_LIST_NAMES_BYTES = 1024 * 1024


def list_files(plugin, fingerprint, directory, *, authority, check_identity, checkpoint, missing_ok=False):
    """List ordinary files as relative paths after directory consent."""
    import stat
    from core.plugin_host_tools import MAX_OUTPUT_DEPTH, MAX_OUTPUT_ENTRIES, _grant, _identity, _ordinary
    from core.plugin_tree import bounded_paths

    if type(missing_ok) is not bool:
        raise ValueError("invalid missing-directory policy")
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
    for entry in bounded_paths(root, max_entries=MAX_OUTPUT_ENTRIES, max_depth=MAX_OUTPUT_DEPTH,
                               skip_dirs=frozenset(), checkpoint=checkpoint):
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
