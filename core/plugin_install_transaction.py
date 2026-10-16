"""Journalled, non-replacing publication of reviewed private plugin candidates.

This boundary grants no execution permission and never deletes a directory.
It is not yet the install/update orchestration or a global maintenance barrier.
"""
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tarfile
import time
import uuid

from core.plugin_fetch_candidate import MAX_ENTRIES, MAX_TREE_BYTES, _layout, validated_archive
from core.plugin_fetch_prepare import PreparedCandidate, _private_root
from core.plugin_fetch_review import CandidateReview, _manifest_digest, _review_tree
from core.plugin_metadata import path_present, sync_directory
from core.plugin_state import _directory, _valid_identity
from core.plugin_workspace import Workspace, journal_binding, recorded_workspace
from core.process_file import _SpawnFile

MAX_RECORD_BYTES = 16384
MAX_TRANSACTIONS = 256
NAMESPACE = ".opskit-install-transactions"
RECORD_SUFFIXES = (".json", ".published.json", ".withdrawal.json", ".withdrawn.json", ".aborted.json", ".retired.json")


class PublicationError(RuntimeError):
    def __init__(self, transaction):
        self.transaction = transaction
        super().__init__("plugin publication unconfirmed; retain workspace and inspect transaction " + transaction)


@dataclass(frozen=True)
class PublishedCandidate:
    transaction: str
    root: Path
    parent_identity: list
    review: CandidateReview


def _allowance(timeout):
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("invalid plugin publication allowance")
    return time.monotonic() + timeout


def _token(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ValueError("invalid publication transaction")
    return value


def _alias(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-z][a-z0-9-]{0,95}", value) is None:
        raise ValueError("invalid publication directory name")
    _layout(value, {})
    return value


def _private_directory(path):
    """Reject existing shared permissions; never repair them silently."""
    identity = _directory(path)
    if os.name == "nt":
        from core.private_file import _windows_permissions
        # The same opened-object ACL checker used for private preparation roots.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        handle = create(str(path), 0x20083, 3, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            _windows_permissions(None, handle=handle, directory=True)
        finally:
            close(handle)
    else:
        from core.private_file import verify_private_file
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            if [opened.st_dev, opened.st_ino] != identity:
                raise ValueError("publication directory changed")
            verify_private_file(descriptor)
        finally:
            os.close(descriptor)
    if _directory(path) != identity:
        raise ValueError("publication directory changed")
    return identity


def _roots(*, create=False):
    from core.paths import data_dir, plugins_dir
    root, state = plugins_dir().absolute(), data_dir().absolute()
    for path in (root, state):
        if len(str(path).encode()) > 4096 or ".." in path.parts:
            raise ValueError("invalid publication root")
        for ancestor in reversed((path, *path.parents)):
            try:
                _directory(ancestor)
            except FileNotFoundError:
                if not create:
                    raise
                ancestor.mkdir(mode=0o700)
        if path.resolve() != path:
            raise ValueError("noncanonical publication root")
    identities = [_private_directory(path) for path in (root, state)]
    journal = root / NAMESPACE
    if create:
        try:
            journal.mkdir(mode=0o700)
        except FileExistsError:
            pass
    return root, state, journal, identities


def _validate(record):
    from core.plugin_json import loads
    if isinstance(record, bytes):
        if len(record) > MAX_RECORD_BYTES:
            raise ValueError("publication record budget exceeded")
        record = loads(record.decode("utf-8"))
    fields = {"schema", "transaction", "target", "parent", "state", "workspace", "candidate",
              "plugin", "version", "manifest", "fingerprint", "receipt", "checksums"}
    if (not isinstance(record, dict) or set(record) != fields
            or type(record["schema"]) is not int or record["schema"] != 1):
        raise ValueError("invalid publication record")
    _token(record["transaction"])
    _alias(record["target"])
    if (not isinstance(record["plugin"], str) or re.fullmatch(r"[a-z][a-z0-9_]{0,127}", record["plugin"]) is None
            or not isinstance(record["version"], str) or not 0 < len(record["version"]) <= 256
            or record["checksums"] not in ("ok", "missing")
            or any(not isinstance(record[k], str) or re.fullmatch(r"[0-9a-f]{64}", record[k]) is None
                   for k in ("manifest", "fingerprint", "receipt"))):
        raise ValueError("invalid publication content binding")
    for key in ("parent", "state"):
        value = record[key]
        if (not isinstance(value, dict) or set(value) != {"path", "identity"}
                or not isinstance(value["path"], str) or len(value["path"].encode()) > 4096
                or not _valid_identity(value["identity"])):
            raise ValueError("invalid publication root binding")
    workspace = recorded_workspace(record["workspace"])
    value = record["candidate"]
    if (not isinstance(value, dict) or set(value) != {"identity", "commit", "archive", "files", "bytes"}
            or not _valid_identity(value["identity"])
            or not isinstance(value["commit"], str) or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value["commit"]) is None
            or not isinstance(value["archive"], str) or re.fullmatch(r"[0-9a-f]{64}", value["archive"]) is None
            or type(value["files"]) is not int or not 0 <= value["files"] <= MAX_ENTRIES
            or type(value["bytes"]) is not int or not 0 <= value["bytes"] <= MAX_TREE_BYTES):
        raise ValueError("invalid publication candidate binding")
    root, state, _, identities = _roots()
    if record["parent"] != {"path": str(root), "identity": identities[0]} or record["state"] != {"path": str(state), "identity": identities[1]}:
        raise ValueError("publication roots changed")
    if _directory(workspace.root.parent) != workspace.parent_identity:
        raise ValueError("publication workspace namespace changed")
    return record


