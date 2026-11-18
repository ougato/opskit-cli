"""Explicit recovery of acknowledged native workspaces, never name adoption."""
from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from core.paths import data_dir
from core.plugin_runtime_inventory import _read_file
from core.plugin_state import _directory
from core.resource_lock import ResourceLock
from core.secrets import atomic_write


def _root():
    return data_dir().absolute() / "native-workspace-leases"


def _path(token):
    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token) is None:
        raise ValueError("invalid native workspace transaction")
    return _root() / (token + ".json")


def _lock(token, *, worker=False):
    suffix = ".worker.lock" if worker else ".lock"
    return ResourceLock(lambda: _path(token).with_suffix(suffix), label="native workspace")


def _read(token):
    from core.plugin_json import loads
    from core.plugin_workspace import recorded_workspace
    path = _path(token)
    raw = _read_file(path, Path(path.anchor), 8192)
    value = loads(raw.decode("utf-8"))
    if (not isinstance(value, dict) or set(value) != {"schema_version", "workspace"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ValueError("invalid native workspace journal")
    workspace = recorded_workspace(value["workspace"])
    if workspace.root.name != "opskit-sandbox-" + token:
        raise ValueError("native workspace journal identity mismatch")
    return workspace, hashlib.sha256(raw).hexdigest()


def _retire_locks(token):
    if os.path.lexists(_path(token)):
        return
    for suffix in (".lock", ".worker.lock"):
        path = _path(token).with_suffix(suffix)
        try:
            info = path.lstat()
            if (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == 0
                    and not getattr(info, "st_file_attributes", 0) & 0x400):
                path.unlink()
        except (FileNotFoundError, PermissionError):
            pass


@contextmanager
def track(workspace):
    """Start only after allocation acknowledgement; pre-ack objects stay unknown."""
    from core.plugin_workspace import journal_binding, validate_binding
    token = workspace.root.name.removeprefix("opskit-sandbox-")
    try:
        with _lock(token).hold():
            validate_binding(workspace)
            path = _path(token)
            if os.path.lexists(path):
                raise ValueError("native workspace already recorded")
            atomic_write(path, json.dumps({"schema_version": 1, "workspace": journal_binding(workspace)},
                                         sort_keys=True))
            yield
    finally:
        _retire_locks(token)


def retire(workspace):
    """The cleanup caller still holds its lifetime lease."""
    token = workspace.root.name.removeprefix("opskit-sandbox-")
    current, _ = _read(token)
    if current != workspace or os.path.lexists(workspace.root):
        raise ValueError("native workspace cleanup unconfirmed")
    if _directory(workspace.root.parent) != workspace.parent_identity:
        raise ValueError("native workspace parent changed")
    _path(token).unlink()


def cleanup_lease(root):
    """Serialize cleanup workers even when their supervisor dies abruptly."""
    token = Path(root).name.removeprefix("opskit-sandbox-")
    path = _path(token)
    return _lock(token, worker=True).hold() if os.path.lexists(path) else nullcontext()


def inspect(*, limit=256):
    if type(limit) is not int or not 1 <= limit <= 256:
        raise ValueError("invalid native workspace inspection limit")
    root = _root()
    for parent in reversed((root, *root.parents)):
        try:
            _directory(parent)
        except FileNotFoundError:
            return []
    result = []
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= 4096:
                raise ValueError("native workspace inventory exceeds limit")
            if not entry.name.endswith(".json"):
                continue
            if len(result) >= limit:
                raise ValueError("native workspace results exceed limit")
            token = entry.name[:-5]
            try:
                workspace, digest = _read(token)
                result.append({"transaction": token, "directory": str(workspace.root),
                               "record_sha256": digest, "valid": True})
            except (OSError, ValueError, RuntimeError):
                result.append({"transaction": token, "valid": False})
    return sorted(result, key=lambda item: item["transaction"])


def recover(token, digest, *, timeout=30):
    from core.plugin_workspace import cleanup
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("invalid native workspace digest")
    # Do not create a lease namespace for unknown operator input. Re-read under
    # the lease below; this preliminary read conveys no deletion authority.
    _, observed = _read(token)
    if observed != digest:
        raise ValueError("native workspace record changed")
    with _lock(token).hold():
        workspace, observed = _read(token)
        if observed != digest:
            raise ValueError("native workspace record changed")
        # cleanup checks the parent and complete bounded tree before mutation.
        # An absent target is repeatable; a replacement identity is never adopted.
        cleanup(workspace, timeout=timeout)
        current, observed = _read(token)
        if current != workspace or observed != digest:
            raise ValueError("native workspace record changed during cleanup")
        retire(workspace)
    _retire_locks(token)
    return {"transaction": token, "removed": True}


def recover_one_idle(*, deadline, attempted):
    """Reclaim one recorded, inactive workspace; never adopt unknown objects."""
    try:
        entries = inspect()
    except (OSError, ValueError, RuntimeError):
        return False
    for item in entries:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if not item.get("valid"):
            continue
        token = item["transaction"]
        if token in attempted:
            continue
        attempted.add(token)
        try:
            info = Path(item["directory"]).lstat()
            if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                continue
        except OSError:
            continue
        try:
            recover(token, item["record_sha256"], timeout=min(30, remaining))
            return True
        except (OSError, ValueError, RuntimeError, TimeoutError):
            continue
    return False
