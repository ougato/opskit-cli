"""Explicit, resumable withdrawal and retirement of publication receipts.

All functions here run inside the existing publication worker. Only metadata
is unlinked; candidate cleanup remains with the identity-bound workspace API.
"""
import hashlib
import os
import re
import stat
from pathlib import Path

from core import plugin_install_transaction as publication
from core.plugin_metadata import path_present, sync_directory
from core.plugin_state import _directory
from core.plugin_workspace import journal_binding, recorded_workspace


def _read_file(path):
    from core.cli_secrets import _read_secret_bytes
    from core.plugin_install_receipt import _version
    from core.plugin_json import loads
    before = _version(path.lstat())
    raw = _read_secret_bytes(path)
    if len(raw) > publication.MAX_RECORD_BYTES or _version(path.lstat()) != before:
        raise ValueError("publication metadata changed while reading")
    return loads(raw.decode("utf-8")), {"version": list(before), "sha256": hashlib.sha256(raw).hexdigest()}


def _withdrawal_value(value, record):
    if (not isinstance(value, dict) or set(value) != {"schema", "transaction", "intent_sha256", "workspace"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or value["transaction"] != record["transaction"]
            or value["intent_sha256"] != hashlib.sha256(publication._encoded(record)).hexdigest()):
        raise ValueError("invalid withdrawal intent")
    workspace = recorded_workspace(value["workspace"])
    original = recorded_workspace(record["workspace"])
    if (workspace.root == original.root or workspace.identity == original.identity
            or workspace.identity == record["candidate"]["identity"]
            or _directory(workspace.root.parent) != workspace.parent_identity):
        raise ValueError("invalid withdrawal workspace binding")
    return value


def _withdrawal(journal, record):
    value, _ = _read_file(journal / (record["transaction"] + ".withdrawal.json"))
    return _withdrawal_value(value, record)


def _withdrawn(journal, record, withdrawal, *, create=False):
    value = {"schema": 1, "transaction": record["transaction"], "outcome": "withdrawn",
             "intent_sha256": hashlib.sha256(publication._encoded(withdrawal)).hexdigest()}
    return publication._marker(journal, record["transaction"], ".withdrawn.json", value, create=create)


def withdrawal_status(journal, record):
    token = record["transaction"]
    if not path_present(journal / (token + ".withdrawal.json")):
        if path_present(journal / (token + ".withdrawn.json")):
            raise ValueError("withdrawal outcome lacks intent")
        return None
    value = _withdrawal(journal, record)
    workspace = recorded_workspace(value["workspace"])
    original = recorded_workspace(record["workspace"])
    source = Path(record["parent"]["path"]) / record["target"]
    target = workspace.root / "candidate"
    if path_present(original.root / "candidate"):
        raise ValueError("withdrawal conflicts with unpublished candidate")
    source_exists, target_exists = path_present(source), path_present(target)
    completed = _withdrawn(journal, record, value)
    if source_exists and not target_exists and not completed:
        from core.plugin_fetch_prepare import _binding
        _binding(workspace)
        publication._private_directory(workspace.root)
        if any(workspace.root.iterdir()):
            raise ValueError("withdrawal workspace is not empty")
        publication._receipt(source, record)
        return "withdrawal-prepared"
    if not source_exists and target_exists:
        from core.plugin_fetch_prepare import _binding
        _binding(workspace)
        publication._private_directory(workspace.root)
        if {p.name for p in workspace.root.iterdir()} != {"candidate"}:
            raise ValueError("unexpected withdrawal workspace contents")
        publication._receipt(target, record)
        return "withdrawn" if completed else "withdrawn-unconfirmed"
    if not source_exists and not target_exists and completed:
        # Cleanup can finish between its last unlink and directory retirement.
        if path_present(workspace.root):
            from core.plugin_fetch_prepare import _binding
            _binding(workspace)
            if any(workspace.root.iterdir()):
                raise ValueError("unknown withdrawn workspace contents")
        return "withdrawn"
    raise ValueError("withdrawal topology uncertain; objects retained")


def _confirm(journal, record, value):
    from core.plugin_trust import authorization_transaction, invalidate
    workspace = recorded_workspace(value["workspace"])
    with authorization_transaction():
        invalidate(record["plugin"])
        if withdrawal_status(journal, record) != "withdrawn-unconfirmed":
            raise ValueError("withdrawal changed before confirmation")
        sync_directory(Path(record["parent"]["path"]), record["parent"]["identity"])
        sync_directory(workspace.root, workspace.identity)
        _withdrawn(journal, record, value, create=True)
        if withdrawal_status(journal, record) != "withdrawn":
            raise ValueError("withdrawal confirmation failed")
    return "withdrawn"


def confirm_withdrawal(journal, record):
    return _confirm(journal, record, _withdrawal(journal, record))


def _aborted(journal, record, *, create=False):
    expected = {"schema": 1, "transaction": record["transaction"], "outcome": "aborted",
                "intent_sha256": hashlib.sha256(publication._encoded(record)).hexdigest()}
    return publication._marker(journal, record["transaction"], ".aborted.json", expected, create=create)


def abort_status(journal, record):
    if not _aborted(journal, record):
        return None
    token = record["transaction"]
    if any(path_present(journal / (token + suffix)) for suffix in
           (".published.json", ".withdrawal.json", ".withdrawn.json")):
        raise ValueError("aborted publication has conflicting evidence")
    if path_present(Path(record["parent"]["path"]) / record["target"]):
        raise ValueError("aborted publication target exists; objects retained")
    workspace = recorded_workspace(record["workspace"])
    if path_present(workspace.root):
        from core.plugin_fetch_prepare import _binding
        _binding(workspace)
        publication._private_directory(workspace.root)
        names = {path.name for path in workspace.root.iterdir()}
        if names == {"candidate"}:
            publication._receipt(workspace.root / "candidate", record)
        elif names:
            raise ValueError("aborted workspace contains unknown objects")
    return "aborted"


def abort(token):
    from core.plugin_trust import authorization_transaction, invalidate
    root, _, journal, _ = publication._roots()
    publication._private_directory(journal)
    record = read_publication(journal, token)
    with publication._locks(root, record["plugin"]), authorization_transaction():
        if read_publication(journal, token) != record or retirement_status(journal, record):
            raise ValueError("publication changed before abort")
        status = publication._topology(journal, record)
        if status == "aborted":
            return status
        if status != "prepared":
            raise ValueError("only an unchanged prepared publication can be aborted")
        invalidate(record["plugin"])
        if read_publication(journal, token) != record or publication._topology(journal, record) != "prepared":
            raise ValueError("publication changed before abort confirmation")
        _aborted(journal, record, create=True)
        if abort_status(journal, record) != "aborted":
            raise ValueError("publication abort unconfirmed")
    return "aborted"


def withdraw(token, workspace):
    from core.directory_publish import publish_new_directory
    from core.plugin_fetch_prepare import _private_root
    from core.plugin_trust import authorization_transaction, invalidate
    from core.private_export import publish_private_file
    root, _, journal, _ = publication._roots()
    publication._private_directory(journal)
    record = read_publication(journal, token)
    with publication._locks(root, record["plugin"]):
        if read_publication(journal, token) != record or retirement_status(journal, record):
            raise ValueError("publication changed before withdrawal")
        status = publication._topology(journal, record)
        path = journal / (token + ".withdrawal.json")
        if path_present(path):
            value = _withdrawal(journal, record)
            if value["workspace"] != journal_binding(workspace):
                raise ValueError("different withdrawal workspace retained")
            if status == "withdrawn":
                return status
            if status == "withdrawn-unconfirmed":
                return _confirm(journal, record, value)
            if status != "withdrawal-prepared":
                raise ValueError("withdrawal is not resumable")
        else:
            if status not in ("published", "published-unconfirmed"):
                raise ValueError("only a published candidate can be withdrawn")
            with _private_root(workspace):
                if any(workspace.root.iterdir()):
                    raise ValueError("withdrawal workspace is not empty")
            value = {"schema": 1, "transaction": token,
                     "intent_sha256": hashlib.sha256(publication._encoded(record)).hexdigest(),
                     "workspace": journal_binding(workspace)}
            _withdrawal_value(value, record)
            publish_private_file(path, publication._encoded(value))
            if _withdrawal(journal, record) != value:
                raise ValueError("withdrawal intent changed")
        with _private_root(workspace), authorization_transaction():
            if read_publication(journal, token) != record or withdrawal_status(journal, record) != "withdrawal-prepared":
                raise ValueError("withdrawal source changed")
            invalidate(record["plugin"])
            source, target = root / record["target"], workspace.root / "candidate"
            publication._receipt(source, record)
            publish_new_directory(source, target)
            publication._receipt(target, record)
            if read_publication(journal, token) != record or _withdrawal(journal, record) != value:
                raise ValueError("withdrawal evidence changed")
            return _confirm(journal, record, value)


def _retirement_value(value, token):
    fields = {"schema", "transaction", "intent", "withdrawal", "files"}
    if (not isinstance(value, dict) or set(value) != fields or type(value["schema"]) is not int
            or value["schema"] != 1 or value["transaction"] != token):
        raise ValueError("invalid publication retirement intent")
    record = publication._validate(value["intent"])
    if record["transaction"] != token:
        raise ValueError("retirement transaction mismatch")
    if value["withdrawal"] is not None:
        _withdrawal_value(value["withdrawal"], record)
    files = value["files"]
    required = ({".json", ".aborted.json"} if value["withdrawal"] is None else
                {".json", ".withdrawal.json", ".withdrawn.json"})
    optional = set() if value["withdrawal"] is None else {".published.json"}
    if not isinstance(files, dict) or not required <= set(files) <= required | optional:
        raise ValueError("invalid retirement file inventory")
    for item in files.values():
        if not isinstance(item, dict) or set(item) != {"version", "sha256"}:
            raise ValueError("invalid retirement file binding")
        version, digest = item["version"], item["sha256"]
        if (not isinstance(version, list) or len(version) != 7
                or any(type(v) is not int or v < 0 for v in version)
                or not stat.S_ISREG(version[2]) or version[1] <= 0 or version[3] != 1
                or version[4] > publication.MAX_RECORD_BYTES
                or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
            raise ValueError("invalid retirement file version")
    return value


def _tombstone(journal, token):
    value, version = _read_file(journal / (token + ".retired.json"))
    return _retirement_value(value, token), version


def read_publication(journal, token):
    if path_present(journal / (token + ".retired.json")):
        return _tombstone(journal, token)[0]["intent"]
    return publication._read(journal, token)


def retirement_status(journal, record):
    token = record["transaction"]
    if not path_present(journal / (token + ".retired.json")):
        return None
    value, _ = _tombstone(journal, token)
    if value["intent"] != record:
        raise ValueError("retirement intent changed")
    _retirement_guard(journal, value)
    return "retiring"


def publication_records(journal):
    tokens = set()
    with os.scandir(journal) as entries:
        for count, entry in enumerate(entries, 1):
            if count > publication.MAX_TRANSACTIONS * len(publication.RECORD_SUFFIXES):
                raise ValueError("publication journal capacity exceeded")
            token, suffix = entry.name[:32], entry.name[32:]
            publication._token(token)
            if suffix not in publication.RECORD_SUFFIXES:
                raise ValueError("unknown publication evidence retained")
            tokens.add(token)
            if len(tokens) > publication.MAX_TRANSACTIONS:
                raise ValueError("publication journal capacity exceeded")
    return [read_publication(journal, token) for token in sorted(tokens)]


def _retirement_guard(journal, tomb):
    record = publication._validate(tomb["intent"])
    if path_present(Path(record["parent"]["path"]) / record["target"]):
        raise ValueError("publication target exists; metadata retained")
    original = recorded_workspace(record["workspace"])
    withdrawn = (recorded_workspace(tomb["withdrawal"]["workspace"])
                 if tomb["withdrawal"] is not None else None)
    if path_present(original.root) or withdrawn is not None and path_present(withdrawn.root):
        raise ValueError("publication workspaces must be cleaned before metadata retirement")
    for suffix in publication.RECORD_SUFFIXES[:-1]:
        if suffix not in tomb["files"] and path_present(journal / (record["transaction"] + suffix)):
            raise ValueError("unexpected publication metadata; retirement refused")
    for suffix, expected in tomb["files"].items():
        path = journal / (record["transaction"] + suffix)
        if path_present(path) and _read_file(path)[1] != expected:
            raise ValueError("publication metadata changed; retirement refused")


def retire(token):
    from core.private_export import publish_private_file
    root, _, journal, _ = publication._roots()
    identity = publication._private_directory(journal)
    names = [journal / (token + suffix) for suffix in publication.RECORD_SUFFIXES]
    if not any(path_present(path) for path in names):
        return "absent"
    record = read_publication(journal, token)
    with publication._locks(root, record["plugin"]):
        path = journal / (token + ".retired.json")
        if not path_present(path):
            status = publication._topology(journal, record)
            if read_publication(journal, token) != record or status not in ("withdrawn", "aborted"):
                raise ValueError("only a withdrawn or aborted publication can be retired")
            value = _withdrawal(journal, record) if status == "withdrawn" else None
            files = {}
            for suffix in publication.RECORD_SUFFIXES[:-1]:
                entry = journal / (token + suffix)
                if path_present(entry):
                    files[suffix] = _read_file(entry)[1]
            tomb = {"schema": 1, "transaction": token, "intent": record, "withdrawal": value, "files": files}
            _retirement_value(tomb, token)
            _retirement_guard(journal, tomb)
            publish_private_file(path, publication._encoded(tomb))
        tomb, version = _tombstone(journal, token)
        if tomb["intent"] != record:
            raise ValueError("retirement intent changed")
        # The tombstone remains sufficient authority after the primary intent
        # has been unlinked. Resume only exact file versions/digests it names.
        for suffix in publication.RECORD_SUFFIXES[:-1]:
            if suffix not in tomb["files"]:
                continue
            _retirement_guard(journal, tomb)
            if _tombstone(journal, token) != (tomb, version):
                raise ValueError("retirement intent replaced")
            entry = journal / (token + suffix)
            if path_present(entry):
                entry.unlink()
                sync_directory(journal, identity)
        _retirement_guard(journal, tomb)
        if any(path_present(journal / (token + suffix)) for suffix in publication.RECORD_SUFFIXES[:-1]):
            raise ValueError("publication metadata remains")
        if _tombstone(journal, token) != (tomb, version):
            raise ValueError("retirement intent changed")
        path.unlink()
        sync_directory(journal, identity)
        if any(path_present(entry) for entry in names):
            raise ValueError("publication metadata reappeared")
    return "absent"
