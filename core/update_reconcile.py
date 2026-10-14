"""Reconcile exited Windows jobs against the current executable authority.

Never execute an artifact, signal a PID, or choose a target from cached paths.
"""
from pathlib import Path
import re
import stat

_ID = re.compile(r"[a-f0-9]{32}")
_BACKUP = re.compile(r"opskit\.v[0-9]+\.[0-9]+\.[a-f0-9]{32}\.bak")
_DIGEST = re.compile(r"[a-f0-9]{64}")


def read_receipt(job, metadata):
    from core.updater import _read_update_record
    try:
        record = _read_update_record(job / "recovery.json")
    except FileNotFoundError:
        return None
    if (type(metadata.get("schema_version")) is not int or metadata["schema_version"] != 2
            or metadata.get("job_id") != job.name
            or type(record.get("schema_version")) is not int or record["schema_version"] != 1
            or record.get("job_id") != job.name
            or type(record.get("version")) is not int or record["version"] != metadata.get("version")
            or not isinstance(record.get("transaction_id"), str)
            or not _ID.fullmatch(record["transaction_id"])
            or record["transaction_id"] != metadata.get("transaction_id")
            or record.get("state") not in {"applied", "failed"}
            or not isinstance(record.get("target_sha256"), str)
            or not _DIGEST.fullmatch(record["target_sha256"])
            or record["target_sha256"] != metadata.get("new_sha256" if record.get("state") == "applied" else "old_sha256")
            or record.get("reason") != {"applied": "target_committed", "failed": "target_unchanged"}.get(record.get("state"))):
        raise ValueError("invalid update reconciliation receipt")
    return record


def _backup(metadata, transaction, target):
    from core import updater
    from core.config import get_data_dir
    identity = metadata.get("backup_id")
    if not isinstance(identity, str) or not _BACKUP.fullmatch(identity):
        raise ValueError("update reconciliation needs an owned backup")
    path = get_data_dir() / "backups" / identity
    updater._update_record_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > updater.MAX_UPDATE_BYTES:
        raise ValueError("invalid update reconciliation backup")
    record = updater._read_update_record(path.with_name(path.name + ".json"))
    if (type(record.get("schema_version")) is not int or record["schema_version"] != 1
            or record.get("backup_id") != identity or record.get("target") != str(target.resolve())
            or type(record.get("from_build")) is not int or record["from_build"] != transaction["from_build"]
            or type(record.get("to_build")) is not int or record["to_build"] != transaction["to_build"]
            or record.get("sha256") != transaction["from_sha256"]
            or record.get("replacement_sha256") != transaction["to_sha256"]
            or updater._sha256_file(path) != transaction["from_sha256"]):
        raise ValueError("update reconciliation backup integrity mismatch")


def _health(metadata, job):
    from core import updater
    path = updater._get_health_path()
    try:
        record = updater._read_health_record(path)
    except FileNotFoundError:
        record = {}
    if record.get("job_id") == job.name:
        if (type(record.get("build")) is not int or record["build"] != metadata["version"]
                or record.get("backup_id") != metadata["backup_id"]
                or type(record.get("confirmed")) is not bool
                or type(record.get("fails")) is not int or record["fails"] < 0):
            raise ValueError("invalid update reconciliation health")
        return record  # Never reset existing failures or health confirmation.
    if record and (type(record.get("build")) is not int or record["build"] >= metadata["version"]):
        raise ValueError("update reconciliation health belongs to another update")
    return {"build": metadata["version"], "confirmed": False, "fails": 0,
            "backup_id": metadata["backup_id"], "job_id": job.name}


def _cache(cache_path):
    from core import updater
    try:
        return updater._read_update_record(cache_path)
    except FileNotFoundError:
        return {}


def _has_work(cache_path):
    from core import updater, update_target_state
    if _cache(cache_path).get("apply_job") is not None:
        return True
    root = cache_path.parent / "update-jobs"
    updater._update_record_path(root)
    if root.exists():
        return True
    transaction = update_target_state.read(updater._self_path())
    return transaction is not None and transaction["phase"] == "prepared"


