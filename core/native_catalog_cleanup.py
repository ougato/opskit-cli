"""Bounded explicit collection of unreferenced, recorded native objects."""
import os
from pathlib import Path
import re

from core.paths import data_dir
from core.plugin_json import loads
from core.plugin_runtime_inventory import _read_file
from core.plugin_state import _directory, _remove_owned_tree
from core.resource_lock import ResourceLock
from core import native_catalog_ownership as ownership


def _root(kind):
    if kind not in {"runtime", "fetch", "prepare"}:
        raise ValueError("invalid native catalog kind")
    return data_dir().absolute() / {
        "runtime": "native-plugin-runtimes", "fetch": "native-fetch-tools",
        "prepare": "native-preparations",
    }[kind]


def _references(root, kind):
    if kind == "prepare":
        return set()  # Temporary preparations never publish pointers here.
    references = set()
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= 4096:
                raise ValueError("native catalog inventory exceeds limit")
            if not entry.name.endswith(".json"):
                continue
            if ((kind == "fetch" and entry.name != "current.json")
                    or re.fullmatch(r"[a-z][a-z0-9_]*\.json", entry.name) is None):
                raise ValueError("unknown native catalog pointer")
            path = root / entry.name
            value = loads(_read_file(path, Path(path.anchor), 4096).decode("utf-8"))
            if (not isinstance(value, dict) or set(value) != {"schema_version", "object", "manifest_sha256"}
                    or type(value["schema_version"]) is not int or value["schema_version"] != 1
                    or not isinstance(value["object"], str)
                    or re.fullmatch(r"[0-9a-f]{32}", value["object"]) is None
                    or not isinstance(value["manifest_sha256"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", value["manifest_sha256"]) is None):
                raise ValueError("invalid native catalog pointer")
            references.add(value["object"])
    return references


def _inspect(root, kind, limit):
    references = _references(root, kind)
    directory = root / "ownership"
    try:
        _directory(directory)
    except FileNotFoundError:
        return []
    result = []
    with os.scandir(directory) as entries:
        names = []
        for index, entry in enumerate(entries):
            if index >= 4096:
                raise ValueError("native catalog ownership inventory exceeds limit")
            names.append(entry.name)
    for name in sorted(names, key=lambda name: name.removesuffix(".json"))[:limit]:
        token = name.removesuffix(".json")
        try:
            if not name.endswith(".json"):
                raise ValueError("unknown ownership entry")
            value, digest = ownership.read(root, token)
            target = root / "objects" / token
            if _directory(target.parent) != value["parent"]:
                raise ValueError("catalog parent changed")
            if os.path.lexists(target):
                ownership.validate(root, token)
            result.append({"object": token, "record_sha256": digest,
                           "referenced": token in references, "valid": True})
        except (OSError, ValueError, RuntimeError):
            result.append({"object": token, "valid": False})
    return result


def _collect(root, kind, token, digest):
    from core.native_catalog_leases import require_inactive
    value, observed = ownership.read(root, token)
    if observed != digest or token in _references(root, kind):
        raise ValueError("changed or referenced native catalog object")
    require_inactive(root, token)
    target = root / "objects" / token
    if _directory(target.parent) != value["parent"]:
        raise ValueError("catalog parent changed")
    # Retain the same identity and per-entry checks while allowing the
    # collector to inspect long Windows paths in the recorded object.
    from core.native_runtime import absolute_path
    target = absolute_path(target)
    if os.path.lexists(target):
        ownership.validate(root, token)
    def checkpoint():
        current, observed = ownership.read(root, token)
        if current != value or observed != digest:
            raise ValueError("native catalog ownership changed during collection")
    # Preparation holds multiple Python/Git/candidate snapshots concurrently.
    # This is a bounded cleanup allowance, not a disk quota or space reservation.
    preparation = kind == "prepare"
    _remove_owned_tree(target, tuple(value["identity"]), checkpoint,
                       max_entries=262144 if preparation else 16388,
                       max_bytes=8 * 1024**3 if preparation else 516 * 1024**2,
                       max_files=262144 if preparation else 16385, max_depth=66)
    checkpoint()
    if os.path.lexists(target) or _directory(target.parent) != value["parent"]:
        raise ValueError("native catalog deletion unconfirmed")
    (root / "ownership" / (token + ".json")).unlink()
    return {"catalog": kind, "object": token, "removed": True}


def _worker(connection, kind, action, token, digest, limit):
    from network.worker import send
    try:
        root = _root(kind)
        for parent in reversed((root, *root.parents)):
            try:
                _directory(parent)
            except FileNotFoundError:
                if action == "inspect":
                    send(connection, {"ok": True, "value": []})
                    return
                raise
        # The worker itself holds the lock until deletion ends. Killing its
        # supervisor cannot release a lock while filesystem mutation continues.
        with ResourceLock(lambda: root / ".catalog.lock", label="native catalog").hold():
            value = _inspect(root, kind, limit) if action == "inspect" else _collect(root, kind, token, digest)
        send(connection, {"ok": True, "value": value})
    except (OSError, ValueError, RuntimeError):
        send(connection, {"ok": False})
    finally:
        connection.close()


def _run(kind, action, token=None, digest=None, *, limit=256, timeout=30):
    from network.worker import run
    _root(kind)
    if type(limit) is not int or not 1 <= limit <= 256:
        raise ValueError("invalid native catalog inspection limit")
    result = run(_worker, (kind, action, token, digest, limit), timeout=timeout,
                 label="native catalog cleanup")
    if not isinstance(result, dict) or result.get("ok") is not True or set(result) != {"ok", "value"}:
        raise RuntimeError("native catalog cleanup unconfirmed")
    return result["value"]


def inspect(kind, *, limit=256, timeout=30):
    return _run(kind, "inspect", limit=limit, timeout=timeout)


def collect(kind, token, digest, *, timeout=None):
    if (not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token) is None
            or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
        raise ValueError("invalid native catalog collection identity")
    if timeout is None:
        timeout = 180 if kind == "prepare" else 30
    return _run(kind, "collect", token, digest, timeout=timeout)
