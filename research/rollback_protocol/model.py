"""Executable recovery protocol model; not a production scheduler or helper.

Helper identity is supplied by the caller as a trusted observation. Source job
authority is a fixed fixture, and one model lock replaces the production lock
pair. The model deliberately cannot launch, restart, terminate, or select a
real executable; it is not a complete implementation or safety proof.
"""
from pathlib import Path
import re
from uuid import uuid4

from core import update_target_state, updater
from core.resource_lock import ResourceBusyError, ResourceIdentityError, ResourceLock


class Model:
    def __init__(self, root, owner=None):
        self.root = Path(root)
        self.owner = Path(owner) if owner is not None else self.root / "data"
        self.intent = self.root / "intent.json"
        self.health = self.root / "health.json"
        self.target = self.root / "target.bin"
        self.backup = self.root / "backup.bin"
        self.lock = ResourceLock(lambda: self.root / "locks" / "model.lock", label="protocol model")

    def _evidence(self):
        transaction = update_target_state.read(self.target)
        health = updater._read_health_record(self.health)
        update_target_state.require_owner(transaction, self.owner)
        if (not transaction or transaction["operation"] != "rollback" or transaction["phase"] not in {"prepared", "applied"}
                or transaction.get("source_transaction_id") != "a" * 32
                or health.get("confirmed") is not False or health.get("fails") != 3
                or health.get("build") != transaction["from_build"]
                or health.get("backup_id") != transaction.get("backup_id")
                or updater._sha256_file(self.backup) != transaction["to_sha256"]):
            raise ValueError("recovery evidence mismatch")
        return transaction

    def schedule(self):
        try:
            with self.lock.hold():
                transaction = self._evidence()
                if self.intent.exists():
                    return "pending"
                if transaction["phase"] != "prepared":
                    return "blocked"
                if updater._sha256_file(self.target) != transaction["from_sha256"]:
                    return "blocked"
                updater._write_update_record(self.intent, {
                    "transaction_id": transaction["transaction_id"],
                    "source_transaction_id": transaction["source_transaction_id"],
                    "backup_id": transaction["backup_id"], "state": "launching",
                    "job_id": uuid4().hex, "attempt": 1,
                })
                return "handoff"
        except ResourceBusyError:
            return "busy"
        except ResourceIdentityError:
            # A competing creation or untrusted directory cannot establish
            # lock ownership. Preserve evidence and block this attempt.
            return "blocked"

    def _matching_intent(self, transaction, job_id):
        intent = updater._read_update_record(self.intent)
        if (intent.get("state") != "launching" or intent.get("job_id") != job_id
                or not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id)
                or type(intent.get("attempt")) is not int or intent["attempt"] <= 0
                or intent.get("transaction_id") != transaction["transaction_id"]
                or intent.get("source_transaction_id") != transaction["source_transaction_id"]
                or intent.get("backup_id") != transaction["backup_id"]):
            raise ValueError("recovery intent changed")
        return intent

    def observe(self, helper, loaded_digest, job_id):
        """Return a startup decision; helper must come from independent identity proof."""
        try:
            with self.lock.hold():
                transaction = self._evidence()
                self._matching_intent(transaction, job_id)
                if helper not in {"alive", "exited"}:
                    return "wait"
                if helper == "alive":
                    return "wait"
                actual = updater._sha256_file(self.target)
                if actual == transaction["from_sha256"]:
                    return "retry_available" if transaction["phase"] == "prepared" else "blocked"
                if actual != transaction["to_sha256"]:
                    return "blocked"
                update_target_state.completed(self.target, transaction)
                return "proceed" if loaded_digest == actual else "restart"
        except (OSError, ValueError, ResourceBusyError):
            return "blocked"

    def retry(self, helper, job_id):
        """Publish a new identity only after the previous helper is proven exited."""
        try:
            with self.lock.hold():
                transaction = self._evidence()
                intent = self._matching_intent(transaction, job_id)
                if helper != "exited":
                    return "wait"
                if (transaction["phase"] != "prepared"
                        or updater._sha256_file(self.target) != transaction["from_sha256"]):
                    return "blocked"
                updater._write_update_record(self.intent, {
                    **intent, "job_id": uuid4().hex, "attempt": intent["attempt"] + 1,
                })
                return "handoff"
        except (OSError, ValueError, ResourceBusyError):
            return "blocked"

    def helper_commit(self, job_id, *, lose_result=False):
        """Simulate helper commit. Real Windows sharing/parent wait are tested separately."""
        with self.lock.hold():
            transaction = self._evidence()
            intent = self._matching_intent(transaction, job_id)
            if (transaction["phase"] != "prepared"
                    or intent.get("transaction_id") != transaction["transaction_id"]
                    or intent.get("source_transaction_id") != transaction["source_transaction_id"]
                    or intent.get("backup_id") != transaction["backup_id"]
                    or updater._sha256_file(self.target) != transaction["from_sha256"]):
                raise ValueError("invalid recovery job")
            updater._atomic_copy(self.backup, self.target, expected_digest=transaction["to_sha256"])
            if not lose_result:
                updater._write_update_record(self.root / "result.json", {"state": "applied"})