def _discover_index(cache_path, cache):
    """Bind only a unique job proven by this executable's current transaction."""
    from core import updater, update_target_state, update_retirement
    from core.update_storage import entries, info
    from core.update_jobs import JOB_FILES
    root = cache_path.parent / "update-jobs"
    target = updater._self_path()
    transaction = update_target_state.read(target)
    if not root.exists():
        if transaction is not None and transaction["phase"] == "prepared":
            return {"state": "deferred", "reason": "missing_job_evidence"}
        return {"state": "no_job"}
    if not stat.S_ISDIR(info(root).st_mode):
        raise ValueError("invalid update job storage")
    target_declaration = str(target.resolve())
    candidates, unresolved = [], False
    rollback_health = None
    for node in entries(root, limit=64):
        if not stat.S_ISDIR(info(node).st_mode) or not _ID.fullmatch(node.name):
            return {"state": "deferred", "reason": "unknown_unindexed_job"}
        for child in entries(node):
            if not stat.S_ISREG(info(child).st_mode) or child.name not in JOB_FILES:
                return {"state": "deferred", "reason": "unknown_unindexed_job"}
        metadata = _cache(node / "job.json")
        if type(metadata.get("schema_version")) is not int or metadata["schema_version"] != 2:
            return {"state": "deferred", "reason": "legacy_unindexed_job"}
        # Compare cached paths as declarations; never resolve or open them.
        if metadata.get("target") != target_declaration:
            if not isinstance(metadata.get("target"), str):
                return {"state": "deferred", "reason": "unknown_unindexed_job"}
            continue
        if metadata.get("job_id") != node.name:
            raise ValueError("invalid unindexed update job identity")
        if update_retirement.receipt(metadata) is not None:
            continue
        unresolved = True
        if transaction is None:
            continue
        backup_transaction = transaction
        if transaction["operation"] == "upgrade":
            if metadata.get("transaction_id") != transaction["transaction_id"]:
                continue
        else:
            # Rollback has a new transaction ID. The health record binds its
            # original job and backup; do not choose by cached paths or mtime.
            if rollback_health is None:
                rollback_health = updater._read_health_record(updater._get_health_path())
            if rollback_health.get("job_id") != node.name:
                continue
            if (rollback_health["build"] != metadata.get("version")
                    or rollback_health.get("backup_id") != metadata.get("backup_id")
                    or not isinstance(metadata.get("transaction_id"), str)
                    or not _ID.fullmatch(metadata["transaction_id"])
                    or metadata["transaction_id"] == transaction["transaction_id"]
                    or transaction["phase"] == "aborted"):
                raise ValueError("unindexed rollback authority changed")
            backup_transaction = {
                "from_build": transaction["to_build"], "to_build": transaction["from_build"],
                "from_sha256": transaction["to_sha256"], "to_sha256": transaction["from_sha256"]}
        if (metadata.get("state") not in {"preparing", "launching", "scheduled"}
                or type(metadata.get("version")) is not int
                or metadata["version"] != backup_transaction["to_build"]
                or metadata.get("old_sha256") != backup_transaction["from_sha256"]
                or metadata.get("new_sha256") != backup_transaction["to_sha256"]):
            raise ValueError("unindexed update transaction mismatch")
        candidates.append((node, metadata, backup_transaction))
    if len(candidates) != 1:
        if unresolved:
            if not candidates and rollback_health is not None:
                raise ValueError("unindexed rollback health job is missing")
            reason = "ambiguous_unindexed_job" if candidates else "unindexed_job_unproven"
            return {"state": "deferred", "reason": reason}
        if transaction is not None and transaction["phase"] == "prepared":
            return {"state": "deferred", "reason": "missing_job_evidence"}
        return {"state": "no_job"}
    job, metadata, backup_transaction = candidates[0]
    updater._update_record_path(target)
    target_info = target.stat()
    if (not stat.S_ISREG(target_info.st_mode) or target_info.st_nlink != 1
            or target_info.st_size > updater.MAX_UPDATE_BYTES):
        raise ValueError("unindexed update target changed")
    digest = updater._sha256_file(target)
    if (digest not in (transaction["from_sha256"], transaction["to_sha256"])
            or (digest == transaction["to_sha256"] and transaction["phase"] == "aborted")
            or (digest == transaction["from_sha256"] and transaction["phase"] == "applied")):
        raise ValueError("unindexed update bytes contradict transaction phase")
    _backup(metadata, backup_transaction, target)
    read_receipt(job, metadata)
    updater._write_update_record(cache_path, {**cache, "apply_job": job.name})
    return None


