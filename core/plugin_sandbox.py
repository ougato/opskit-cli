"""Docker sandbox supervisor. No third-party code runs in the platform process.

Only immutable plugin snapshots and that plugin's state are visible. Persistent
state is copied into bounded tmpfs and returned over a bounded JSON protocol.
The container never receives platform credentials, Docker socket or a host shell.
"""
from __future__ import annotations

import base64
import io
import json
import os
import queue
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from core.paths import data_dir
from core.plugin_trust import compute_fingerprint, is_trusted

STATE_LIMIT = 16 * 1024**2
WIRE_LIMIT = 32 * 1024**2
MAX_FILES = 4096
PREFIX = "OPSKIT_RPC:"


class SandboxError(RuntimeError):
    pass


def _encode(value, state_root: Path):
    if isinstance(value, Path):
        if value.as_posix().startswith("/data/"):
            target = value.as_posix()
        else:
            try:
                target = "/data/" + value.resolve().relative_to(state_root.resolve()).as_posix()
            except ValueError as exc:
                raise SandboxError("file path is outside the authorized plugin state") from exc
        return {"__opskit_path__": target}
    if isinstance(value, (list, tuple)):
        return [_encode(v, state_root) for v in value]
    if isinstance(value, dict):
        return {k: _encode(v, state_root) for k, v in value.items()}
    return value


def docker_command() -> list[str]:
    from core.config import load_config
    if shutil.which("docker"):
        return ["docker"]
    if os.name == "nt" and shutil.which("wsl"):
        distribution = load_config().get("plugin", {}).get("sandbox", {}).get("wsl_distribution", "Debian")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", distribution):
            raise SandboxError("invalid WSL distribution")
        return ["wsl", "-d", distribution, "--exec", "docker"]
    raise SandboxError("Docker sandbox unavailable; refusing unisolated plugin execution")


def mount_path(path: Path, command: list[str]) -> str:
    path = path.resolve()
    if command[0] == "wsl":
        if not re.fullmatch(r"[A-Za-z]:", path.drive):
            raise SandboxError("WSL sandbox requires a local drive path")
        value = "/mnt/" + path.drive[0].lower() + path.as_posix()[2:]
    else:
        value = str(path)
    if any(c in value for c in ",\n\r"):
        raise SandboxError("unsupported mount path")
    return value


