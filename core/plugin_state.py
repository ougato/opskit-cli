"""Host-only ownership records for plugin state transactions.

These records describe ownership, not authorization. Callers must hold the plugin
state lock. Reading a record never renames, deletes, or publishes directories.
"""
from dataclasses import dataclass
from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import re
import stat
import uuid

from core.plugin_metadata import path_present as _present

RECORD_LIMIT = 4096
MAX_STAGING_DIRECTORIES = 8
MAX_STAGING_BYTES = 128 * 1024**2
STAGING_RESERVATION_BYTES = 16 * 1024**2
MAX_PARTITION_ENTRIES = 4096
MAX_STAGING_ENTRIES = MAX_STAGING_DIRECTORIES * 8192
_FIELDS = {"schema", "transaction", "plugin", "caller", "target", "parent",
           "parent_identity", "old_identity", "new_identity", "staging",
           "staging_identity"}


class StateTransactionError(ValueError):
    """Ownership cannot be established; retain all transaction evidence."""


class StateCleanupError(StateTransactionError):
    """The outcome is known, but retained cleanup evidence needs another pass."""

    def __init__(self, outcome):
        self.outcome = outcome
        super().__init__(f"state transaction {outcome}; cleanup incomplete")


def _plain(info, kind):
    return (kind(info.st_mode)
            and not getattr(info, "st_file_attributes", 0) & 0x400)


def _identity(info):
    if info.st_ino <= 0:
        raise StateTransactionError("state directory identity unavailable")
    return [info.st_dev, info.st_ino]


def _directory(path):
    info = path.lstat()
    if not _plain(info, stat.S_ISDIR):
        raise StateTransactionError("state directory must not be linked or special")
    return _identity(info)


def _parent(root):
    root = Path(root).absolute()
    if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", root.name) is None:
        raise StateTransactionError("invalid state target name")
    for path in (root.parent, *root.parent.parents):
        _directory(path)
    if root.parent.resolve() != root.parent:
        raise StateTransactionError("state parent must be canonical")
    return root


def _binding(plugin, caller):
    if (not isinstance(plugin, str) or not isinstance(caller, str)
            or any(re.fullmatch(r"[a-z][a-z0-9_]{0,127}", value) is None
                   for value in (plugin, caller)) or plugin == "platform"):
        raise StateTransactionError("invalid state ownership binding")


def record_path(root):
    root = Path(root)
    return root.with_name(root.name + ".transaction.json")


@contextmanager
def transaction_guard(root):
    """Serialize transaction mutations independently of the invocation owner.

    The lock file remains in place: removing it can split old and new fd locks.
    This guard is deliberately non-reentrant across separate instances. Workers
    must acquire it themselves, never while their parent holds the same guard.
    """
    from core.resource_lock import ResourceLock
    root = _parent(root)
    parent_identity = _directory(root.parent)
    lock_path = transaction_lock_path(root)
    lock = ResourceLock(lambda: lock_path, label="plugin state transaction")
    with lock.hold():
        if _parent(root) != root or _directory(root.parent) != parent_identity:
            raise StateTransactionError("state transaction parent changed")
        yield


def transaction_lock_path(root):
    """Platform metadata, outside the plugin's persistent payload namespace."""
    from core.paths import data_dir
    root = _parent(root)
    key = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()
    return data_dir() / "plugin-runtimes" / "state-transactions" / (key + ".lock")


@contextmanager
def staging_guard(root):
    """Serialize allocation and candidate growth across targets in one parent."""
    from core.paths import data_dir
    from core.resource_lock import ResourceLock
    root = _parent(root)
    parent_identity = _directory(root.parent)
    key = hashlib.sha256(os.path.normcase(str(root.parent)).encode("utf-8")).hexdigest()
    path = data_dir() / "plugin-runtimes" / "state-transactions" / ("staging-" + key + ".lock")
    with ResourceLock(lambda: path, label="plugin state staging").hold():
        if _parent(root) != root or _directory(root.parent) != parent_identity:
            raise StateTransactionError("state staging parent changed")
        yield