def reconcile():
    """Repair receipts for the indexed job of this executable under both locks."""
    from core import updater
    if not updater._is_frozen():
        raise ValueError("binary updates require a packaged executable")
    cache_path = updater._get_cache_path()
    # A no-job read does not initialize a runtime directory or lock.
    if not _has_work(cache_path):
        return {"state": "no_job"}
    with updater._update_resource.hold(), updater._update_target_resource.hold():
        return _reconcile_locked(cache_path)


def reconcile_on_boot():
    """Resume only an authenticated, prepared rollback of an exited job."""
    from core import updater
    if not updater._is_frozen():
        raise ValueError("binary updates require a packaged executable")
    cache_path = updater._get_cache_path()
    if not _has_work(cache_path):
        return {"state": "no_job"}
    with updater._update_resource.hold(), updater._update_target_resource.hold():
        result = _reconcile_locked(cache_path)
        if result.get("reason") != "rollback_pending":
            return result
        if not updater.rollback():
            return result
        # Restored bytes require restarting even if retirement publication fails.
        try:
            result = _reconcile_locked(cache_path)
        except (OSError, ValueError):
            result = {"id": result["id"], "state": "rolled_back", "retirement_pending": True}
        return {**result, "restart_required": True}


def _reconcile_locked(cache_path):
    from core import updater, update_jobs, update_target_state
    cache = _cache(cache_path)
    identity = cache.get("apply_job")
    if identity is None:
        result = _discover_index(cache_path, cache)
        if result is not None:
            return result
        cache = _cache(cache_path)
        identity = cache["apply_job"]
    if not isinstance(identity, str) or not _ID.fullmatch(identity):
        raise ValueError("invalid update reconciliation job reference")
    job = cache_path.parent / "update-jobs" / identity
    metadata = updater._read_update_record(job / "job.json")
    if metadata.get("schema_version") != 2:
        return {"id": identity, "state": "deferred", "reason": "legacy_job"}
    target = updater._self_path()
    updater._update_record_path(target)
    target_info = target.stat()
    if (not stat.S_ISREG(target_info.st_mode) or target_info.st_nlink != 1
            or target_info.st_size > updater.MAX_UPDATE_BYTES
            or type(metadata["schema_version"]) is not int or metadata.get("job_id") != identity
            or metadata.get("target") != str(target.resolve())
            or type(metadata.get("version")) is not int or metadata["version"] <= 0
            or metadata.get("state") not in {"preparing", "launching", "scheduled", "retired"}
            or not isinstance(metadata.get("transaction_id"), str)
            or not _ID.fullmatch(metadata["transaction_id"])):
        raise ValueError("invalid update reconciliation ownership")
    from core import update_retirement
    retired = update_retirement.receipt(metadata)
    if retired is not None:
        update_retirement.clear_index(cache_path, metadata)
        result = {"id": identity, "version": metadata["version"], "state": retired["outcome"], "active": False}
        if retired["outcome"] == "rolled_back":
            result["restored_version"] = retired["restored_build"]
        else:
            result["reason"] = "target_committed" if retired["outcome"] == "applied" else "target_unchanged"
        return result
    unlaunched = False
    if metadata["state"] == "preparing":
        # The launcher persists launching before Popen. A preparing record
        # with no ACK/result is therefore proof it never reached launch intent.
        unlaunched = True
        for name in ("running.json", "result.json"):
            try:
                updater._read_update_record(job / name)
            except FileNotFoundError:
                continue
            unlaunched = False
    helper = "not_launched" if unlaunched else update_jobs.helper_status(job, metadata)
    if not unlaunched and helper != "exited":
        return {"id": identity, "state": "deferred", "reason": "helper_" + helper}
    transaction = update_target_state.read(target)
    if (transaction is not None and transaction["operation"] == "rollback"
            and transaction["from_build"] == metadata["version"]
            and transaction["from_sha256"] == metadata.get("new_sha256")
            and transaction["to_sha256"] == metadata.get("old_sha256") and not unlaunched):
        health = updater._read_health_record(updater._get_health_path())
        if (health.get("job_id") != identity or health["build"] != metadata["version"]
                or health.get("backup_id") != metadata.get("backup_id")):
            raise ValueError("rollback retirement health belongs to another update")
        _backup(metadata, {"from_build": transaction["to_build"], "to_build": transaction["from_build"],
            "from_sha256": transaction["to_sha256"], "to_sha256": transaction["from_sha256"]}, target)
        if (transaction["phase"] == "prepared"
                and updater._sha256_file(target) == transaction["from_sha256"]):
            return {"id": identity, "state": "deferred", "reason": "rollback_pending"}
        update_target_state.completed(target, transaction)  # Actual restored digest is mandatory.
        metadata = update_retirement.rolled_back(job, metadata, transaction)
        update_retirement.clear_index(cache_path, metadata)
        return {"id": identity, "version": metadata["version"], "state": "rolled_back",
                "active": False, "restored_version": transaction["to_build"]}
    if (transaction is None or transaction["transaction_id"] != metadata["transaction_id"]
            or transaction["operation"] != "upgrade"
            or transaction["to_build"] != metadata["version"]
            or transaction["from_sha256"] != metadata.get("old_sha256")
            or transaction["to_sha256"] != metadata.get("new_sha256")):
        raise ValueError("update reconciliation transaction changed")
    _backup(metadata, transaction, target)
    read_receipt(job, metadata)  # Reject malformed previous recovery evidence.
    digest = updater._sha256_file(target)
    if digest == transaction["to_sha256"] and transaction["phase"] != "aborted" and not unlaunched:
        health = _health(metadata, job)
        update_target_state.completed(target, transaction)
        updater._write_update_record(updater._get_health_path(), health)
        state, reason = "applied", "target_committed"
    elif digest == transaction["from_sha256"] and transaction["phase"] != "applied":
        update_target_state.aborted(target, transaction)
        state, reason = "failed", "target_unchanged"
    else:
        raise ValueError("update reconciliation target changed")
    updater._write_update_record(job / "recovery.json", {
        "schema_version": 1, "job_id": identity, "version": metadata["version"],
        "transaction_id": transaction["transaction_id"], "target_sha256": digest,
        "state": state, "reason": reason})
    retired_metadata = update_retirement.terminal(job, metadata, transaction, outcome=state, unlaunched=unlaunched)
    if retired_metadata is not None:
        update_retirement.clear_index(cache_path, retired_metadata)
    return {"id": identity, "version": metadata["version"], "state": state, "reason": reason}


