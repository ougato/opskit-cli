"""Owned transaction recovery worker; cannot publish a new candidate."""
from pathlib import Path
from dataclasses import dataclass
import uuid


@dataclass
class OwnedStaging:
    """Invocation-local ownership; never permission to reclaim unknown orphans."""
    root: Path
    wrapper: Path
    parent_identity: list
    wrapper_identity: list
    retired: bool = False
    candidate_identity: list | None = None

    @property
    def candidate(self):
        return self.wrapper / "new"


def _discard_worker(connection, root, wrapper, parent_identity, wrapper_identity, request_id, plugin=None, caller=None):
    import os
    import re
    from core.plugin_state import (_parent, _directory, _optional_directory,
                                   _remove_owned_tree, record_path, transaction_guard)
    from core.plugin_metadata import path_present
    from core.plugin_state_budget import platform_state_guard
    from network.worker import send
    try:
        target = _parent(root)
        staging = Path(wrapper)
        if (staging.parent != target.parent
                or re.fullmatch(r"\.state-[a-zA-Z0-9_-]{1,120}", staging.name) is None
                or _directory(target.parent) != parent_identity):
            raise ValueError("state staging ownership changed")
        with platform_state_guard(), transaction_guard(target):
            if path_present(record_path(target)):
                if plugin is None:
                    outcome = "retained"
                else:
                    from core.plugin_state import inspect_transaction, finish_transaction
                    plan = inspect_transaction(target, plugin=plugin, caller=caller)
                    if (plan.phase != "uncommitted" or plan.record.staging != staging.name
                            or plan.record.staging_identity != tuple(wrapper_identity)):
                        raise ValueError("state staging journal is not owned by this invocation")
                    if finish_transaction(target, plugin=plugin, caller=caller) != "aborted":
                        raise ValueError("state staging cleanup outcome changed")
                    outcome = "removed"
            elif _optional_directory(staging) is None:
                outcome = "absent"
            else:
                if _directory(staging) != wrapper_identity:
                    raise ValueError("state staging ownership changed")
                # Only this invocation's candidate is permitted. Bound the
                # wrapper scan independently of the candidate tree budget.
                with os.scandir(staging) as entries:
                    for index, entry in enumerate(entries):
                        if index or entry.name != "new":
                            raise ValueError("unknown staging entry")
                candidate = staging / "new"
                if _optional_directory(candidate) is not None:
                    identity = tuple(_directory(candidate))
                    _remove_owned_tree(candidate, identity, lambda: None)
                if (_directory(target.parent) != parent_identity
                        or _directory(staging) != wrapper_identity):
                    raise ValueError("state staging ownership changed")
                staging.rmdir()
                outcome = "removed"
        result = {"ok": True, "request": request_id, "root": root,
                  "wrapper": wrapper, "outcome": outcome}
    except (OSError, ValueError, RuntimeError):
        result = {"ok": False, "reason": "retained"}
    try:
        send(connection, result)
    finally:
        connection.close()


def discard_supervised(lease, *, timeout, plugin=None, caller=None):
    from core.plugin_state import _valid_identity
    from network.worker import run
    if (not isinstance(lease, OwnedStaging)
            or not _valid_identity(lease.parent_identity)
            or not _valid_identity(lease.wrapper_identity)):
        raise ValueError("invalid staging ownership")
    if plugin is not None or caller is not None:
        from core.plugin_state import _binding
        _binding(plugin, caller)
    root, wrapper = str(lease.root), str(lease.wrapper)
    request_id = uuid.uuid4().hex
    arguments = (root, wrapper, lease.parent_identity, lease.wrapper_identity, request_id)
    if plugin is not None:
        arguments += (plugin, caller)
    result = run(_discard_worker, arguments,
                 timeout=timeout, label="plugin state staging cleanup")
    if result == {"ok": False, "reason": "retained"}:
        raise RuntimeError("state staging cleanup refused; evidence retained")
    if (not isinstance(result, dict)
            or set(result) != {"ok", "request", "root", "wrapper", "outcome"}
            or result["ok"] is not True or result["request"] != request_id
            or result["root"] != root or result["wrapper"] != wrapper
            or result["outcome"] not in ("retained", "absent", "removed")):
        raise RuntimeError("invalid state staging cleanup result")
    return result["outcome"]


