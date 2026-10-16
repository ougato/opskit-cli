"""Docker sandbox supervisor. No third-party code runs in the platform process.

Only immutable plugin snapshots and that plugin's state are visible. Persistent
state is copied into bounded tmpfs and returned over a bounded JSON protocol.
The container never receives platform credentials, Docker socket or a host shell.
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import queue
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from core.paths import data_dir
from core.plugin_trust import is_trusted
from core.process import capture, start, terminate_tree
from core import plugin_json
from core.plugin_tree import bounded_paths, SKIP_DIRS

STATE_LIMIT = 16 * 1024**2
WIRE_LIMIT = 32 * 1024**2
MAX_FILES = 4096
PREFIX = "OPSKIT_RPC:"
CONTROL_TIMEOUT = 30
CONFIG_READ_TIMEOUT = 30
CONTROL_OUTPUT_LIMIT = 16384


class SandboxError(RuntimeError):
    pass


class RuntimeCatalogError(SandboxError):
    """A runtime identity could not be read or validated; never an approval."""


def _encode(value, state_root: Path):
    if isinstance(value, Path):
        if value.as_posix().startswith("/data/"):
            target = value.as_posix()
            if ".." in value.parts or "\0" in target:
                raise SandboxError("file path is outside the authorized plugin state")
        else:
            target = _encode_host_path(value, state_root)
        return {"__opskit_path__": target}
    if isinstance(value, (list, tuple)):
        return [_encode(v, state_root) for v in value]
    if isinstance(value, dict):
        return {k: _encode(v, state_root) for k, v in value.items()}
    return value


def _encode_host_path(value, state_root):
    from core.plugin_inspection import ContentInspector, StatePathError
    scoped = getattr(_invocations, "inspection_scope", False)
    inspector = getattr(_invocations, "inspector", None) if scoped else None
    if inspector is None:
        inspector = ContentInspector()
        if scoped:
            _invocations.inspector = inspector
    try:
        result = inspector.state_path(state_root, value, timeout=_remaining(CONTROL_TIMEOUT))
        _remaining()
        return result
    except StatePathError as exc:
        raise SandboxError("file path is outside the authorized plugin state") from exc
    except TimeoutError:
        raise SandboxError("plugin exceeded execution deadline resolving file path") from None
    except (OSError, ValueError, RuntimeError):
        raise SandboxError("plugin file path inspection failed") from None
    finally:
        if not scoped:
            inspector.close()


def docker_command() -> list[str]:
    if shutil.which("docker"):
        return ["docker"]
    if os.name == "nt" and shutil.which("wsl"):
        distribution = _read_sandbox_setting("distribution")
        return ["wsl", "-d", distribution, "--exec", "docker"]
    raise SandboxError("Docker sandbox unavailable; refusing unisolated plugin execution")


def _read_sandbox_setting(field, name=""):
    from core.config import get_config_path
    from core.plugin_inspection import ContentInspector
    scoped = getattr(_invocations, "inspection_scope", False)
    inspector = getattr(_invocations, "inspector", None) if scoped else None
    if inspector is None:
        inspector = ContentInspector()
        if scoped:
            _invocations.inspector = inspector
    try:
        value = inspector.config_setting(get_config_path(), field, name,
                                         timeout=_remaining(CONFIG_READ_TIMEOUT))
        _remaining()
        return value
    except SandboxError:
        raise
    except TimeoutError:
        raise SandboxError("plugin exceeded execution deadline reading sandbox settings") from None
    except (OSError, ValueError, RuntimeError):
        raise SandboxError("plugin sandbox settings inspection failed") from None
    finally:
        if not scoped:
            inspector.close()


def mount_path(path: Path, command: list[str]) -> str:
    from core.plugin_inspection import ContentInspector
    scoped = getattr(_invocations, "inspection_scope", False)
    inspector = getattr(_invocations, "inspector", None) if scoped else None
    if inspector is None:
        inspector = ContentInspector()
        if scoped:
            _invocations.inspector = inspector
    try:
        path = inspector.mount_path(path, timeout=_remaining(CONTROL_TIMEOUT))
        _remaining()
    except TimeoutError:
        raise SandboxError("plugin exceeded execution deadline resolving mount path") from None
    except (OSError, ValueError, RuntimeError):
        raise SandboxError("plugin mount path inspection failed") from None
    finally:
        if not scoped:
            inspector.close()
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
    timeout = _remaining(CONTROL_TIMEOUT)
    try:
        result = capture(command + list(args), timeout=timeout,
                         max_bytes=CONTROL_OUTPUT_LIMIT, stdin=subprocess.DEVNULL)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise SandboxError("Docker sandbox operation failed") from None
    if result.returncode:
        raise SandboxError("Docker sandbox operation failed")
    _remaining()
    return result.stdout.strip()


def _cleanup_control(command: list[str], *args: str) -> str:
    """Owned resource cleanup has its own bounded control budget after expiry."""
    deadline = getattr(_invocations, "deadline", None)
    _invocations.deadline = None
    try:
        return _control(command, *args)
    finally:
        _invocations.deadline = deadline


def _require_sandbox_capabilities(command):
    """Reject engines that would silently ignore required resource limits."""
    template = ('{"os":{{json .OSType}},"memory":{{json .MemoryLimit}},'
                '"swap":{{json .SwapLimit}},"pids":{{json .PidsLimit}},'
                '"cpu_quota":{{json .CPUCfsQuota}},"cpu_period":{{json .CPUCfsPeriod}},'
                '"security":{{json .SecurityOptions}}}')
    message = "Docker Linux sandbox requires memory, swap, PID, CPU and default seccomp support"
    try:
        value = json.loads(_control(command, "info", "--format", template))
        required = {"memory", "swap", "pids", "cpu_quota", "cpu_period"}
        if (not isinstance(value, dict) or set(value) != required | {"os", "security"}
                or value["os"] != "linux" or any(value[key] is not True for key in required)
                or not isinstance(value["security"], list) or len(value["security"]) > 32
                or any(not isinstance(option, str) or len(option) > 512 for option in value["security"])
                or not {"name=seccomp,profile=default", "name=seccomp,profile=builtin"}.intersection(value["security"])):
            raise ValueError()
    except (ValueError, TypeError):
        raise SandboxError(message) from None


def _read_runtime_catalog(path, *, gateway=False):
    if not getattr(_invocations, "inspection_scope", False):
        with _execution_budget():
            return _read_runtime_catalog(path, gateway=gateway)
    from core.plugin_inspection import ContentInspector
    inspector = getattr(_invocations, "inspector", None)
    if inspector is None:
        inspector = ContentInspector()
        _invocations.inspector = inspector
    try:
        record = inspector.runtime_catalog(path, timeout=_remaining(CONTROL_TIMEOUT), gateway=gateway)
        _remaining()
        return record
    except SandboxError:
        raise
    except TimeoutError:
        raise SandboxError("plugin exceeded execution deadline reading runtime catalog") from None
    except (OSError, ValueError, RuntimeError):
        raise RuntimeCatalogError("plugin runtime catalog inspection failed") from None


def _read_manifest(root):
    if not getattr(_invocations, "inspection_scope", False):
        with _execution_budget():
            return _read_manifest(root)
    from core.plugin_inspection import ContentInspector
    inspector = getattr(_invocations, "inspector", None)
    if inspector is None:
        inspector = ContentInspector()
        _invocations.inspector = inspector
    try:
        manifest = inspector.manifest(root, timeout=_remaining(CONTROL_TIMEOUT))
        _remaining()
        return manifest
    except SandboxError:
        raise
    except TimeoutError:
        raise SandboxError("plugin exceeded execution deadline reading manifest") from None
    except (OSError, ValueError, RuntimeError):
        raise SandboxError("plugin manifest inspection failed") from None


def _read_inventory(root, *, select="all", value=""):
    from core.plugin import PluginInventoryError
    try:
        if not getattr(_invocations, "inspection_scope", False):
            with _execution_budget():
                return _read_inventory(root, select=select, value=value)
        from core.plugin_inspection import ContentInspector
        inspector = getattr(_invocations, "inspector", None)
        if inspector is None:
            inspector = ContentInspector()
            _invocations.inspector = inspector
        manifests = inspector.inventory(root, select=select, value=value, timeout=_remaining(CONTROL_TIMEOUT))
        _remaining()
        return manifests
    except PluginInventoryError:
        raise
    except TimeoutError:
        raise PluginInventoryError("plugin inventory inspection deadline exceeded") from None
    except (OSError, ValueError, RuntimeError):
        raise PluginInventoryError("plugin inventory inspection failed") from None


def _image(manifest, command) -> str:
    from core.plugin_runtime_inventory import RUNTIME_POLICY
    if not getattr(_invocations, "inspection_scope", False):
        with _execution_budget():
            return _image(manifest, command)
    try:
        record = _read_runtime_catalog(data_dir() / "plugin-runtimes" / (manifest.name + ".json"))
        image = record["image"]
        if record["fingerprint"] != compute_fingerprint(manifest.root):
            raise SandboxError("plugin runtime needs rebuilding for this plugin version")
        labels = ("org.opskit.sandbox", "org.opskit.lock-sha256",
                  "org.opskit.base-digest", "org.opskit.code-sha256")
        # Select only the identity fields: full image metadata may be unbounded
        # or contain sensitive environment values. One query is one snapshot.
        fields = ["{{json .Id}}", *("{{json (index .Config.Labels " + json.dumps(label) + ")}}" for label in labels)]
        response = json.loads(_control(command, "image", "inspect", "--format", "[" + ",".join(fields) + "]", image))
        if not isinstance(response, list) or len(response) != 5:
            raise SandboxError("invalid runtime image identity response")
        if response[0] != image:
            raise SandboxError("runtime image identity mismatch")
        if response[1] != str(RUNTIME_POLICY):
            raise SandboxError("not a platform sandbox runtime")
        expected = [record["requirements_sha256"], record["base_reference"].rsplit("@", 1)[1], record["code_sha256"]]
        if response[2:] != expected:
            raise SandboxError("runtime supply chain identity mismatch")
        return image
    except SandboxError:
        raise
    except TimeoutError:
        raise SandboxError("plugin exceeded execution deadline reading runtime catalog") from None
    except (OSError, KeyError, ValueError, RuntimeError):
        raise SandboxError("Build the plugin runtime with scripts/build_plugin_runtime.py first") from None



def _inspect_content(root, *, verify=False):
    from core.plugin_inspection import ContentIntegrityError, ContentInspector, inspect_content
    timeout = _remaining(300)
    try:
        if not getattr(_invocations, "inspection_scope", False):
            fingerprint = inspect_content(root, verify_checksums=verify, timeout=timeout)
        else:
            inspector = getattr(_invocations, "inspector", None)
            if inspector is None:
                inspector = ContentInspector()
                _invocations.inspector = inspector
            fingerprint = inspector.inspect(root, verify_checksums=verify, timeout=timeout)
    except TimeoutError:
        raise SandboxError("plugin exceeded execution deadline during content inspection") from None
    except ContentIntegrityError:
        raise SandboxError("plugin integrity check failed") from None
    except (OSError, ValueError, RuntimeError):
        raise SandboxError("plugin content inspection worker failed") from None
    _remaining()
    return fingerprint


def compute_fingerprint(root):
    """Sandbox checks always inspect content in a reclaimable host process."""
    return _inspect_content(root)


def check_authorization(manifest) -> str:
    if not getattr(_invocations, "inspection_scope", False):
        with _execution_budget():
            return check_authorization(manifest)
    if not _read_sandbox_setting("enabled", manifest.name):
        raise SandboxError("plugin is disabled")
    fingerprint = _inspect_content(manifest.root, verify=True)
    _check_live_authorizations(extra=((manifest.name, fingerprint),))
    if "root" in manifest.permissions:
        raise SandboxError("third-party plugins cannot request host root access")
    return fingerprint


def _copy_tree(source: Path, dest: Path, limit: int, *, skip_dirs=SKIP_DIRS) -> None:
    size = 0
    count = 0
    if not source.exists():
        dest.mkdir(parents=True, exist_ok=True)
        return
    if source.is_symlink() or source.resolve() != source.absolute():
        raise SandboxError("linked plugin roots are forbidden")
    def paths():
        try:
            yield from bounded_paths(source, checkpoint=_remaining, skip_dirs=skip_dirs)
        except ValueError as error:
            raise SandboxError(str(error)) from error

    for path in paths():
        _remaining()
        rel = path.relative_to(source)
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise SandboxError("plugin links are forbidden")
        if path.is_file():
            count += 1
            if count > MAX_FILES:
                raise SandboxError("plugin snapshot exceeds limits")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            size += _copy_snapshot_file(path, target, limit - size)
    dest.mkdir(parents=True, exist_ok=True)


def _copy_supervised(source, dest, limit, *, skip_dirs=SKIP_DIRS):
    from core.plugin_snapshot import copy_snapshot
    try:
        copy_snapshot(source, dest, limit, timeout=_remaining(3600), skip_dirs=skip_dirs)
    except TimeoutError as error:
        raise SandboxError("plugin exceeded execution deadline during snapshot") from error
    except (OSError, ValueError, RuntimeError) as error:
        raise SandboxError("plugin snapshot worker failed") from error
    _remaining()


def _open_snapshot_source(source: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    if os.name != "posix":
        return os.open(source, flags)
    # Open every ancestor relative to its already-open directory. O_NOFOLLOW
    # on only the final filename would still follow replaced parent links.
    parent = os.open(source.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in source.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        return os.open(source.name, flags, dir_fd=parent)
    finally:
        os.close(parent)


def _copy_snapshot_file(source: Path, target: Path, budget: int) -> int:
    """Copy bounded ordinary files and detect identity/content changes."""
    before = source.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_size > budget or source.resolve() != source.absolute()):
        raise SandboxError("plugin snapshot requires bounded ordinary files")
    descriptor = _open_snapshot_source(source.absolute())
    created = False
    try:
        with os.fdopen(descriptor, "rb") as incoming:
            current = os.fstat(incoming.fileno())
            if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                    or current.st_size > budget
                    or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)):
                raise SandboxError("plugin snapshot source changed")
            copied = 0
            with target.open("xb") as outgoing:
                created = True
                while True:
                    _remaining()
                    chunk = incoming.read(min(65536, budget - copied + 1))
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > budget:
                        raise SandboxError("plugin snapshot exceeds limits")
                    outgoing.write(chunk)
                _remaining()
                after = os.fstat(incoming.fileno())
                published = source.lstat()
                if (copied != current.st_size
                        or (after.st_size, after.st_mtime_ns) != (current.st_size, current.st_mtime_ns)
                        or (published.st_dev, published.st_ino) != (current.st_dev, current.st_ino)
                        or not stat.S_ISREG(published.st_mode) or published.st_nlink != 1
                        or source.resolve() != source.absolute()):
                    raise SandboxError("plugin snapshot source changed")
            target.chmod(0o755 if current.st_mode & 0o111 else 0o644)
            return copied
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise


def _require_resolved_state(root: Path) -> None:
    # A missing root can mean either a first run or an interrupted publication.
    # Never seed an empty state while the previous committed state still exists.
    from core.plugin_metadata import path_present
    if path_present(root.with_name(root.name + ".previous")):
        raise SandboxError("unresolved previous state transaction")
    from core.plugin_state import record_path
    if path_present(record_path(root)):
        raise SandboxError("unresolved state transaction")


def _recover_state(root: Path, *, plugin: str, caller: str, initialize=False) -> None:
    from core.plugin_state import StateCleanupError
    from core.plugin_state_io import finish_supervised
    from core.resource_lock import ResourceBusyError
    try:
        finish_supervised(root, plugin=plugin, caller=caller, timeout=_remaining(CONTROL_TIMEOUT),
                          initialize=initialize)
    except ResourceBusyError:
        raise SandboxError("plugin state transaction is busy; recovery not started") from None
    except StateCleanupError as exc:
        raise SandboxError(f"plugin state {exc.outcome}; recovery cleanup incomplete") from exc
    except TimeoutError:
        raise SandboxError("plugin exceeded execution deadline during state recovery") from None
    except (OSError, ValueError) as exc:
        raise SandboxError("plugin state recovery refused; transaction evidence retained") from exc


@contextmanager
def _state_staging(root: Path, state: str, *, plugin: str, caller: str, budget_base=None):
    """One worker allocates and prepares; cleanup waits for confirmed exit."""
    from core.plugin_state_io import discard_supervised
    from core.plugin_state_prepare import prepare_candidate
    lease = None
    def allocated(value):
        nonlocal lease
        lease = value
    retain = False
    failure = None
    try:
        identity = prepare_candidate(state, root, plugin=plugin, caller=caller,
                                     timeout=_remaining(CONTROL_TIMEOUT), on_allocate=allocated,
                                     budget_base=budget_base)
        lease.candidate_identity = identity
        yield lease
    except BaseException as exc:
        from core.plugin_state_prepare import PreparationCleanupError
        from core.plugin_state_publish import PublicationUncertainError
        retain = isinstance(exc, (PreparationCleanupError, PublicationUncertainError))
        failure = exc
        raise
    finally:
        if lease is not None and not retain and not lease.retired:
            try:
                # As for Docker resource cleanup, expiry has a separate bounded
                # cleanup allowance. Never replace the primary cancellation.
                discard_supervised(lease, plugin=plugin, caller=caller, timeout=CONTROL_TIMEOUT)
            except BaseException as exc:
                if failure is None:
                    if isinstance(exc, Exception):
                        raise SandboxError("plugin staging cleanup incomplete; evidence retained") from exc
                    raise
                from core.cleanup import add_exception_note
                add_exception_note(failure, "plugin staging cleanup incomplete; evidence retained")


def _check_staging_quota(root: Path) -> None:
    from core.plugin_inspection import ContentInspector
    scoped = getattr(_invocations, "deadline", None) is not None
    inspector = getattr(_invocations, "inspector", None) if scoped else None
    if inspector is None:
        inspector = ContentInspector()
        if scoped:
            _invocations.inspector = inspector
    try:
        inspector.state_quota(root, timeout=_remaining(CONTROL_TIMEOUT))
    except (OSError, ValueError, RuntimeError) as exc:
        # Exhausted storage/retained evidence is an operational failure (exit 1),
        # not invalid user arguments (exit 2). Do not expose filesystem paths.
        raise SandboxError("plugin state staging quota check failed; retained evidence preserved") from exc
    finally:
        if not scoped:
            inspector.close()


def _save_state(state: str, root: Path, *, plugin: str, caller: str, budget_base=None) -> None:
    if not isinstance(state, str) or len(state) > WIRE_LIMIT:
        raise SandboxError("invalid state payload")
    with _state_staging(root, state, plugin=plugin, caller=caller, budget_base=budget_base) as lease:
        from core.plugin_state_publish import publish_supervised
        from core.config import get_config_path
        from core.plugin_trust import _trust_file
        requirements = {"operation": "authorize", "config": str(get_config_path()), "trust": str(_trust_file()),
                        "identities": getattr(_invocations, "authorizations", ()),
                        "edges": getattr(_invocations, "service_edges", ()),
                        "networks": getattr(_invocations, "network_authorizations", ())}
        publish_supervised(lease, lease.candidate_identity, plugin=plugin, caller=caller,
                           requirements=requirements, timeout=_remaining(CONTROL_TIMEOUT))
        # Publication has completed under fresh authorization. Cleanup owns its
        # transaction guard, but no longer blocks unrelated config/trust writes.
        try:
            from core.plugin_state_io import finish_supervised
            outcome = finish_supervised(root, plugin=plugin, caller=caller, timeout=_remaining(CONTROL_TIMEOUT))
            lease.retired = outcome == "committed"
        except Exception as exc:
            raise SandboxError("plugin state committed; transaction cleanup incomplete") from exc


def _publish_state_locked(staging: Path, root: Path, *, plugin: str, caller: str) -> None:
    """The invocation, authorization and transaction guards are held."""
    from core.plugin_state import confirm_prepared_record
    plan = confirm_prepared_record(root, staging, plugin=plugin, caller=caller)
    backup = root.with_name(root.name + ".previous")
    _remaining()
    if plan.record.old_identity is not None:
        _rename_state(root, backup)
    _remaining()
    # Failure or cancellation retains both the journal and owned candidate.
    # The next locked invocation reconciles topology instead of guessing rollback.
    _rename_state(staging, root)


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
    if (not isinstance(request, dict) or set(request) - {"rpc_version", "type", "method", "args", "kwargs"}
            or request.get("type", "call") != "call"):
        raise SandboxError("invalid broker request envelope")
    method = request.get("method")
    if not isinstance(method, str) or len(method) > 128 or not method.isascii():
        raise SandboxError("invalid broker method")
    args, kwargs = request.get("args", []), request.get("kwargs", {})
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        raise SandboxError("invalid broker arguments")
    try:
        plugin_json.require_version(request)
    except ValueError:
        raise SandboxError("unsupported broker protocol version") from None
    if len(plugin_json.dumps(request)) > 65536:
        raise SandboxError("broker request too large")
    fingerprint = check_authorization(manifest)
    _remaining()
    _check_live_authorizations(extra=((manifest.name, fingerprint),))
    ui = {"select", "multi_select", "confirm", "text_input", "pause", "clear_screen", "print_header"}
    if method in ui:
        from core import prompt
        next_check = 0

        def checkpoint():
            nonlocal next_check
            _remaining()
            if time.monotonic() >= next_check:
                _check_live_authorizations(extra=((manifest.name, fingerprint),))
                next_check = time.monotonic() + 1

        with prompt.guard_input(checkpoint):
            checkpoint()
            value = getattr(prompt, method)(*args, **kwargs)
            checkpoint()
            return value
    if method == "service.call":
        if (kwargs or len(args) != 4 or not isinstance(args[0], str)
                or not isinstance(args[1], str) or not isinstance(args[2], list)
                or not isinstance(args[3], dict)):
            raise SandboxError("invalid service call arguments")
        from core.plugin_services import call_service
        return call_service(*args, caller=manifest.name)
    if method == "service.describe":
        if kwargs or len(args) != 1 or not isinstance(args[0], str):
            raise SandboxError("invalid service description arguments")
        from core.plugin_services import describe_service
        return describe_service(*args, caller=manifest.name)
    raise SandboxError("broker capability not granted")


_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()
_invocations = threading.local()


def _check_live_authorizations(*, extra=(), extra_edges=()):
    """Check each supervisor-bound identity, including outer RPC callers."""
    if not getattr(_invocations, "inspection_scope", False):
        with _execution_budget():
            return _check_live_authorizations(extra=extra, extra_edges=extra_edges)
    identities = (*getattr(_invocations, "authorizations", ()), *extra)
    edges = (*getattr(_invocations, "service_edges", ()), *extra_edges)
    networks = getattr(_invocations, "network_authorizations", ())
    from core.config import get_config_path
    from core.plugin_trust import _trust_file
    from core.plugin_inspection import ContentInspector
    inspector = getattr(_invocations, "inspector", None)
    if inspector is None:
        inspector = ContentInspector()
        _invocations.inspector = inspector
    try:
        options = {"networks": networks} if networks else {}
        failure = inspector.authorize(get_config_path(), _trust_file(), identities, edges,
                                       timeout=_remaining(300), **options)
    except TimeoutError:
        raise SandboxError("plugin exceeded execution deadline during authorization inspection") from None
    except (OSError, ValueError, RuntimeError):
        raise SandboxError("plugin authorization inspection worker failed") from None
    _remaining()
    _raise_authorization_failure(failure)


def _raise_authorization_failure(failure):
    errors = {"settings": "invalid plugin authorization settings",
              "identity": "plugin authorization revoked during execution",
              "service": "service authorization revoked during execution",
              "network": "plugin private network authorization revoked during execution"}
    if failure is not None:
        raise SandboxError(errors[failure])


def _read_live_authorizations(identities, edges, *, config_path=None, trust_path=None, networks=()):
    """Fresh reads and one set of predicates; no mutation locks in the reader."""
    from core.config import load_config
    if config_path is None:
        config = load_config()
    else:
        from core.config import _load_config_path
        config = _load_config_path(Path(config_path))
    modules = config.get("modules", {})
    if not isinstance(modules, dict):
        return "settings"
    from core.plugin_inspection import _config_setting
    for name, targets in networks:
        # Existing proxies retain their initial grants. Additions cannot expand
        # a live proxy; removing any original grant invalidates the invocation.
        current = _config_setting(config, "private_targets", name)
        if not set(targets).issubset(current):
            return "network"
    from core.plugin_trust import authorization_snapshot, is_service_authorized
    with authorization_snapshot(path=trust_path):
        for name, fingerprint in identities:
            record = modules.get(name, {})
            if (not isinstance(record, dict) or not record.get("enabled", True)
                    or not is_trusted(name, fingerprint)):
                return "identity"
        if any(not is_service_authorized(*edge) or any(
                not isinstance(modules.get(name, {}), dict) or not modules.get(name, {}).get("enabled", True)
                for name in (edge[0], edge[3])) for edge in edges):
            return "service"
    return None


def _remaining(cap=None):
    deadline = getattr(_invocations, "deadline", None)
    if deadline is None:
        return cap
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SandboxError("plugin exceeded execution deadline")
    return min(remaining, cap) if cap is not None else remaining


@contextmanager
def _execution_budget():
    from core.config import get_config_path
    from core.plugin_inspection import ContentInspector
    started = time.monotonic()
    previous = getattr(_invocations, "deadline", None)
    previous_scope = getattr(_invocations, "inspection_scope", False)
    startup_deadline = started + CONFIG_READ_TIMEOUT
    _invocations.deadline = min(previous, startup_deadline) if previous is not None else startup_deadline
    _invocations.inspection_scope = True
    try:
        _remaining()
        inspector = getattr(_invocations, "inspector", None)
        if inspector is None:
            inspector = ContentInspector()
            _invocations.inspector = inspector
        try:
            timeout = inspector.execution_timeout(get_config_path(), timeout=_remaining(CONFIG_READ_TIMEOUT))
        except SandboxError:
            raise
        except TimeoutError:
            raise SandboxError("plugin exceeded execution deadline reading sandbox settings") from None
        except (OSError, ValueError, RuntimeError):
            raise SandboxError("plugin sandbox settings inspection failed") from None
        _remaining()
        _invocations.deadline = min(previous, started + timeout) if previous is not None else started + timeout
        _remaining()
        yield
    finally:
        try:
            if not previous_scope:
                inspector = getattr(_invocations, "inspector", None)
                _invocations.inspector = None
                if inspector is not None:
                    inspector.close()
        finally:
            _invocations.deadline = previous
            _invocations.inspection_scope = previous_scope


def _sandbox_timeout(config):
    """Single policy parser for bounded startup reads and result validation."""
    plugin = config.get("plugin", {})
    if not isinstance(plugin, dict) or not isinstance(plugin.get("sandbox", {}), dict):
        raise SandboxError("invalid sandbox settings")
    raw = plugin.get("sandbox", {}).get("timeout", 300)
    try:
        timeout = float(raw)
        if isinstance(raw, bool) or not math.isfinite(timeout) or not 1 <= timeout <= 3600:
            raise ValueError("invalid budget")
    except (ValueError, TypeError):
        raise SandboxError("invalid sandbox execution timeout") from None
    return timeout


@contextmanager
def _state_lock(name: str):
    """Serialize transactions across CLI processes without deleting lock files."""
    from core.resource_lock import ResourceBusyError
    from core.plugin_invocation_lock import hold
    if not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,127}", name) is None:
        raise SandboxError("invalid plugin state lock identity")
    # Catch acquisition failures only; preserve invocation exceptions.
    owner = hold(plugin=name, timeout=_remaining(CONTROL_TIMEOUT))
    try:
        owner.__enter__()
    except ResourceBusyError as exc:
        raise SandboxError("plugin is busy in another process; retry later") from exc
    except TimeoutError as exc:
        raise SandboxError("plugin exceeded execution deadline acquiring state lock") from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise SandboxError("plugin state lock acquisition failed") from exc
    try:
        yield
    finally:
        owner.__exit__(None, None, None)


def invoke(manifest, action: str, *, args=None, service="", method="", kwargs=None, caller="platform"):
    stack = getattr(_invocations, "stack", [])
    if manifest.name in stack or len(stack) >= 4:
        raise SandboxError("cyclic or excessive nested plugin service calls denied")
    with _locks_guard:
        lock = _locks.setdefault(manifest.name, threading.RLock())
    with _execution_budget():
        if not lock.acquire(timeout=_remaining()):
            raise SandboxError("plugin exceeded execution deadline waiting for state lock")
        try:
            with _state_lock(manifest.name):
                _remaining()
                _invocations.stack = [*stack, manifest.name]
                authorizations = getattr(_invocations, "authorizations", ())
                service_edges = getattr(_invocations, "service_edges", ())
                network_authorizations = getattr(_invocations, "network_authorizations", ())
                try:
                    return _invoke(manifest, action, args=args, service=service, method=method, kwargs=kwargs, caller=caller)
                finally:
                    _invocations.stack = stack
                    _invocations.authorizations = authorizations
                    used_edges = getattr(_invocations, "service_edges", ())
                    # A caller's later state still depends on services it used,
                    # including a revoked edge whose exception it catches.
                    _invocations.service_edges = tuple(dict.fromkeys((*service_edges, *used_edges))) if stack else service_edges
                    used_networks = getattr(_invocations, "network_authorizations", ())
                    _invocations.network_authorizations = (
                        tuple(dict.fromkeys((*network_authorizations, *used_networks))) if stack
                        else network_authorizations)
        finally:
            lock.release()


@contextmanager
def _resource_scope(command, kind, name):
    from core.plugin_resources import ResourceLease
    try:
        lease = ResourceLease(command, kind, name, lambda *args: _control(command, *args))
    except Exception:
        raise SandboxError("cannot record sandbox resource; execution refused") from None
    failed = False
    try:
        yield lease
    except BaseException:
        failed = True
        raise
    finally:
        deadline = getattr(_invocations, "deadline", None)
        _invocations.deadline = time.monotonic() + CONTROL_TIMEOUT
        try:
            lease.close(lambda *args: _control(command, *args))
        except Exception:
            if not failed:
                raise SandboxError("sandbox resource cleanup pending; recovery required") from None
            from core.prompt import console
            console.print("sandbox resource cleanup pending; recovery required", markup=False)
        finally:
            _invocations.deadline = deadline


@contextmanager
def _network(manifest, command):
    if "network" not in manifest.permissions:
        yield "none", []
        return
    name = "opskit-net-" + uuid.uuid4().hex
    proxy = name + "-proxy"
    token = uuid.uuid4().hex + uuid.uuid4().hex
    private = _read_sandbox_setting("private_targets", manifest.name)
    from core.plugin_gateway import image as gateway_image
    image = gateway_image(command)
    with ExitStack() as resources:
        network_labels = resources.enter_context(_resource_scope(command, "network", name)).labels
        _control(command, "network", "create", "--driver=bridge", "--internal", "--ipv6=false",
                 "--opt", "com.docker.network.bridge.inhibit_ipv4=true", *network_labels, name)
        proxy_labels = resources.enter_context(_resource_scope(command, "container", proxy)).labels
        _control(command, "run", "-d", "--rm", "--name", proxy, "--pull=never",
                 *proxy_labels,
                 "--log-driver=none",
                 "--read-only", "--user", "65534:65534", "--cap-drop=ALL",
                 "--security-opt=no-new-privileges", "--pids-limit=32", "--memory=64m", "--cpus=0.5",
                 "--env", "OPSKIT_PROXY_TOKEN=" + token,
                 "--env", "OPSKIT_PRIVATE_TARGETS=" + json.dumps(private),
                 "--entrypoint=python", image, "-I", "-S", "-u", "/runtime/gateway.py")
        _control(command, "network", "connect", name, proxy)
        try:
            network_data = json.loads(_control(command, "inspect", "--format", "{{json .NetworkSettings.Networks}}", proxy))
            raw_address = network_data[name]["IPAddress"]
            if not isinstance(raw_address, str):
                raise ValueError("invalid proxy address type")
            address = ipaddress.ip_address(raw_address)
            if (address.version != 4 or address.is_loopback or address.is_reserved
                    or address.is_link_local or address.is_unspecified or address.is_multicast):
                raise ValueError("invalid internal proxy address")
        except (ValueError, KeyError, TypeError):
            raise SandboxError("invalid sandbox network response") from None
        url = f"http://opskit:{token}@{address}:8080"
        previous = getattr(_invocations, "network_authorizations", ())
        bound = (*previous, (manifest.name, tuple(private))) if private else previous
        _invocations.network_authorizations = bound
        try:
            yield name, ["--env", "HTTP_PROXY=" + url, "--env", "HTTPS_PROXY=" + url,
                         "--env", "http_proxy=" + url, "--env", "https_proxy=" + url]
        finally:
            # Retain used grants through cleanup and state publication, just
            # like service edges. invoke owns restoration of the outer scope.
            if not getattr(_invocations, "stack", ()):
                _invocations.network_authorizations = previous


def _invoke(manifest, action, *, args, service, method, kwargs, caller):
    current = _read_manifest(manifest.root)
    if current is None or current.name != manifest.name:
        raise SandboxError("plugin manifest changed or identity is invalid")
    manifest = current
    fingerprint = check_authorization(manifest)
    if action.startswith("service") and service not in manifest.provides:
        raise SandboxError("service not provided by current authorized manifest")
    service_contract = None
    if action.startswith("service"):
        from core.plugin_services import resolve_service_access
        edges = resolve_service_access(manifest, service, method, caller)
        _invocations.service_edges = (*getattr(_invocations, "service_edges", ()), *edges)
        _invocations.authorizations = (*getattr(_invocations, "authorizations", ()),
                                      *((edge[0], edge[1]) for edge in edges))
        service_contract = manifest.service_contracts[service]
    _invocations.authorizations = (*getattr(_invocations, "authorizations", ()), (manifest.name, fingerprint))
    _check_live_authorizations()
    command = docker_command()
    _require_sandbox_capabilities(command)
    image = _image(manifest, command)
    _remaining()
    _check_live_authorizations()
    owner_root = data_dir() / "plugin-data" / manifest.name
    state_root = owner_root / "self"
    # A provider's persistent state is partitioned by the authenticated caller.
    if action.startswith("service"):
        state_root = owner_root / "callers" / caller
    _recover_state(state_root, plugin=manifest.name,
                   caller=caller if action.startswith("service") else "platform", initialize=True)
    _check_staging_quota(state_root)
    name = "opskit-" + uuid.uuid4().hex
    payload = {"action": action, "name": manifest.name, "entry": manifest.entry,
               "kind": manifest.kind, "args": args or [], "kwargs": kwargs or {},
               "service": service, "method": method, "caller": caller,
               "service_contract": service_contract}
    request_text = plugin_json.dumps_message(_encode(payload, state_root))
    from core.plugin_workspace import owned_workspace
    with _network(manifest, command) as (network, proxy_env), _resource_scope(command, "container", name) as resource, owned_workspace(timeout=_remaining(CONTROL_TIMEOUT), cleanup_timeout=CONTROL_TIMEOUT, request_text=request_text, resource=resource) as workspace:
        base = workspace.root
        resource_labels = resource.labels
        _copy_supervised(manifest.root, base / "plugin", 128 * 1024**2)
        if compute_fingerprint(base / "plugin") != fingerprint:
            raise SandboxError("plugin changed during snapshot creation")
        from dataclasses import replace
        snapshot_manifest = _read_manifest(base / "plugin")
        if snapshot_manifest is None or replace(snapshot_manifest, root=manifest.root) != manifest:
            raise SandboxError("plugin manifest changed during authorization")
        _copy_supervised(state_root, base / "seed", STATE_LIMIT, skip_dirs=frozenset())
        mounts = []
        for source, target in [(base / "plugin", "/plugin"), (base / "seed", "/seed"), (base / "request.json", "/request.json")]:
            mounts += ["--mount", f"type=bind,src={mount_path(source, command)},dst={target},readonly"]
        cmd = command + ["run", "--rm", "--name", name, "--pull=never", "-i",
            *resource_labels,
            "--log-driver=none",
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
        _remaining()
        _check_live_authorizations()
        try:
            process = start(cmd, stdin=subprocess.PIPE, binary=True)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            raise SandboxError("sandbox client launch failed") from None
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
        deadline = _invocations.deadline
        next_authorization_check = time.monotonic()
        result = None
        total_output = 0
        writers = []
        try:
            thread.start()
            while True:
                if time.monotonic() >= deadline:
                    raise SandboxError("plugin exceeded execution deadline")
                if time.monotonic() >= next_authorization_check:
                    _check_live_authorizations()
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
                if line.startswith(PREFIX.encode("ascii")):
                    try:
                        text = line[len(PREFIX):].decode("utf-8", errors="strict")
                        message = plugin_json.loads_message(text)
                        if not isinstance(message, dict):
                            raise ValueError("protocol message must be an object")
                    except (ValueError, TypeError, RecursionError):
                        raise SandboxError("invalid plugin protocol") from None
                    if message.get("type") == "result":
                        if result is not None:
                            raise SandboxError("duplicate plugin result")
                        try:
                            result = plugin_json.require_result(message)
                        except ValueError:
                            raise SandboxError("invalid plugin protocol") from None
                    elif message.get("type") == "call":
                        if result is not None:
                            raise SandboxError("plugin call after terminal result")
                        from core.prompt import UserCancel
                        try:
                            reply = {"value": _broker(manifest, message)}
                        except UserCancel:
                            reply = {"cancel": True}
                        except Exception:
                            reply = {"error": "broker request denied or failed"}
                        if time.monotonic() >= deadline:
                            raise SandboxError("plugin exceeded execution deadline")
                        try:
                            reply_text = plugin_json.dumps_message(reply) + "\n"
                            if len(reply_text.encode("utf-8")) > WIRE_LIMIT:
                                raise ValueError("reply exceeds limit")
                        except (ValueError, TypeError, RecursionError):
                            raise SandboxError("invalid broker reply") from None
                        if time.monotonic() >= deadline:
                            raise SandboxError("plugin exceeded execution deadline")
                        done = threading.Event()
                        errors = []
                        def write_reply():
                            try:
                                process.stdin.write(reply_text.encode('utf-8'))
                                process.stdin.flush()
                            except (OSError, ValueError):
                                errors.append(True)
                            finally:
                                done.set()
                        writer = threading.Thread(target=write_reply, daemon=True)
                        writers.append(writer)
                        writer.start()
                        while not done.wait(min(0.1, max(0, deadline - time.monotonic()))):
                            if time.monotonic() >= deadline:
                                raise SandboxError("plugin exceeded execution deadline")
                        writer.join()
                        writers.clear()
                        if time.monotonic() >= deadline:
                            raise SandboxError("plugin exceeded execution deadline")
                        if errors:
                            raise SandboxError("broker reply delivery failed")
                    else:
                        raise SandboxError("invalid plugin protocol")
                else:
                    from core.prompt import console
                    console.print(line.decode("utf-8", errors="replace").rstrip(), markup=False, highlight=False)
            try:
                code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise SandboxError("plugin exceeded execution deadline") from None
            if time.monotonic() >= deadline:
                raise SandboxError("plugin exceeded execution deadline")
            if code or result is None or result.get("error"):
                raise SandboxError("plugin execution failed")
            if action == "service.describe":
                value = result.get("value")
                expected = {"service_api_version": service_contract["api_version"], "methods": service_contract["methods"]}
                if (not isinstance(value, dict) or type(value.get("service_api_version")) is not int or value != expected):
                    raise SandboxError("service description does not match authorized contract")
            check_authorization(manifest)
            if compute_fingerprint(manifest.root) != fingerprint:
                raise SandboxError("plugin changed during execution")
            _remaining()
            _check_live_authorizations()
        finally:
            stopped.set()
            # Release the attached client before Docker removal. A full stdout
            # pipe otherwise makes daemon removal wait for its blocked attach.
            # The resource scope removes the owned Docker ID after pipe cleanup.
            try:
                terminate_tree(process)
            finally:
                for writer in writers:
                    if writer.ident is not None:
                        writer.join(timeout=5)
                if thread.ident is not None:
                    thread.join(timeout=5)
                if not any(writer.is_alive() for writer in writers):
                    process.stdin.close()
                if not thread.is_alive():
                    process.stdout.close()
    # Publish only after all owned resources have been confirmed removed.
    # Cleanup may consume time or race with a trust/edge revocation.
    _remaining()
    _check_live_authorizations()
    if compute_fingerprint(manifest.root) != fingerprint:
        raise SandboxError("plugin changed during cleanup")
    _save_state(result["state"], state_root, plugin=manifest.name,
                caller=caller if action.startswith("service") else "platform",
                budget_base=data_dir().absolute() / "plugin-data")
    return result.get("value")
