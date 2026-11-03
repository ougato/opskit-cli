"""Fixed host tool operations, explicitly authorized outside the sandbox."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import stat
import time
import uuid

from core.native_sandbox import CapabilityRequest
from core.plugin_runtime_inventory import _read_file

MAX_OUTPUT_BYTES = 2 * 1024**3
MAX_OUTPUT_ENTRIES = 65536
MAX_OUTPUT_DEPTH = 64


def _message(code):
    from core.i18n import t
    return t("plugin_native.creator." + code)


def _creator_environment(tool):
    """Support Creator's Node forks without inheriting the user's search path."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    system_directory = kernel.GetSystemDirectoryW
    system_directory.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    system_directory.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = system_directory(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise OSError(_message("system_unavailable"))
    system = Path(buffer.value)
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP",
                   "USERPROFILE", "LOCALAPPDATA", "APPDATA", "SystemDrive") if key in os.environ}
    # libuv requires a usable PATH even when child_process.fork uses an absolute
    # executable. Only the approved tool directory and Windows tools are exposed.
    environment["PATH"] = str(tool.parent) + os.pathsep + str(system)
    environment["ComSpec"] = str(system / "cmd.exe")
    return environment


def _ordinary(path, *, directory=False):
    if not isinstance(path, str) or not Path(path).is_absolute() or any(ord(c) < 32 for c in path):
        raise ValueError(_message("absolute_path_required"))
    target = Path(path).absolute()
    for parent in (target, *target.parents):
        info = parent.lstat()
        if parent.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(_message("linked_path_forbidden"))
    if directory != target.is_dir() or (not directory and not target.is_file()):
        raise ValueError(_message("invalid_resource"))
    return target


def _inspect_output(root, platform, checkpoint):
    """Bounded post-build inspection, not a write quota or tool confinement."""
    root = _ordinary(str(root), directory=True)
    root_info = root.stat()
    pending = [(root, 0, (root_info.st_dev, root_info.st_ino))]
    entries = files = size = 0
    while pending:
        checkpoint()
        directory, depth, expected_identity = pending.pop()
        before = _ordinary(str(directory), directory=True).stat()
        if (before.st_dev, before.st_ino) != expected_identity:
            raise ValueError(_message("invalid_artifact"))
        with os.scandir(directory) as children:
            for child in children:
                checkpoint()
                entries += 1
                if entries > MAX_OUTPUT_ENTRIES:
                    raise ValueError(_message("artifact_limit_exceeded"))
                # Windows DirEntry caches incomplete metadata (including a
                # zero link count). Query the file itself before classifying.
                info = Path(child.path).lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError(_message("invalid_artifact"))
                if stat.S_ISDIR(info.st_mode):
                    if depth >= MAX_OUTPUT_DEPTH:
                        raise ValueError(_message("artifact_limit_exceeded"))
                    pending.append((Path(child.path), depth + 1, (info.st_dev, info.st_ino)))
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    files += 1
                    size += info.st_size
                    if size > MAX_OUTPUT_BYTES:
                        raise ValueError(_message("artifact_limit_exceeded"))
                else:
                    raise ValueError(_message("invalid_artifact"))
        after = _ordinary(str(directory), directory=True).stat()
        if (before.st_dev, before.st_ino, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_mtime_ns):
            raise ValueError(_message("invalid_artifact"))
    if not files or not size:
        raise ValueError(_message("invalid_artifact"))
    if platform in {"web-mobile", "web-desktop"}:
        from core.host_file_access import open_bounded
        with open_bounded(root / "index.html", root, 16 * 1024**2) as stream:
            if not stream.read(1):
                raise ValueError(_message("invalid_artifact"))
    checkpoint()
    final_root = _ordinary(str(root), directory=True).stat()
    if (final_root.st_dev, final_root.st_ino, final_root.st_mtime_ns) != (
            root_info.st_dev, root_info.st_ino, root_info.st_mtime_ns):
        raise ValueError(_message("invalid_artifact"))
    return {"directory": str(root), "files": files, "bytes": size}


def creator_build(plugin, fingerprint, executable, project, options, *, authority,
                  check_identity, deadline, checkpoint, output):
    from core.plugin_build_history import project_lock
    root = _ordinary(project, directory=True)
    with project_lock(root):
        return _creator_build(plugin, fingerprint, executable, project, options, authority=authority,
            check_identity=check_identity, deadline=deadline, checkpoint=checkpoint, output=output)


