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


class WorkspaceError(SandboxError):
    """The host-owned isolated workspace could not be prepared or cleaned."""

    def __init__(self, message, *, reason=None):
        super().__init__(message)
        self.reason = reason


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
    from core.constants import PLUGIN_API_VERSION
    if manifest.api_version != PLUGIN_API_VERSION:
        from core.i18n import t
        raise SandboxError(t("plugin_native.errors.sdk_required"))
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
    if method in {"ui.wait_start", "ui.wait_stop"}:
        display = getattr(_invocations, "wait_display", None)
        if display is None or kwargs or len(args) != (1 if method == "ui.wait_start" else 0):
            raise SandboxError("invalid waiting scope")
        if method == "ui.wait_start":
            display.start(args[0])
        else:
            display.stop()
        return None
    # check_authorization already checks this identity together with every
    # active caller, service edge and network grant in the invocation.
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
    if method in {"host.execute", "host.artifacts", "host.path_info", "host.find_executable", "host.file_version", "host.list_files"}:
        expected = 4 if method == "host.execute" else (1 if method in {"host.file_version", "host.list_files"} else 2)
        allowed = {"missing_ok"} if method == "host.list_files" else set()
        if set(kwargs) - allowed or (len(args) != expected and not (method == "host.path_info" and len(args) in (3, 4))):
            raise SandboxError("invalid host capability arguments")
        authority = getattr(_invocations, "capability_authority", None)
        if authority is None:
            raise SandboxError("host capability scope unavailable")
        def identity(name, expected):
            return name == manifest.name and check_authorization(manifest) == expected
        def checkpoint():
            _remaining()
            _check_live_authorizations()
        from core.prompt import console
        context = dict(authority=authority, check_identity=identity, deadline=lambda: _invocations.deadline,
                       checkpoint=checkpoint,
                       output=lambda line: console.print(line, markup=False, highlight=False))
        if method == "host.execute":
            from core.plugin_host_tools import execute
            def output_start(header):
                from core.cli import non_interactive
                from core.prompt import clear_screen, print_header
                from contextlib import nullcontext
                if non_interactive.get() or not console.is_terminal:
                    return
                display = getattr(_invocations, "wait_display", None)
                with display.suspend() if display is not None else nullcontext():
                    clear_screen()
                    print_header(header)
            return execute(manifest.name, fingerprint, *args, host_work_scope=_input_wait_budget,
                           output_start=output_start, **context)
        if method == "host.path_info":
            from core.plugin_host_tools import path_info
            return path_info(manifest.name, fingerprint, *args, **context)
        if method == "host.find_executable":
            from core.plugin_host_tools import find_executable
            return find_executable(manifest.name, fingerprint, *args, **context)
        if method == "host.file_version":
            from core.plugin_host_tools import file_version
            return file_version(manifest.name, fingerprint, *args, **context)
        if method == "host.list_files":
            from core.plugin_host_files import list_files
            return list_files(manifest.name, fingerprint, *args, authority=authority,
                              check_identity=identity, checkpoint=checkpoint, **kwargs)
        from core.plugin_artifacts import manage
        return manage(manifest.name, fingerprint, *args, **context)
    if method == "host.asar_directories":
        if kwargs or len(args) != 3:
            raise SandboxError("invalid host archive arguments")
        authority = getattr(_invocations, "capability_authority", None)
        if authority is None:
            raise SandboxError("host capability scope unavailable")
        def identity(name, expected):
            return name == manifest.name and check_authorization(manifest) == expected
        def checkpoint():
            _remaining()
            _check_live_authorizations()
        from core.plugin_host_archive import directories
        return directories(manifest.name, fingerprint, *args, authority=authority,
                           check_identity=identity, checkpoint=checkpoint)
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
                             check_identity=identity, deadline=lambda: _invocations.deadline,
                             checkpoint=checkpoint)
    if method in {"host.compare_write", "host.create_file"}:
        if kwargs or len(args) != (4 if method == "host.compare_write" else 3):
            raise SandboxError("invalid host file arguments")
        authority = getattr(_invocations, "capability_authority", None)
        if authority is None:
            raise SandboxError("host capability scope unavailable")
        def identity(name, expected):
            return name == manifest.name and check_authorization(manifest) == expected
        def checkpoint():
            _remaining()
            _check_live_authorizations()
        from core.plugin_host_write import compare_write, create_file
        from core.prompt import guard_input
        with guard_input(checkpoint):
            operation = compare_write if method == "host.compare_write" else create_file
            return operation(manifest.name, fingerprint, *args, authority=authority,
                             check_identity=identity, checkpoint=checkpoint)
    if method in {"host.read_file", "host.file_status"}:
        if kwargs or len(args) != 2:
            raise SandboxError("invalid host file arguments")
        from core.plugin_host_files import read_file, file_status
        authority = getattr(_invocations, "capability_authority", None)
        if authority is None:
            raise SandboxError("host capability scope unavailable")
        def identity(name, expected):
            return name == manifest.name and check_authorization(manifest) == expected
        from core.prompt import guard_input
        with guard_input(lambda: (_remaining(), _check_live_authorizations())):
            operation = read_file if method == "host.read_file" else file_status
            return operation(manifest.name, fingerprint, *args,
                             authority=authority, check_identity=identity)
    ui = {"select", "select_input", "multi_select", "confirm", "text_input", "pause", "clear_screen", "print_header"}
    if method in ui:
        from core import prompt
        display = getattr(_invocations, "wait_display", None)
        next_check = 0

        def checkpoint():
            nonlocal next_check
            _remaining()
            if time.monotonic() >= next_check:
                _check_live_authorizations(extra=((manifest.name, fingerprint),))
                next_check = time.monotonic() + 1

        from contextlib import nullcontext
        with (display.suspend() if display is not None else nullcontext()), prompt.guard_input(checkpoint):
            checkpoint()
            value = getattr(prompt, method)(*args, **kwargs)
            checkpoint()
            return value
    if method == "service.install_provider":
        if kwargs or len(args) != 1 or not isinstance(args[0], str):
            raise SandboxError("invalid plugin installation arguments")
        from core.plugin_provider_install import request_install
        from core.prompt import guard_input

        def checkpoint():
            _remaining()
            _check_live_authorizations(extra=((manifest.name, fingerprint),))

        with guard_input(checkpoint):
            return request_install(manifest, fingerprint, args[0], checkpoint=checkpoint)
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
    preload = getattr(_invocations, "preload", None)
    if preload is not None:
        preload.check_cancelled()
    _credit_input_wait()
    deadline = getattr(_invocations, "deadline", None)
    if deadline is None:
        return cap
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SandboxError("plugin exceeded execution deadline")
    return min(remaining, cap) if cap is not None else remaining


