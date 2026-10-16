"""Host-only diagnostics for damaged transaction tails; no implicit repair.

Callers serialize state access. Observations are never retained as permission
for a later mutation; a repair must repeat ownership and file-version checks.
"""
from dataclasses import dataclass, replace
import hashlib
import json
import os
import re
import stat
import uuid

from core import plugin_state as state


@dataclass(frozen=True)
class StateObservation:
    status: str
    sha256: str
    phase: str


@dataclass(frozen=True)
class StateRepair:
    outcome: str
    evidence: str
    sha256: str


def _tail_record(content, root, plugin, caller):
    """Accept a complete bound header, never infer ownership from a filename."""
    header, separator, tail = content.partition(b"\n")
    if not separator or not tail:
        raise state.StateTransactionError("state transaction tail is not repairable")
    record = state._record_header(header, root, plugin, caller)
    try:
        outcome = json.loads(tail, object_pairs_hook=state._pairs)
    except json.JSONDecodeError:
        # A torn append can leave an incomplete JSON value. JSON metadata uses
        # ASCII, so invalid UTF-8 is refused separately below.
        return record
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise state.StateTransactionError("state transaction tail is not repairable") from exc
    if (tail.endswith(b"\n") or not isinstance(outcome, dict)
            or set(outcome) != {"transaction", "resolution"}
            or outcome["transaction"] != record.transaction
            or outcome["resolution"] not in ("aborted", "committed")):
        raise state.StateTransactionError("state transaction tail is not repairable")
    # An otherwise complete outcome without its final newline must agree with
    # the actual directories; never discard a contradictory completed outcome.
    state._inspect_topology(root, replace(record, resolution=outcome["resolution"]))
    if outcome["resolution"] == "committed":
        if state._inspect_topology(root, record).phase != "committed":
            raise state.StateTransactionError("state transaction outcome conflicts with topology")
    return record


def inspect_state_record(root, *, plugin, caller):
    """Read bounded metadata only and refuse invalid ownership or topology.

    No directories, lock files or output files are created. Missing records and
    OS errors propagate, so permission denial cannot be reported as clean state.
    """
    state._binding(plugin, caller)
    root = state._parent(root)
    content, version = state._read_record_bytes(root)
    try:
        record = state._decode_record(content, root, plugin, caller)
    except state.StateTransactionError:
        record = _tail_record(content, root, plugin, caller)
        status = "repairable-tail"
    else:
        status = "valid"
    plan = state._inspect_topology(root, record)
    if state._read_record_bytes(root) != (content, version):
        raise state.StateTransactionError("state transaction record changed")
    return StateObservation(status, hashlib.sha256(content).hexdigest(), plan.phase)


def _artifact_version(path, expected):
    """Verify a protected exclusive file without following a substituted link."""
    before = path.lstat()
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    if (not state._plain(before, stat.S_ISREG) or before.st_nlink != 1
            or before.st_size != len(expected)):
        raise state.StateTransactionError("state repair artifact changed")
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        content = stream.read(state.RECORD_LIMIT + 1)
        after = os.fstat(stream.fileno())
    published = path.lstat()
    versions = [(tuple(state._identity(info)), info.st_size, info.st_mtime_ns,
                 info.st_nlink, info.st_mode) for info in (before, opened, after, published)]
    if any(version != versions[0] for version in versions) or content != expected:
        raise state.StateTransactionError("state repair artifact changed")
    return versions[0]


def _write_artifact(path, content):
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        opened = os.fstat(stream.fileno())
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    version = _artifact_version(path, content)
    if version[0] != tuple(state._identity(opened)):
        raise state.StateTransactionError("state repair artifact changed")
    return version


def repair_state_record(root, *, plugin, caller, expected_sha256, checkpoint=lambda: None):
    """Repair one bound torn tail, retaining its original bytes permanently.

    The invoking parent must also hold its plugin state lock. A worker acquires
    this transaction guard itself. This is process-crash recovery; fsync here
    does not promise directory durability after power loss.
    """
    if not isinstance(expected_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise state.StateTransactionError("invalid state record SHA256")
    state._binding(plugin, caller)
    root = state._parent(root)
    from core.plugin_state_budget import platform_state_guard, reserve_platform_state, budget_scope
    with platform_state_guard(), state.transaction_guard(root):
        checkpoint()
        content, version = state._read_record_bytes(root)
        observation = inspect_state_record(root, plugin=plugin, caller=caller)
        if observation.sha256 != expected_sha256 or observation.status != "repairable-tail":
            raise state.StateTransactionError("state record is changed or not repairable")
        record = _tail_record(content, root, plugin, caller)
        reserve_platform_state(budget_scope(root),
                               reserve_bytes=state.RECORD_LIMIT * 2,
                               reserve_entries=2, reserve_staging=0)
        prefix = root.name + ".repair-"
        artifacts = 0
        with os.scandir(root.parent) as entries:
            for index, entry in enumerate(entries):
                if index >= state.MAX_PARTITION_ENTRIES:
                    raise state.StateTransactionError("state repair scan exceeds limit")
                if entry.name.startswith(prefix):
                    artifacts += 1
        # Per-target bounded evidence; unknown and partial artifacts count too.
        if artifacts > 14:
            raise state.StateTransactionError("state repair evidence quota exceeded")
        token = uuid.uuid4().hex
        evidence = root.with_name(prefix + token + ".evidence")
        pending = root.with_name(prefix + token + ".pending")
        checkpoint()
        evidence_version = _write_artifact(evidence, content)
        checkpoint()
        header = content.partition(b"\n")[0] + b"\n"
        pending_version = _write_artifact(pending, header)
        checkpoint()
        if (state._read_record_bytes(root) != (content, version)
                or _artifact_version(evidence, content) != evidence_version
                or _artifact_version(pending, header) != pending_version):
            raise state.StateTransactionError("state repair evidence changed")
        state._inspect_topology(root, record)
        # Recheck the original after topology observation, before replacement.
        if state._read_record_bytes(root) != (content, version):
            raise state.StateTransactionError("state transaction record changed")
        os.replace(pending, state.record_path(root))
        if _artifact_version(state.record_path(root), header) != pending_version:
            raise state.StateTransactionError("state repaired record changed")
        checkpoint()
        outcome = state.finish_transaction(root, plugin=plugin, caller=caller, checkpoint=checkpoint)
        return StateRepair(outcome, token, expected_sha256)