def _control(command: list[str], *args: str) -> str:
    result = subprocess.run(command + list(args), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
    if result.returncode:
        raise SandboxError("Docker sandbox operation failed: " + result.stderr[-500:])
    return result.stdout.strip()


def _image(manifest, command) -> str:
    catalog = data_dir() / "plugin-runtimes" / (manifest.name + ".json")
    try:
        record = json.loads(catalog.read_text(encoding="utf-8"))
        image = record["image"]
        if record["fingerprint"] != compute_fingerprint(manifest.root):
            raise SandboxError("plugin runtime needs rebuilding for this plugin version")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise SandboxError("runtime must use an immutable local image ID")
        actual = _control(command, "image", "inspect", "--format", "{{.Id}}", image)
        if actual != image:
            raise SandboxError("runtime image identity mismatch")
        if _control(command, "image", "inspect", "--format", '{{index .Config.Labels "org.opskit.sandbox"}}', image) != "2":
            raise SandboxError("not a platform sandbox runtime")
        return image
    except (OSError, KeyError, ValueError) as exc:
        raise SandboxError("Build the plugin runtime with scripts/build_plugin_runtime.py first") from exc


def check_authorization(manifest) -> str:
    from core.config import load_config
    if not load_config().get("modules", {}).get(manifest.name, {}).get("enabled", True):
        raise SandboxError("plugin is disabled")
    from core.plugin_integrity import CHECK_MISMATCH, verify_checksums
    if verify_checksums(manifest.root) == CHECK_MISMATCH:
        raise SandboxError("plugin integrity check failed")
    fingerprint = compute_fingerprint(manifest.root)
    if not is_trusted(manifest.name, fingerprint):
        raise SandboxError("plugin content changed or authorization revoked")
    if "root" in manifest.permissions:
        raise SandboxError("third-party plugins cannot request host root access")
    return fingerprint


def _copy_tree(source: Path, dest: Path, limit: int) -> None:
    size = 0
    count = 0
    if not source.exists():
        dest.mkdir(parents=True, exist_ok=True)
        return
    if source.is_symlink() or source.resolve() != source.absolute():
        raise SandboxError("linked plugin roots are forbidden")
    for path in source.rglob("*"):
        rel = path.relative_to(source)
        if any(part in {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"} for part in rel.parts):
            continue
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise SandboxError("plugin links are forbidden")
        if path.is_file():
            size += path.stat().st_size
            count += 1
            if size > limit or count > MAX_FILES:
                raise SandboxError("plugin snapshot exceeds limits")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            target.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
    dest.mkdir(parents=True, exist_ok=True)


def _save_state(state: str, root: Path) -> None:
    if not isinstance(state, str) or len(state) > WIRE_LIMIT:
        raise SandboxError("invalid state payload")
    raw = base64.b64decode(state, validate=True)
    if len(raw) > STATE_LIMIT + MAX_FILES * 2048 + 10240:
        raise SandboxError("state payload too large")
    from core.archive import extract_tar
    with tempfile.TemporaryDirectory(dir=root.parent, prefix=".state-") as temp:
        staging = Path(temp) / "new"
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            members = archive.getmembers()
            if len(members) > MAX_FILES or sum(m.size for m in members) > STATE_LIMIT:
                raise SandboxError("persistent state exceeds quota")
            extract_tar(archive, staging)
        backup = root.with_name(root.name + ".previous")
        if backup.exists():
            raise SandboxError("unresolved previous state transaction")
        if root.exists():
            _rename_state(root, backup)
        try:
            _rename_state(staging, root)
        except BaseException:
            if backup.exists():
                _rename_state(backup, root)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        from core.secrets import restrict_permissions
        restrict_permissions(root)
        for path in root.rglob("*"):
            restrict_permissions(path)


def _rename_state(source: Path, target: Path) -> None:
    # Windows scanners briefly retain directory handles after file creation.
    for attempt in range(10):
        try:
            source.rename(target)
            return
        except PermissionError:
            if os.name != "nt" or attempt == 9:
                raise
            time.sleep(0.1)


def _broker(manifest, request: dict):
    """Identity comes from this supervisor, never from the plugin's payload."""
    check_authorization(manifest)
    method = request.get("method")
    if len(json.dumps(request)) > 65536:
        raise SandboxError("broker request too large")
    args, kwargs = request.get("args", []), request.get("kwargs", {})
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        raise SandboxError("invalid broker arguments")
    ui = {"select", "multi_select", "paged_select", "confirm", "text_input", "pause", "clear_screen", "print_header"}
    if method in ui:
        from core import prompt
        return getattr(prompt, method)(*args, **kwargs)
    if method == "service.call":
        from core.plugin_services import call_service
        return call_service(*args, caller=manifest.name, **kwargs)
    raise SandboxError("broker capability not granted")


_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()
_invocations = threading.local()


@contextmanager
def _state_lock(name: str):
    """Serialize transactions across CLI processes without deleting lock files."""
    root = data_dir() / "plugin-runtimes"
    root.mkdir(parents=True, exist_ok=True)
    with (root / (name + ".lock")).open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SandboxError("plugin is busy in another process; retry later") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def invoke(manifest, action: str, *, args=None, service="", method="", kwargs=None, caller="platform"):
    stack = getattr(_invocations, "stack", [])
    if manifest.name in stack or len(stack) >= 4:
        raise SandboxError("cyclic or excessive nested plugin service calls denied")
    with _locks_guard:
        lock = _locks.setdefault(manifest.name, threading.RLock())
    with lock, _state_lock(manifest.name):
        _invocations.stack = [*stack, manifest.name]
        try:
            return _invoke(manifest, action, args=args, service=service, method=method, kwargs=kwargs, caller=caller)
        finally:
            _invocations.stack = stack


@contextmanager
def _network(manifest, command, image, settings):
    if "network" not in manifest.permissions:
        yield "none", []
        return
    name = "opskit-net-" + uuid.uuid4().hex
    proxy = name + "-proxy"
    token = uuid.uuid4().hex + uuid.uuid4().hex
    private = settings.get("private_targets", {}).get(manifest.name, [])
    if not isinstance(private, list) or any(not isinstance(x, str) for x in private):
        raise SandboxError("invalid private network grants")
    try:
        _control(command, "network", "create", "--internal", name)
        _control(command, "run", "-d", "--rm", "--name", proxy, "--pull=never",
                 "--read-only", "--user", "65534:65534", "--cap-drop=ALL",
                 "--security-opt=no-new-privileges", "--pids-limit=32", "--memory=64m", "--cpus=0.5",
                 "--env", "OPSKIT_PROXY_TOKEN=" + token,
                 "--env", "OPSKIT_PRIVATE_TARGETS=" + json.dumps(private),
                 image, "python", "-u", "-m", "core.plugin_egress")
        _control(command, "network", "connect", name, proxy)
        network_data = json.loads(_control(command, "inspect", proxy))[0]["NetworkSettings"]["Networks"]
        address = network_data[name]["IPAddress"]
        url = f"http://opskit:{token}@{address}:8080"
        yield name, ["--env", "HTTP_PROXY=" + url, "--env", "HTTPS_PROXY=" + url,
                     "--env", "http_proxy=" + url, "--env", "https_proxy=" + url]
    finally:
        for arguments in [("rm", "-f", proxy), ("network", "rm", name)]:
            try:
                _control(command, *arguments)
            except (SandboxError, subprocess.TimeoutExpired):
                pass


def _invoke(manifest, action, *, args, service, method, kwargs, caller):
    from core.plugin import load_manifest
    current = load_manifest(manifest.root)
    if current is None or current.name != manifest.name:
        raise SandboxError("plugin manifest changed or identity is invalid")
    manifest = current
    fingerprint = check_authorization(manifest)
    command = docker_command()
    image = _image(manifest, command)
    from core.config import load_config
    settings = load_config().get("plugin", {}).get("sandbox", {})
    timeout = min(max(float(settings.get("timeout", 300)), 1), 3600)
    owner_root = data_dir() / "plugin-data" / manifest.name
    state_root = owner_root / "self"
    state_root.parent.mkdir(parents=True, exist_ok=True)
    # A provider's persistent state is partitioned by the authenticated caller.
    if action.startswith("service"):
        state_root = owner_root / "callers" / caller
        state_root.parent.mkdir(parents=True, exist_ok=True)
    name = "opskit-" + uuid.uuid4().hex
    with _network(manifest, command, image, settings) as (network, proxy_env), tempfile.TemporaryDirectory(prefix="opskit-sandbox-") as temp:
        base = Path(temp)
        _copy_tree(manifest.root, base / "plugin", 128 * 1024**2)
        if compute_fingerprint(base / "plugin") != fingerprint:
            raise SandboxError("plugin changed during snapshot creation")
        from dataclasses import replace
        snapshot_manifest = load_manifest(base / "plugin")
        if snapshot_manifest is None or replace(snapshot_manifest, root=manifest.root) != manifest:
            raise SandboxError("plugin manifest changed during authorization")
        _copy_tree(state_root, base / "seed", STATE_LIMIT)
        payload = {"action": action, "name": manifest.name, "entry": manifest.entry,
                   "kind": manifest.kind, "args": args or [], "kwargs": kwargs or {},
                   "service": service, "method": method, "caller": caller}
        (base / "request.json").write_text(json.dumps(_encode(payload, state_root)), encoding="utf-8")
        mounts = []
        for source, target in [(base / "plugin", "/plugin"), (base / "seed", "/seed"), (base / "request.json", "/request.json")]:
            mounts += ["--mount", f"type=bind,src={mount_path(source, command)},dst={target},readonly"]
        cmd = command + ["run", "--rm", "--name", name, "--pull=never", "-i",
            "--read-only", "--user", "65534:65534", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=256m",
            "--memory-swap=256m", "--cpus=1", "--ulimit", "nofile=256:256",
            "--network", network,
            *proxy_env,
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777",
            "--tmpfs", "/data:rw,nosuid,nodev,noexec,size=32m,uid=65534,gid=65534,mode=0700",
            *mounts, "--env", "HOME=/data", "--env", "OPSKIT_DATA_DIR=/data",
            "--env", "OPSKIT_SANDBOX=1", "--env", "PYTHONDONTWRITEBYTECODE=1",
            image, "python", "-u", "-m", "core.plugin_worker", "/request.json"]
        process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        lines: queue.Queue = queue.Queue(maxsize=4)
        stopped = threading.Event()

        def read() -> None:
            try:
                while not stopped.is_set():
                    line = process.stdout.readline(WIRE_LIMIT + 1)
                    if not line:
                        break
                    while not stopped.is_set():
                        try:
                            lines.put(line, timeout=0.1)
                            break
                        except queue.Full:
                            pass
            finally:
                while not stopped.is_set():
                    try:
                        lines.put(None, timeout=0.1)
                        break
                    except queue.Full:
                        pass

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        deadline = time.monotonic() + timeout
        next_authorization_check = time.monotonic()
        result = None
        total_output = 0
        try:
            while True:
                if time.monotonic() >= deadline:
                    raise SandboxError("plugin exceeded execution deadline")
                if time.monotonic() >= next_authorization_check:
                    enabled = load_config().get("modules", {}).get(manifest.name, {}).get("enabled", True)
                    if not enabled or not is_trusted(manifest.name, fingerprint):
                        raise SandboxError("plugin authorization revoked during execution")
                    next_authorization_check = time.monotonic() + 1
                try:
                    line = lines.get(timeout=0.1)
                except queue.Empty:
                    continue
                if line is None:
                    break
                total_output += len(line)
                if len(line) > WIRE_LIMIT or total_output > WIRE_LIMIT * 2:
                    raise SandboxError("plugin exceeded output quota")
                if line.startswith(PREFIX):
                    message = json.loads(line[len(PREFIX):])
                    if message.get("type") == "result":
                        if result is not None:
                            raise SandboxError("duplicate plugin result")
                        result = message
                    elif message.get("type") == "call":
                        from core.prompt import UserCancel
                        try:
                            reply = {"value": _broker(manifest, message)}
                        except UserCancel:
                            reply = {"cancel": True}
                        except Exception:
                            reply = {"error": "broker request denied or failed"}
                        process.stdin.write(json.dumps(reply) + "\n")
                        process.stdin.flush()
                    else:
                        raise SandboxError("invalid plugin protocol")
                else:
                    from core.prompt import console
                    console.print(line.rstrip(), markup=False, highlight=False)
            code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
            if code or result is None or result.get("error"):
                raise SandboxError("plugin execution failed" + (": " + str(result.get("error")) if result else ""))
            check_authorization(manifest)
            if compute_fingerprint(manifest.root) != fingerprint:
                raise SandboxError("plugin changed during execution")
            _save_state(result["state"], state_root)
            return result.get("value")
        finally:
            stopped.set()
            # Docker must terminate the container, not just the docker CLI.
            try:
                _control(command, "rm", "-f", name)
            except (SandboxError, subprocess.TimeoutExpired):
                pass
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            thread.join(timeout=5)
            process.stdin.close()
            if not thread.is_alive():
                process.stdout.close()