def _credit_input_wait():
    started = getattr(_invocations, "input_wait_tick", None)
    if started is not None:
        now = time.monotonic()
        elapsed = max(0.0, now - started)
        if getattr(_invocations, "deadline", None) is not None:
            _invocations.deadline += elapsed
        _invocations.input_wait_total = getattr(_invocations, "input_wait_total", 0.0) + elapsed
        _invocations.input_wait_tick = now


@contextmanager
def _input_wait_budget():
    """Do not charge genuine terminal wait; retain continuous revocation checks."""
    _remaining()
    if getattr(_invocations, "input_wait_tick", None) is not None:
        yield
        return
    _invocations.input_wait_tick = time.monotonic()
    try:
        yield
    finally:
        _credit_input_wait()
        _invocations.input_wait_tick = None


@contextmanager
def _execution_budget():
    from core.config import get_config_path
    from core.plugin_inspection import ContentInspector
    started = time.monotonic()
    previous = getattr(_invocations, "deadline", None)
    wait_total = getattr(_invocations, "input_wait_total", 0.0)
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
            elapsed_wait = getattr(_invocations, "input_wait_total", 0.0) - wait_total
            _invocations.deadline = previous + elapsed_wait if previous is not None else None
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


@contextmanager
def _invocation_locks(name, lock):
    """Keep lock ownership on this thread, allowing speculative suspension."""
    owner = None
    locked = False

    def acquire():
        nonlocal owner, locked
        if locked:
            raise RuntimeError("plugin invocation lock already held")
        if not lock.acquire(timeout=_remaining()):
            raise SandboxError("plugin exceeded execution deadline waiting for state lock")
        locked = True
        try:
            candidate = _state_lock(name)
            candidate.__enter__()
            owner = candidate
        except BaseException:
            locked = False
            lock.release()
            raise

    def release():
        nonlocal owner, locked
        current, owner = owner, None
        try:
            if current is not None:
                current.__exit__(None, None, None)
        finally:
            if locked:
                locked = False
                lock.release()

    acquire()
    try:
        yield release, acquire
    finally:
        release()


