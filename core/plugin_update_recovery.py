"""Identity-bound rollback of interrupted native updates; retain all evidence."""
import hashlib
import json
from pathlib import Path
import re

from core.directory_publish import publish_new_directory
from core.paths import plugins_dir
from core.plugin_install_receipt import content_receipt
from core.plugin_metadata import path_present
from core.plugin_state import _directory, _valid_identity
from core.plugin_runtime_inventory import _read_file

MAX_RECORD_BYTES = 8192
MAX_ENTRIES = 4096
RECORD = "recovery.json"


def _token(token):
    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token) is None:
        raise ValueError("invalid update transaction")
    return plugins_dir().absolute() / (".opskit-update-" + token)


def _validate(record):
    keys = {"schema_version", "plugin", "directory", "parent", "workspace", "old", "candidate",
            "old_receipt", "candidate_receipt", "installation"}
    if (not isinstance(record, dict) or set(record) != keys
            or type(record["schema_version"]) is not int or record["schema_version"] != 1
            or not isinstance(record["plugin"], str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", record["plugin"]) is None
            or not isinstance(record["directory"], str)
            or re.fullmatch(r"[a-z][a-z0-9-]{0,95}", record["directory"]) is None
            or any(not _valid_identity(record[key]) for key in ("parent", "workspace", "old", "candidate"))
            or any(not isinstance(record[key], str) or re.fullmatch(r"[0-9a-f]{64}", record[key]) is None
                   for key in ("old_receipt", "candidate_receipt"))):
        raise ValueError("invalid update recovery record")
    installation = record["installation"]
    if installation is not None:
        if (not isinstance(installation, dict)
                or set(installation) != {"source", "version", "branch", "source_commit"}
                or any(not isinstance(installation[key], str) or not 0 < len(installation[key]) <= limit
                       or any(ord(char) < 32 for char in installation[key])
                       for key, limit in (("source", 4096), ("version", 128), ("source_commit", 64)))
                or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", installation["source_commit"]) is None
                or (installation["branch"] is not None and (
                    not isinstance(installation["branch"], str) or len(installation["branch"]) > 256
                    or any(ord(char) < 32 for char in installation["branch"])))):
            raise ValueError("invalid original installation metadata")
    return record


def _binding(work, record):
    if work != _token(work.name.removeprefix(".opskit-update-")):
        raise ValueError("update outside installation namespace")
    for path in (work, *work.parents):
        _directory(path)
    if _directory(work) != record["workspace"] or _directory(work.parent) != record["parent"]:
        raise ValueError("update recovery directory changed")


def record(work, *, plugin, root, old, candidate, old_receipt, candidate_receipt, installation=None):
    """Durably record both objects before revocation or either directory move."""
    work, root = Path(work), Path(root)
    if root.parent != work.parent:
        raise ValueError("update recovery parent mismatch")
    value = _validate({"schema_version": 1, "plugin": plugin, "directory": root.name,
                       "parent": _directory(work.parent), "workspace": _directory(work),
                       "old": old, "candidate": candidate, "old_receipt": old_receipt,
                       "candidate_receipt": candidate_receipt,
                       "installation": None if installation is None else {
                           key: installation.get(key) for key in ("source", "version", "branch", "source_commit")}})
    _binding(work, value)
    from core.secrets import atomic_write
    atomic_write(work / RECORD, json.dumps(value, sort_keys=True))


def _load(work):
    raw = _read_file(work / RECORD, Path(work.anchor), MAX_RECORD_BYTES)
    from core.plugin_json import loads
    value = _validate(loads(raw.decode("utf-8")))
    _binding(work, value)
    return value, hashlib.sha256(raw).hexdigest()


def inspect(*, limit=256):
    """Bounded metadata inventory; discovery alone never authorizes rollback."""
    if type(limit) is not int or not 1 <= limit <= 256:
        raise ValueError("invalid update inspection limit")
    parent = plugins_dir().absolute()
    for path in reversed((parent, *parent.parents)):
        try:
            _directory(path)
        except FileNotFoundError:
            return []
    result = []
    import os
    with os.scandir(parent) as entries:
        for index, entry in enumerate(entries):
            if index >= MAX_ENTRIES:
                raise ValueError("update inspection entry limit exceeded")
            if not entry.name.startswith(".opskit-update-"):
                continue
            if len(result) >= limit:
                raise ValueError("update inspection result limit exceeded")
            token = entry.name.removeprefix(".opskit-update-")
            try:
                work = _token(token)
                value, digest = _load(work)
                result.append({"transaction": token, "plugin": value["plugin"],
                               "directory": value["directory"], "record_sha256": digest, "valid": True})
            except (OSError, ValueError, RuntimeError):
                result.append({"transaction": token, "valid": False})
    return sorted(result, key=lambda item: item["transaction"])


def _matches(path, identity, receipt):
    return _directory(path) == identity and content_receipt(path) == receipt


def _restored_authority(value):
    from core.plugin_trust import grant, invalidate, revoke
    original = value["installation"]
    if original is None:
        invalidate(value["plugin"])
    else:
        # Restore the acquisition baseline, never execution trust or service edges.
        # grant(None branch) normally preserves a prior branch; remove the stale
        # record first so recovery restores an original branch of None exactly.
        revoke(value["plugin"])
        grant(value["plugin"], "", original["version"], original["source"], original["branch"],
              installation_receipt=value["old_receipt"], source_commit=original["source_commit"])


def recover(token, digest):
    """Restore only recorded old bytes; never regrant trust or delete candidates.

    Excludes invocations and update/install publication. A changed independent
    writer is retained. Repeating after restoration is safe and keeps the journal.
    """
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("invalid recovery digest")
    work = _token(token)
    initial, initial_digest = _load(work)
    if initial_digest != digest:
        raise ValueError("update recovery record changed")
    from core.plugin_invocation_lock import hold
    from plugin.commands import _installation_lock
    from core.plugin_trust import authorization_transaction, invalidate
    with hold(plugin=initial["plugin"], timeout=30), _installation_lock.hold(), authorization_transaction():
        value, current_digest = _load(work)
        if value != initial or current_digest != digest:
            raise ValueError("update recovery record changed")
        root, previous = work.parent / value["directory"], work / "previous"
        old_at_root = path_present(root) and _matches(root, value["old"], value["old_receipt"])
        if old_at_root:
            if path_present(previous):
                raise ValueError("ambiguous update backup")
            _restored_authority(value)
            return {"transaction": token, "restored": True, "evidence_retained": True}
        if not path_present(previous) or not _matches(previous, value["old"], value["old_receipt"]):
            raise ValueError("update backup changed or missing")
        if path_present(root):
            if not _matches(root, value["candidate"], value["candidate_receipt"]):
                raise ValueError("independent installation preserved")
            withdrawn = work / "recovered-candidate"
            if path_present(withdrawn):
                raise ValueError("recovery candidate path occupied")
        else:
            withdrawn = None
        _binding(work, value)
        current, current_digest = _load(work)
        if current != value or current_digest != digest:
            raise ValueError("update recovery record changed during validation")
        invalidate(value["plugin"])
        if withdrawn is not None:
            publish_new_directory(root, withdrawn)
            if not _matches(withdrawn, value["candidate"], value["candidate_receipt"]):
                # Never place old bytes over a replacement that was just moved.
                publish_new_directory(withdrawn, root)
                raise ValueError("update candidate changed during withdrawal")
        if not _matches(previous, value["old"], value["old_receipt"]):
            raise ValueError("update backup changed during recovery")
        publish_new_directory(previous, root)
        if not _matches(root, value["old"], value["old_receipt"]):
            raise ValueError("update restoration unconfirmed")
        _restored_authority(value)
        return {"transaction": token, "restored": True, "evidence_retained": True}
