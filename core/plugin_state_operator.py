"""Bounded operator workers for explicitly selected plugin state partitions."""
import re
import uuid


def _target(plugin, caller):
    from core.paths import data_dir
    from core.plugin_state import _binding
    _binding(plugin, "platform" if caller is None else caller)
    owner = data_dir().absolute() / "plugin-data" / plugin
    return owner / "self" if caller is None else owner / "callers" / caller


def _inspect(target, plugin, caller):
    from core.plugin_metadata import path_present
    from core.plugin_state import _directory, _parent, _optional_directory, record_path, StateTransactionError
    from core.plugin_state_repair import inspect_state_record
    for directory in reversed((target.parent, *target.parent.parents)):
        try:
            _directory(directory)
        except FileNotFoundError:
            return {"status": "clean"}
    _parent(target)
    _optional_directory(target)
    if not path_present(record_path(target)):
        if path_present(target.with_name(target.name + ".previous")):
            raise StateTransactionError("unowned previous state")
        return {"status": "clean"}
    observation = inspect_state_record(target, plugin=plugin, caller=caller)
    return {"status": observation.status, "sha256": observation.sha256, "phase": observation.phase}


def _worker(connection, plugin, caller, digest, request):
    from network.worker import send
    from core.plugin_state_repair import repair_state_record
    try:
        target = _target(plugin, caller)
        binding = "platform" if caller is None else caller
        if digest is None:
            result = _inspect(target, plugin, binding)
        else:
            repaired = repair_state_record(target, plugin=plugin, caller=binding, expected_sha256=digest)
            result = {"status": repaired.outcome, "sha256": repaired.sha256, "evidence": repaired.evidence}
        response = {"ok": True, "request": request, "plugin": plugin, "caller": caller, "result": result}
    except (OSError, ValueError, RuntimeError):
        response = {"ok": False, "reason": "unconfirmed"}
    try:
        send(connection, response)
    finally:
        connection.close()


def state_operation(plugin, *, caller=None, digest=None, timeout):
    """Parent holds the invocation lock for repair; worker owns transaction lock."""
    from core.plugin_state import _binding
    from network.worker import run
    _binding(plugin, "platform" if caller is None else caller)
    if digest is not None and (not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
        raise ValueError("invalid state record SHA256")
    request = uuid.uuid4().hex
    response = run(_worker, (plugin, caller, digest, request), timeout=timeout, label="plugin state operator")
    if (not isinstance(response, dict) or set(response) != {"ok", "request", "plugin", "caller", "result"}
            or response["ok"] is not True or response["request"] != request
            or response["plugin"] != plugin or response["caller"] != caller):
        raise RuntimeError("state operation unconfirmed; inspect before retry")
    result = response["result"]
    valid = False
    if isinstance(result, dict):
        if digest is None:
            valid = result == {"status": "clean"} or (
                set(result) == {"status", "sha256", "phase"}
                and result["status"] in ("valid", "repairable-tail")
                and isinstance(result["sha256"], str)
                and re.fullmatch(r"[0-9a-f]{64}", result["sha256"]) is not None
                and result["phase"] in ("uncommitted", "restore_previous", "committed"))
        else:
            valid = (set(result) == {"status", "sha256", "evidence"}
                     and result["status"] in ("aborted", "committed") and result["sha256"] == digest
                     and isinstance(result["evidence"], str)
                     and re.fullmatch(r"[0-9a-f]{32}", result["evidence"]) is not None)
    if not valid:
        raise RuntimeError("invalid state operator result")
    return {"plugin": plugin, "caller": caller, **result}