def _initialize_state_parent(root, plugin, caller):
    """Create only the bound partition's parents, rejecting linked ancestors."""
    from core.paths import data_dir
    from core.plugin_state import _binding, _directory, _parent
    from core.plugin_metadata import path_present
    from core.plugin_state_budget import reserve_platform_state, MAX_PLATFORM_STATE_ENTRIES
    _binding(plugin, caller)
    target = Path(root)
    owner = data_dir().absolute() / "plugin-data" / plugin
    allowed = {owner / "callers" / caller}
    if caller == "platform":
        allowed.add(owner / "self")
    if target not in allowed or ".." in target.parts:
        raise ValueError("state initialization target is not bound")
    missing = 0
    for directory in (target.parent, *target.parent.parents):
        try:
            _directory(directory)
        except FileNotFoundError:
            missing += 1
    if missing:
        platform = data_dir().absolute() / "plugin-data"
        if path_present(platform):
            reserve_platform_state(platform, reserve_bytes=0, reserve_entries=missing, reserve_staging=0)
        elif missing > MAX_PLATFORM_STATE_ENTRIES:
            raise ValueError("platform state initialization quota exhausted")
    for directory in reversed((target.parent, *target.parent.parents)):
        try:
            _directory(directory)
        except FileNotFoundError:
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                pass
            _directory(directory)
    _parent(target)


def _finish_worker(connection, root, plugin, caller, request_id, initialize=False):
    from core.plugin_metadata import path_present
    from core.plugin_state import transaction_guard, finish_transaction, record_path, StateCleanupError
    from core.plugin_sandbox import _rename_state, _require_resolved_state, SandboxError
    from core.resource_lock import ResourceBusyError
    from network.worker import send
    from core.plugin_state_budget import platform_state_guard
    try:
        target = Path(root)
        with platform_state_guard():
            if initialize:
                _initialize_state_parent(target, plugin, caller)
            with transaction_guard(target):
                if not path_present(record_path(target)):
                    _require_resolved_state(target)
                    outcome = None
                else:
                    outcome = finish_transaction(target, plugin=plugin, caller=caller, rename=_rename_state)
                    _require_resolved_state(target)
        result = {"ok": True, "request": request_id, "root": root, "outcome": outcome}
    except ResourceBusyError:
        result = {"ok": False, "reason": "busy"}
    except StateCleanupError as exc:
        result = {"ok": False, "reason": exc.outcome + "_cleanup"}
    except SandboxError:
        result = {"ok": False, "reason": "unresolved_previous"}
    except (OSError, ValueError):
        result = {"ok": False, "reason": "refused"}
    try:
        send(connection, result)
    finally:
        connection.close()


def finish_supervised(root, *, plugin, caller, timeout, initialize=False):
    from core.plugin_state import _binding, StateCleanupError, StateTransactionError
    from core.resource_lock import ResourceBusyError
    from core.plugin_sandbox import SandboxError
    from network.worker import run
    _binding(plugin, caller)
    if type(initialize) is not bool:
        raise ValueError("invalid state initialization option")
    root = str(root)
    if not 0 < len(root) <= 65536 or not Path(root).is_absolute():
        raise ValueError("invalid state transaction target")
    request_id = uuid.uuid4().hex
    arguments = (root, plugin, caller, request_id)
    if initialize:
        arguments += (True,)
    result = run(_finish_worker, arguments,
                 timeout=timeout, label="plugin state recovery")
    if result == {"ok": False, "reason": "busy"}:
        raise ResourceBusyError("plugin state transaction is busy")
    if result == {"ok": False, "reason": "unresolved_previous"}:
        raise SandboxError("unresolved previous state transaction")
    for outcome in ("committed", "aborted"):
        if result == {"ok": False, "reason": outcome + "_cleanup"}:
            raise StateCleanupError(outcome)
    if result == {"ok": False, "reason": "refused"}:
        raise StateTransactionError("state recovery refused")
    if (not isinstance(result, dict) or set(result) != {"ok", "request", "root", "outcome"}
            or result["ok"] is not True or result["request"] != request_id or result["root"] != root
            or result["outcome"] not in (None, "committed", "aborted")):
        raise RuntimeError("invalid state recovery result")
    return result["outcome"]
