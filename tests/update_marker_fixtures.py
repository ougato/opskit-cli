"""Seed explicit marker/backup authority for disposable update tests."""
import hashlib
from pathlib import Path

from core import updater, update_target_state


def bind_marker(target: Path, pending: Path, version: int):
    old_digest = hashlib.sha256(target.read_bytes()).hexdigest()
    new_digest = hashlib.sha256(pending.read_bytes()).hexdigest()
    backup = updater._get_backup_path()
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(target.read_bytes())
    updater._write_update_record(backup.with_name(backup.name + ".json"), {
        "schema_version": 1, "backup_id": backup.name, "target": str(target.resolve()),
        "from_build": updater.APP_VERSION, "to_build": version,
        "sha256": old_digest, "replacement_sha256": new_digest})
    transaction = update_target_state.prepare(target, operation="upgrade",
        from_build=updater.APP_VERSION, to_build=version, from_digest=old_digest, to_digest=new_digest)
    marker = updater._get_cache_path().parent / "update_pending_path.json"
    record = {"schema_version": 2, "pending": str(pending), "target": str(target), "version": version,
              "backup_id": backup.name, "transaction_id": transaction["transaction_id"],
              "old_sha256": old_digest, "new_sha256": new_digest}
    updater._write_update_record(marker, record)
    return marker, backup, transaction
