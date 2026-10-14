"""Bounded inventory and nonrecursive reclamation of owned update candidates.

Callers must hold the fixed cache-root update lock. Unknown content is counted
but preserved; active and startup-retry candidates are never reclaimed.
"""
from pathlib import Path
import re
import stat

from core.update_storage import entries as _entries, info as _info, unlink_owned as _unlink

MAX_CANDIDATES = 4
MAX_CANDIDATE_BYTES = 2 * 1024 ** 3 + 1024 ** 2
MAX_INVENTORY_ENTRIES = 64
_ID = re.compile(r"[a-f0-9]{32}")
_FILES = {"opskit.pending", "opskit.pending.tmp", "opskit.pending.sig.json", "candidate.json"}


def _pins(cache_path, root):
    from core import updater
    try:
        cache = updater._read_update_record(cache_path)
    except FileNotFoundError:
        cache = {}
    pins = set()
    candidate = cache.get("candidate_id")
    if candidate is not None:
        if not isinstance(candidate, str) or not _ID.fullmatch(candidate):
            raise ValueError("invalid update candidate identity")
        pins.add(candidate)
    try:
        marker = updater._read_update_record(cache_path.parent / "update_pending_path.json")
    except FileNotFoundError:
        return pins
    pending = marker.get("pending")
    if not isinstance(pending, str):
        raise ValueError("invalid update retry marker")
    pending = Path(pending)
    if pending == cache_path.parent / "opskit.pending":
        return pins  # Legacy retry source, outside candidate storage.
    try:
        relative = pending.relative_to(root)
    except ValueError:
        raise ValueError("invalid update retry candidate") from None
    if (len(relative.parts) != 2 or not _ID.fullmatch(relative.parts[0])
            or relative.parts[1] != "opskit.pending"):
        raise ValueError("invalid update retry candidate")
    updater._update_record_path(pending)
    pins.add(relative.parts[0])
    return pins


def _inventory(root, checkpoint):
    from core import updater
    rows = []
    for node in _entries(root, MAX_INVENTORY_ENTRIES):
        checkpoint()
        info = _info(node)
        if not stat.S_ISDIR(info.st_mode):
            rows.append({"path": node, "bytes": info.st_size, "eligible": False, "state": "unknown"})
            continue
        owned = bool(_ID.fullmatch(node.name))
        files = {}
        for child in _entries(node):
            checkpoint()
            child_info = _info(child)
            if stat.S_ISDIR(child_info.st_mode):
                if child.name != "locks":
                    raise ValueError("unknown nested update candidate directory")
                for lock in _entries(child):
                    lock_info = _info(lock)
                    if not stat.S_ISREG(lock_info.st_mode):
                        raise ValueError("unsafe update candidate lock")
                    files[lock] = lock_info
                    owned = owned and lock.name == "update.lock"
            else:
                files[child] = child_info
                owned = owned and child.name in _FILES
        try:
            record = updater._read_update_record(node / "candidate.json")
        except FileNotFoundError:
            record = None
        if record is not None and (type(record.get("schema_version")) is not int or record["schema_version"] != 1
                or record.get("candidate_id") != node.name or type(record.get("build")) is not int
                or record["build"] <= 0 or record.get("state") not in {"preparing", "verified"}
                or (record.get("sha256") is not None and (not isinstance(record["sha256"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])))
                or (record.get("state") == "verified" and record.get("sha256") is None)):
            raise ValueError("invalid update candidate ownership record")
        rows.append({"path": node, "bytes": sum(value.st_size for value in files.values()),
                     "eligible": owned and record is not None, "files": files,
                     "state": record["state"] if record else "unknown", "mtime": info.st_mtime_ns})
    return rows


def _reclaim(row, checkpoint):
    from core.resource_lock import ResourceLock
    node = row["path"]
    ownership = node / "candidate.json"
    locks = node / "locks"
    lock_path = locks / "update.lock"
    # Also reject an old orphan worker still holding its local staging lock.
    resource = ResourceLock(lambda: lock_path, label="update candidate")
    with resource.hold():
        for path, info in row["files"].items():
            checkpoint()
            if path not in {ownership, lock_path}:
                _unlink(path, info)
    checkpoint()
    _info(lock_path)
    lock_path.unlink()
    locks.rmdir()
    if _entries(node) != [ownership]:
        raise ValueError("update candidate gained unknown content")
    _unlink(ownership, row["files"][ownership])
    node.rmdir()


def reserve_candidate(cache_path, incoming_bytes, *, checkpoint=lambda: None):
    """Reclaim inactive owned generations, then reserve one bounded generation."""
    cache_path = Path(cache_path)
    if type(incoming_bytes) is not int or not 0 < incoming_bytes <= MAX_CANDIDATE_BYTES:
        raise ValueError("invalid update candidate reservation")
    root = cache_path.parent / "update-candidates"
    from core import updater
    updater._update_record_path(root)
    pins = _pins(cache_path, root)
    if not root.exists():
        return {"removed": 0, "retained": 0, "bytes": 0}
    if not stat.S_ISDIR(_info(root).st_mode):
        raise ValueError("invalid update candidate storage")
    rows = _inventory(root, checkpoint)
    eligible = sorted((row for row in rows if row["eligible"] and row["path"].name not in pins),
                      key=lambda row: (row["mtime"], row["path"].name))
    total = sum(row["bytes"] for row in rows)
    count = len(rows)
    removed = 0
    for row in eligible:
        checkpoint()
        if row["state"] != "preparing" and count + 1 <= MAX_CANDIDATES and total + incoming_bytes <= MAX_CANDIDATE_BYTES:
            continue
        _reclaim(row, checkpoint)
        count -= 1
        total -= row["bytes"]
        removed += 1
    if count + 1 > MAX_CANDIDATES or total + incoming_bytes > MAX_CANDIDATE_BYTES:
        raise RuntimeError("update candidate quota exhausted; referenced or unknown content retained")
    return {"removed": removed, "retained": count, "bytes": total}
