"""Discover installed tools without executing host interpreters or wrappers."""
from pathlib import Path
import shutil
import sys
import os
import stat

from core.plugin_runtime_inventory import _read_file

_SKIP = frozenset({"site-packages", "__pycache__", "test", "tests", "idlelib", "tkinter", "ensurepip"})


def _installed_file(path, limit):
    # Installed Git commonly uses hard links. Copy bytes into a fresh ordinary
    # file; never share its inode or grant sandbox access to the installed tree.
    for parent in (path, *path.parents):
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise ValueError("linked installed tool path")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ValueError("invalid installed tool file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as stream:
        current = os.fstat(stream.fileno())
        if (not stat.S_ISREG(current.st_mode) or current.st_size > limit
                or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError("installed tool changed during copy")
        body = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if len(body) != current.st_size or (after.st_size, after.st_mtime_ns) != (current.st_size, current.st_mtime_ns):
        raise ValueError("installed tool changed during copy")
    return body


def python_candidates():
    roots = []
    if not getattr(sys, "frozen", False) and "__compiled__" not in globals():
        roots.append(Path(sys.base_prefix))
    executable = shutil.which("python.exe")
    if executable:
        root = Path(executable).parent
        roots.append(root)
        config = root.parent / "pyvenv.cfg"
        try:
            text = _read_file(config, Path(config.anchor), 16384).decode("utf-8")
            for line in text.splitlines():
                key, separator, value = line.partition("=")
                if separator and key.strip() == "home":
                    roots.append(Path(value.strip()))
        except (OSError, ValueError, UnicodeError):
            pass
    if sys.platform == "win32":
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r"Software\Python\PythonCore", 0, winreg.KEY_READ | view) as key:
                        for index in range(min(winreg.QueryInfoKey(key)[0], 32)):
                            name = winreg.EnumKey(key, index)
                            try:
                                with winreg.OpenKey(key, name + r"\InstallPath") as install:
                                    value, kind = winreg.QueryValueEx(install, "")
                                    if kind == winreg.REG_SZ:
                                        roots.append(Path(value))
                            except OSError:
                                pass
                except OSError:
                    pass
    return list(dict.fromkeys(root.absolute() for root in roots))[:16]


def git_candidates():
    executable = shutil.which("git.exe")
    if not executable:
        return []
    binary = Path(executable).absolute().parent
    # The PATH cmd/git.exe is a launcher, not a standalone Git object reader.
    roots = [binary.parent / name / "bin" for name in ("ucrt64", "mingw64")]
    roots.append(binary)
    return list(dict.fromkeys(roots))


def snapshot(source, destination, kind):
    """Copy bounded ordinary files; never change permissions of installed tools.

    SDK wheels are pinned for CPython 3.14 Windows x64. Other interpreters must
    not be silently reused for a runtime they cannot execute.
    """
    from core.native_runtime import inventory, MAX_BYTES, MAX_ENTRIES
    from core.plugin_tree import bounded_paths
    from core.plugin_state import _directory
    source, destination = Path(source).absolute(), Path(destination)
    for parent in (source, *source.parents):
        _directory(parent)
    if kind == "python":
        if not (source / "python.exe").is_file() or not (source / "python314.dll").is_file():
            raise ValueError("incompatible local Python")
        paths = [path for path in source.iterdir() if path.is_file() and (
            path.name == "python.exe" or path.suffix.lower() in {".dll", ".pyd"} or path.name == "python314.zip")]
        for name in ("Lib", "DLLs"):
            tree = source / name
            if tree.exists():
                paths.extend(path for path in bounded_paths(tree, max_entries=MAX_ENTRIES, skip_dirs=_SKIP)
                             if path.is_file() and path.suffix.lower() not in {".pyc", ".pyo"})
        if not (source / "python314.zip").is_file() and not (source / "Lib/encodings/__init__.py").is_file():
            raise ValueError("local Python standard library missing")
    elif kind == "git":
        if not (source / "git.exe").is_file():
            raise ValueError("local Git object reader missing")
        paths = [path for path in source.iterdir() if path.is_file()
                 and (path.name == "git.exe" or path.suffix.lower() == ".dll")]
    else:
        raise ValueError("unknown local tool")
    if len(paths) > MAX_ENTRIES or destination.exists():
        raise ValueError("invalid local tool snapshot")
    destination.mkdir()
    total = 0
    for path in paths:
        body = _installed_file(path, min(128 * 1024**2, MAX_BYTES - total))
        total += len(body)
        if total > MAX_BYTES:
            raise ValueError("local tool snapshot exceeds budget")
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    if kind == "python":
        # Never inherit an installed interpreter's user-customized search paths.
        (destination / "python314._pth").write_text("python314.zip\n.\nLib\nDLLs\n", encoding="ascii")
    inventory(destination)
    return destination
