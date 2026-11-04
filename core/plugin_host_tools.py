"""Consent-bound, business-neutral host tools. Never import plugin code here."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from core.native_sandbox import CapabilityRequest
from core.plugin_runtime_inventory import _read_file

MAX_OUTPUT_BYTES = 2 * 1024**3
MAX_OUTPUT_ENTRIES = 65536
MAX_OUTPUT_DEPTH = 64


def _ordinary(path, *, directory=False):
    if not isinstance(path, str) or not Path(path).is_absolute() or any(ord(c) < 32 for c in path):
        raise ValueError("ordinary absolute path required")
    target = Path(path).absolute()
    if any(part == ".." or ":" in part or part.endswith((".", " ")) for part in target.parts[1:]):
        raise ValueError("ambiguous host path forbidden")
    for parent in (target, *target.parents):
        info = parent.lstat()
        if parent.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("linked host path forbidden")
    if directory != target.is_dir() or (not directory and not target.is_file()):
        raise ValueError("invalid host resource")
    return target


def _identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino


def _environment(tool):
    """Minimal desktop-tool environment; no inherited PATH or credentials."""
    if os.name != "nt":
        raise ValueError("host tool backend not implemented for this platform")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_system = kernel.GetSystemDirectoryW
    get_system.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    get_system.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_system(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise OSError("system directory unavailable")
    system = Path(buffer.value)
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP",
                   "USERPROFILE", "LOCALAPPDATA", "APPDATA", "SystemDrive") if key in os.environ}
    environment["PATH"] = str(tool.parent) + os.pathsep + str(system)
    environment["ComSpec"] = str(system / "cmd.exe")
    return environment


def _grant(plugin, fingerprint, operation, resource, authority, check_identity, checkpoint):
    scope = CapabilityRequest(plugin, fingerprint, operation, resource)
    from core.prompt import guard_input
    checkpoint()
    with guard_input(checkpoint):
        authority.require(scope, check_identity=check_identity)
    return scope


def path_info(plugin, fingerprint, path, directory, *, authority, check_identity, checkpoint, **unused):
    if type(directory) is not bool:
        raise ValueError("invalid path kind")
    target = _ordinary(path, directory=directory)
    identity = _identity(target)
    scope = _grant(plugin, fingerprint, "directory.read", str(target if directory else target.parent),
                   authority, check_identity, checkpoint)
    authority.check(scope, check_identity=check_identity)
    if _ordinary(path, directory=directory) != target or _identity(target) != identity:
        raise ValueError("host resource changed")
    return {"path": str(target), "directory": directory}


def execute(plugin, fingerprint, executable, arguments, directory, options, *, authority,
            check_identity, deadline, checkpoint, output):
    """Return raw exit status and a pending output token; success is plugin policy."""
    from core import plugin_artifacts as artifacts
    from core.plugin_disk_policy import check_space
    if (not isinstance(arguments, list) or len(arguments) > 128
            or any(not isinstance(a, str) or len(a) > 16384 or any(ord(c) < 32 for c in a) for a in arguments)
            or not isinstance(options, dict) or set(options) - {"output_parent"}):
        raise ValueError("invalid host tool arguments")
    tool = _ordinary(executable)
    if os.name != "nt" or tool.suffix.lower() != ".exe":
        raise ValueError("host executable backend unavailable")
    root = _ordinary(directory, directory=True)
    base = options.get("output_parent")
    if base is not None:
        base = _ordinary(base, directory=True)
    check_space(root)
    if base:
        check_space(base)
    token = uuid.uuid4().hex
    destination = base / ("opskit-" + token) if base else None
    argv = [a.replace("{output_directory}", str(destination) if destination else "")
             .replace("{output_name}", destination.name if destination else "") for a in arguments]
    digest = hashlib.sha256(_read_file(tool, Path(tool.anchor), 256 * 1024**2)).hexdigest()
    resource = json.dumps({"executable": str(tool), "sha256": digest, "arguments": argv,
                           "directory": str(root), "output_parent": str(base) if base else None},
                          ensure_ascii=False, sort_keys=True)
    tool_id, root_id = _identity(tool), _identity(root)
    base_id = _identity(base) if base else None
    scope = _grant(plugin, fingerprint, "tool.execute", resource, authority, check_identity, checkpoint)
    from core.native_task_control import task_checkpoint
    checkpoint = task_checkpoint(checkpoint, output=output)
    next_check = 0.0
    def live():
        nonlocal next_check
        checkpoint()
        if time.monotonic() >= deadline:
            raise TimeoutError("host tool deadline exceeded")
        authority.check(scope, check_identity=check_identity)
        if (_identity(_ordinary(executable)) != tool_id
                or _identity(_ordinary(directory, directory=True)) != root_id
                or (base and _identity(_ordinary(str(base), directory=True)) != base_id)):
            raise PermissionError("host resource changed")
        if time.monotonic() >= next_check:
            check_space(root)
            if base:
                check_space(base)
            next_check = time.monotonic() + 2
    with artifacts.resource_lock(root):
        live()
        if hashlib.sha256(_read_file(tool, Path(tool.anchor), 256 * 1024**2)).hexdigest() != digest:
            raise PermissionError("executable changed")
        if destination:
            destination.mkdir()
            artifacts.pending(plugin, token, root, destination, live)
        from core.process import stream
        iterator = stream([str(tool), *argv], cwd=root, env=_environment(tool),
                          stdin=subprocess.DEVNULL, timeout=max(.001, deadline - time.monotonic()), checkpoint=live)
        total = 0
        try:
            while True:
                try:
                    line = next(iterator)
                except StopIteration as completed:
                    code = completed.value
                    break
                total += len(line.encode("utf-8"))
                if total > 16 * 1024**2:
                    raise RuntimeError("host tool output limit exceeded")
                output(line)
        finally:
            iterator.close()
        live()
        return {"returncode": code, "output": {"id": token, "directory": str(destination)} if destination else None}
