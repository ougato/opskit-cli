"""Quota reservation for owned jobs; ambiguous transactions remain protected.

Call only under the fixed cache-root update lock. No cached PID is signalled.
"""
from pathlib import Path
import re
import stat

from core.update_storage import entries, info, unlink_owned
from core.update_jobs import JOB_FILES as _FILES

MAX_JOBS = 8
MAX_JOB_BYTES = 2 * 1024 ** 3 + 1024 ** 2
_ID = re.compile(r"[a-f0-9]{32}")


def _read_optional(path):
    from core.updater import _read_update_record
    try:
        return _read_update_record(path)
    except FileNotFoundError:
        return {}


def _pins(cache_path):
    from core.constants import FILE_UPDATE_HEALTH
    job_ids, backups = set(), set()
    cache = _read_optional(cache_path)
    for record, key in ((cache, "apply_job"), (cache, "last_apply_job"),
                        (_read_optional(cache_path.parent / FILE_UPDATE_HEALTH), "job_id"),
                        (_read_optional(cache_path.parent / "update_pending_path.json"), "job_id")):
        value = record.get(key)
        if value is not None:
            if not isinstance(value, str) or not _ID.fullmatch(value):
                raise ValueError("invalid update job reference")
            job_ids.add(value)
        backup = record.get("backup_id")
        if backup is not None:
            if not isinstance(backup, str) or Path(backup).name != backup or backup in {"", ".", ".."}:
                raise ValueError("invalid update backup reference")
            backups.add(backup)
    return job_ids, backups


def _inventory(root, checkpoint):
    from core.update_jobs import helper_status
    rows = []
    for node in entries(root, limit=64):
        checkpoint()
        node_info = info(node)
        if not stat.S_ISDIR(node_info.st_mode):
            rows.append({"path": node, "bytes": node_info.st_size, "eligible": False})
            continue
        files = {}
        known = bool(_ID.fullmatch(node.name))
        for child in entries(node):
            child_info = info(child)
            if not stat.S_ISREG(child_info.st_mode):
                raise ValueError("nested update job directory retained")
            files[child] = child_info
            known = known and child.name in _FILES
        metadata = _read_optional(node / "job.json")
        eligible = False
        if metadata.get("schema_version") == 2:
            if (type(metadata["schema_version"]) is not int or metadata.get("job_id") != node.name
                    or type(metadata.get("version")) is not int or metadata["version"] <= 0
                    or metadata.get("state") not in {"preparing", "launching", "scheduled", "retired"}
                    or "backup_id" not in metadata or "transaction_id" not in metadata
                    or (metadata["backup_id"] is not None and (not isinstance(metadata["backup_id"], str)
                        or Path(metadata["backup_id"]).name != metadata["backup_id"]
                        or metadata["backup_id"] in {"", ".", ".."}))
                    or (metadata["transaction_id"] is not None and (not isinstance(metadata["transaction_id"], str)
                        or not _ID.fullmatch(metadata["transaction_id"])) )):
                raise ValueError("invalid update job ownership")
            from core.update_retirement import receipt
            retired = receipt(metadata)
            result = {} if retired is not None else _read_optional(node / "result.json")
            if result and (type(result.get("version")) is not int or result["version"] != metadata["version"]
                    or result.get("state") not in {"applied", "failed", "launch_failed", "applied_recovery_incomplete"}):
                raise ValueError("invalid update job result")
            has_ack = node / "running.json" in files
            # Preparing is persisted before launch intent/Popen. Failed results
            # need an authenticated exited helper; absence of an ACK is not proof.
            eligible = (metadata["state"] == "preparing" and not result and not has_ack
                        and metadata["transaction_id"] is None)
            if retired is not None:
                eligible = True  # Durable proof survives partial cleanup/PID reuse.
            if result.get("state") in {"failed", "launch_failed"} and has_ack:
                # A failed helper may still own a prepared target transaction.
                # Keep its recovery evidence until target reconciliation exists.
                eligible = (metadata["transaction_id"] is None
                            and helper_status(node, metadata) == "exited")
        rows.append({"path": node, "bytes": sum(value.st_size for value in files.values()),
            "files": files, "eligible": known and eligible, "metadata": metadata, "mtime": node_info.st_mtime_ns})
    return rows


def _reclaim(row, checkpoint):
    node = row["path"]
    ownership = node / "job.json"
    for path, previous in row["files"].items():
        checkpoint()
        if path != ownership:
            unlink_owned(path, previous)
    if entries(node) != [ownership]:
        raise ValueError("update job gained unknown content")
    unlink_owned(ownership, row["files"][ownership])
    node.rmdir()


def reserve_job(cache_path, incoming_bytes, *, checkpoint=lambda: None):
    cache_path = Path(cache_path)
    if type(incoming_bytes) is not int or not 0 < incoming_bytes <= MAX_JOB_BYTES:
        raise ValueError("invalid update job reservation")
    root = cache_path.parent / "update-jobs"
    from core.updater import _update_record_path
    _update_record_path(root)
    job_ids, backups = _pins(cache_path)
    if not root.exists():
        return {"removed": 0, "retained": 0, "bytes": 0}
    if not stat.S_ISDIR(info(root).st_mode):
        raise ValueError("invalid update job storage")
    rows = _inventory(root, checkpoint)
    total, count, removed = sum(row["bytes"] for row in rows), len(rows), 0
    eligible = sorted((row for row in rows if row["eligible"] and row["path"].name not in job_ids
        and row["metadata"].get("backup_id") not in backups), key=lambda row: (row["mtime"], row["path"].name))
    for row in eligible:
        checkpoint()
        if count + 1 <= MAX_JOBS and total + incoming_bytes <= MAX_JOB_BYTES:
            break
        _reclaim(row, checkpoint)
        total -= row["bytes"]
        count -= 1
        removed += 1
    if count + 1 > MAX_JOBS or total + incoming_bytes > MAX_JOB_BYTES:
        raise RuntimeError("update job quota exhausted; active, ambiguous or referenced jobs retained")
    return {"removed": removed, "retained": count, "bytes": total}