def _creator_build(plugin, fingerprint, executable, project, options, *, authority,
                   check_identity, deadline, checkpoint, output):
    """Creator runs project scripts on the host: authorization states this explicitly."""
    from core.plugin_tool_config import resolve_creator
    tool = _ordinary(resolve_creator(executable))
    root = _ordinary(project, directory=True)
    def identity(path):
        info = path.stat()
        return info.st_dev, info.st_ino
    tool_identity, project_identity = identity(tool), identity(root)
    if tool.name.casefold() not in {"creator.exe", "cocoscreator.exe"} or os.name != "nt":
        raise ValueError(_message("unsupported_executable"))
    if not isinstance(options, dict) or set(options) - {"platform", "debug"}:
        raise ValueError(_message("invalid_options"))
    platform = options.get("platform")
    if platform not in {"web-mobile", "web-desktop", "windows", "android"}:
        raise ValueError(_message("invalid_platform"))
    debug = options.get("debug", False)
    if type(debug) is not bool:
        raise ValueError(_message("invalid_debug"))
    from core.plugin_disk_policy import policy, check_space
    disk = policy()
    base = Path(disk["creator_output_directory"]) if disk["creator_output_directory"] else root / "build"
    check_space(root)
    check_space(base)
    base_identity = identity(base) if disk["creator_output_directory"] else None
    # Tool content and exact project/options are part of the persistent grant.
    digest = hashlib.sha256(_read_file(tool, Path(tool.anchor), 256 * 1024**2)).hexdigest()
    resource = {
        "operation": "creator.build", "executable": str(tool), "sha256": digest,
        "project": str(root), "platform": platform, "debug": debug,
        "output_policy": "project-build-unique-v1",
    }
    if disk["creator_output_directory"]:
        resource["output_directory"] = str(base)
    scope = CapabilityRequest(plugin, fingerprint, "tool.execute", json.dumps(resource, ensure_ascii=False, sort_keys=True))
    from core.prompt import guard_input
    with guard_input(checkpoint):
        authority.require(scope, check_identity=check_identity)
    from core.native_task_control import task_checkpoint
    checkpoint = task_checkpoint(checkpoint, output=output)
    next_space_check = 0.0
    def live():
        nonlocal next_space_check
        checkpoint()
        if base_identity is not None and (
                _ordinary(str(base), directory=True) != base or identity(base) != base_identity):
            raise PermissionError(_message("resource_changed"))
        if time.monotonic() >= next_space_check:
            check_space(base)
            next_space_check = time.monotonic() + 2
        authority.check(scope, check_identity=check_identity)
        if (_ordinary(resolve_creator(executable)) != tool or _ordinary(str(root), directory=True) != root
                or identity(tool) != tool_identity or identity(root) != project_identity):
            raise PermissionError(_message("resource_changed"))
    live()
    if hashlib.sha256(_read_file(tool, Path(tool.anchor), 256 * 1024**2)).hexdigest() != digest:
        raise PermissionError(_message("executable_changed"))
    environment = _creator_environment(tool)
    # A unique, explicit output avoids reporting stale files from an earlier
    # successful build. Never remove existing user build directories.
    if os.path.lexists(base):
        _ordinary(str(base), directory=True)
    output_name = "opskit-" + platform + "-" + uuid.uuid4().hex
    artifact_root = base / output_name
    if os.path.lexists(artifact_root):
        raise ValueError(_message("invalid_artifact"))
    from core.process import stream
    iterator = stream([str(tool), "--project", str(root), "--build",
                       "platform=" + platform + ";debug=" + str(debug).lower()
                       + ";buildPath=" + (str(base).replace("\\", "/") if disk["creator_output_directory"] else "project://build")
                       + ";outputName=" + output_name],
                      cwd=root, env=environment, stdin=subprocess.DEVNULL,
                      timeout=max(0.001, deadline - time.monotonic()), checkpoint=live)
    total = 0
    from core.i18n import t
    output(t("plugin_native.creator.starting", platform=platform))
    try:
        while True:
            try:
                line = next(iterator)
            except StopIteration as completed:
                code = completed.value
                break
            total += len(line.encode("utf-8"))
            if total > 16 * 1024**2:
                raise RuntimeError(_message("log_limit_exceeded"))
            output(line)
    except BaseException:
        output(t("plugin_native.creator.interrupted"))
        raise
    finally:
        iterator.close()
    live()
    # Creator 3.x documents 36 as build success (32/34 are failures). Preserve
    # its original exit status; older/conventional launchers may return zero.
    success = code in (0, 36)
    artifact = None
    if success:
        try:
            artifact = _inspect_output(artifact_root, platform, live)
        except BaseException:
            output(t("plugin_native.creator.artifact_failed"))
            raise
    output(t("plugin_native.creator.completed", platform=platform) if success
           else t("plugin_native.creator.failed", code=code))
    result = {"returncode": code, "success": success, "platform": platform}
    if artifact is not None:
        result["artifact"] = artifact
        from core.plugin_build_history import record
        record(root, artifact, live)
    return result
