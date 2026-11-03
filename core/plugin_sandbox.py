"""Native plugin supervisor. No third-party code runs in the platform process.

Only immutable plugin snapshots and that plugin's state are visible. Persistent
state is copied into private staging and returned over a bounded JSON protocol.
"""
from __future__ import annotations

import math
import os
import re
import stat
import threading
import time
from contextlib import contextmanager, ExitStack
from pathlib import Path

from core.paths import data_dir
from core.plugin_trust import is_trusted
from core import plugin_json
from core.plugin_tree import bounded_paths, SKIP_DIRS

STATE_LIMIT = 16 * 1024**2
WIRE_LIMIT = 32 * 1024**2
MAX_FILES = 4096
CONTROL_TIMEOUT = 30
CONFIG_READ_TIMEOUT = 30


class SandboxError(RuntimeError):
    pass


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
                # Expiry has a separate bounded cleanup allowance. Never
                # replace the primary cancellation.
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
    if method == "host.upload_file":
        if "network" not in manifest.permissions or kwargs or len(args) != 5:
            raise SandboxError("network capability not granted")
        authority = getattr(_invocations, "capability_authority", None)
        if authority is None:
            raise SandboxError("host capability scope unavailable")
        private = _read_sandbox_setting("private_targets", manifest.name)
        previous = getattr(_invocations, "network_authorizations", ())
        if private:
            _invocations.network_authorizations = (*previous, (manifest.name, tuple(private)))
        def identity(name, expected):
            return name == manifest.name and check_authorization(manifest) == expected
        def checkpoint():
            _remaining()
            _check_live_authorizations()
        from core.plugin_upload import upload_file
        return upload_file(manifest.name, fingerprint, *args, authority=authority,
                           check_identity=identity, private_targets=private,
                           deadline=_invocations.deadline, checkpoint=checkpoint)
    if method == "host.https":
        if "network" not in manifest.permissions or kwargs or len(args) != 4:
            raise SandboxError("network capability not granted")
        import base64
        if not isinstance(args[3], str) or len(args[3]) > 44000:
            raise SandboxError("invalid HTTP request body")
        body = base64.b64decode(args[3], validate=True)
        private = _read_sandbox_setting("private_targets", manifest.name)
        previous = getattr(_invocations, "network_authorizations", ())
        if private:
            _invocations.network_authorizations = (*previous, (manifest.name, tuple(private)))
        def checkpoint():
            _remaining()
            _check_live_authorizations()
        from core.plugin_http import request as https_request
        return https_request(*args[:3], body, private_targets=private,
                             deadline=_invocations.deadline, checkpoint=checkpoint)
    if method == "host.creator_build":
        if kwargs or len(args) != 3:
            raise SandboxError("invalid Creator arguments")
        from core.plugin_host_tools import creator_build
        authority = getattr(_invocations, "capability_authority", None)
        if authority is None:
            raise SandboxError("host capability scope unavailable")
        def identity(name, expected):
            return name == manifest.name and check_authorization(manifest) == expected
        def checkpoint():
            _remaining()
            _check_live_authorizations()
        from core.prompt import console
        return creator_build(manifest.name, fingerprint, *args, authority=authority,
                             check_identity=identity, deadline=_invocations.deadline,
                             checkpoint=checkpoint,
                             output=lambda line: console.print(line, markup=False, highlight=False))
    if method == "host.artifact_info":
        if kwargs or len(args) != 2:
            raise SandboxError("invalid artifact arguments")
        from core.plugin_artifact import artifact_info
        authority = getattr(_invocations, "capability_authority", None)
        if authority is None:
            raise SandboxError("host capability scope unavailable")
        def identity(name, expected):
            return name == manifest.name and check_authorization(manifest) == expected
        def checkpoint():
            _remaining()
            _check_live_authorizations()
        return artifact_info(manifest.name, fingerprint, *args, authority=authority,
                             check_identity=identity, deadline=_invocations.deadline,
                             checkpoint=checkpoint)
    if method == "host.read_file":
        if kwargs or len(args) != 2:
            raise SandboxError("invalid host file arguments")
        from core.plugin_host_files import read_file
        authority = getattr(_invocations, "capability_authority", None)
        if authority is None:
            raise SandboxError("host capability scope unavailable")
        def identity(name, expected):
            return name == manifest.name and check_authorization(manifest) == expected
        from core.prompt import guard_input
        with guard_input(lambda: (_remaining(), _check_live_authorizations())):
            return read_file(manifest.name, fingerprint, *args,
                             authority=authority, check_identity=identity)
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
                previous_authority = getattr(_invocations, "capability_authority", None)
                from core.plugin_capabilities import CapabilityAuthority
                authority = CapabilityAuthority()
                _invocations.capability_authority = authority
                try:
                    return _invoke(manifest, action, args=args, service=service, method=method, kwargs=kwargs, caller=caller)
                finally:
                    authority.close()
                    _invocations.capability_authority = previous_authority
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
    from core.native_runtime_catalog import RuntimeCatalog
    from core.plugin_runtime_inventory import code_digest
    from core.native_sandbox import NativeSandboxError, get_backend
    get_backend()
    with ExitStack() as lease:
        try:
            runtime, runtime_record = lease.enter_context(
                RuntimeCatalog(data_dir() / "native-plugin-runtimes").acquire(
                    manifest.name, fingerprint=fingerprint, sdk_sha256=code_digest()))
        except FileNotFoundError:
            raise NativeSandboxError("runtime_missing") from None
        except ValueError:
            raise NativeSandboxError("runtime_invalid") from None
        return _invoke_prepared(manifest, action, runtime=runtime, runtime_record=runtime_record,
                                fingerprint=fingerprint, service_contract=service_contract,
                                args=args, service=service, method=method, kwargs=kwargs, caller=caller)


