"""Host-owned publication: fresh authorization and mutation share worker locks."""
import json
import math
from pathlib import Path
import time
import uuid


class PublicationUncertainError(RuntimeError):
    """Worker startup/exit or transport was uncertain; retain owned staging."""


def _valid_requirements(request, plugin, caller):
    from core.plugin_inspection import _valid_authorization_request, REQUEST_LIMIT
    if not _valid_authorization_request(request):
        return False
    if any(not Path(request[key]).is_absolute() for key in ("config", "trust")):
        return False
    names = {identity[0] for identity in request["identities"]}
    return (plugin in names and (caller == "platform" or caller in names)
            and len(json.dumps(request, ensure_ascii=False).encode("utf-8")) <= REQUEST_LIMIT)


def _publish_worker(connection, root, candidate, parent_identity, wrapper_identity,
                    candidate_identity, plugin, caller, requirements, deadline, request_id):
    from core.config import configuration_transaction, get_config_path
    from core.plugin_trust import authorization_transaction, _trust_file
    from core.plugin_state import transaction_guard, _binding, _parent, _directory
    from core.plugin_state_budget import platform_state_guard
    from core.plugin_state_prepare import _binding as candidate_binding
    from core.plugin_sandbox import _read_live_authorizations, _publish_state_locked, _invocations
    from core.resource_lock import ResourceBusyError
    from network.worker import send
    result = {"request": request_id, "root": root, "candidate": candidate}
    try:
        _binding(plugin, caller)
        if (not _valid_requirements(requirements, plugin, caller)
                or str(get_config_path()) != requirements["config"]
                or str(_trust_file()) != requirements["trust"]):
            raise ValueError("publication authorization scope changed")
        target = _parent(root)
        staging = Path(candidate)
        if staging.parent.parent != target.parent:
            raise ValueError("publication candidate scope changed")
        _invocations.deadline = deadline
        with platform_state_guard(), configuration_transaction(), authorization_transaction(), transaction_guard(target):
            candidate_binding(staging, parent_identity, wrapper_identity)
            if _directory(staging) != candidate_identity:
                raise ValueError("publication candidate changed")
            failure = _read_live_authorizations(requirements["identities"], requirements["edges"],
                                                config_path=requirements["config"],
                                                trust_path=requirements["trust"],
                                                networks=requirements.get("networks", ()))
            if failure is not None:
                result.update(ok=False, reason="authorization", failure=failure)
            else:
                # The same worker owns authorization and transaction locks until
                # both renames finish. No cached permit crosses a lock boundary.
                from core.plugin_sandbox import _remaining
                _remaining()
                _publish_state_locked(staging, target, plugin=plugin, caller=caller)
                result.update(ok=True, outcome="committed")
    except ResourceBusyError:
        result.update(ok=False, reason="busy")
    except (OSError, ValueError, RuntimeError):
        # A journal or rename may have reached disk. Never promise rollback or
        # report "not committed" after an ambiguous filesystem failure.
        result.update(ok=False, reason="uncertain")
    try:
        send(connection, result)
    finally:
        connection.close()


def publish_supervised(lease, candidate_identity, *, plugin, caller, requirements, timeout):
    from core.plugin_state import _binding, _valid_identity
    from core.plugin_state_io import OwnedStaging
    from core.plugin_sandbox import SandboxError, _raise_authorization_failure
    from network.worker import run
    _binding(plugin, caller)
    if (not isinstance(lease, OwnedStaging) or not _valid_identity(candidate_identity)
            or not _valid_identity(lease.parent_identity) or not _valid_identity(lease.wrapper_identity)
            or not _valid_requirements(requirements, plugin, caller)):
        raise SandboxError("invalid state publication ownership or authorization requirements")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("invalid state publication allowance")
    deadline = time.monotonic() + timeout
    root, candidate = str(lease.root), str(lease.candidate)
    if any(not Path(path).is_absolute() or not 0 < len(path) <= 65536 for path in (root, candidate)):
        raise ValueError("invalid state publication target")
    request_id = uuid.uuid4().hex
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("state publication allowance exhausted")
        result = run(_publish_worker, (root, candidate, lease.parent_identity, lease.wrapper_identity,
                                      candidate_identity, plugin, caller, requirements, deadline, request_id),
                     timeout=remaining, label="plugin state publication")
    except TimeoutError:
        raise SandboxError("plugin exceeded execution deadline during state publication; outcome unconfirmed") from None
    except (OSError, RuntimeError) as exc:
        raise PublicationUncertainError("plugin state publication outcome unconfirmed; evidence retained") from exc
    binding = {"request": request_id, "root": root, "candidate": candidate}
    if result == {**binding, "ok": True, "outcome": "committed"} and result.get("ok") is True:
        return
    if result == {**binding, "ok": False, "reason": "busy"} and result.get("ok") is False:
        raise SandboxError("plugin authorization or state transaction is busy; state not committed")
    for failure in ("settings", "identity", "service", "network"):
        if result == {**binding, "ok": False, "reason": "authorization", "failure": failure} and result.get("ok") is False:
            _raise_authorization_failure(failure)
    raise PublicationUncertainError("plugin state publication outcome unconfirmed; evidence retained")
