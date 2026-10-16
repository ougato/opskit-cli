"""Owned host worker for candidate extraction; never publishes committed state."""
import base64
import io
import math
import multiprocessing
from pathlib import Path
import re
import tarfile
import time
import uuid


class PreparationCleanupError(RuntimeError):
    """Ownership or worker exit is unconfirmed; retain possible staging."""


def _binding(staging, parent_identity, wrapper_identity):
    from core.plugin_state import _directory, _parent
    staging = _parent(staging)
    if (staging.name != "new" or re.fullmatch(r"\.state-[a-zA-Z0-9_-]{1,120}", staging.parent.name) is None
            or _directory(staging.parent.parent) != parent_identity
            or _directory(staging.parent) != wrapper_identity):
        raise ValueError("state preparation ownership changed")
    return staging


def _prepare_worker(connection, buffer, length, root, wrapper, allocated, request_id, plugin, caller, budget_base=None):
    from core.archive import extract_tar
    from core.archive_reader import open_tar
    from core.plugin_sandbox import WIRE_LIMIT, STATE_LIMIT, MAX_FILES, _require_resolved_state, SandboxError
    from core.plugin_state import _binding as owner_binding, _directory, _parent, prepare_record, reserve_staging, staging_guard, transaction_guard
    from core.resource_lock import ResourceBusyError
    from core.plugin_tree import bounded_paths
    from core.secrets import restrict_permissions
    from network.worker import send
    from core.plugin_state_budget import platform_state_guard, reserve_platform_state
    try:
        owner_binding(plugin, caller)
        if type(length) is not int or not 0 <= length <= WIRE_LIMIT or length > len(buffer):
            raise ValueError("invalid state payload")
        target = _parent(root)
        budget_base = Path(budget_base) if budget_base is not None else target.parent
        if not budget_base.is_absolute() or not target.is_relative_to(budget_base):
            raise ValueError("invalid state budget scope")
        wrapper = Path(wrapper)
        if (wrapper.parent != target.parent
                or re.fullmatch(r"\.state-[a-f0-9]{32}", wrapper.name) is None):
            raise ValueError("invalid state staging target")
        with platform_state_guard(), staging_guard(target), transaction_guard(target):
            _require_resolved_state(target)
            try:
                reserve_platform_state(budget_base)
                reserve_staging(target)
            except (OSError, ValueError):
                try:
                    send(connection, {"ok": False, "reason": "quota"})
                finally:
                    connection.close()
                return
            parent_identity = _directory(target.parent)
            wrapper.mkdir(mode=0o700)
            wrapper_identity = _directory(wrapper)
            staging = _binding(wrapper / "new", parent_identity, wrapper_identity)
            staging.mkdir(mode=0o700)
            ownership = prepare_record(target, staging, plugin=plugin, caller=caller)
            # Acknowledge identity before allowing any candidate writes. The
            # supervisor can reclaim only after this worker is confirmed gone.
            send(connection, {"ok": True, "request": request_id, "stage": "allocated",
                              "root": root, "path": str(wrapper), "parent": parent_identity,
                              "wrapper": wrapper_identity})
            allocated.wait()
            staging = _binding(wrapper / "new", parent_identity, wrapper_identity)
            if tuple(_directory(staging)) != ownership.new_identity:
                raise ValueError("state candidate identity changed")
            # The journal already binds this empty directory's identity.
            # Extraction never writes before durable ownership confirmation.
            view = memoryview(buffer).cast("B")
            try:
                raw = base64.b64decode(bytes(view[:length]), validate=True)
            finally:
                view.release()
            if len(raw) > STATE_LIMIT + MAX_FILES * 2048 + 10240:
                raise ValueError("state payload too large")
            with open_tar(fileobj=io.BytesIO(raw), mode="r:", max_files=MAX_FILES,
                          max_payload_bytes=STATE_LIMIT, max_metadata_bytes=STATE_LIMIT) as archive:
                members = archive.getmembers()
                if len(members) > MAX_FILES or sum(member.size for member in members) > STATE_LIMIT:
                    raise ValueError("persistent state exceeds quota")
                extract_tar(archive, staging)
            _binding(staging, parent_identity, wrapper_identity)
            if tuple(_directory(staging)) != ownership.new_identity:
                raise ValueError("state candidate identity changed")
            restrict_permissions(staging)
            for path in bounded_paths(staging, skip_dirs=frozenset()):
                restrict_permissions(path)
            _binding(staging, parent_identity, wrapper_identity)
        result = {"ok": True, "request": request_id, "wrapper": wrapper_identity,
                  "candidate": _directory(staging)}
    except ResourceBusyError:
        result = {"ok": False, "reason": "busy"}
    except SandboxError as exc:
        reason = "previous" if str(exc) == "unresolved previous state transaction" else "unresolved"
        result = {"ok": False, "reason": reason}
    except (ValueError, tarfile.TarError, EOFError) as exc:
        # Only fixed categories cross the pipe; never paths or archive text.
        reason = "layout" if "layout" in str(exc) else "inventory" if "inventory" in str(exc) else "invalid"
        result = {"ok": False, "reason": reason}
    except OSError:
        result = {"ok": False, "reason": "filesystem"}
    try:
        send(connection, result)
    finally:
        connection.close()


