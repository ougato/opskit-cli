"""Consent-bound, business-neutral host tools. Never import plugin code here."""
import hashlib
import base64
import binascii
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
from contextlib import nullcontext

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


def _environment(tool, dependencies=()):
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
    environment["PATH"] = os.pathsep.join(dict.fromkeys([str(tool.parent), *(str(item.parent) for item in dependencies), str(system)]))
    environment["ComSpec"] = str(system / "cmd.exe")
    return environment


def _grant(plugin, fingerprint, operation, resource, authority, check_identity, checkpoint):
    scope = CapabilityRequest(plugin, fingerprint, operation, resource)
    from core.prompt import guard_input
    checkpoint()
    with guard_input(checkpoint):
        authority.require(scope, check_identity=check_identity)
    return scope


def path_info(plugin, fingerprint, path, directory, write=False, execute=False, *, authority, check_identity, checkpoint, **unused):
    if (type(directory) is not bool or type(write) is not bool or type(execute) is not bool
            or (write and not directory) or (execute and (not directory or not write))):
        raise ValueError("invalid path kind")
    target = _ordinary(path, directory=directory)
    identity = _identity(target)
    scope = _grant(plugin, fingerprint, "directory.manage" if execute else "directory.read_write" if write else "directory.read",
                   str(target if directory else target.parent), authority, check_identity, checkpoint)
    authority.check(scope, check_identity=check_identity)
    if _ordinary(path, directory=directory) != target or _identity(target) != identity:
        raise ValueError("host resource changed")
    return {"path": str(target), "directory": directory}


def find_executable(plugin, fingerprint, name, preferred, *, authority, check_identity, checkpoint, **unused):
    """Find a named executable without searching the working directory or running it."""
    if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}\.exe", name, re.IGNORECASE)
            or not isinstance(preferred, str) or len(preferred) > 32767):
        raise ValueError("invalid executable name or preferred path")
    if os.name != "nt":
        raise ValueError("host tool backend not implemented for this platform")
    candidates = []
    def add_candidate(path):
        candidates.append(path)
        directory = path.parent
        if directory.name.casefold() in {"cmd", "bin"}:
            sibling = "bin" if directory.name.casefold() == "cmd" else "cmd"
            candidates.append(directory.parent / sibling / name)

    if preferred:
        if Path(preferred).name.casefold() != name.casefold() or not Path(preferred).is_absolute():
            raise ValueError("invalid preferred executable path")
        add_candidate(Path(preferred))
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if entry and Path(entry).is_absolute():
            add_candidate(Path(entry) / name)
    # Conventional per-machine and per-user installations, when PATH omits them.
    stem = name[:-4]
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = os.environ.get(variable, "")
        if root and Path(root).is_absolute():
            base = Path(root) / ("Programs" if variable == "LOCALAPPDATA" else "") / stem
            candidates.extend((base / "cmd" / name, base / "bin" / name, base / name))
    if name.casefold() == "node.exe":
        from software.recipes.nodejs.common import node_versions_dir
        managed = node_versions_dir()
        try:
            active = (managed / "active").resolve(strict=True)
        except (OSError, RuntimeError):
            pass
        else:
            if active.parent == managed and re.fullmatch(r"node[0-9]+\.[0-9]+\.[0-9]+", active.name):
                add_candidate(active / name)
    seen = set()
    for candidate in candidates:
        checkpoint()
        key = str(candidate).casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            target = _ordinary(str(candidate))
            info = target.stat()
            if info.st_nlink != 1 or info.st_size > 256 * 1024**2:
                continue
        except (OSError, ValueError):
            continue
        # A candidate is returned only after the existing directory consent and
        # identity recheck. Rejection or cancellation must never trigger fallback.
        result = path_info(plugin, fingerprint, str(target), False, authority=authority,
                           check_identity=check_identity, checkpoint=checkpoint)["path"]
        after = _ordinary(result).stat()
        if (after.st_dev, after.st_ino, after.st_nlink, after.st_size) != (
                info.st_dev, info.st_ino, info.st_nlink, info.st_size):
            raise ValueError("host executable changed")
        return result
    return ""