def _read(journal, token):
    from core.cli_secrets import _read_secret_bytes
    record = _validate(_read_secret_bytes(journal / (_token(token) + ".json")))
    if record["transaction"] != token:
        raise ValueError("publication record token changed")
    return record


def _encoded(record):
    raw = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError("publication record budget exceeded")
    return raw


def _marker(journal, token, suffix, expected, *, create=False):
    from core.cli_secrets import _read_secret_bytes
    from core.plugin_json import loads
    from core.private_export import publish_private_file
    path = journal / (token + suffix)
    if not path_present(path):
        if not create:
            return False
        publish_private_file(path, _encoded(expected))
    actual = loads(_read_secret_bytes(path).decode())
    if not isinstance(actual, dict) or actual != expected or type(actual.get("schema")) is not int:
        raise ValueError("publication outcome changed")
    return True


def _outcome(journal, record, *, create=False):
    expected = {"schema": 1, "transaction": record["transaction"], "outcome": "published",
                "intent_sha256": hashlib.sha256(_encoded(record)).hexdigest()}
    return _marker(journal, record["transaction"], ".published.json", expected, create=create)


def _receipt(root, record):
    from core.plugin import load_manifest
    from core.plugin_install_receipt import _inventory
    from core.plugin_integrity import verify_checksums
    from core.plugin_trust import compute_fingerprint
    from core.plugin_tree import bounded_paths
    if _directory(root) != record["candidate"]["identity"]:
        raise ValueError("publication candidate identity changed")
    _private_directory(root)
    # Rollback's legacy receipt intentionally supports local Git object hard
    # links. Reviewed fetch candidates do not: reject newly observed links even
    # if their path/content/mode digest would otherwise remain unchanged.
    for path in bounded_paths(root, max_entries=MAX_ENTRIES, skip_dirs=frozenset()):
        info = path.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError("publication file acquired additional links")
    manifest = load_manifest(root)
    if (manifest is None or manifest.name != record["plugin"] or manifest.version != record["version"]
            or _manifest_digest(manifest) != record["manifest"] or _inventory(root) != record["receipt"]
            or compute_fingerprint(root) != record["fingerprint"] or verify_checksums(root) != record["checksums"]):
        raise ValueError("publication candidate content changed")
    if _directory(root) != record["candidate"]["identity"]:
        raise ValueError("publication candidate identity changed")


def _topology(journal, record):
    from core.plugin_install_retirement import abort_status, withdrawal_status
    resolved = abort_status(journal, record) or withdrawal_status(journal, record)
    if resolved is not None:
        return resolved
    workspace = recorded_workspace(record["workspace"])
    candidate = workspace.root / "candidate"
    destination = Path(record["parent"]["path"]) / record["target"]
    source_exists, target_exists = path_present(candidate), path_present(destination)
    completed = _outcome(journal, record)
    if source_exists and not target_exists and not completed:
        from core.plugin_fetch_prepare import _binding
        _binding(workspace)
        _private_directory(workspace.root)
        _receipt(candidate, record)
        return "prepared"
    if not source_exists and target_exists:
        _receipt(destination, record)
        return "published" if completed else "published-unconfirmed"
    raise ValueError("publication topology uncertain; objects retained")


@contextmanager
def _locks(root, plugin):
    from core.paths import data_dir
    from core.resource_lock import ResourceLock
    # Native locks are acquired in this supervised worker, never via a nested
    # multiprocessing child. The files match invoke and existing installation.
    with ResourceLock(lambda: data_dir() / "plugin-runtimes" / (plugin + ".lock"),
                      label="plugin invocation").hold():
        with ResourceLock(lambda: root / ".installation.lock", label="plugin installation").hold():
            yield


