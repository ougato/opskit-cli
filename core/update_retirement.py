"""Durable retirement proof, stored in the job ownership record itself."""
import re
import os

_ID = re.compile(r"[a-f0-9]{32}")
_SHA = re.compile(r"[a-f0-9]{64}")
_BACKUP = re.compile(r"opskit\.v[0-9]+\.[0-9]+\.[a-f0-9]{32}\.bak")


def receipt(metadata):
    if metadata.get("state") != "retired":
        return None
    record = metadata.get("retirement")
    if (type(metadata.get("schema_version")) is not int or metadata["schema_version"] != 2
            or not isinstance(metadata.get("job_id"), str) or not _ID.fullmatch(metadata["job_id"])
            or type(metadata.get("version")) is not int or metadata["version"] <= 0
            or not isinstance(metadata.get("target"), str) or not 0 < len(metadata["target"]) <= 8192
            or not os.path.isabs(metadata["target"])
            or not isinstance(metadata.get("transaction_id"), str) or not _ID.fullmatch(metadata["transaction_id"])
            or not isinstance(metadata.get("backup_id"), str) or not _BACKUP.fullmatch(metadata["backup_id"])
            or any(not isinstance(metadata.get(key), str) or not _SHA.fullmatch(metadata[key])
                   for key in ("old_sha256", "new_sha256"))
            or metadata["old_sha256"] == metadata["new_sha256"] or not isinstance(record, dict)
            or type(record.get("schema_version")) is not int or record["schema_version"] not in {1, 2}
            or record.get("job_transaction_id") != metadata["transaction_id"]):
        raise ValueError("invalid update job retirement")
    if record["schema_version"] == 1:
        invalid = (record.get("outcome") != "rolled_back" or not isinstance(record.get("rollback_transaction_id"), str)
            or not _ID.fullmatch(record["rollback_transaction_id"])
            or record["rollback_transaction_id"] == metadata["transaction_id"]
            or type(record.get("restored_build")) is not int or not 0 < record["restored_build"] < metadata["version"]
            or record.get("target_sha256") != metadata["old_sha256"]
            or type(record.get("helper_created_ticks")) is not int or record["helper_created_ticks"] <= 0)
    else:
        outcome = record.get("outcome")
        proof = record.get("helper_proof")
        invalid = (not isinstance(outcome, str) or outcome not in {"applied", "failed"}
            or type(record.get("from_build")) is not int or not 0 < record["from_build"] < metadata["version"]
            or type(record.get("terminal_build")) is not int
            or record["terminal_build"] != (metadata["version"] if outcome == "applied" else record["from_build"])
            or record.get("target_sha256") != metadata["new_sha256" if outcome == "applied" else "old_sha256"]
            or not isinstance(proof, str) or proof not in {"exited", "not_launched"}
            or (proof == "not_launched" and (outcome != "failed" or "helper_created_ticks" in record))
            or (proof == "exited" and (type(record.get("helper_created_ticks")) is not int
                                       or record["helper_created_ticks"] <= 0))
            or (outcome == "applied" and record.get("health_confirmed") is not True))
    if invalid:
        raise ValueError("invalid update job retirement")
    return record


def clear_index(cache_path, metadata):
    """After durable retirement, move only the matching active index to history."""
    from core import updater
    if receipt(metadata) is None:
        raise ValueError("update index retirement needs durable proof")
    cache = updater._read_update_record(cache_path)
    if cache.get("apply_job") == metadata["job_id"]:
        updater._write_update_record(cache_path, {**cache, "apply_job": None,
            "last_apply_job": metadata["job_id"]})


def rolled_back(job, metadata, transaction):
    """Caller holds both locks and proved backup/target plus exited identity."""
    from core import updater, update_jobs
    if update_jobs.helper_status(job, metadata) != "exited":
        raise ValueError("update retirement needs an exited helper")
    ack = updater._read_update_record(job / "running.json")
    if (type(ack.get("schema_version")) is not int or ack["schema_version"] != 1
            or ack.get("job_id") != job.name or type(ack.get("version")) is not int
            or ack["version"] != metadata["version"]
            or type(ack.get("created_ticks")) is not int or ack["created_ticks"] <= 0):
        raise ValueError("invalid update retirement acknowledgement")
    retired = {**metadata, "state": "retired", "retirement": {
        "schema_version": 1, "outcome": "rolled_back", "job_transaction_id": metadata["transaction_id"],
        "rollback_transaction_id": transaction["transaction_id"], "restored_build": transaction["to_build"],
        "target_sha256": transaction["to_sha256"], "helper_created_ticks": ack["created_ticks"]}}
    receipt(retired)
    updater._write_update_record(job / "job.json", retired)
    return retired


def terminal(job, metadata, transaction, *, outcome, unlaunched=False):
    """Persist upgrade completion under both locks after reconciliation proof."""
    from core import updater, update_jobs, update_target_state
    current = update_target_state.read(updater._self_path())
    expected_phase = "applied" if outcome == "applied" else "aborted"
    if (outcome not in {"applied", "failed"} or current is None
            or current["operation"] != "upgrade" or current["phase"] != expected_phase
            or current["transaction_id"] != metadata["transaction_id"]
            or current["transaction_id"] != transaction["transaction_id"]
            or current["to_build"] != metadata["version"]
            or current["from_sha256"] != metadata["old_sha256"]
            or current["to_sha256"] != metadata["new_sha256"]
            or updater._sha256_file(updater._self_path()) != current["to_sha256" if outcome == "applied" else "from_sha256"]):
        raise ValueError("update terminal retirement authority changed")
    if outcome == "applied":
        health = updater._read_health_record(updater._get_health_path())
        if (health["build"] != metadata["version"] or health.get("job_id") != job.name
                or health.get("backup_id") != metadata["backup_id"]):
            raise ValueError("update terminal retirement health changed")
        if health["confirmed"] is not True:
            return None
    proof = {"helper_proof": "not_launched" if unlaunched else "exited"}
    if unlaunched:
        if outcome != "failed" or metadata["state"] != "preparing":
            raise ValueError("update terminal retirement launch intent changed")
        for name in ("running.json", "result.json"):
            try:
                updater._read_update_record(job / name)
            except FileNotFoundError:
                continue
            raise ValueError("update terminal retirement gained launch evidence")
    else:
        try:
            ack = updater._read_update_record(job / "running.json")
        except FileNotFoundError:
            return None  # Retain recovery evidence without durable exit identity.
        if (update_jobs.helper_status(job, metadata) != "exited"
                or type(ack.get("schema_version")) is not int or ack["schema_version"] != 1
                or ack.get("job_id") != job.name or type(ack.get("version")) is not int
                or ack["version"] != metadata["version"]
                or type(ack.get("created_ticks")) is not int or ack["created_ticks"] <= 0):
            raise ValueError("invalid update terminal retirement acknowledgement")
        proof["helper_created_ticks"] = ack["created_ticks"]
    record = {"schema_version": 2, "outcome": outcome, "job_transaction_id": current["transaction_id"],
        "from_build": current["from_build"], "terminal_build": current["to_build" if outcome == "applied" else "from_build"],
        "target_sha256": current["to_sha256" if outcome == "applied" else "from_sha256"], **proof}
    if outcome == "applied":
        record["health_confirmed"] = True
    retired = {**metadata, "state": "retired", "retirement": record}
    receipt(retired)
    updater._write_update_record(job / "job.json", retired)
    return retired
