"""Supervised, private candidate writes inside an already owned workspace."""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import tarfile
import time
import uuid

from core.plugin_fetch_candidate import MAX_ENTRIES, MAX_TREE_BYTES, validated_archive
from core.plugin_workspace import Workspace, journal_binding
from core.process_file import _SpawnFile


@dataclass(frozen=True)
class PreparedCandidate:
    root: Path
    identity: list
    reported_commit: str
    archive_sha256: str
    files: int
    bytes: int


def _binding(workspace):
    from core.plugin_state import _directory
    journal_binding(workspace)
    for path in (workspace.root, *workspace.root.parents):
        _directory(path)
    if (_directory(workspace.root) != workspace.identity
            or _directory(workspace.root.parent) != workspace.parent_identity):
        raise ValueError("candidate workspace identity changed")


@contextmanager
def _private_root(workspace):
    """Pin the owned root; POSIX writes below it use directory descriptors."""
    _binding(workspace)
    if os.name != "nt":
        from core.private_file import verify_private_file
        descriptor = os.open(workspace.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            if [opened.st_dev, opened.st_ino] != workspace.identity:
                raise ValueError("candidate workspace changed while opening")
            verify_private_file(descriptor)
            _binding(workspace)
            yield descriptor
            _binding(workspace)
        finally:
            os.close(descriptor)
        return
    import ctypes
    from ctypes import wintypes
    from core.private_file import _windows_permissions
    from core.secrets import restrict_permissions
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # Directory + open-reparse-point; no delete sharing pins the root itself.
    handle = create(str(workspace.root), 0x20083, 3, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        _binding(workspace)
        # Reject foreign writers before tightening inherited read permissions.
        _windows_permissions(None, handle=handle, directory=True)
        restrict_permissions(workspace.root)
        _windows_permissions(None, handle=handle, directory=True)
        _binding(workspace)
        yield None
        _binding(workspace)
    finally:
        close(handle)


@contextmanager
def _file_parent(root, descriptor, parts):
    from core.plugin_state import _directory
    with ExitStack() as stack:
        path = root
        for part in parts:
            path = path / part
            try:
                if descriptor is None:
                    path.mkdir(mode=0o700)
                else:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            if descriptor is None:
                _directory(path)
            else:
                descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=descriptor)
                stack.callback(os.close, descriptor)
                from core.private_file import verify_private_file
                verify_private_file(descriptor)
        yield path, descriptor


def _write_file(archive, member, expected, root, descriptor):
    from core.private_file import verify_private_file
    parts = expected.path.split("/")
    with _file_parent(root, descriptor, parts[:-1]) as (parent, parent_fd):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        descriptor = (os.open(parent / parts[-1], flags, 0o600) if parent_fd is None
                      else os.open(parts[-1], flags, 0o600, dir_fd=parent_fd))
        with os.fdopen(descriptor, "wb") as output:
            verify_private_file(output.fileno())
            before = os.fstat(output.fileno())
            digest = hashlib.sha256()
            remaining = expected.size
            with archive.extractfile(member) as source:
                while remaining:
                    chunk = source.read(min(65536, remaining))
                    if not chunk or output.write(chunk) != len(chunk):
                        raise OSError("incomplete candidate write")
                    digest.update(chunk)
                    remaining -= len(chunk)
            if digest.hexdigest() != expected.sha256:
                raise ValueError("candidate contents changed")
            output.flush()
            if os.name != "nt":
                os.fchmod(output.fileno(), 0o700 if expected.mode == 0o755 else 0o600)
            os.fsync(output.fileno())
            verify_private_file(output.fileno())
            current = (os.stat(parent / parts[-1], follow_symlinks=False) if parent_fd is None
                       else os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False))
            if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                    or current.st_size != expected.size
                    or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)):
                raise ValueError("candidate file identity changed")


