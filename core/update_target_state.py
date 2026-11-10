"""Target-scoped build authority, reconciled against bytes under the target lock."""
import os
import re
from pathlib import Path
from uuid import uuid4


class UpdateOwnerMismatch(ValueError):
    """A recovery record was created for a different data directory."""


def path(target):
    from core import updater
    return updater._update_target_lock_path(target).with_suffix(".state.json")


def read(target):
    from core import updater
    try:
        record = updater._read_update_record(path(target))
    except FileNotFoundError:
        return None
    if (type(record.get("schema_version")) is not int or record["schema_version"] != 1
            or record.get("target") != str(target.resolve())
            or record.get("operation") not in {"upgrade", "rollback"}
            or record.get("phase") not in {"prepared", "applied", "aborted"}
            or any(type(record.get(key)) is not int or record[key] <= 0 for key in ("from_build", "to_build"))
            or any(not isinstance(record.get(key), str) or not re.fullmatch(r"[a-f0-9]{64}", record[key])
                   for key in ("from_sha256", "to_sha256"))
            or not isinstance(record.get("transaction_id"), str)
            or not re.fullmatch(r"[a-f0-9]{32}", record["transaction_id"])
            or ("owner_data_dir" in record and (not isinstance(record["owner_data_dir"], str)
                or not os.path.isabs(record["owner_data_dir"])))
            or ("source_transaction_id" in record and (record["operation"] != "rollback"
                or not isinstance(record["source_transaction_id"], str)
                or not re.fullmatch(r"[a-f0-9]{32}", record["source_transaction_id"])))
            or ("backup_id" in record and (not isinstance(record["backup_id"], str)
                or not re.fullmatch(r"opskit\.v[0-9]+\.[0-9]+\.[a-f0-9]{32}\.bak", record["backup_id"])))
            or ("upgrade_floor" in record and (type(record["upgrade_floor"]) is not int
                or record["upgrade_floor"] < 0
                or (record["operation"] == "upgrade" and record["upgrade_floor"] >= record["to_build"])
                or (record["operation"] == "rollback" and record["upgrade_floor"] < record["from_build"])))):
        raise ValueError("invalid executable update state")
    if (record["from_sha256"] == record["to_sha256"]
            or (record["operation"] == "upgrade" and record["to_build"] <= record["from_build"])
            or (record["operation"] == "rollback" and record["to_build"] >= record["from_build"])):
        raise ValueError("invalid executable update transition")
    return record


def current_build(target, digest, runtime_build):
    record = read(target)
    if record is None:
        return runtime_build
    if digest == record["to_sha256"] and record["phase"] != "aborted":
        return record["to_build"]
    if digest == record["from_sha256"]:
        return record["from_build"]
    raise ValueError("executable bytes do not match recorded update state")


def data_directory_identity(directory):
    """Comparable canonical location; this is not an inode ownership proof."""
    # resolve() uses the filesystem's actual spelling for existing Windows
    # paths. Do not fold case: NTFS directories can opt into case sensitivity.
    return str(Path(directory).resolve())


def require_owner(record, directory):
    """Enforce owner binding when a newer transaction recorded one."""
    if record and "owner_data_dir" in record:
        if record["owner_data_dir"] != data_directory_identity(directory):
            raise UpdateOwnerMismatch("update data directory changed; recovery evidence retained")


def prepare(target, *, operation, from_build, to_build, from_digest, to_digest,
            owner_data_dir=None, source_transaction_id=None, backup_id=None):
    from core import updater
    previous = read(target)
    floor = _upgrade_floor(previous)
    if operation == "upgrade":
        if to_build <= floor:
            raise ValueError("rolled back update version refused")
    elif operation == "rollback":
        floor = max(floor, from_build)
    if from_digest == to_digest:
        raise ValueError("update versions must identify distinct executable bytes")
    record = {"schema_version": 1, "target": str(target.resolve()), "operation": operation,
              "phase": "prepared", "transaction_id": uuid4().hex,
              "from_build": from_build, "to_build": to_build,
              "from_sha256": from_digest, "to_sha256": to_digest, "upgrade_floor": floor}
    if owner_data_dir is not None:
        record["owner_data_dir"] = data_directory_identity(owner_data_dir)
    if backup_id is not None:
        if (not isinstance(backup_id, str)
                or not re.fullmatch(r"opskit\.v[0-9]+\.[0-9]+\.[a-f0-9]{32}\.bak", backup_id)):
            raise ValueError("invalid update backup identity")
        record["backup_id"] = backup_id
    if source_transaction_id is not None:
        if operation != "rollback" or not re.fullmatch(r"[a-f0-9]{32}", source_transaction_id):
            raise ValueError("invalid source update transaction")
        record["source_transaction_id"] = source_transaction_id
    updater._write_update_record(path(target), record)
    return record


def validate_upgrade(target, build):
    """A release already rolled back must not be automatically reapplied."""
    record = read(target)
    if build <= _upgrade_floor(record):
        raise ValueError("rolled back update version refused")


def _upgrade_floor(record):
    if record is None:
        return 0
    floor = record.get("upgrade_floor", 0)
    if record["operation"] == "rollback" and record["phase"] in {"prepared", "applied"}:
        floor = max(floor, record["from_build"])
    return floor


def completed(target, record):
    from core import updater
    latest = read(target)
    if (latest is None or not _same_transition(latest, record)
            or latest["phase"] == "aborted"):
        raise ValueError("executable update transaction changed")
    if updater._sha256_file(target) != record["to_sha256"]:
        raise ValueError("executable update did not commit expected bytes")
    if latest["phase"] == "applied":
        return
    updater._write_update_record(path(target), {**record, "phase": "applied"})


def aborted(target, record):
    """Close an inactive transaction only while its original bytes remain."""
    from core import updater
    latest = read(target)
    if (latest is None or not _same_transition(latest, record)
            or latest["phase"] == "applied"):
        raise ValueError("executable update transaction changed")
    if updater._sha256_file(target) != record["from_sha256"]:
        raise ValueError("executable update original bytes changed")
    if latest["phase"] == "aborted":
        return
    updater._write_update_record(path(target), {**record, "phase": "aborted"})


def _same_transition(left, right):
    return ({key: value for key, value in left.items() if key != "phase"}
            == {key: value for key, value in right.items() if key != "phase"})
