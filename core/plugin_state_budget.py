"""Host-wide logical state accounting; filesystem quotas remain separate.

The mutation caller must hold platform_state_guard across accounting and
allocation. All state mutation paths must participate before this can serve as
an enforced platform budget. Merely calling the scanner grants no allocation.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import stat

from core import plugin_state as state

MAX_PLATFORM_STATE_BYTES = 1024**3
MAX_PLATFORM_STATE_ENTRIES = 262144
MAX_PLATFORM_STATE_STAGING = 64
MAX_PLATFORM_STATE_DEPTH = 64
WRAPPER_RESERVATION_BYTES = state.STAGING_RESERVATION_BYTES + state.RECORD_LIMIT
# A new wrapper and its sibling journal are both charged after allocation.
NEW_STATE_RESERVATION_BYTES = WRAPPER_RESERVATION_BYTES + state.RECORD_LIMIT
NEW_STATE_RESERVATION_ENTRIES = state.MAX_STAGING_ENTRIES // state.MAX_STAGING_DIRECTORIES + 3


@dataclass(frozen=True)
class StateBudget:
    payload_bytes: int
    charged_bytes: int
    entries: int
    staging: int


@contextmanager
def platform_state_guard():
    """Stable OS lock shared by providers and callers, never deleted on exit."""
    from core.paths import data_dir
    from core.resource_lock import ResourceLock
    path = data_dir() / "plugin-runtimes" / "state-transactions" / "platform-budget.lock"
    with ResourceLock(lambda: path, label="platform plugin state budget").hold():
        yield


def budget_scope(target):
    """Platform partitions share one scope; standalone host tools use theirs."""
    from core.paths import data_dir
    target = Path(target).absolute()
    platform = data_dir().absolute() / "plugin-data"
    return platform if target.is_relative_to(platform) else target.parent


def reserve_platform_state(base, *, checkpoint=lambda: None,
                           reserve_bytes=NEW_STATE_RESERVATION_BYTES,
                           reserve_entries=NEW_STATE_RESERVATION_ENTRIES, reserve_staging=1):
    """Account all entries, reserving a full candidate for every staging tree.

    Unknown content is charged, never deleted. No payload content is read.
    A missing/inaccessible base propagates; this function never creates it.
    Time O(entries), auxiliary traversal space O(depth), with hard limits.
    """
    if any(type(value) is not int or value < 0 for value in (reserve_bytes, reserve_entries, reserve_staging)):
        raise state.StateTransactionError("invalid platform state reservation")
    base = Path(base).absolute()
    for ancestor in (base, *base.parents):
        state._directory(ancestor)
    if base.resolve() != base:
        raise state.StateTransactionError("platform state parent must be canonical")
    entries = staging = 0

    def scan(directory, depth):
        nonlocal entries, staging
        checkpoint()
        before = directory.lstat()
        if not state._plain(before, stat.S_ISDIR):
            raise state.StateTransactionError("platform state has linked or special entries")
        if depth > MAX_PLATFORM_STATE_DEPTH:
            raise state.StateTransactionError("platform state depth exceeds limit")
        payload = charged = 0
        with os.scandir(directory) as children:
            for child in children:
                checkpoint()
                entries += 1
                if entries + reserve_entries > MAX_PLATFORM_STATE_ENTRIES:
                    raise state.StateTransactionError("platform state entry quota exhausted")
                path = directory / child.name
                info = path.lstat()
                if state._plain(info, stat.S_ISDIR):
                    # Only wrapper positions used by the platform count as
                    # reservations. A payload's .state-* directory is data.
                    wrapper = child.name.startswith(".state-") and (
                        depth in (0, 1) or depth == 2 and directory.name == "callers")
                    if wrapper:
                        staging += 1
                        if staging + reserve_staging > MAX_PLATFORM_STATE_STAGING:
                            raise state.StateTransactionError("platform state staging quota exhausted")
                    child_payload, child_charged = scan(path, depth + 1)
                    payload += child_payload
                    charged += max(child_charged, state.STAGING_RESERVATION_BYTES) + state.RECORD_LIMIT if wrapper else child_charged
                elif state._plain(info, stat.S_ISREG) and info.st_nlink == 1:
                    payload += info.st_size
                    charged += info.st_size
                    after = path.lstat()
                    if (state._identity(after) != state._identity(info)
                            or (after.st_mode, after.st_nlink, after.st_size, after.st_mtime_ns)
                            != (info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns)):
                        raise state.StateTransactionError("platform state file changed")
                else:
                    raise state.StateTransactionError("platform state has linked or special entries")
                if charged + reserve_bytes > MAX_PLATFORM_STATE_BYTES:
                    raise state.StateTransactionError("platform state byte quota exhausted")
        checkpoint()
        after = directory.lstat()
        if (not state._plain(after, stat.S_ISDIR)
                or state._identity(after) != state._identity(before)
                or (after.st_mtime_ns, after.st_ctime_ns) != (before.st_mtime_ns, before.st_ctime_ns)):
            raise state.StateTransactionError("platform state directory changed")
        return payload, charged

    payload, charged = scan(base, 0)
    if charged + reserve_bytes > MAX_PLATFORM_STATE_BYTES:
        raise state.StateTransactionError("platform state byte quota exhausted")
    if entries + reserve_entries > MAX_PLATFORM_STATE_ENTRIES or staging + reserve_staging > MAX_PLATFORM_STATE_STAGING:
        raise state.StateTransactionError("platform state inventory quota exhausted")
    return StateBudget(payload, charged, entries, staging)
