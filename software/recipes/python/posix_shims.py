"""Owned Python shell wrappers with group preflight and late compensation."""
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import shlex
import stat
import os

from software._shared import shell_path
from software._shared.mutation import mutation_lock
from software._shared.activation import ActivationRollbackError
from software.base import InstallError
from .constants import LEGACY_SHIM_SH_TEMPLATE


def _literal(path):
    if (not isinstance(path, str) or
            not (Path(path).is_absolute() or PurePosixPath(path).is_absolute()) or
            any(c in path for c in "\x00\r\n")):
        raise InstallError("Python shim fallback must be an absolute path")
    return shlex.quote(path)


def _route_parts(route):
    literal = _literal(route)
    return ('#!/bin/sh\n# opskit python command v1\n'
            f'if [ -x {literal} ]; then\n    exec {literal} "$@"\nfi\nexec ', ' "$@"\n')


def shim_text(fallback, route):
    prefix, suffix = _route_parts(route)
    return prefix + _literal(fallback) + suffix


def owned(text, route):
    # Historical text-mode writers can produce CRLF; normalize only line
    # endings, keeping the complete template and quoted target checks intact.
    text = text.replace("\r\n", "\n")
    for prefix, suffix in (_route_parts(route), LEGACY_SHIM_SH_TEMPLATE.split("{fallback}")):
        if not text.startswith(prefix) or not text.endswith(suffix):
            continue
        raw = text[len(prefix):-len(suffix)]
        try:
            tokens = shlex.split(raw)
            if len(tokens) == 1 and text == prefix + _literal(tokens[0]) + suffix:
                return True
        except (ValueError, InstallError):
            pass
    return False


@contextmanager
def shim_files(directory, replacement, route):
    with mutation_lock():
        changes, created, modes, originals, targets = [], [], {}, {}, {}
        restoring = False
        for name in ("python3", "python"):
            path = directory / name
            previous = shell_path._profile_text(path)
            originals[path] = previous
            if previous is not None:
                modes[path] = stat.S_IMODE(path.stat().st_mode)
                if modes[path] & 0o7000:
                    raise InstallError("Python shim must not have special permission bits")
            if previous is not None and not owned(previous, route):
                raise InstallError("refusing an unmanaged Python shim")
            targets[path] = (modes.get(path, 0o755) & 0o777) | 0o100
            if previous != replacement or (replacement is not None and os.name != "nt" and modes.get(path) != targets[path]):
                changes.append((path, previous, replacement))
        if replacement is not None and not owned(replacement, route):
            raise InstallError("invalid Python shim content")
        def publish(path, text):
            mode = modes.get(path, 0o755) if restoring or text != replacement else targets[path]
            shell_path._publish_profile(path, text, mode_override=mode)
        try:
            if replacement is not None:
                for parent in reversed(directory.parents):
                    if not parent.exists():
                        parent.mkdir()
                        created.append(parent)
                if not directory.exists():
                    directory.mkdir()
                    created.append(directory)
            def verify():
                for name in ("python3", "python"):
                    path = directory / name
                    if shell_path._profile_text(path) != replacement:
                        raise InstallError("Python shim changed before commit")
                    if replacement is not None and os.name != "nt" and stat.S_IMODE(path.stat().st_mode) != targets[path]:
                        raise InstallError("Python shim permissions changed before commit")
            try:
                shell_path._apply_profiles(changes, publish=publish)
                yield verify
                verify()
            except BaseException:
                restoring = True
                incomplete = False
                for path, previous, replacement in reversed(changes):
                    try:
                        current = shell_path._profile_text(path)
                        mode = stat.S_IMODE(path.stat().st_mode) if current is not None else None
                        if current == previous and (os.name == "nt" or mode == modes.get(path)):
                            continue
                        if current != replacement or (current is not None and os.name != "nt" and mode != targets[path]):
                            raise InstallError("Python shim changed during rollback")
                        shell_path._apply_profiles([(path, current, previous)], publish=publish)
                    except Exception:
                        incomplete = True
                if incomplete:
                    raise ActivationRollbackError("Python shim operation failed; rollback incomplete") from None
                raise
        except BaseException:
            for parent in reversed(created):
                try:
                    parent.rmdir()
                except OSError:
                    pass
            raise