def _publish(source, workspace, reviewed, alias, transaction):
    from core.directory_publish import publish_new_directory
    from core.plugin import _list_manifests
    from core.plugin_trust import authorization_transaction, invalidate
    from core.private_export import publish_private_file
    from core.private_file import verify_private_file
    from core.plugin_install_retirement import publication_records
    root, state, journal, identities = _roots(create=True)
    journal_identity = _private_directory(journal)
    with source:
        verify_private_file(source.fileno())
        source.seek(0)
        with validated_archive(source) as (inventory, _), _private_root(workspace):
            manifest, actual = _review_tree(reviewed.candidate, inventory)
            if actual != reviewed:
                raise ValueError("reviewed candidate changed")
            with _locks(root, manifest.name), authorization_transaction():
                if _directory(root) != identities[0] or _directory(state) != identities[1]:
                    raise ValueError("publication roots changed")
                destination = root / alias
                if path_present(destination) or any(m.name == manifest.name for m in _list_manifests(root)):
                    raise ValueError("publication destination or plugin identity already exists")
                records = publication_records(journal)
                if len(records) >= MAX_TRANSACTIONS:
                    raise ValueError("publication journal capacity exceeded")
                if any(previous["target"] == alias or previous["plugin"] == manifest.name for previous in records):
                    raise ValueError("previous publication evidence retained")
                _, current = _review_tree(reviewed.candidate, inventory)
                if current != reviewed:
                    raise ValueError("reviewed candidate changed before publication")
                candidate = reviewed.candidate
                record = {"schema": 1, "transaction": transaction, "target": alias,
                          "parent": {"path": str(root), "identity": identities[0]},
                          "state": {"path": str(state), "identity": identities[1]},
                          "workspace": journal_binding(workspace), "candidate": {
                              "identity": candidate.identity, "commit": candidate.reported_commit,
                              "archive": candidate.archive_sha256, "files": candidate.files, "bytes": candidate.bytes},
                          "plugin": manifest.name, "version": manifest.version,
                          "manifest": reviewed.manifest_sha256, "fingerprint": reviewed.fingerprint,
                          "receipt": reviewed.content_receipt, "checksums": reviewed.checksums}
                _validate(record)
                publish_private_file(journal / (transaction + ".json"), _encoded(record))
                if _read(journal, transaction) != record or _directory(journal) != journal_identity:
                    raise ValueError("publication intent changed")
                # Revocation precedes visibility, while concurrent grants are
                # excluded. An old matching fingerprint must not authorize this.
                invalidate(manifest.name)
                _receipt(candidate.root, record)
                publish_new_directory(candidate.root, destination)
                _receipt(destination, record)
                if _read(journal, transaction) != record:
                    raise ValueError("publication intent changed")
                sync_directory(workspace.root, workspace.identity)
                sync_directory(root, identities[0])
                _outcome(journal, record, create=True)
                if _topology(journal, record) != "published":
                    raise ValueError("publication outcome unconfirmed")
                return identities[0]


def _worker(connection, operation, arguments, request):
    from network.worker import send
    try:
        if operation == "publish":
            result = {"parent": _publish(*arguments), "status": "published"}
        elif operation in ("withdraw", "retire", "abort"):
            from core.plugin_install_retirement import abort, withdraw, retire
            result = {"status": {"withdraw": withdraw, "retire": retire, "abort": abort}[operation](*arguments)}
        else:
            from core.plugin_install_retirement import read_publication, retirement_status, confirm_withdrawal
            token, = arguments
            root, _, journal, _ = _roots()
            _private_directory(journal)
            record = read_publication(journal, token)
            with (_locks(root, record["plugin"]) if operation == "recover" else nullcontext()):
                if read_publication(journal, token) != record:
                    raise ValueError("publication intent changed")
                status = retirement_status(journal, record) or _topology(journal, record)
                if operation == "recover" and status == "published-unconfirmed":
                    from core.plugin_trust import authorization_transaction, invalidate
                    with authorization_transaction():
                        invalidate(record["plugin"])
                        if _topology(journal, record) != status:
                            raise ValueError("publication topology changed")
                        sync_directory(root, record["parent"]["identity"])
                        workspace = recorded_workspace(record["workspace"])
                        if path_present(workspace.root):
                            sync_directory(workspace.root, workspace.identity)
                        _outcome(journal, record, create=True)
                        status = _topology(journal, record)
                elif operation == "recover" and status == "withdrawn-unconfirmed":
                    status = confirm_withdrawal(journal, record)
                result = {"status": status}
        result.update(ok=True, request=request)
    except (OSError, ValueError, TypeError, RuntimeError, EOFError, tarfile.TarError):
        result = {"ok": False, "request": request}
    try:
        send(connection, result)
    finally:
        connection.close()