def reserve_staging(root, *, checkpoint=lambda: None):
    """Check logical-payload and entry headroom under the plugin state lock.

    Unknown .state-* entries are charged, never reclaimed or trusted by name.
    The next mkdir must occur under the same lock. This is not a filesystem
    allocation quota: directory metadata, allocation units and free space differ.
    """
    from core.plugin_tree import bounded_paths
    root = _parent(root)
    parent_identity = _directory(root.parent)
    count = size = nodes = 0
    with os.scandir(root.parent) as entries:
        for index, entry in enumerate(entries):
            checkpoint()
            if index >= MAX_PARTITION_ENTRIES:
                raise StateTransactionError("state partition inventory exceeds limit")
            if not entry.name.startswith(".state-"):
                continue
            count += 1
            if count + 1 > MAX_STAGING_DIRECTORIES:
                raise StateTransactionError("state staging quota exhausted; retained evidence preserved")
            wrapper = root.parent / entry.name
            identity = _directory(wrapper)
            for path in bounded_paths(wrapper, max_entries=MAX_STAGING_ENTRIES,
                                      skip_dirs=frozenset(), checkpoint=checkpoint):
                nodes += 1
                if nodes > MAX_STAGING_ENTRIES:
                    raise StateTransactionError("state staging inventory exceeds limit")
                info = path.lstat()
                if _plain(info, stat.S_ISREG) and info.st_nlink == 1:
                    size += info.st_size
                elif not _plain(info, stat.S_ISDIR):
                    raise StateTransactionError("state staging has linked or special entries")
                if size + STAGING_RESERVATION_BYTES > MAX_STAGING_BYTES:
                    raise StateTransactionError("state staging byte quota exhausted; retained evidence preserved")
            if _directory(wrapper) != identity:
                raise StateTransactionError("state staging changed during inventory")
    checkpoint()
    if _directory(root.parent) != parent_identity:
        raise StateTransactionError("state partition changed during inventory")
    return count, size


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise StateTransactionError("duplicate state transaction field")
        result[key] = value
    return result


def _valid_identity(value):
    return (isinstance(value, list) and len(value) == 2
            and all(type(part) is int and part >= 0 for part in value)
            and value[1] > 0)


@dataclass(frozen=True)
class StateRecord:
    """Validated metadata; no file path is taken from an unchecked record."""

    transaction: str
    old_identity: tuple | None
    new_identity: tuple
    staging: str
    staging_identity: tuple
    resolution: str = "pending"


def _validate(record, root, plugin, caller):
    if (not isinstance(record, dict) or set(record) != _FIELDS
            or type(record["schema"]) is not int or record["schema"] != 1
            or not isinstance(record["transaction"], str)
            or re.fullmatch(r"[0-9a-f]{32}", record["transaction"]) is None
            or record["plugin"] != plugin or record["caller"] != caller
            or record["target"] != root.name or record["parent"] != str(root.parent)
            or not isinstance(record["staging"], str)
            or re.fullmatch(r"\.state-[a-zA-Z0-9_-]{1,120}", record["staging"]) is None
            or any(not _valid_identity(record[key])
                   for key in ("parent_identity", "new_identity", "staging_identity"))
            or (record["old_identity"] is not None
                and not _valid_identity(record["old_identity"]))):
        raise StateTransactionError("invalid state transaction record")
    if record["parent_identity"] != _directory(root.parent):
        raise StateTransactionError("state transaction parent replaced")
    identities = [record["parent_identity"], record["new_identity"], record["staging_identity"]]
    if record["old_identity"] is not None:
        identities.append(record["old_identity"])
    if len({tuple(value) for value in identities}) != len(identities):
        raise StateTransactionError("state transaction directories overlap")
    return StateRecord(record["transaction"],
                       tuple(record["old_identity"]) if record["old_identity"] is not None else None,
                       tuple(record["new_identity"]), record["staging"],
                       tuple(record["staging_identity"]))


