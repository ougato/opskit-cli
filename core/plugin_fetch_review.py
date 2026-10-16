"""Review a private candidate against captured bytes without granting trust."""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import PurePosixPath
import re
import stat
import tarfile
import time
import uuid

from core.plugin_fetch_candidate import MAX_ENTRIES, MAX_TREE_BYTES, validated_archive
from core.plugin_fetch_prepare import PreparedCandidate, _private_root
from core.process_file import _SpawnFile
from core.plugin_workspace import Workspace, journal_binding


@dataclass(frozen=True)
class CandidateReview:
    candidate: PreparedCandidate
    manifest_sha256: str
    fingerprint: str
    content_receipt: str
    checksums: str


def _manifest_digest(manifest):
    from core.plugin_inspection import _manifest_record
    encoded = json.dumps(_manifest_record(manifest), ensure_ascii=False,
                         sort_keys=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _raw_tree(root, inventory):
    """Include every node, including files excluded by the execution fingerprint."""
    from core.plugin_install_receipt import _version
    from core.plugin_tree import bounded_paths
    from core.private_file import verify_private_file
    expected = {entry.path: entry for entry in inventory.files}
    directories = {str(parent) for entry in inventory.files
                   for parent in PurePosixPath(entry.path).parents if str(parent) != "."}
    seen = set()
    for path in bounded_paths(root, max_entries=MAX_ENTRIES, max_depth=64, skip_dirs=frozenset()):
        name = path.relative_to(root).as_posix()
        before = path.lstat()
        if stat.S_ISDIR(before.st_mode):
            if name not in directories:
                raise ValueError("unexpected candidate directory")
            directories.remove(name)
            continue
        item = expected.get(name)
        if (item is None or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size != item.size):
            raise ValueError("unexpected candidate file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(descriptor, "rb") as source:
            verify_private_file(source.fileno())
            opened = os.fstat(source.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or _version(opened)[:-1] != _version(before)[:-1]):
                raise ValueError("candidate changed while opening")
            if os.name != "nt" and opened.st_mode & 0o777 != (0o700 if item.mode == 0o755 else 0o600):
                raise ValueError("candidate execution mode changed")
            digest = hashlib.sha256()
            remaining = item.size
            while remaining:
                chunk = source.read(min(65536, remaining))
                if not chunk:
                    raise ValueError("candidate truncated")
                digest.update(chunk)
                remaining -= len(chunk)
            if (source.read(1) or digest.hexdigest() != item.sha256
                    or _version(os.fstat(source.fileno())) != _version(opened)):
                raise ValueError("candidate bytes changed")
        if _version(path.lstat()) != _version(before):
            raise ValueError("candidate file replaced")
        seen.add(name)
    if seen != set(expected) or directories:
        raise ValueError("candidate inventory changed")


def _entry(manifest, inventory):
    # Reuse the pure envelope validator used by the actual Linux plugin worker.
    # It does not import or execute the candidate's entry module.
    from core.plugin_worker import _request
    from core.plugin_json import RPC_VERSION
    _request({"rpc_version": RPC_VERSION, "action": "entry", "name": manifest.name,
              "entry": manifest.entry, "kind": manifest.kind, "args": [], "kwargs": {},
              "service": "", "method": "", "caller": "platform", "service_contract": None})
    files = {item.path for item in inventory.files}
    if manifest.kind == "python":
        if not {manifest.entry + ".py", manifest.entry + "/__init__.py"} & files:
            raise ValueError("candidate Python entry is missing")
    else:
        entry = manifest.entry.get("linux") if isinstance(manifest.entry, dict) else manifest.entry
        if entry not in files:
            raise ValueError("candidate executable entry is missing")
        selected = next(item for item in inventory.files if item.path == entry)
        if selected.mode != 0o755:
            raise ValueError("candidate executable entry is not executable")


def _review_tree(prepared, inventory):
    """Shared final-content check; callers pin the private owning workspace."""
    from core.loader import builtin_module_keys
    from core.plugin import load_manifest
    from core.plugin_integrity import CHECK_MISMATCH, verify_checksums
    from core.plugin_install_receipt import _inventory
    from core.plugin_state import _directory
    from core.plugin_trust import compute_fingerprint
    if ((prepared.reported_commit, prepared.archive_sha256, prepared.files, prepared.bytes)
            != (inventory.reported_commit, inventory.archive_sha256, len(inventory.files), inventory.bytes)):
        raise ValueError("candidate response binding changed")
    if _directory(prepared.root) != prepared.identity:
        raise ValueError("candidate identity changed")
    _raw_tree(prepared.root, inventory)
    manifest = load_manifest(prepared.root)
    if manifest is None or manifest.name in builtin_module_keys():
        raise ValueError("candidate manifest rejected")
    _entry(manifest, inventory)
    checksums = verify_checksums(prepared.root)
    if checksums == CHECK_MISMATCH:
        raise ValueError("candidate checksum mismatch")
    fingerprint = compute_fingerprint(prepared.root)
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("candidate fingerprint unavailable")
    receipt = _inventory(prepared.root)
    _raw_tree(prepared.root, inventory)
    if _directory(prepared.root) != prepared.identity:
        raise ValueError("candidate identity changed")
    return manifest, CandidateReview(prepared, _manifest_digest(manifest), fingerprint, receipt, checksums)


def _review_worker(connection, source, workspace, prepared, request):
    from core.private_file import verify_private_file
    from network.worker import send
    try:
        with source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("candidate response must be ordinary")
            verify_private_file(source.fileno())
            source.seek(0)
            with validated_archive(source) as (inventory, _):
                with _private_root(workspace):
                    _, review = _review_tree(prepared, inventory)
                    result = {"ok": True, "request": request, "manifest": review.manifest_sha256,
                              "fingerprint": review.fingerprint, "receipt": review.content_receipt,
                              "checksums": review.checksums}
    except (OSError, ValueError, TypeError, RuntimeError, EOFError, tarfile.TarError):
        # Metadata/parser errors never disclose candidate content over the pipe.
        result = {"ok": False, "request": request}
    try:
        send(connection, result)
    finally:
        connection.close()


def review_candidate(response, workspace, prepared, *, timeout):
    """Check all captured bytes plus existing manifest/integrity/entry policies.

    A missing CHECKSUMS.yaml retains the existing explicit TOFU decision; this
    result grants no permission and authenticates no Git source. The caller
    must rebind this private-tree receipt and manifest digest at publication.
    Workspace ownership and hostile-filesystem limits match prepare_candidate.
    """
    from core.plugin_state import _valid_identity
    from network.worker import run
    if (not isinstance(workspace, Workspace) or not isinstance(prepared, PreparedCandidate)
            or prepared.root != workspace.root / "candidate" or not _valid_identity(prepared.identity)
            or type(prepared.files) is not int or not 0 <= prepared.files <= MAX_ENTRIES
            or type(prepared.bytes) is not int or not 0 <= prepared.bytes <= MAX_TREE_BYTES
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("invalid candidate review request")
    journal_binding(workspace)
    deadline = time.monotonic() + timeout
    descriptor = response.fileno()
    request = uuid.uuid4().hex
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("candidate review deadline exceeded; workspace retained")
    result = run(_review_worker, (_SpawnFile(descriptor), workspace, prepared, request),
                 timeout=remaining, label="plugin candidate review")
    if result == {"ok": False, "request": request}:
        raise ValueError("candidate review rejected; workspace retained")
    if (not isinstance(result, dict)
            or set(result) != {"ok", "request", "manifest", "fingerprint", "receipt", "checksums"}
            or result["ok"] is not True or result["request"] != request
            or result["checksums"] not in ("ok", "missing")
            or any(not isinstance(result[key], str) or not re.fullmatch(r"[0-9a-f]{64}", result[key])
                   for key in ("manifest", "fingerprint", "receipt"))):
        raise RuntimeError("invalid candidate review result; workspace retained")
    if time.monotonic() >= deadline:
        raise TimeoutError("candidate review deadline exceeded; workspace retained")
    return CandidateReview(prepared, result["manifest"], result["fingerprint"], result["receipt"], result["checksums"])
