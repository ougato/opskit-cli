"""Version activation with checked execution and failure compensation."""
import copy
import os
import re
import shutil

from software.base import InstallError


class ActivationRollbackError(InstallError):
    """The previous activation could not be fully restored; stop mutations."""


def verify_command_version(command, version, arguments=("--version",)):
    """Check the command selected by PATH, not a version claimed by a snapshot."""
    from core.process import stream
    executable = shutil.which(command)
    if not executable:
        raise InstallError("activated command is unavailable")
    iterator = stream([executable, *arguments], timeout=10)
    parts = []
    size = 0
    try:
        while True:
            try:
                part = next(iterator)
            except StopIteration as done:
                if done.value != 0:
                    raise InstallError("activated command version check failed")
                break
            size += len(part)
            if size > 65536:
                raise InstallError("activated command version output exceeds limit")
            parts.append(part)
    finally:
        iterator.close()
    expected = version.lstrip("v")
    if not re.search(r"(?<![0-9.])" + re.escape(expected) + r"(?![0-9.])", "".join(parts)):
        raise InstallError("activated command does not match requested version")


def activate_version(snapshot, updates, *, save, apply, restore, verify):
    """Publish shim routing, apply links, verify; restore both on any failure."""
    previous = copy.deepcopy(snapshot)
    target = {**copy.deepcopy(snapshot), **updates}
    previous_path = os.environ.get("PATH")
    try:
        save(target)
        apply()
        verify()
    except BaseException:
        failures = []
        for operation in (lambda: save(previous), restore):
            try:
                operation()
            except Exception:
                failures.append(True)
        if previous_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = previous_path
        if failures:
            raise ActivationRollbackError("version activation failed; rollback incomplete") from None
        raise