def invoke(manifest, action: str, *, args=None, service="", method="", kwargs=None, caller="platform", _preload=None):
    if _preload is not None and (action != "entry" or caller != "platform"):
        raise ValueError("preload is only available for a platform menu entry")
    stack = getattr(_invocations, "stack", [])
    if manifest.name in stack or len(stack) >= 4:
        raise SandboxError("cyclic or excessive nested plugin service calls denied")
    with _locks_guard:
        lock = _locks.setdefault(manifest.name, threading.RLock())
    with _execution_budget():
        if _preload is not None:
            _preload.check_cancelled()
        with _invocation_locks(manifest.name, lock) as (suspend, resume):
            if _preload is not None:
                _preload.check_cancelled()
                _preload.set_locks(suspend, resume)
            _remaining()
            _invocations.stack = [*stack, manifest.name]
            authorizations = getattr(_invocations, "authorizations", ())
            service_edges = getattr(_invocations, "service_edges", ())
            network_authorizations = getattr(_invocations, "network_authorizations", ())
            previous_authority = getattr(_invocations, "capability_authority", None)
            previous_preload = getattr(_invocations, "preload", None)
            previous_wait = getattr(_invocations, "wait_display", None)
            _invocations.preload = _preload
            from core.plugin_capabilities import CapabilityAuthority
            from core.installation_progress import WaitingDisplay
            from core.prompt import console
            authority = CapabilityAuthority()
            display = WaitingDisplay(output_console=console)
            authority.suspend_wait = display.suspend
            _invocations.capability_authority = authority
            _invocations.wait_display = display
            try:
                return _invoke(manifest, action, args=args, service=service, method=method, kwargs=kwargs, caller=caller)
            finally:
                try:
                    display.close()
                finally:
                    authority.close()
                    _invocations.capability_authority = previous_authority
                    _invocations.wait_display = previous_wait
                    _invocations.preload = previous_preload
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
    from core.plugin_runtime_manager import acquire
    from core.native_sandbox import get_backend
    get_backend()
    def authorize():
        _check_live_authorizations()
        current = _read_manifest(manifest.root)
        return current == manifest and check_authorization(current) == fingerprint
    with acquire(manifest, fingerprint, checkpoint=_remaining, authorize=authorize,
                 speculative=getattr(_invocations, "preload", None) is not None) as (runtime, runtime_record):
        return _invoke_prepared(manifest, action, runtime=runtime, runtime_record=runtime_record,
                                fingerprint=fingerprint, service_contract=service_contract,
                                args=args, service=service, method=method, kwargs=kwargs, caller=caller)