def file_version(plugin, fingerprint, path, *, authority, check_identity, checkpoint, **unused):
    """Read Windows fixed executable version metadata under directory consent."""
    if os.name != "nt":
        raise ValueError("file version metadata backend unavailable")
    import ctypes
    from ctypes import wintypes
    target = _ordinary(path)
    before = target.stat()
    if before.st_nlink != 1:
        raise ValueError("linked executable forbidden")
    scope = _grant(plugin, fingerprint, "directory.read", str(target.parent), authority, check_identity, checkpoint)
    authority.check(scope, check_identity=check_identity)
    checkpoint()
    version = ctypes.WinDLL("version", use_last_error=True)
    size_fn = version.GetFileVersionInfoSizeW
    size_fn.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    size_fn.restype = wintypes.DWORD
    get_info = version.GetFileVersionInfoW
    get_info.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
    get_info.restype = wintypes.BOOL
    query = version.VerQueryValueW
    query.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPVOID),
                      ctypes.POINTER(wintypes.UINT)]
    query.restype = wintypes.BOOL
    ignored = wintypes.DWORD()
    size = size_fn(str(target), ctypes.byref(ignored))
    if not 0 < size <= 16 * 1024 * 1024:
        raise ValueError("executable version metadata unavailable")
    buffer = ctypes.create_string_buffer(size)
    if not get_info(str(target), 0, size, buffer):
        raise OSError("executable version metadata read failed")
    pointer, length = wintypes.LPVOID(), wintypes.UINT()
    if not query(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52:
        raise ValueError("invalid executable version metadata")
    words = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD * 13)).contents
    if words[0] != 0xFEEF04BD:
        raise ValueError("invalid executable version signature")
    def components(high, low):
        return [high >> 16, high & 0xffff, low >> 16, low & 0xffff]
    result = {"file_version": components(words[2], words[3]),
              "product_version": components(words[4], words[5])}
    checkpoint()
    authority.check(scope, check_identity=check_identity)
    after = _ordinary(path).stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_nlink) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_nlink):
        raise ValueError("executable changed during metadata inspection")
    return result


