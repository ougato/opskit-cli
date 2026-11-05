"""Bounded host file reads behind explicit directory authorization."""
from pathlib import Path, PurePosixPath
import base64

from core.native_sandbox import CapabilityRequest
from core.host_file_access import read_bounded as _read_file

MAX_READ = 1024 * 1024


def read_file(plugin, fingerprint, directory, relative, *, authority, check_identity):
    if (not isinstance(directory, str) or not Path(directory).is_absolute()
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
    body = _read_file(target, root, MAX_READ)
    current = root.lstat()
    if (current.st_dev, current.st_ino) != root_identity:
        raise ValueError("host directory changed during read")
    authority.check(scope, check_identity=check_identity)
    return {"encoding": "base64", "content": base64.b64encode(body).decode("ascii")}


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