def _run(operation, arguments, transaction, deadline):
    from network.worker import run
    request = uuid.uuid4().hex
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("plugin publication deadline exceeded; inspect " + transaction)
    try:
        result = run(_worker, (operation, arguments, request), timeout=remaining, label="plugin publication")
    except TimeoutError:
        raise TimeoutError("plugin publication deadline exceeded; retain workspace and inspect " + transaction) from None
    except (OSError, ValueError, RuntimeError):
        raise PublicationError(transaction) from None
    expected = {"ok", "request", "status"} | ({"parent"} if operation == "publish" else set())
    statuses = {"publish": ("published",), "withdraw": ("withdrawn",), "retire": ("absent",), "abort": ("aborted",)}
    allowed = statuses.get(operation, ("prepared", "published-unconfirmed", "published", "withdrawal-prepared",
                                       "withdrawn-unconfirmed", "withdrawn", "retiring", "aborted"))
    if (set(result) != expected or result.get("ok") is not True or result.get("request") != request
            or result.get("status") not in allowed
            or operation == "publish" and not _valid_identity(result["parent"])):
        raise PublicationError(transaction)
    if time.monotonic() >= deadline:
        raise TimeoutError("plugin publication deadline exceeded; inspect " + transaction)
    return result


def publish_candidate(response, workspace, reviewed, alias, *, timeout):
    """Publish once, without replacing, deleting or trusting any plugin.

    Caller must retain the workspace on uncertainty. A durable intent precedes
    revocation and rename; an immutable outcome follows content checks and sync.
    Recovery may confirm an already moved candidate, never publish a prepared
    one or guess ownership. Local trusted-account filesystems only; no claim of
    power-loss durability, global maintenance, or hostile same-account safety.
    """
    from core.paths import plugins_dir
    from core.plugin_services import invalidate_service_cache
    deadline = _allowance(timeout)
    if (not isinstance(workspace, Workspace) or not isinstance(reviewed, CandidateReview)
            or not isinstance(reviewed.candidate, PreparedCandidate)
            or reviewed.candidate.root != workspace.root / "candidate"):
        raise ValueError("invalid plugin publication request")
    _alias(alias)
    journal_binding(workspace)
    transaction = uuid.uuid4().hex
    result = _run("publish", (_SpawnFile(response.fileno()), workspace, reviewed, alias, transaction),
                  transaction, deadline)
    invalidate_service_cache()
    return PublishedCandidate(transaction, plugins_dir().absolute() / alias, result["parent"], reviewed)


def inspect_publication(transaction, *, timeout):
    """Read and verify an existing intent/topology; never create or recover it."""
    return _run("inspect", (_token(transaction),), transaction, _allowance(timeout))["status"]


def recover_publication(transaction, *, timeout):
    """Confirm a moved publication/withdrawal; never rename, delete or grant."""
    return _run("recover", (_token(transaction),), transaction, _allowance(timeout))["status"]


def withdraw_publication(transaction, workspace, *, timeout):
    """Explicitly withdraw only the unchanged receipted tree into an owned workspace.

    The workspace must be empty on first use. Keep it and the transaction token
    on uncertainty. No tree is deleted here; later cleanup is a separate action.
    """
    from core.plugin_services import invalidate_service_cache
    deadline = _allowance(timeout)
    _token(transaction)
    journal_binding(workspace)
    result = _run("withdraw", (transaction, workspace), transaction, deadline)["status"]
    invalidate_service_cache()
    return result


def retire_publication(transaction, *, timeout):
    """Retire exact metadata after withdrawal/abort and owned workspaces are gone.

    A resumable tombstone precedes any unlink. This never deletes plugin trees,
    grants trust, or removes unknown/changed records. 'absent' describes current
    metadata absence, not proof of a historical installation.
    """
    return _run("retire", (_token(transaction),), transaction, _allowance(timeout))["status"]


def abort_publication(transaction, *, timeout):
    """Explicitly abandon a still-prepared, unchanged publication.

    Records the decision and revokes matching trust; never moves or deletes a
    tree. The caller must separately clean the bound workspace before retiring
    metadata. Generic recovery never makes this decision on the caller's behalf.
    """
    return _run("abort", (_token(transaction),), transaction, _allowance(timeout))["status"]
