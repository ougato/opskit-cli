"""Host metadata checks shared by resource and state transactions."""
from pathlib import Path


def path_present(path: Path) -> bool:
    """Count links as present; only a missing path means absence.

    Unlike os.path.lexists, permission and other I/O errors propagate so they
    cannot silently authorize creation, retirement, or recovery cleanup.
    """
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def sync_directory(path: Path, identity: list) -> bool:
    """Sync POSIX directory entries through a checked handle in a worker.

    Windows retains identity checks but has no POSIX directory-fsync contract.
    This barrier alone does not prove power-loss durability of ancestor paths.
    """
    import os
    import stat
    from core.plugin_state import _directory, _identity, _plain
    if _directory(path) != identity:
        raise ValueError('directory changed before sync')
    if os.name == 'nt':
        return False
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
                         | getattr(os, 'O_NOFOLLOW', 0))
    try:
        info = os.fstat(descriptor)
        if (not _plain(info, stat.S_ISDIR) or _identity(info) != identity
                or _directory(path) != identity):
            raise ValueError('directory changed during sync opening')
        os.fsync(descriptor)
        if _directory(path) != identity:
            raise ValueError('directory changed during sync')
    finally:
        os.close(descriptor)
    return True
