"""PowerShell PATH blocks with literal paths and compensated file publication."""
from contextlib import contextmanager
import base64
import ntpath
from pathlib import Path

from software._shared.mutation import mutation_lock, versioned_mutation
from software._shared.shell_path import _apply_profiles, _profile_text, _validate_markers, _without_block


def _profiles():
    docs = Path.home() / "Documents"
    return (docs / "PowerShell/profile.ps1", docs / "WindowsPowerShell/Microsoft.PowerShell_profile.ps1",
            docs / "WindowsPowerShell/profile.ps1")


def _plan_injection(directory, begin, end):
    _validate_markers(begin, end)
    if not isinstance(directory, str) or not ntpath.isabs(directory) or any(c in directory for c in '\x00\r\n;"'):
        raise ValueError("invalid PowerShell PATH directory")
    literal = "'" + directory.replace("'", "''") + "'"
    if not directory.isascii():
        encoded = base64.b64encode(directory.encode("utf-8")).decode("ascii")
        literal = f"[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))"
    block = f"{begin}\n$env:PATH = {literal} + ';' + $env:PATH\n{end}\n"
    changes = []
    for path in _profiles():
        previous = _profile_text(path)
        bom = "\ufeff" if previous and previous.startswith("\ufeff") else ""
        retained = _without_block((previous or "").removeprefix(bom), begin, end)
        replacement = bom + retained + ("" if not retained or retained.endswith("\n") else "\n") + block
        if previous != replacement:
            changes.append((path, previous, replacement))
    return changes


def _plan_removal(begin, end):
    _validate_markers(begin, end)
    changes = []
    for path in _profiles():
        previous = _profile_text(path)
        if previous is not None:
            bom = "\ufeff" if previous.startswith("\ufeff") else ""
            replacement = bom + _without_block(previous.removeprefix(bom), begin, end)
            if replacement != previous:
                changes.append((path, previous, replacement))
    return changes


def _verify_changes(changes):
    if any(_profile_text(path) != replacement for path, _, replacement in changes):
        raise ValueError("PowerShell profile changed before commit")


@contextmanager
def profile_transaction(directory, begin, end, *, remove=False):
    with mutation_lock():
        changes = _plan_removal(begin, end) if remove else _plan_injection(directory, begin, end)
        created = []
        try:
            for path, _, _ in changes:
                for parent in reversed(path.parents):
                    if not parent.exists():
                        parent.mkdir()
                        created.append(parent)
            _apply_profiles(changes)
            try:
                yield lambda: _verify_changes(changes)
            except BaseException:
                incomplete = False
                for path, previous, replacement in reversed(changes):
                    try:
                        current = _profile_text(path)
                        if current == previous:
                            continue
                        if current != replacement:
                            raise ValueError("PowerShell profile changed during rollback")
                        _apply_profiles([(path, replacement, previous)])
                    except Exception:
                        incomplete = True
                if incomplete:
                    from software.base import InstallError
                    raise InstallError("PowerShell profile operation failed; rollback incomplete") from None
                raise
        except BaseException:
            for parent in reversed(created):
                try:
                    parent.rmdir()
                except OSError:
                    # Never remove content introduced by another process.
                    pass
            raise


@versioned_mutation
def inject_profile(directory, begin, end):
    with profile_transaction(directory, begin, end) as verify:
        verify()


@versioned_mutation
def remove_profile(begin, end):
    with profile_transaction(None, begin, end, remove=True) as verify:
        verify()
