"""Open authorized host files without following a raced parent link."""
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import stat

MAX_ARTIFACT_BYTES = 512 * 1024**2


def source_path(directory, relative):
    """Authenticate an ordinary root and a relative name without reading bytes."""
    if (not isinstance(directory, str) or not Path(directory).is_absolute()
            or not isinstance(relative, str) or not relative or "\\" in relative
            or any(ord(c) < 32 for c in directory + relative)):
        raise ValueError("invalid host file source")
    root = Path(directory).absolute()
    name = PurePosixPath(relative)
    if (name.is_absolute() or not name.parts or ".." in name.parts
            or any(":" in part or part.endswith((".", " ")) for part in name.parts)):
        raise ValueError("invalid host relative path")
    for parent in (root, *root.parents):
        info = parent.lstat()
        if parent.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("linked host directory forbidden")
    if not root.is_dir():
        raise ValueError("host directory required")
    info = root.stat()
    return root, root.joinpath(*name.parts), (info.st_dev, info.st_ino)


def _windows_long_path(path):
    """Expand 8.3 spelling without resolving junction targets."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    expand = kernel.GetLongPathNameW
    expand.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    expand.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    size = expand(str(path), buffer, len(buffer))
    if not 0 < size < len(buffer):
        raise OSError("host file canonical spelling unavailable")
    return Path(buffer.value)


def _windows_final_path(descriptor):
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    final = kernel.GetFinalPathNameByHandleW
    final.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    final.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    size = final(msvcrt.get_osfhandle(descriptor), buffer, len(buffer), 0)
    if not 0 < size < len(buffer):
        raise OSError("host file handle path unavailable")
    name = buffer.value
    if name.startswith("\\\\?\\UNC\\"):
        name = "\\\\" + name[8:]
    elif name.startswith("\\\\?\\"):
        name = name[4:]
    return Path(name)


def _open(path):
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    if os.name == "nt":
        expected = _windows_long_path(path)
        descriptor = os.open(path, flags)
        try:
            # Inspect the opened object before reading any bytes. A junction
            # replaced after lstat must not redirect an authorized read.
            if _windows_final_path(descriptor) != expected:
                raise ValueError("host file path redirected during open")
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise
    parent = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        return os.open(path.name, flags | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)


@contextmanager
def open_bounded(path, root, limit):
    """Yield an identity-checked file; reject changed content before returning."""
    path, root = Path(path).absolute(), Path(root).absolute()
    path.relative_to(root)
    if type(limit) is not int or limit < 0 or ".." in path.parts:
        raise ValueError("invalid host file budget or path")
    for parent in (path, *path.parents):
        info = parent.lstat()
        if parent.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("linked host file path forbidden")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
        raise ValueError("host file must be bounded and ordinary")
    with os.fdopen(_open(path), "rb") as stream:
        current = os.fstat(stream.fileno())
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or current.st_size > limit
                or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError("host file changed during open")
        yield stream
        after = os.fstat(stream.fileno())
        published = path.lstat()
        def signature(value):
            return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_nlink
        # CPython's Windows fstat/lstat paths can report different ctime
        # semantics. Compare ctime within the same handle API, never across
        # these two APIs; retain identity/length/mtime/link checks for both.
        if (signature(after) != signature(current) or after.st_ctime_ns != current.st_ctime_ns
                or signature(published) != signature(current)
                or (os.name != "nt" and published.st_ctime_ns != current.st_ctime_ns)):
            raise ValueError("host file changed during read")


def read_bounded(path, root, limit):
    with open_bounded(path, root, limit) as stream:
        content = stream.read(limit + 1)
        if len(content) > limit or len(content) != os.fstat(stream.fileno()).st_size:
            raise ValueError("host file exceeds read budget")
    return content
