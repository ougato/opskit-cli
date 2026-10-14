"""Fail-fast, reentrant protection for shared version routing and shell profiles.

All versioned recipes share one lock: different software can modify the same
PATH/profile files. Keep the file permanently; the OS releases locks on exit.
"""
from functools import wraps
import os
from pathlib import Path
import stat

from core.resource_lock import ResourceLock
from software.base import InstallError


def lock_path():
    if os.name == "nt":
        from software._shared.windows_account import local_app_data
        return local_app_data() / "OpsKit" / "account-locks" / "versioned-software.lock"
    # Same home scope as snapshots/shims, regardless of OPSKIT_DATA_DIR.
    return Path.home() / ".opskit" / "locks" / "versioned-software.lock"


_resource = ResourceLock(lambda: lock_path(), label="versioned software", error_type=InstallError)
mutation_lock = _resource.hold


def system_lock_path():
    """One root-owned authority for shared POSIX command and profile writes."""
    path = Path("/var/lib/opskit/system-locks/versioned-software.lock")
    for node in (*reversed(path.parents), path):
        try:
            info = node.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise InstallError("system software lock requires root-owned non-writable paths")
    return path


_system_resource = ResourceLock(lambda: system_lock_path(), label="system software", error_type=InstallError)
system_mutation_lock = _system_resource.hold


def _system_privileged():
    return os.name != "nt" and os.geteuid() == 0


def system_mutation(operation):
    @wraps(operation)
    def guarded(*args, **kwargs):
        if _system_privileged():
            with system_mutation_lock():
                return operation(*args, **kwargs)
        return operation(*args, **kwargs)
    return guarded


def versioned_mutation(operation):
    @wraps(operation)
    def guarded(*args, **kwargs):
        with mutation_lock():
            return operation(*args, **kwargs)
    # Always acquire the shared system authority before the account-local lock.
    # Nested recipe/helper calls reuse both locks without reversing this order.
    return system_mutation(guarded)