def prepare_candidate(state, root, *, plugin, caller, timeout, on_allocate, budget_base=None):
    """Allocate and prepare in one owned worker; report ownership before writes."""
    from core.plugin_sandbox import WIRE_LIMIT
    from core.plugin_state import _binding as owner_binding, _valid_identity
    from core.plugin_state_io import OwnedStaging
    from network import worker
    owner_binding(plugin, caller)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("invalid state preparation allowance")
    deadline = time.monotonic() + timeout
    if not isinstance(state, str) or len(state) > WIRE_LIMIT:
        raise ValueError("invalid state payload")
    encoded = state.encode("ascii")
    root = Path(root)
    if not root.is_absolute() or not 0 < len(str(root)) <= 65536:
        raise ValueError("invalid state preparation target")
    if budget_base is not None:
        budget_base = Path(budget_base)
        if (not budget_base.is_absolute() or not root.is_relative_to(budget_base)
                or not 0 < len(str(budget_base)) <= 65536):
            raise ValueError("invalid state budget scope")
    wrapper = root.parent / (".state-" + uuid.uuid4().hex)
    if time.monotonic() >= deadline:
        raise TimeoutError("state preparation deadline exceeded")
    context = multiprocessing.get_context("spawn")
    buffer = context.Array("B", max(1, len(encoded)), lock=False)
    view = memoryview(buffer).cast("B")
    try:
        view[:len(encoded)] = encoded
    finally:
        view.release()
    if time.monotonic() >= deadline:
        raise TimeoutError("state preparation deadline exceeded")
    request_id = uuid.uuid4().hex
    allocated = context.Event()
    arguments = (buffer, len(encoded), str(root), str(wrapper), allocated, request_id, plugin, caller)
    if budget_base is not None:
        arguments += (str(budget_base),)
    owner = worker.owned_process(_prepare_worker, arguments,
                                label="plugin state preparation")
    # The owner retires its tuple on exit. Do not retain a second copy in a
    # callback failure traceback after clearing the local Event reference.
    arguments = ()
    try:
        receive, process = owner.__enter__()
    except (KeyboardInterrupt, SystemExit):
        # Release synchronization objects even if this traceback is retained.
        allocated = None
        raise
    except BaseException as exc:
        allocated = None
        raise PreparationCleanupError("state preparation startup incomplete; candidate retained") from exc
    try:
        initial = worker.wait_result(receive, deadline=deadline, label="plugin state allocation")
        if isinstance(initial, dict) and initial.get("ok") is False:
            _preparation_failure(initial)
        if (not isinstance(initial, dict)
                or set(initial) != {"ok", "request", "stage", "root", "path", "parent", "wrapper"}
                or initial["ok"] is not True or initial["request"] != request_id
                or initial["stage"] != "allocated" or initial["root"] != str(root)
                or initial["path"] != str(wrapper)
                or not _valid_identity(initial["parent"]) or not _valid_identity(initial["wrapper"])):
            raise PreparationCleanupError("invalid state allocation result; staging retained")
        lease = OwnedStaging(root, wrapper, initial["parent"], initial["wrapper"])
        on_allocate(lease)
        allocated.set()
        result = worker.wait_result(receive, deadline=deadline, label="plugin state preparation")
    finally:
        try:
            owner.__exit__(None, None, None)
        except BaseException as exc:
            raise PreparationCleanupError("state preparation worker cleanup incomplete; candidate retained") from exc
        finally:
            allocated = None
    if time.monotonic() >= deadline:
        raise TimeoutError("state preparation deadline exceeded")
    if isinstance(result, dict) and result.get("ok") is False:
        _preparation_failure(result)
    if (not isinstance(result, dict) or set(result) != {"ok", "request", "wrapper", "candidate"}
            or result["ok"] is not True or result["request"] != request_id
            or not _valid_identity(result["wrapper"]) or result["wrapper"] != lease.wrapper_identity
            or not _valid_identity(result["candidate"])):
        raise RuntimeError("invalid state preparation result")
    return result["candidate"]


def _preparation_failure(result):
    from core.plugin_sandbox import SandboxError
    if result == {"ok": False, "reason": "filesystem"}:
        raise OSError("state preparation filesystem operation failed")
    for reason in ("layout", "inventory", "invalid"):
        if result == {"ok": False, "reason": reason}:
            raise ValueError(f"state preparation {reason} limits or data rejected")
    errors = {"quota": "plugin state staging quota check failed; retained evidence preserved",
              "busy": "plugin state staging or transaction is busy; state not committed",
              "previous": "unresolved previous state transaction", "unresolved": "unresolved state transaction"}
    for reason, message in errors.items():
        if result == {"ok": False, "reason": reason}:
            raise SandboxError(message)
    raise RuntimeError("invalid state preparation result")