def _prepare_worker(connection, source, workspace, request):
    from core.archive_reader import open_tar
    from core.plugin_state import _directory
    from core.private_file import verify_private_file
    from network.worker import send
    try:
        with source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("candidate response must be a regular file")
            verify_private_file(source.fileno())
            source.seek(0)
            with validated_archive(source) as (inventory, snapshot):
                # Match existing workspace cleanup depth, including candidate.
                if any(len(item.path.split("/")) > 64 for item in inventory.files):
                    raise ValueError("candidate depth exceeds workspace limits")
                with _private_root(workspace) as root_fd:
                    with os.scandir(workspace.root if root_fd is None else root_fd) as entries:
                        if next(entries, None) is not None:
                            raise ValueError("candidate workspace is not empty")
                    target = workspace.root / "candidate"
                    if root_fd is None:
                        target.mkdir(mode=0o700)
                    else:
                        os.mkdir("candidate", 0o700, dir_fd=root_fd)
                    identity = _directory(target)
                    with _file_parent(workspace.root, root_fd, ["candidate"]) as (_, candidate_fd):
                        with open_tar(fileobj=snapshot, mode="r:", max_files=MAX_ENTRIES,
                                      max_payload_bytes=MAX_TREE_BYTES, max_metadata_bytes=2 * 1024**2) as archive:
                            count = 0
                            for member in archive:
                                if count >= len(inventory.files):
                                    raise ValueError("candidate inventory changed")
                                expected = inventory.files[count]
                                if (member.name, member.size, member.mode) != (expected.path, expected.size, expected.mode):
                                    raise ValueError("candidate inventory changed")
                                _write_file(archive, member, expected, target, candidate_fd)
                                count += 1
                            if count != len(inventory.files):
                                raise ValueError("candidate inventory changed")
                    if _directory(target) != identity:
                        raise ValueError("candidate directory identity changed")
                result = {"ok": True, "request": request, "identity": identity,
                          "commit": inventory.reported_commit, "archive": inventory.archive_sha256,
                          "files": len(inventory.files), "bytes": inventory.bytes}
    except (OSError, ValueError, EOFError, tarfile.TarError):
        result = {"ok": False, "request": request}
    try:
        send(connection, result)
    finally:
        connection.close()


def prepare_candidate(response, workspace, *, timeout):
    """Write in an owned worker; caller owns workspace recovery on every outcome.

    Does not publish or grant trust. The workspace must be exclusively held by
    the trusted platform, never mounted writable into a plugin. Same-account
    attackers, hostile ancestor renames and crash-durable publication are not
    established by these local ownership checks.
    """
    from core.plugin_state import _valid_identity
    from network.worker import run
    if (not isinstance(workspace, Workspace) or type(timeout) not in (int, float)
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("invalid candidate preparation request")
    journal_binding(workspace)
    deadline = time.monotonic() + timeout
    descriptor = response.fileno()
    request = uuid.uuid4().hex
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("candidate preparation deadline exceeded; workspace retained")
    result = run(_prepare_worker, (_SpawnFile(descriptor), workspace, request),
                 timeout=remaining, label="plugin fetch preparation")
    if result == {"ok": False, "request": request}:
        raise ValueError("candidate preparation rejected; workspace retained")
    if (not isinstance(result, dict)
            or set(result) != {"ok", "request", "identity", "commit", "archive", "files", "bytes"}
            or result["ok"] is not True or result["request"] != request
            or not _valid_identity(result["identity"])
            or not isinstance(result["commit"], str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", result["commit"])
            or not isinstance(result["archive"], str) or not re.fullmatch(r"[0-9a-f]{64}", result["archive"])
            or type(result["files"]) is not int or not 0 <= result["files"] <= MAX_ENTRIES
            or type(result["bytes"]) is not int or not 0 <= result["bytes"] <= MAX_TREE_BYTES):
        raise RuntimeError("invalid candidate preparation result; workspace retained")
    if time.monotonic() >= deadline:
        raise TimeoutError("candidate preparation deadline exceeded; workspace retained")
    return PreparedCandidate(workspace.root / "candidate", result["identity"], result["commit"],
                             result["archive"], result["files"], result["bytes"])
