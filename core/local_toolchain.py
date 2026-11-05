"""Discover installed tools without executing host interpreters or wrappers."""
from pathlib import Path
import shutil
import sys
import os
import stat
import re
import zipfile
from contextlib import ExitStack

from core.plugin_runtime_inventory import _read_file

_SKIP = frozenset({"site-packages", "__pycache__", "test", "tests", "idlelib", "tkinter", "ensurepip"})


def _python_abi(source):
    """Read the interpreter ABI from its PE version resource, without running it."""
    executable = source / "python.exe"
    matches = {int(match[2]): match[1] for path in source.glob("python3*.dll")
               if (match := re.fullmatch(r"(python3([0-9]{1,2}))\.dll", path.name, re.IGNORECASE))}
    version = None
    try:
        import ctypes
        from ctypes import wintypes
        library = ctypes.WinDLL("version", use_last_error=True)
        size_of = library.GetFileVersionInfoSizeW
        size_of.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
        size_of.restype = wintypes.DWORD
        size = size_of(str(executable), None)
        if size:
            data = ctypes.create_string_buffer(size)
            get_info = library.GetFileVersionInfoW
            get_info.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID)
            get_info.restype = wintypes.BOOL
            if get_info(str(executable), 0, size, data):
                value = ctypes.c_void_p()
                length = wintypes.UINT()
                query = library.VerQueryValueW
                query.argtypes = (wintypes.LPCVOID, wintypes.LPCWSTR,
                                  ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT))
                query.restype = wintypes.BOOL
                if query(data, "\\", ctypes.byref(value), ctypes.byref(length)) and length.value >= 16:
                    fields = ctypes.cast(value, ctypes.POINTER(wintypes.DWORD * 13)).contents
                    if fields[0] == 0xFEEF04BD:
                        version = (fields[2] >> 16, fields[2] & 0xFFFF)
    except (AttributeError, OSError, ValueError):
        pass
    if version is None and len(matches) == 1:
        version = (3, next(iter(matches)))
    if (version is None or version[0] != 3 or version[1] < 10
            or version[1] not in matches):
        raise ValueError("unsupported local Python ABI")
    return version[1], matches[version[1]]


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
        if not (source / "python.exe").is_file():
            raise ValueError("incompatible local Python")
        minor, dll = _python_abi(source)
        library_zip = f"python3{minor}.zip"
        paths = [path for path in source.iterdir() if path.is_file() and (
            path.name == "python.exe" or path.name.lower() in {"python3.dll", dll.lower()}
            or path.suffix.lower() in {".dll", ".pyd"} or path.name.lower() == library_zip.lower())]
        for name in ("Lib", "DLLs"):
            tree = source / name
            if tree.exists():
                paths.extend(path for path in bounded_paths(tree, max_entries=MAX_ENTRIES, skip_dirs=_SKIP)
                             if path.is_file() and path.suffix.lower() not in {".pyc", ".pyo"})
        if not (source / library_zip).is_file() and not (source / "Lib/encodings/__init__.py").is_file():
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
    # Hundreds of small standard-library files otherwise get copied, hashed and
    # ACL-processed at every isolation stage. Python can import the same bytes
    # from one uncompressed archive; native extensions remain ordinary files.
    pack_library = kind == "python" and not (source / library_zip).is_file()
    with ExitStack() as stack:
        library = stack.enter_context(zipfile.ZipFile(destination / library_zip, "w", compression=zipfile.ZIP_STORED)) if pack_library else None
        for path in paths:
            body = _installed_file(path, min(128 * 1024**2, MAX_BYTES - total))
            total += len(body)
            if total > MAX_BYTES:
                raise ValueError("local tool snapshot exceeds budget")
            relative = path.relative_to(source)
            if library is not None and relative.parts[0] == "Lib" and path.suffix.lower() not in {".dll", ".pyd"}:
                library.writestr(relative.relative_to("Lib").as_posix(), body)
            else:
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
    if kind == "python":
        # Never inherit an installed interpreter's user-customized search paths.
        (destination / f"python3{minor}._pth").write_text(
            f"{library_zip}\n.\nLib\nDLLs\n", encoding="ascii")
    inventory(destination)
    return destination