def prepare_record(root, staging, *, plugin, caller):
    """Create an exclusive ownership marker before any publication rename.

    Failed writes deliberately retain the marker. This fsyncs the record only;
    it does not establish directory or state-data power-loss durability.
    """
    _binding(plugin, caller)
    root = _parent(root)
    staging = Path(staging).absolute()
    if (staging.name != "new" or staging.parent.parent != root.parent
            or _present(root.with_name(root.name + ".previous"))):
        raise StateTransactionError("unresolved or invalid state staging")
    record = {
        "schema": 1, "transaction": uuid.uuid4().hex, "plugin": plugin, "caller": caller,
        "target": root.name, "parent": str(root.parent),
        "parent_identity": _directory(root.parent),
        "old_identity": _directory(root) if _present(root) else None,
        "new_identity": _directory(staging), "staging": staging.parent.name,
        "staging_identity": _directory(staging.parent),
    }
    validated = _validate(record, root, plugin, caller)
    data = json.dumps(record, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
    if len(data) > RECORD_LIMIT - 128:
        raise StateTransactionError("state transaction record exceeds limit")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(record_path(root), flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return validated


def _read_record_bytes(root):
    """Return bounded bytes and their exact file version, without parsing."""
    root = _parent(root)
    path = record_path(root)
    before = path.lstat()
    if (not _plain(before, stat.S_ISREG) or before.st_nlink != 1
            or before.st_size > RECORD_LIMIT):
        raise StateTransactionError("invalid state transaction file")
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(os.open(path, flags), "rb") as stream:
        current = os.fstat(stream.fileno())
        if (not _plain(current, stat.S_ISREG) or current.st_nlink != 1
                or _identity(current) != _identity(before) or current.st_size > RECORD_LIMIT):
            raise StateTransactionError("state transaction file changed")
        content = stream.read(RECORD_LIMIT + 1)
        after = os.fstat(stream.fileno())
    published = path.lstat()
    if (len(content) > RECORD_LIMIT or len(content) != current.st_size
            or (after.st_size, after.st_mtime_ns) != (current.st_size, current.st_mtime_ns)
            or not _plain(published, stat.S_ISREG) or published.st_nlink != 1
            or _identity(published) != _identity(current)
            or (published.st_size, published.st_mtime_ns) != (current.st_size, current.st_mtime_ns)):
        raise StateTransactionError("state transaction file changed")
    return content, (*_identity(current), current.st_size, current.st_mtime_ns)


def _record_header(content, root, plugin, caller):
    initial = content.partition(b"\n")[0]
    try:
        return _validate(json.loads(initial, object_pairs_hook=_pairs), root, plugin, caller)
    except StateTransactionError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise StateTransactionError("invalid state transaction JSON") from exc


def _decode_record(content, root, plugin, caller):
    try:
        initial, _, tail = content.partition(b"\n")
        validated = _record_header(initial, root, plugin, caller)
        if tail:
            outcome = json.loads(tail, object_pairs_hook=_pairs)
            if (not tail.endswith(b"\n") or not isinstance(outcome, dict)
                    or set(outcome) != {"transaction", "resolution"}
                    or outcome["transaction"] != validated.transaction
                    or outcome["resolution"] not in ("aborted", "committed")):
                raise StateTransactionError("invalid state transaction resolution")
            from dataclasses import replace
            validated = replace(validated, resolution=outcome["resolution"])
    except StateTransactionError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise StateTransactionError("invalid state transaction JSON") from exc
    return validated


def read_record(root, *, plugin, caller):
    """Bounded read; reject changed, linked, duplicated or foreign metadata."""
    _binding(plugin, caller)
    root = _parent(root)
    content, _ = _read_record_bytes(root)
    return _decode_record(content, root, plugin, caller)


@dataclass(frozen=True)
class RecoveryPlan:
    """Observed topology under the caller's state lock; never a cached permit."""

    record: StateRecord
    phase: str


def _optional_directory(path):
    try:
        return tuple(_directory(path))
    except FileNotFoundError:
        return None


def _inspect_topology(root, record):
    """Classify only identity-matching topologies, without reading state data.

    Missing or replaced candidate data before commit is ambiguous and refused.
    Missing backup/wrapper after a proven new-root publication permits later
    cleanup reconciliation. Unknown wrapper entries are never claimed.
    """
    root = _parent(root)
    current = _optional_directory(root)
    backup = _optional_directory(root.with_name(root.name + ".previous"))
    wrapper = root.parent / record.staging
    wrapper_identity = _optional_directory(wrapper)
    candidate = None
    if wrapper_identity is not None:
        if wrapper_identity != record.staging_identity:
            raise StateTransactionError("state staging directory replaced")
        with os.scandir(wrapper) as entries:
            for index, entry in enumerate(entries):
                if index > 0 or entry.name != "new":
                    raise StateTransactionError("state staging has unknown entries")
        candidate = _optional_directory(wrapper / "new")
    if candidate not in (None, record.new_identity):
        raise StateTransactionError("state candidate directory replaced")
    if backup is not None and (record.old_identity is None or backup != record.old_identity):
        raise StateTransactionError("state backup directory replaced")
    if record.resolution == "aborted":
        if current != record.old_identity or backup is not None:
            raise StateTransactionError("aborted state transaction changed")
        phase = "uncommitted"
    elif current == record.new_identity and candidate is None:
        phase = "committed"
    elif record.resolution == "pending" and candidate == record.new_identity and wrapper_identity == record.staging_identity:
        if current is None and backup is not None and record.old_identity is not None:
            phase = "restore_previous"
        elif current == record.old_identity and backup is None:
            phase = "uncommitted"
        else:
            raise StateTransactionError("ambiguous state transaction topology")
    else:
        raise StateTransactionError("ambiguous state transaction topology")
    return RecoveryPlan(record, phase)


def inspect_transaction(root, *, plugin, caller):
    """Classify directory ownership and re-read the bound journal afterward."""
    _binding(plugin, caller)
    root = _parent(root)
    content, version = _read_record_bytes(root)
    record = _decode_record(content, root, plugin, caller)
    plan = _inspect_topology(root, record)
    # Re-read the exact bounded record and parent after observing the topology.
    # No caller may retain this plan across releasing its plugin state lock.
    if _read_record_bytes(root) != (content, version):
        raise StateTransactionError("state transaction record changed")
    return plan


def restore_previous(root, *, plugin, caller, rename=os.rename):
    """Restore only the recorded old directory; retain journal and candidate.

    Repeating after process exit immediately following rename is safe: the
    topology then reports uncommitted, with no second rename. This intentionally
    does not retire evidence or authorize the next invocation; cleanup and
    production integration must be completed before automatic execution resumes.
    """
    root = _parent(root)
    plan = inspect_transaction(root, plugin=plugin, caller=caller)
    if plan.phase != "restore_previous":
        return plan
    if inspect_transaction(root, plugin=plugin, caller=caller) != plan:
        raise StateTransactionError("state transaction topology changed")
    rename(root.with_name(root.name + ".previous"), root)
    restored = inspect_transaction(root, plugin=plugin, caller=caller)
    if restored.record != plan.record or restored.phase != "uncommitted":
        raise StateTransactionError("state restoration could not be verified")
    return restored


def confirm_prepared_record(root, staging, *, plugin, caller):
    """Reconfirm the preparation journal under publication's authorization lock."""
    root, staging = _parent(root), Path(staging)
    plan = inspect_transaction(root, plugin=plugin, caller=caller)
    if (plan.phase != "uncommitted" or plan.record.resolution != "pending"
            or staging.parent.parent != root.parent
            or plan.record.staging != staging.parent.name
            or plan.record.staging_identity != tuple(_directory(staging.parent))
            or plan.record.new_identity != tuple(_directory(staging))):
        raise StateTransactionError("state publication ownership changed")
    path = record_path(root)
    before = path.lstat()
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "r+b") as stream:
        opened = os.fstat(stream.fileno())
        if (not _plain(opened, stat.S_ISREG) or opened.st_nlink != 1
                or _identity(opened) != _identity(before)):
            raise StateTransactionError("state preparation journal changed")
        os.fsync(stream.fileno())
        published = path.lstat()
        if (not _plain(published, stat.S_ISREG) or published.st_nlink != 1
                or _identity(published) != _identity(opened)
                or (published.st_size, published.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns)):
            raise StateTransactionError("state preparation journal changed")
        if inspect_transaction(root, plugin=plugin, caller=caller) != plan:
            raise StateTransactionError("state preparation journal changed")
    return plan


def _record_resolution(root, plan, *, plugin, caller):
    """Append the one bounded outcome before deleting any owned directory.

    A truncated/invalid append is retained and refused, never guessed or erased.
    This is process-crash recovery, not a power-loss durability guarantee.
    """
    from dataclasses import replace
    outcome = "committed" if plan.phase == "committed" else "aborted"
    expected = replace(plan.record, resolution=outcome)
    if plan.record.resolution != "pending":
        if plan.record != expected:
            raise StateTransactionError("state transaction outcome changed")
    path = record_path(root)
    before = path.lstat()
    flags = os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "a+b") as stream:
        opened = os.fstat(stream.fileno())
        if (not _plain(opened, stat.S_ISREG) or opened.st_nlink != 1
                or _identity(opened) != _identity(before)
                or read_record(root, plugin=plugin, caller=caller) != plan.record):
            raise StateTransactionError("state transaction record changed")
        if plan.record.resolution == "pending":
            # Existing schema-1 records without a final newline remain readable.
            stream.seek(-1, os.SEEK_END)
            prefix = b"" if stream.read(1) == b"\n" else b"\n"
            data = prefix + json.dumps({"transaction": plan.record.transaction,
                                       "resolution": outcome}, separators=(",", ":")).encode() + b"\n"
            if opened.st_size + len(data) > RECORD_LIMIT:
                raise StateTransactionError("state transaction resolution exceeds limit")
            stream.write(data)
        stream.flush()
        # A previous pass may have written a complete outcome but failed fsync.
        # Re-establish it before any deletion, including on resumed cleanup.
        os.fsync(stream.fileno())
    if read_record(root, plugin=plugin, caller=caller) != expected:
        raise StateTransactionError("state transaction resolution changed")
    return expected


