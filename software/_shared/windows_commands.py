"""Owned command files, literal relative routes and compensated publication."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat

from software._shared.mutation import mutation_lock
from software._shared.shell_path import _apply_profiles, _profile_text
from software.base import InstallError


def _managed_root(managed_root):
    root = Path(managed_root).absolute()
    for path in (root, *root.parents):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_reparse_tag", 0):
            raise InstallError("managed command root must not be a link")
    return root


def _command_text(path):
    for node in (path, *path.parents):
        try:
            info = node.lstat()
        except FileNotFoundError:
            continue
        if getattr(info, "st_reparse_tag", 0):
            raise InstallError("command path must not be a reparse point")
    return _profile_text(path)


def _publish_command(path, text):
    from software._shared import shell_path
    shell_path._publish_profile(path, text, private=True)


def route_text(directory, target, feature):
    if not isinstance(feature, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", feature):
        raise InstallError("invalid command feature identity")
    relative = os.path.relpath(target, directory).replace("/", "\\")
    # The account/home portion comes from %~dp0, avoiding batch source encoding
    # and interpolation of a Unicode or metacharacter-containing account path.
    if not relative.isascii() or any(ord(c) < 32 or ord(c) == 127 or c in '%"' for c in relative):
        raise InstallError("command route cannot be represented safely")
    return ("@echo off\r\nsetlocal DisableDelayedExpansion\r\n"
            f"rem opskit {feature} command v1\r\n"
            f'"%~dp0{relative}" %*\r\nexit /b %errorlevel%\r\n')


def route_owned(directory, name, text, feature, managed_root, legacy=()):
    canonical = text.replace("\r\r\n", "\r\n")
    if canonical in legacy:
        return True
    lines = canonical.splitlines()
    root = _managed_root(managed_root)
    # Accept only exact known routes, not a marker followed by arbitrary code.
    if (len(lines) == 5 and lines[:3] == ["@echo off", "setlocal DisableDelayedExpansion",
                                         f"rem opskit {feature} command v1"]
            and lines[3].startswith('"%~dp0') and lines[3].endswith('" %*')
            and lines[4] == "exit /b %errorlevel%"):
        relative = lines[3][6:-4].replace("\\", os.sep)
        target = Path(os.path.abspath(Path(directory) / relative))
        expected = route_text(directory, target, feature)
    elif len(lines) == 2 and lines[0] == "@echo off" and lines[1].startswith('"') and lines[1].endswith('" %*'):
        target = Path(lines[1][1:-4])
        if not target.is_absolute():
            return False
        expected = f'@echo off\r\n"{target}" %*\r\n'
    else:
        return False
    try:
        target.relative_to(root)
        target.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return target.name in (Path(name).stem + ".exe", Path(name).stem + ".cmd") and canonical == expected


def version_routes(directory, bin_dir, commands, feature, managed_root):
    root = _managed_root(managed_root)
    source = Path(bin_dir).absolute()
    try:
        source.relative_to(root)
        source.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        raise InstallError("command source is outside its managed root or unavailable") from None
    result = {}
    for index, (name, extensions) in enumerate(commands.items()):
        target = next((source / (name + ext) for ext in extensions if (source / (name + ext)).exists()), None)
        if target is None:
            if index == 0:
                raise InstallError("primary version command is unavailable")
            result[name + ".cmd"] = None
            continue
        info = target.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, "st_reparse_tag", 0)
                or target.resolve().parent != source.resolve()):
            raise InstallError("version command must be an owned regular file")
        result[name + ".cmd"] = route_text(directory, target, feature)
    return result


@contextmanager
def command_transaction(directory, replacements, *, owned):
    """Preflight every file; retain compensation until the caller commits."""
    with mutation_lock():
        directory = Path(directory)
        changes, created = [], []
        try:
            for name, replacement in replacements.items():
                if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}\.cmd", name):
                    raise InstallError("invalid managed command name")
                if replacement is not None and not owned(name, replacement):
                    raise InstallError("invalid managed command content")
                path = directory / name
                previous = _command_text(path)
                if previous is not None and not owned(name, previous):
                    raise InstallError("refusing an unmanaged command file")
                if previous != replacement:
                    changes.append((path, previous, replacement))
            for path, _, replacement in changes:
                if replacement is not None:
                    for parent in reversed(path.parents):
                        if not parent.exists():
                            parent.mkdir()
                            created.append(parent)
                            from core.secrets import restrict_permissions
                            restrict_permissions(parent)
            _apply_profiles(changes, publish=_publish_command)
            def verify():
                for name, replacement in replacements.items():
                    if _command_text(directory / name) != replacement:
                        raise InstallError("command file changed before commit")
            try:
                yield verify
                verify()
            except BaseException:
                incomplete = False
                for path, previous, replacement in reversed(changes):
                    try:
                        current = _command_text(path)
                        if current == previous:
                            continue
                        if current != replacement:
                            raise InstallError("command changed during rollback")
                        _apply_profiles([(path, replacement, previous)], publish=_publish_command)
                    except Exception:
                        incomplete = True
                if incomplete:
                    raise InstallError("command operation failed; rollback incomplete") from None
                raise
        except BaseException as error:
            for parent in reversed(created):
                try:
                    parent.rmdir()
                except OSError:
                    pass  # Never delete content introduced by another process.
            if isinstance(error, (OSError, ValueError)):
                raise InstallError("command file operation failed") from error
            raise