def backup_pins(cache_path):
    """Return references, or None when legacy/unknown content prevents proof."""
    from core import updater
    from core.update_storage import entries, info
    from core.update_jobs import JOB_FILES
    from core.constants import FILE_UPDATE_HEALTH
    cache_path = Path(cache_path)
    pins = set()
    for path in (cache_path, cache_path.parent / FILE_UPDATE_HEALTH,
                 cache_path.parent / "update_pending_path.json"):
        try:
            record = updater._read_update_record(path)
        except FileNotFoundError:
            continue
        value = record.get("backup_id")
        if value is not None:
            if not isinstance(value, str) or not _BACKUP.fullmatch(value):
                raise ValueError("invalid update backup reference")
            pins.add(value)
    root = cache_path.parent / "update-jobs"
    updater._update_record_path(root)
    if not root.exists():
        return pins
    if not stat.S_ISDIR(info(root).st_mode):
        raise ValueError("invalid update job storage")
    for node in entries(root, limit=64):
        if not stat.S_ISDIR(info(node).st_mode) or not _ID.fullmatch(node.name):
            return None
        for child in entries(node):
            if not stat.S_ISREG(info(child).st_mode) or child.name not in JOB_FILES:
                return None
        try:
            record = updater._read_update_record(node / "job.json")
        except FileNotFoundError:
            return None
        if record.get("schema_version") != 2 or "backup_id" not in record:
            return None
        if (type(record["schema_version"]) is not int or record.get("job_id") != node.name
                or type(record.get("version")) is not int or record["version"] <= 0
                or record.get("state") not in {"preparing", "launching", "scheduled", "retired"}
                or "transaction_id" not in record
                or (record["transaction_id"] is not None and (not isinstance(record["transaction_id"], str)
                    or not _ID.fullmatch(record["transaction_id"])))):
            raise ValueError("invalid update backup job ownership")
        if record["state"] == "retired":
            from core.update_retirement import receipt
            receipt(record)
        value = record["backup_id"]
        if value is not None:
            if not isinstance(value, str) or not _BACKUP.fullmatch(value):
                raise ValueError("invalid update backup job reference")
            pins.add(value)
    return pins
