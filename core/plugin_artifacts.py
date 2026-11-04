"""Host-owned, plugin-partitioned output receipts and explicit safe collection."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
import uuid

from core.paths import data_dir
from core.plugin_state import _directory, _remove_owned_tree
from core.resource_lock import ResourceLock
from core.plugin_host_tools import _ordinary, _grant, MAX_OUTPUT_BYTES, MAX_OUTPUT_ENTRIES
from core.plugin_runtime_inventory import _read_file
from core.secrets import atomic_write


def _root(plugin):
    if not isinstance(plugin, str) or re.fullmatch(r"[a-z][a-z0-9_]*", plugin) is None:
        raise ValueError("invalid receipt owner")
    return data_dir().absolute() / "plugin-artifacts" / plugin


def _record_path(plugin, token):
    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token) is None:
        raise ValueError("invalid output token")
    return _root(plugin) / (token + ".json")


@contextmanager
def resource_lock(resource):
    identity = _directory(_ordinary(str(Path(resource).absolute()), directory=True))
    key = hashlib.sha256(json.dumps(list(identity), separators=(",", ":")).encode()).hexdigest()
    with ResourceLock(lambda: data_dir() / "plugin-artifact-locks" / (key + ".lock"), label="host resource").hold():
        yield


def _snapshot(path, checkpoint):
    from core.plugin_tree import bounded_paths
    from core.host_file_access import open_bounded
    path = _ordinary(str(path), directory=True)
    identity = _directory(path)
    digest = hashlib.sha256()
    size = files = 0
    paths = list(bounded_paths(path, max_entries=MAX_OUTPUT_ENTRIES, max_depth=64,
                              skip_dirs=frozenset(), checkpoint=checkpoint))
    for child in sorted(paths):
        checkpoint()
        info = child.lstat()
        if getattr(info, "st_file_attributes", 0) & 0x400 or stat.S_ISLNK(info.st_mode):
            raise ValueError("unsafe output link")
        if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            files += 1
            size += info.st_size
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError("unsafe output entry")
        if size > MAX_OUTPUT_BYTES:
            raise ValueError("output exceeds limit")
        if stat.S_ISREG(info.st_mode):
            with open_bounded(child, path, MAX_OUTPUT_BYTES) as source:
                while body := source.read(1024**2):
                    checkpoint()
                    digest.update(body)
        digest.update(json.dumps([child.relative_to(path).as_posix(), info.st_dev, info.st_ino,
            info.st_mode, info.st_size, info.st_mtime_ns], separators=(",", ":")).encode())
    if _directory(path) != identity:
        raise ValueError("output replaced")
    return {"identity": list(identity), "parent_identity": list(_directory(path.parent)),
            "inventory_sha256": digest.hexdigest(), "bytes": size, "files": files}


def pending(plugin, token, resource, path, checkpoint):
    checkpoint()
    record = _record_path(plugin, token)
    if os.path.lexists(record):
        raise ValueError("output token already exists")
    value = {"schema_version": 1, "owner": plugin, "resource": str(resource), "directory": str(path),
             "phase": "pending", "completed_ns": time.time_ns(),
             "identity": list(_directory(path)), "parent_identity": list(_directory(path.parent))}
    _save(plugin, token, value)


def _save(plugin, token, value):
    record = _record_path(plugin, token)
    for parent in record.parents:
        if os.path.lexists(parent):
            _directory(parent)
    record.parent.mkdir(parents=True, exist_ok=True)
    from core.secrets import restrict_permissions
    restrict_permissions(record.parent)
    _ordinary(str(record.parent), directory=True)
    atomic_write(record, json.dumps(value, sort_keys=True))


def _load(plugin, token):
    path = _record_path(plugin, token)
    raw = _read_file(path, Path(path.anchor), 16384)
    value = json.loads(raw)
    base = {"schema_version", "owner", "resource", "directory", "phase", "completed_ns", "identity", "parent_identity"}
    if (not isinstance(value, dict) or value.get("phase") not in {"pending", "committed"}
            or set(value) != (base if value["phase"] == "pending" else base | {"inventory_sha256", "bytes", "files"})
            or type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["owner"] != plugin
            or type(value["completed_ns"]) is not int
            or not all(isinstance(value[k], str) and Path(value[k]).is_absolute() for k in ("resource", "directory"))
            or not all(isinstance(value[k], list) and len(value[k]) == 2
                       and all(type(n) is int for n in value[k]) for k in ("identity", "parent_identity"))):
        raise ValueError("invalid output receipt")
    if value["phase"] == "committed" and (
            type(value["bytes"]) is not int or not 0 <= value["bytes"] <= MAX_OUTPUT_BYTES
            or type(value["files"]) is not int or not 0 <= value["files"] <= MAX_OUTPUT_ENTRIES
            or not isinstance(value["inventory_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", value["inventory_sha256"]) is None):
        raise ValueError("invalid output snapshot")
    return value, hashlib.sha256(raw).hexdigest()


def _matches(value, snapshot):
    return all(value.get(key) == observed for key, observed in snapshot.items())


def _required(path, names, checkpoint):
    from core.host_file_access import open_bounded
    if not isinstance(names, list) or len(names) > 32:
        raise ValueError("invalid required output files")
    for name in names:
        if not isinstance(name, str) or "\\" in name:
            raise ValueError("invalid required output path")
        relative = PurePosixPath(name)
        if relative.is_absolute() or not relative.parts or any(p in {"..", "."} or ":" in p for p in relative.parts):
            raise ValueError("invalid required output path")
        checkpoint()
        with open_bounded(path.joinpath(*relative.parts), path, MAX_OUTPUT_BYTES) as source:
            if not source.read(1):
                raise ValueError("required output is empty")


def manage(plugin, fingerprint, action, parameters, *, authority, check_identity, deadline, checkpoint, **unused):
    if not isinstance(parameters, dict):
        raise ValueError("invalid artifact parameters")
    if action == "mkdir":
        if set(parameters) != {"directory"} or not isinstance(parameters["directory"], str):
            raise ValueError("invalid directory request")
        path = Path(parameters["directory"])
        if not path.is_absolute() or path.name in {"", ".", ".."} or any(ord(c) < 32 for c in str(path)):
            raise ValueError("ordinary absolute directory required")
        parent = _ordinary(str(path.parent), directory=True)
        identity = _directory(parent)
        scope = _grant(plugin, fingerprint, "directory.write", str(path), authority, check_identity, checkpoint)
        with resource_lock(parent):
            checkpoint()
            authority.check(scope, check_identity=check_identity)
            if _directory(_ordinary(str(parent), directory=True)) != identity:
                raise ValueError("directory parent changed")
            if not os.path.lexists(path):
                path.mkdir()
            return {"directory": str(_ordinary(str(path), directory=True))}
    allowed = {"list": {"resource", "keep"}, "commit": {"id", "required_files"},
               "clean": {"id", "record_sha256", "keep"},
               "adopt": {"resource", "directory", "snapshot", "required_files", "completed_ns"}}
    if action not in allowed or set(parameters) != allowed[action]:
        raise ValueError("invalid artifact operation")
    inspection_deadline = min(deadline, time.monotonic() + 30)
    def check():
        checkpoint()
        if time.monotonic() >= inspection_deadline:
            raise TimeoutError("artifact operation deadline exceeded")
    if action == "list":
        resource = _ordinary(parameters["resource"], directory=True)
    elif action == "adopt":
        resource = _ordinary(parameters["resource"], directory=True)
    else:
        value, digest = _load(plugin, parameters["id"])
        resource = _ordinary(value["resource"], directory=True)
    keep = parameters.get("keep", 1)
    if type(keep) is not int or not 1 <= keep <= 100:
        raise ValueError("invalid output retention")
    with resource_lock(resource):
        check()
        if action == "list":
            scope = _grant(plugin, fingerprint, "directory.read", str(resource), authority, check_identity, check)
            def live():
                check()
                authority.check(scope, check_identity=check_identity)
            return _inspect(plugin, resource, keep, live)
        if action == "adopt":
            if type(parameters["completed_ns"]) is not int or not 0 < parameters["completed_ns"] <= time.time_ns():
                raise ValueError("invalid receipt timestamp")
            path = _ordinary(parameters["directory"], directory=True)
        else:
            # Reload after acquiring the shared build/cleanup lock.
            value, digest = _load(plugin, parameters["id"])
            path = _ordinary(value["directory"], directory=True)
        scope = _grant(plugin, fingerprint, "directory.write", str(path), authority, check_identity, check)
        def live():
            check()
            authority.check(scope, check_identity=check_identity)
        live()
        snapshot = _snapshot(path, live)
        if action == "adopt":
            expected = parameters["snapshot"]
            # Import never trusts caller-provided content hashes without recomputing them.
            if not isinstance(expected, dict) or not all(expected.get(k) == snapshot[k] for k in (
                    "identity", "parent_identity", "inventory_sha256", "bytes")):
                raise ValueError("legacy output no longer matches its receipt")
            _required(path, parameters["required_files"], live)
            for row in _inspect(plugin, resource, 1, live):
                if row["directory"] == str(path):
                    return row
            token = uuid.uuid4().hex
            pending(plugin, token, resource, path, live)
            value, digest = _load(plugin, token)
        else:
            token = parameters["id"]
        if action in {"commit", "adopt"}:
            if value["phase"] != "pending" or snapshot["identity"] != value["identity"] or snapshot["parent_identity"] != value["parent_identity"]:
                raise ValueError("pending output changed")
            if snapshot["files"] == 0 or snapshot["bytes"] == 0:
                raise ValueError("empty output")
            _required(path, parameters["required_files"], live)
            value.update(snapshot, phase="committed", completed_ns=parameters.get("completed_ns", time.time_ns()))
            live()
            _save(plugin, token, value)
            return {"id": token, "directory": str(path), "bytes": snapshot["bytes"], "files": snapshot["files"]}
        rows = _inspect(plugin, resource, keep, live)
        row = next((row for row in rows if row["id"] == token), None)
        if (not row or not row["cleanup_candidate"] or digest != parameters["record_sha256"]
                or not _matches(value, snapshot)):
            raise ValueError("output changed, uncommitted, or retained")
        if _snapshot(path, live) != snapshot:
            raise ValueError("output changed after candidate inspection")
        def deleting():
            live()
            if _load(plugin, token)[1] != digest or list(_directory(path.parent)) != snapshot["parent_identity"]:
                raise ValueError("output receipt or parent changed")
        _remove_owned_tree(path, tuple(snapshot["identity"]), deleting,
                           max_entries=MAX_OUTPUT_ENTRIES, max_files=MAX_OUTPUT_ENTRIES,
                           max_bytes=MAX_OUTPUT_BYTES, max_depth=64)
        _record_path(plugin, token).unlink()
        return {"removed": token}


def _inspect(plugin, resource, keep, checkpoint):
    root = _root(plugin)
    if not root.exists():
        return []
    _ordinary(str(root), directory=True)
    rows, total = [], 0
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            checkpoint()
            if index >= 4096:
                raise ValueError("receipt inventory limit")
            value, digest = _load(plugin, entry.name.removesuffix(".json"))
            if value["resource"] != str(resource) or value["phase"] != "committed":
                continue
            total += value["bytes"]
            if len(rows) >= 128 or total > 8 * 1024**3:
                raise ValueError("receipt inspection budget exceeded")
            try:
                valid = _matches(value, _snapshot(Path(value["directory"]), checkpoint))
            except TimeoutError:
                raise
            except (OSError, ValueError):
                valid = False
            rows.append({"id": entry.name[:-5], "directory": value["directory"], "completed_ns": value["completed_ns"],
                         "bytes": value["bytes"], "valid": valid, "record_sha256": digest})
    rows.sort(key=lambda row: (row["completed_ns"], row["id"]), reverse=True)
    for index, row in enumerate(rows):
        row["cleanup_candidate"] = index >= keep and row["valid"]
    return rows
