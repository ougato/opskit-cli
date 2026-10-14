"""Explicit registry PATH transactions, independent of interface/profile code."""
from contextlib import contextmanager
import ntpath

from software._shared.mutation import mutation_lock, versioned_mutation
from software.base import InstallError


@versioned_mutation
def change_user_path(directory, begin, end, *, remove=False, verify_commands=None):
    from software._shared.windows_profile import profile_transaction
    # Verify profile bytes inside the registry scope, so either verification
    # failure can compensate the other resource before either context commits.
    with profile_transaction(directory, begin, end, remove=remove) as verify:
        with path_transaction(directory, remove=remove):
            verify()
            if verify_commands is not None:
                verify_commands()


@versioned_mutation
def remove_user_paths(directories, begin, end, *, verify_commands=None):
    from software._shared.windows_profile import profile_transaction
    directories = tuple(directories)
    if not directories:
        raise ValueError("PATH removal requires a directory")
    with profile_transaction(None, begin, end, remove=True) as verify:
        with path_transaction(directories[0], remove=True, other_directories=directories[1:]):
            verify()
            if verify_commands is not None:
                verify_commands()


def _read(registry, key):
    try:
        return registry.QueryValueEx(key, "PATH")
    except FileNotFoundError:
        return None


@contextmanager
def path_transaction(directory, *, remove=False, other_directories=(), hive=None, subkey="Environment"):
    """Keep the prior value/type until all work inside the context succeeds.

    User scope is the default. Machine scope must be supplied explicitly by an
    authorized caller; this primitive does not establish machine-wide locking.
    """
    import winreg
    directories = (directory, *other_directories)
    if len(directories) > 16 or (other_directories and not remove):
        raise ValueError("invalid registry PATH directory set")
    for entry in directories:
        if not isinstance(entry, str) or not ntpath.isabs(entry) or any(c in entry for c in '\x00\r\n;"%'):
            raise ValueError("invalid registry PATH directory")
    hive = winreg.HKEY_CURRENT_USER if hive is None else hive
    with mutation_lock():
        key = winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE)
        try:
            previous = _read(winreg, key)
            if previous is None:
                value, kind = "", winreg.REG_EXPAND_SZ
            else:
                value, kind = previous
                if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not isinstance(value, str):
                    raise ValueError("registry PATH must be a string value")
            if len(value) > 32767:
                raise ValueError("registry PATH exceeds limit")
            identities = {ntpath.normcase(ntpath.normpath(entry)) for entry in directories}
            parts = value.split(";") if value else []
            retained = [part for part in parts if ntpath.normcase(ntpath.normpath(part.strip('"'))) not in identities]
            replacement = (";".join(retained if remove else [directory, *retained]), kind)
            if len(replacement[0]) > 32767:
                raise ValueError("registry PATH exceeds limit after update")
            changed = replacement != previous and not (remove and previous is None)
            try:
                if _read(winreg, key) != previous:
                    raise InstallError("registry PATH changed before publication")
                if changed:
                    winreg.SetValueEx(key, "PATH", 0, kind, replacement[0])
                yield
                if _read(winreg, key) != (replacement if changed else previous):
                    raise InstallError("registry PATH changed before commit")
            except BaseException:
                if changed:
                    try:
                        current = _read(winreg, key)
                        if current != previous:
                            if current != replacement:
                                raise InstallError("registry PATH changed during rollback")
                            if previous is None:
                                winreg.DeleteValue(key, "PATH")
                            else:
                                winreg.SetValueEx(key, "PATH", 0, previous[1], previous[0])
                    except Exception:
                        raise InstallError("registry PATH operation failed; rollback incomplete") from None
                raise
        finally:
            winreg.CloseKey(key)