def _remove_owned_tree(path, identity, checkpoint, *, max_entries=8192,
                       max_bytes=16 * 1024**2, max_files=4096, max_depth=64):
    """Validate the complete bounded tree before deleting its first entry."""
    from core.plugin_tree import bounded_paths
    if _optional_directory(path) is None:
        return
    if tuple(_directory(path)) != identity:
        raise StateTransactionError("state cleanup directory replaced")
    entries, total, files = [], 0, 0
    for entry in bounded_paths(path, max_entries=max_entries, max_depth=max_depth,
                               skip_dirs=frozenset(), checkpoint=checkpoint):
        info = entry.lstat()
        if _plain(info, stat.S_ISREG) and info.st_nlink == 1:
            total += info.st_size
            files += 1
        elif not _plain(info, stat.S_ISDIR):
            raise StateTransactionError("state cleanup contains unowned entries")
        if total > max_bytes or files > max_files:
            raise StateTransactionError("state cleanup exceeds limits")
        entries.append((entry, info))
    checkpoint()
    if tuple(_directory(path)) != identity:
        raise StateTransactionError("state cleanup directory replaced")
    for entry, before in reversed(entries):
        checkpoint()
        current = entry.lstat()
        if (_identity(current) != _identity(before) or current.st_mode != before.st_mode
                or (stat.S_ISREG(current.st_mode)
                    and (current.st_nlink != 1 or current.st_size != before.st_size
                         or current.st_mtime_ns != before.st_mtime_ns))):
            raise StateTransactionError("state cleanup entry changed")
        if stat.S_ISDIR(current.st_mode):
            entry.rmdir()
        else:
            entry.unlink()
    checkpoint()
    if tuple(_directory(path)) != identity:
        raise StateTransactionError("state cleanup directory replaced")
    path.rmdir()