def execute(plugin, fingerprint, executable, arguments, directory, options, *, authority,
            check_identity, deadline, checkpoint, output, host_work_scope=None, output_start=None,
            progress_console=None):
    """Return raw exit status and a pending output token; success is plugin policy."""
    from core import plugin_artifacts as artifacts
    from core.plugin_disk_policy import check_space
    if (not isinstance(arguments, list) or len(arguments) > 128
            or any(not isinstance(a, str) or len(a) > 16384 or any(ord(c) < 32 for c in a) for a in arguments)
            or not isinstance(options, dict) or set(options) - {"output_parent", "capture_output", "capture_after", "tool_dependencies", "stdin_base64", "config_base64", "timeout_seconds", "authorization_scope", "output_header", "progress"}
            or ("capture_output" in options and type(options["capture_output"]) is not bool)
            or not isinstance(options.get("tool_dependencies", []), list)
            or len(options.get("tool_dependencies", [])) > 8):
        raise ValueError("invalid host tool arguments")
    progress = None
    if "progress" in options:
        if options.get("capture_output") or progress_console is None:
            raise ValueError("progress requires streamed console output")
        from core.plugin_tool_progress import ToolProgress
        progress = ToolProgress(options["progress"], progress_console, output)
    header = options.get("output_header")
    if header is not None and (not isinstance(header, list) or not 1 <= len(header) <= 8
            or any(not isinstance(part, str) or not 1 <= len(part) <= 200
                   or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in part) for part in header)):
        raise ValueError("invalid host output header")
    grant_scope = options.get("authorization_scope", "command")
    if grant_scope not in ("command", "tool") or (grant_scope == "tool" and
            (options.get("tool_dependencies") or options.get("output_parent") is not None or "config_base64" in options)):
        raise ValueError("invalid host tool authorization scope")
    timeout = options.get("timeout_seconds", 300)
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError("invalid host tool timeout")
    stdin_data = None
    if "stdin_base64" in options:
        encoded = options["stdin_base64"]
        if not isinstance(encoded, str) or len(encoded) > 21848:
            raise ValueError("invalid host tool input")
        try:
            stdin_data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("invalid host tool input") from error
        if len(stdin_data) > 16384:
            raise ValueError("host tool input exceeds limit")
    config_data = None
    if "config_base64" in options:
        encoded = options["config_base64"]
        if not isinstance(encoded, str) or len(encoded) > 21848:
            raise ValueError("invalid host tool configuration")
        try:
            config_data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("invalid host tool configuration") from error
        if len(config_data) > 16384 or not config_data:
            raise ValueError("host tool configuration exceeds limit")
    if bool(config_data is not None) != any("{config_file}" in a for a in arguments):
        raise ValueError("host tool configuration placeholder mismatch")
    capture_after = options.get("capture_after")
    if capture_after is not None and (not isinstance(capture_after, str) or not 1 <= len(capture_after) <= 128
                                      or any(ord(c) < 32 for c in capture_after)):
        raise ValueError("invalid host tool capture marker")
    tool = _ordinary(executable)
    if os.name != "nt" or tool.suffix.lower() != ".exe":
        raise ValueError("host executable backend unavailable")
    root = _ordinary(directory, directory=True)
    dependencies = [_ordinary(path) for path in options.get("tool_dependencies", [])]
    if any(path.suffix.lower() != ".exe" for path in dependencies):
        raise ValueError("host tool dependencies must be executables")
    dependency_ids = [_identity(path) for path in dependencies]
    dependency_records = [{"executable": str(path), "search_directory": str(path.parent),
                           "sha256": hashlib.sha256(_read_file(path, Path(path.anchor), 256 * 1024**2)).hexdigest()}
                          for path in dependencies]
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
    # The random output name is confined to the approved parent. Authorize
    # the literal template instead of an unusable, one-off expanded name.
    resource_details = {"executable": str(tool), "sha256": digest, "arguments": arguments,
                           "directory": str(root), "output_parent": str(base) if base else None,
                           **({"capture_output": True} if options.get("capture_output") else {}),
                           **({"stdin": {"sha256": hashlib.sha256(stdin_data).hexdigest(), "size": len(stdin_data)}}
                              if stdin_data is not None else {}),
                           **({"configuration": {"sha256": hashlib.sha256(config_data).hexdigest(), "size": len(config_data)}}
                              if config_data is not None else {}),
                           **({"capture_after": capture_after} if capture_after is not None else {}),
                           **({"timeout_seconds": timeout} if "timeout_seconds" in options else {}),
                           **({"tool_dependencies": dependency_records} if dependencies else {})}
    if grant_scope == "tool":
        resource_details = {"executable": str(tool), "sha256": digest, "authorization_scope": "tool"}
    resource = json.dumps(resource_details, ensure_ascii=False, sort_keys=True)
    tool_id, root_id = _identity(tool), _identity(root)
    base_id = _identity(base) if base else None
    # A separately approved project-management grant includes host execution
    # from that directory. Older read/write grants never imply execution.
    from core.plugin_capabilities import CapabilityDenied
    project_scope = CapabilityRequest(plugin, fingerprint, "directory.manage", str(root))
    checkpoint()
    try:
        authority.check(project_scope, check_identity=check_identity)
        scope = project_scope
    except CapabilityDenied as error:
        if error.code != "missing_or_revoked":
            raise
        scope = _grant(plugin, fingerprint, "tool.execute", resource, authority, check_identity, checkpoint)
    from core.native_task_control import task_checkpoint
    checkpoint = task_checkpoint(checkpoint, output=output)
    next_check = 0.0
    def current_deadline():
        return deadline() if callable(deadline) else deadline
    def live():
        nonlocal next_check
        checkpoint()
        if time.monotonic() >= current_deadline():
            raise TimeoutError("host tool deadline exceeded")
        authority.check(scope, check_identity=check_identity)
        if (_identity(_ordinary(executable)) != tool_id
                or _identity(_ordinary(directory, directory=True)) != root_id
                or any(_identity(_ordinary(str(path))) != identity for path, identity in zip(dependencies, dependency_ids))
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
        for dependency, record in zip(dependencies, dependency_records):
            if hashlib.sha256(_read_file(dependency, Path(dependency.anchor), 256 * 1024**2)).hexdigest() != record["sha256"]:
                raise PermissionError("tool dependency changed")
        if destination:
            destination.mkdir()
            artifacts.pending(plugin, token, root, destination, live)
        from core.process import stream
        input_args = {"stdin_data": stdin_data} if stdin_data is not None else {"stdin": subprocess.DEVNULL}
        if progress is not None and os.name == "nt":
            import locale
            input_args["fallback_encoding"] = locale.getencoding()
        total = 0
        captured = []
        captures_after = []
        marker_count = 0
        capture_next = False
        # Only the supervised external process gets a separate bounded budget.
        # The worker is blocked on this broker call, while live() continues
        # checking all authorizations and resource identities.
        with tempfile.TemporaryDirectory(prefix="opskit-tool-") if config_data is not None else nullcontext(None) as scratch:
            if scratch is not None:
                config_file = Path(scratch) / "request.json"
                config_file.write_bytes(config_data)
                if ";" in str(config_file):
                    raise ValueError("unsupported temporary configuration path")
                argv = [a.replace("{config_file}", str(config_file).replace("\\", "/")) for a in argv]
            if header is not None and output_start is not None:
                output_start(header)
            with (host_work_scope() if host_work_scope is not None else nullcontext(),
                  progress if progress is not None else nullcontext()):
                iterator = stream([str(tool), *argv], cwd=root, env=_environment(tool, dependencies),
                                  **input_args, timeout=timeout, checkpoint=live)
                try:
                    while True:
                        try:
                            line = next(iterator)
                        except StopIteration as completed:
                            code = completed.value
                            break
                        line_size = len(line.encode("utf-8"))
                        total += line_size
                        if total > 16 * 1024**2:
                            raise RuntimeError("host tool output limit exceeded")
                        if capture_next:
                            captures_after.append(line if line_size <= 16384 else None)
                            capture_next = False
                        if capture_after is not None and capture_after in line:
                            marker_count += 1
                            capture_next = True
                        if options.get("capture_output"):
                            if total > 16384:
                                raise RuntimeError("captured host tool output limit exceeded")
                            captured.append(line)
                        elif progress is not None:
                            progress.line(line)
                        else:
                            output(line)
                    if progress is not None:
                        progress.finish(code)
                finally:
                    iterator.close()
        live()
        result = {"returncode": code, "output": {"id": token, "directory": str(destination)} if destination else None}
        if options.get("capture_output"):
            result["captured_output"] = "\n".join(captured)
        if capture_after is not None:
            result["captured_after"] = captures_after[0] if marker_count == 1 and len(captures_after) == 1 else None
        return result