def _invoke_prepared(manifest, action, *, runtime, runtime_record, fingerprint, service_contract,
                     args, service, method, kwargs, caller):
    from core.native_invocation import invoke as invoke_native
    from core.native_invocation import prepare_runtime
    from core.native_runtime import inventory
    from core.plugin_runtime_inventory import code_digest
    _remaining()
    _check_live_authorizations()
    owner_root = data_dir() / "plugin-data" / manifest.name
    state_root = owner_root / "self"
    if action.startswith("service"):
        state_root = owner_root / "callers" / caller
    state_caller = caller if action.startswith("service") else "platform"
    preload = getattr(_invocations, "preload", None)
    from core.plugin_workspace import owned_workspace
    with owned_workspace(timeout=_remaining(CONTROL_TIMEOUT), cleanup_timeout=CONTROL_TIMEOUT) as workspace, ExitStack() as tasks:
        base = workspace.root
        if preload is not None:
            preload.check_cancelled()
        sdk_sha256 = code_digest()
        wait_runtime = tasks.enter_context(prepare_runtime(
            runtime, runtime_record, fingerprint=fingerprint, sdk_sha256=sdk_sha256,
            deadline=_invocations.deadline, staging_parent=base,
            cancel_event=preload.cancelled if preload is not None else None))
        if preload is None:
            _recover_state(state_root, plugin=manifest.name, caller=state_caller, initialize=True)
        else:
            # Reuse this invocation's supervised reader; the same guarded
            # topology check is repeated after the user selects the plugin.
            _invocations.inspector.preload_state(state_root, manifest.name, state_caller,
                                                 timeout=_remaining(CONTROL_TIMEOUT))
        _check_staging_quota(state_root)
        if preload is not None:
            preload.check_cancelled()
        payload = _encode({"action": action, "name": manifest.name, "entry": manifest.entry,
                           "kind": manifest.kind, "args": args or [], "kwargs": kwargs or {},
                           "service": service, "method": method, "caller": caller,
                           "service_contract": service_contract}, state_root)
        from core.plugin_snapshot import copy_snapshot_pair
        try:
            copy_snapshot_pair(((manifest.root, base / "plugin", 128 * 1024**2, SKIP_DIRS),
                                (state_root, base / "seed", STATE_LIMIT, frozenset())),
                               timeout=_remaining(3600),
                               cancel_event=preload.cancelled if preload is not None else None)
        except InterruptedError:
            raise
        except TimeoutError as error:
            raise SandboxError("plugin exceeded execution deadline during snapshot") from error
        except (OSError, ValueError, RuntimeError) as error:
            raise SandboxError("plugin snapshot worker failed") from error
        _remaining()
        if preload is not None:
            preload.check_cancelled()
        if compute_fingerprint(base / "plugin") != fingerprint:
            raise SandboxError("plugin changed during snapshot creation")
        from dataclasses import replace
        snapshot_manifest = _read_manifest(base / "plugin")
        if snapshot_manifest is None or replace(snapshot_manifest, root=manifest.root) != manifest:
            raise SandboxError("plugin manifest changed during authorization")
        seed = base / "seed"
        seed_files = inventory(seed, allow_empty=True)
        if preload is not None:
            preload.suspend_locks()
        def checkpoint():
            _remaining()
            _check_live_authorizations()
        def dispatch(message):
            from core.prompt import UserCancel
            from core.prompt import input_wait_scope
            from core.plugin_capabilities import CapabilityDenied
            from core.i18n import t
            try:
                from contextlib import nullcontext
                display = getattr(_invocations, "wait_display", None)
                suspend = display.suspend() if display is not None and message.get("method") in {"service.call", "service.describe", "service.install_provider"} else nullcontext()
                with suspend, input_wait_scope(_input_wait_budget):
                    return {"value": _broker(manifest, message)}
            except UserCancel:
                return {"cancel": True}
            except CapabilityDenied as error:
                # CapabilityDenied contains only host-owned localized messages.
                return {"error": str(error)}
            except Exception:
                return {"error": t("plugin_native.errors.broker_denied")}
        from core.prompt import console
        def validate_preload():
            from core.plugin_entry_preload import PreloadStale
            from core.plugin_inspection import ContentInspector
            import os
            try:
                inspector = getattr(_invocations, "inspector", None)
                if inspector is None:
                    inspector = ContentInspector()
                    _invocations.inspector = inspector
                inspector.preload_state(state_root, manifest.name, state_caller, timeout=_remaining(CONTROL_TIMEOUT))
                current_state = inventory(state_root, allow_empty=True) if os.path.lexists(state_root) else {}
                if current_state != seed_files:
                    raise ValueError("plugin state changed")
                from core.native_runtime_catalog import RuntimeCatalog
                catalog = RuntimeCatalog(data_dir() / "native-plugin-runtimes")
                if not catalog.is_current(manifest.name, runtime, runtime_record):
                    raise ValueError("plugin runtime changed")
            except (OSError, ValueError, RuntimeError) as error:
                raise PreloadStale() from error
            _check_staging_quota(state_root)
            current = _read_manifest(manifest.root)
            if current != manifest or check_authorization(current) != fingerprint:
                raise SandboxError("plugin changed during preparation")
            if code_digest() != sdk_sha256:
                raise SandboxError("plugin SDK changed during preparation")
        try:
            prepared_runtime = wait_runtime()
        except TimeoutError as error:
            raise SandboxError("plugin exceeded execution deadline during runtime snapshot") from error
        preload_options = {"preload": preload, "validate_preload": validate_preload,
                           "preload_state": dict(_invocations.__dict__)} if preload is not None else {}
        result = invoke_native(runtime, runtime_record, base / "plugin", inventory(base / "plugin"),
                               seed, seed_files, payload, fingerprint=fingerprint,
                               sdk_sha256=code_digest(), deadline=lambda: _invocations.deadline,
                               checkpoint=checkpoint, dispatch=dispatch,
                               output=lambda line: console.print(line, markup=False, highlight=False),
                               staging_parent=base, prepared_runtime=prepared_runtime, **preload_options)
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