def finish_transaction(root, *, plugin, caller, rename=os.rename, checkpoint=lambda: None):
    """Reconcile, resolve, and retire one transaction under its plugin lock.

    Returns committed/aborted, or None when already retired. The outcome is
    synced before partial cleanup; the journal is removed last. Invalid or
    foreign records and legacy backups are preserved and refused.
    """
    root = _parent(root)
    checkpoint()
    if not _present(record_path(root)):
        if _present(root.with_name(root.name + ".previous")):
            raise StateTransactionError("unowned previous state transaction")
        return None
    plan = restore_previous(root, plugin=plugin, caller=caller, rename=rename)
    outcome = "committed" if plan.phase == "committed" else "aborted"
    try:
        resolved = _record_resolution(root, plan, plugin=plugin, caller=caller)
        checkpoint()
        current = inspect_transaction(root, plugin=plugin, caller=caller)
        if current.record != resolved:
            raise StateTransactionError("state transaction record changed")
        if resolved.resolution == "committed":
            if resolved.old_identity is not None:
                _remove_owned_tree(root.with_name(root.name + ".previous"),
                                   resolved.old_identity, checkpoint)
        else:
            _remove_owned_tree(root.parent / resolved.staging / "new",
                               resolved.new_identity, checkpoint)
        wrapper = root.parent / resolved.staging
        checkpoint()
        if _optional_directory(wrapper) is not None:
            if tuple(_directory(wrapper)) != resolved.staging_identity:
                raise StateTransactionError("state staging directory replaced")
            wrapper.rmdir()
        checkpoint()
        if inspect_transaction(root, plugin=plugin, caller=caller).record != resolved:
            raise StateTransactionError("state transaction record changed")
        record_path(root).unlink()
    except (OSError, ValueError) as exc:
        raise StateCleanupError(outcome) from exc
    return resolved.resolution
