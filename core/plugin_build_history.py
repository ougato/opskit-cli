"""Explicit cleanup of completed, recorded Creator outputs; never adopt old names."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from core.paths import data_dir
from core.plugin_state import _directory, _remove_owned_tree
from core.resource_lock import ResourceLock


def _root():
    return data_dir().absolute() / "creator-build-history"


def _project_key(project):
    return hashlib.sha256(os.path.normcase(str(Path(project).absolute())).encode()).hexdigest()


@contextmanager
def project_lock(project):
    with ResourceLock(lambda: _root() / (_project_key(project) + ".lock"), label="Creator build/history").hold():
        yield


def _snapshot(path, checkpoint=lambda: None):
    from core.plugin_host_tools import _ordinary, MAX_OUTPUT_BYTES, MAX_OUTPUT_ENTRIES
    from core.plugin_tree import bounded_paths
    path = _ordinary(str(path), directory=True)
    identity = _directory(path)
    digest = hashlib.sha256()
    size = 0
    paths = list(bounded_paths(path, max_entries=MAX_OUTPUT_ENTRIES, max_depth=64, skip_dirs=frozenset(), checkpoint=checkpoint))
    for child in sorted(paths):
        checkpoint()
        info = child.lstat()
        if getattr(info, "st_file_attributes", 0) & 0x400 or stat.S_ISLNK(info.st_mode):
            raise ValueError("unsafe build output")
        if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            size += info.st_size
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError("unsafe build entry")
        if size > MAX_OUTPUT_BYTES:
            raise ValueError("build output exceeds history limit")
        if stat.S_ISREG(info.st_mode):
            from core.host_file_access import open_bounded
            with open_bounded(child, path, MAX_OUTPUT_BYTES) as source:
                while body := source.read(1024**2):
                    checkpoint()
                    digest.update(body)
        digest.update(json.dumps([child.relative_to(path).as_posix(), info.st_dev, info.st_ino,
            info.st_mode, info.st_size, info.st_mtime_ns], separators=(",", ":")).encode())
    if _directory(path) != identity:
        raise ValueError("build output replaced")
    return {"identity": identity, "parent_identity": _directory(path.parent),
            "inventory_sha256": digest.hexdigest(), "bytes": size}


def record(project, artifact, checkpoint=lambda: None):
    from core.secrets import atomic_write
    path = Path(artifact["directory"])
    token = path.name.rsplit("-", 1)[-1]
    if re.fullmatch(r"[0-9a-f]{32}", token) is None:
        raise ValueError("invalid build token")
    value = {"schema_version": 1, "project": str(project), "directory": str(path),
             "completed_ns": time.time_ns(), **_snapshot(path, checkpoint)}
    destination = _root() / (token + ".json")
    if os.path.lexists(destination):
        raise ValueError("build history already exists")
    atomic_write(destination, json.dumps(value, sort_keys=True))


def inspect(project):
    from core.plugin_disk_policy import policy
    from core.plugin_runtime_inventory import _read_file
    root = _root()
    if not root.exists():
        return []
    _directory(root)
    rows = []
    deadline = time.monotonic() + 30
    total_bytes = 0
    def checkpoint():
        if time.monotonic() >= deadline:
            raise TimeoutError("build history inspection deadline exceeded")
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= 4096:
                raise ValueError("build history inventory limit")
            if not entry.name.endswith(".json"):
                continue
            token = entry.name[:-5]
            if re.fullmatch(r"[0-9a-f]{32}", token) is None:
                raise ValueError("unknown build history record")
            raw = _read_file(root / entry.name, Path(root.anchor), 8192)
            value = json.loads(raw)
            required = {"schema_version", "project", "directory", "completed_ns", "identity",
                        "parent_identity", "inventory_sha256", "bytes"}
            if (not isinstance(value, dict) or set(value) != required or value["schema_version"] != 1
                    or not isinstance(value["project"], str) or not isinstance(value["directory"], str)
                    or type(value["completed_ns"]) is not int or type(value["bytes"]) is not int
                    or value["bytes"] < 0):
                raise ValueError("invalid build history receipt")
            if value.get("project") != str(project):
                continue
            total_bytes += value["bytes"]
            if len(rows) >= 128 or total_bytes > 8 * 1024**3:
                raise ValueError("build history inspection budget exceeded")
            checkpoint()
            path = Path(value["directory"])
            valid = (path.name.endswith("-" + token) and path.is_absolute())
            try:
                snapshot = _snapshot(path, checkpoint)
                valid = valid and all(value.get(key) == observed for key, observed in snapshot.items())
            except TimeoutError:
                raise
            except (OSError, ValueError, RuntimeError):
                valid = False
            rows.append({"id": token, "directory": str(path), "completed_ns": value["completed_ns"],
                "bytes": value["bytes"], "valid": valid,
                "identity": value["identity"], "parent_identity": value["parent_identity"],
                "inventory_sha256": value["inventory_sha256"],
                "record_sha256": hashlib.sha256(raw).hexdigest()})
    rows.sort(key=lambda row: (row["completed_ns"], row["id"]), reverse=True)
    keep = policy()["keep_builds"]
    for index, row in enumerate(rows):
        row["cleanup_candidate"] = index >= keep and row["valid"]
    return rows


def clean(project, token, record_sha256):
    from core.plugin_host_tools import _ordinary, MAX_OUTPUT_BYTES, MAX_OUTPUT_ENTRIES
    project = _ordinary(str(project), directory=True)
    if re.fullmatch(r"[0-9a-f]{32}", token) is None:
        raise ValueError("invalid build token")
    with project_lock(project):
        rows = inspect(project)
        row = next((item for item in rows if item["id"] == token), None)
        if not row or not row["cleanup_candidate"] or row["record_sha256"] != record_sha256:
            raise ValueError("build history changed or retained")
        path = Path(row["directory"])
        deadline = time.monotonic() + 30
        def snapshot_checkpoint():
            if time.monotonic() >= deadline:
                raise TimeoutError("build cleanup inspection deadline exceeded")
        snapshot = _snapshot(path, snapshot_checkpoint)
        if any(snapshot[key] != row[key] for key in snapshot):
            raise ValueError("build changed after inspection")
        # Recheck the receipt and complete entry inventory before removing anything.
        def checkpoint():
            if time.monotonic() >= deadline:
                raise TimeoutError("build cleanup deadline exceeded; receipt retained")
            from core.plugin_runtime_inventory import _read_file
            raw = _read_file(_root() / (token + ".json"), Path(_root().anchor), 8192)
            if hashlib.sha256(raw).hexdigest() != record_sha256:
                raise ValueError("build receipt changed")
            for parent in (path.parent, *path.parent.parents):
                _directory(parent)
            if _directory(path.parent) != snapshot["parent_identity"]:
                raise ValueError("build parent replaced")
        _remove_owned_tree(path, tuple(snapshot["identity"]), checkpoint,
            max_entries=MAX_OUTPUT_ENTRIES, max_files=MAX_OUTPUT_ENTRIES,
            max_bytes=MAX_OUTPUT_BYTES, max_depth=64)
        (_root() / (token + ".json")).unlink()
    return {"removed": token}
