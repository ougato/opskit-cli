"""Bounded ASAR directory metadata, behind the existing host read grant."""
import json
import struct
from pathlib import PurePosixPath

from core.host_file_access import open_bounded, source_path
from core.native_sandbox import CapabilityRequest
from core.prompt import guard_input

MAX_ARCHIVE = 2 * 1024**3
MAX_HEADER = 32 * 1024**2


def directories(plugin, fingerprint, directory, relative, member, *, authority, check_identity, checkpoint):
    if (not isinstance(member, str) or not member or "\\" in member
            or PurePosixPath(member).is_absolute() or len(member) > 512
            or any(part in {".", ".."} or ":" in part for part in PurePosixPath(member).parts)):
        raise ValueError("invalid archive directory")
    root, target, identity = source_path(directory, relative)
    if target.suffix.lower() != ".asar":
        raise ValueError("ASAR archive required")
    scope = CapabilityRequest(plugin, fingerprint, "directory.read", str(root))
    checkpoint()
    with guard_input(checkpoint):
        authority.require(scope, check_identity=check_identity)
    authority.check(scope, check_identity=check_identity)
    if source_path(directory, relative) != (root, target, identity):
        raise ValueError("host archive directory changed")
    with open_bounded(target, root, MAX_ARCHIVE) as stream:
        header = stream.read(16)
        if len(header) != 16:
            raise ValueError("invalid ASAR header")
        magic, size, payload_size, json_size = struct.unpack("<4I", header)
        if (magic != 4 or not 8 <= size <= MAX_HEADER or size % 4 or payload_size != size - 4
                or not 2 <= json_size <= size - 8):
            raise ValueError("invalid ASAR header size")
        content = stream.read(json_size)
        if len(content) != json_size:
            raise ValueError("truncated ASAR header")
        checkpoint()
        try:
            node = json.loads(content)
            for part in PurePosixPath(member).parts:
                if not isinstance(node, dict) or not isinstance(node.get("files"), dict):
                    raise ValueError("invalid ASAR directory table")
                node = node["files"].get(part)
                if node is None:
                    break
            children = {} if node is None else node["files"]
        except (UnicodeError, json.JSONDecodeError, KeyError, TypeError, RecursionError) as error:
            raise ValueError("invalid ASAR directory table") from error
        if not isinstance(children, dict) or len(children) > 512:
            raise ValueError("ASAR directory exceeds limits")
        names = []
        for name, entry in children.items():
            if (not isinstance(name, str) or not name or len(name) > 100
                    or any(c in name for c in "/\\:") or any(ord(c) < 32 or ord(c) == 127 for c in name)
                    or name in {".", ".."} or not isinstance(entry, dict)):
                raise ValueError("invalid ASAR directory entry")
            if "files" in entry and isinstance(entry["files"], dict) and "link" not in entry:
                names.append(name)
        if len(names) > 256:
            raise ValueError("too many ASAR directories")
        authority.check(scope, check_identity=check_identity)
        checkpoint()
    if source_path(directory, relative) != (root, target, identity):
        raise ValueError("host archive directory changed")
    authority.check(scope, check_identity=check_identity)
    return sorted(names)
