"""Read-only checks shared by official CLI entry points."""
def report(*, timeout=30):
    from tailscale import server
    from core.deadline import Deadline
    budget = Deadline(timeout, "Tailscale diagnostics")
    version = budget.call(server.detect_tailscale_version, timeout=budget.remaining())
    status = budget.call(server._status_json, timeout=budget.remaining())
    # macOS service health uses the same status snapshot, not a second query.
    active = bool(status) if server._is_darwin() else budget.call(server.is_service_active, timeout=budget.remaining())
    return {"checks": [{"name": "version", "ok": bool(version)},
                       {"name": "service", "ok": active},
                       {"name": "login", "ok": status.get("BackendState") == server.TAILSCALE_BACKEND_RUNNING}]}
