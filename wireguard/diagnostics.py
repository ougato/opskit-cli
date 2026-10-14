"""Read-only WireGuard service reports with validated tunnel identifiers."""
def server_report(*, timeout=30):
    from wireguard.utils import is_service_active
    from wireguard.constants import WG_SERVICE, XRAY_SERVICE
    from core.deadline import Deadline
    budget = Deadline(timeout, "WireGuard server diagnostics")
    return {"checks": [{"name": name, "ok": budget.call(is_service_active, name, timeout=budget.remaining())}
                       for name in (WG_SERVICE, XRAY_SERVICE, "nginx")]}


def client_report(tunnel_id="", *, timeout=30):
    from wireguard import client
    from wireguard.utils import is_service_active
    from core.privilege import unix_file_nonempty
    from core.deadline import Deadline
    budget = Deadline(timeout, "WireGuard client diagnostics")
    entries = budget.call(client._load_client_state, timeout=budget.remaining()).get("tunnels", [])
    if tunnel_id:
        entries = [entries[client._select_tunnel(entries, tunnel_id)]]
    validated = [client._validate_tunnel(entry) for entry in entries]
    checks = []
    for label, iface, service in validated:
        checks.extend([
            {"name": f"{iface}:wireguard", "ok": budget.call(is_service_active, f"wg-quick@{iface}", timeout=budget.remaining())},
            {"name": f"{iface}:xray", "ok": budget.call(is_service_active, service, timeout=budget.remaining())},
            {"name": f"{iface}:config", "ok": budget.call(unix_file_nonempty, f"/etc/wireguard/{iface}.conf", timeout=budget.remaining())
             and budget.call(unix_file_nonempty, f"/usr/local/etc/xray/{label}.json", timeout=budget.remaining())},
        ])
    return {"checks": checks}