def _invoke_prepared(manifest, action, *, runtime, runtime_record, fingerprint, service_contract,
                     args, service, method, kwargs, caller):
    from core.native_invocation import invoke as invoke_native
    from core.native_runtime import inventory
    from core.plugin_runtime_inventory import code_digest
    _remaining()
    _check_live_authorizations()
    owner_root = data_dir() / "plugin-data" / manifest.name
    state_root = owner_root / "self"
    if action.startswith("service"):
        state_root = owner_root / "callers" / caller
    state_caller = caller if action.startswith("service") else "platform"
    _recover_state(state_root, plugin=manifest.name, caller=state_caller, initialize=True)
    _check_staging_quota(state_root)
    payload = _encode({"action": action, "name": manifest.name, "entry": manifest.entry,
                       "kind": manifest.kind, "args": args or [], "kwargs": kwargs or {},
                       "service": service, "method": method, "caller": caller,
                       "service_contract": service_contract}, state_root)
    from core.plugin_workspace import owned_workspace
    with owned_workspace(timeout=_remaining(CONTROL_TIMEOUT), cleanup_timeout=CONTROL_TIMEOUT) as workspace:
        base = workspace.root
        _copy_supervised(manifest.root, base / "plugin", 128 * 1024**2)
        if compute_fingerprint(base / "plugin") != fingerprint:
            raise SandboxError("plugin changed during snapshot creation")
        from dataclasses import replace
        snapshot_manifest = _read_manifest(base / "plugin")
        if snapshot_manifest is None or replace(snapshot_manifest, root=manifest.root) != manifest:
            raise SandboxError("plugin manifest changed during authorization")
        _copy_supervised(state_root, base / "seed", STATE_LIMIT, skip_dirs=frozenset())
        seed = base / "seed"
        seed_files = inventory(seed, allow_empty=True)
        def checkpoint():
            _remaining()
            _check_live_authorizations()
        def dispatch(message):
            from core.prompt import UserCancel
            from core.i18n import t
            try:
                return {"value": _broker(manifest, message)}
            except UserCancel:
                return {"cancel": True}
            except Exception:
                return {"error": t("plugin_native.errors.broker_denied")}
        from core.prompt import console
        result = invoke_native(runtime, runtime_record, base / "plugin", inventory(base / "plugin"),
                               seed, seed_files, payload, fingerprint=fingerprint,
                               sdk_sha256=code_digest(), deadline=_invocations.deadline,
                               checkpoint=checkpoint, dispatch=dispatch,
                               output=lambda line: console.print(line, markup=False, highlight=False),
                               staging_parent=base)
        if action == "service.describe":
            value = result.get("value")
            expected = {"service_api_version": service_contract["api_version"],
                        "methods": service_contract["methods"]}
            if not isinstance(value, dict) or type(value.get("service_api_version")) is not int or value != expected:
                raise SandboxError("service description does not match authorized contract")
    _remaining()
    _check_live_authorizations()
    if compute_fingerprint(manifest.root) != fingerprint:
        raise SandboxError("plugin changed during execution")
    check_authorization(manifest)
    _save_state(result["state"], state_root, plugin=manifest.name, caller=state_caller,
                budget_base=data_dir().absolute() / "plugin-data")
    return result.get("value")
