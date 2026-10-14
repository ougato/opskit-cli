"""Official WireGuard server CLI; immutable public keys identify peers."""
import typer
from core.cli import emit, fail, guarded
from wireguard import server


def register(app, context_settings):
    group = typer.Typer(context_settings=context_settings)
    srv = typer.Typer(context_settings=context_settings)
    peer = typer.Typer(context_settings=context_settings)
    app.add_typer(group, name="wireguard")
    group.add_typer(srv, name="server")
    srv.add_typer(peer, name="peer")
    client_group = typer.Typer(context_settings=context_settings)
    tunnel = typer.Typer(context_settings=context_settings)
    group.add_typer(client_group, name="client")
    client_group.add_typer(tunnel, name="tunnel")

    def client_tunnels(tunnel_id=""):
        from wireguard import client
        entries = client._load_client_state().get("tunnels", [])
        if tunnel_id:
            entries = [entries[client._select_tunnel(entries, tunnel_id)]]
        result = []
        for entry in entries:
            label, iface, service = client._validate_tunnel(entry)
            result.append({"id": iface, "label": label, "xray_service": service,
                           **{key: entry.get(key) for key in ("local_port", "client_ip", "vpn_gateway", "vpn_subnet")}})
        return result

    @tunnel.command("list")
    @guarded
    def list_tunnels():
        supported()
        emit(client_tunnels())

    @client_group.command("status")
    @guarded
    def client_status(tunnel_id: str = typer.Option("", "--tunnel")):
        supported()
        from wireguard.utils import is_service_active
        entries = client_tunnels(tunnel_id)
        for entry in entries:
            entry["wireguard_active"] = is_service_active(f"wg-quick@{entry['id']}")
            entry["xray_active"] = is_service_active(entry["xray_service"])
        emit(entries)

    @client_group.command("diagnose")
    @guarded
    def client_diagnose(tunnel_id: str = typer.Option("", "--tunnel"), timeout: float = typer.Option(30, "--timeout", min=0.1, max=3600)):
        supported()
        from wireguard.diagnostics import client_report
        from core.cli import emit_diagnostics
        emit_diagnostics(client_report(tunnel_id, timeout=timeout))

    def client_operation(operation, **kwargs):
        from contextlib import redirect_stdout
        import sys
        with redirect_stdout(sys.stderr):
            result = operation(**kwargs)
        if result is None:
            fail("client operation did not complete")
        emit(result)

    @tunnel.command("add")
    @guarded
    def add_tunnel(token_file: str = typer.Option(..., "--token-file")):
        supported()
        from core.cli_secrets import read_secret_file
        from wireguard import client
        client_operation(client.install_client, token=read_secret_file(token_file))

    @tunnel.command("update-token")
    @guarded
    def update_token(tunnel_id: str, token_file: str = typer.Option(..., "--token-file")):
        supported()
        from core.cli_secrets import read_secret_file
        from wireguard import client
        client_operation(client.update_client_token, breadcrumb=["OpsKit", "wireguard"], tunnel_id=tunnel_id, token=read_secret_file(token_file))

    @tunnel.command("remove")
    @guarded
    def remove_client_tunnel(tunnel_id: str):
        supported()
        from core.prompt import confirm
        from wireguard import client
        client_tunnels(tunnel_id)
        if not confirm(breadcrumb=["OpsKit", "wireguard"], prompt="Remove tunnel?"):
            fail("tunnel removal cancelled")
        client_operation(client.remove_tunnel, breadcrumb=["OpsKit", "wireguard"], tunnel_id=tunnel_id)

    def supported():
        from core.platform import get_platform
        if get_platform().os_type != "linux":
            fail("WireGuard requires Linux", 2)

    def valid_name(name):
        if not name.strip() or len(name) > 128 or any(ord(char) < 32 for char in name):
            fail("invalid peer name", 2)

    @srv.command("status")
    @guarded
    def status():
        supported()
        state = server._load_state()
        if not state:
            fail("WireGuard server state not found")
        emit({key: state.get(key) for key in ("server_ip", "server_port", "sni", "vpn_subnet", "vpn_gateway", "tunnel_label")})

    @srv.command("diagnose")
    @guarded
    def diagnose(timeout: float = typer.Option(30, "--timeout", min=0.1, max=3600)):
        supported()
        from wireguard.diagnostics import server_report
        from core.cli import emit_diagnostics
        emit_diagnostics(server_report(timeout=timeout))

    @peer.command("list")
    @guarded
    def list_peers():
        supported()
        emit([{"id": item.get("pubkey"), "name": item.get("name"), "ip": item.get("ip")} for item in server._get_clients()])

    @peer.command("add")
    @guarded
    def add(name: str = typer.Option(..., "--name")):
        supported()
        valid_name(name)
        emit(server.add_peer(["OpsKit", "wireguard"], name=name))

    @peer.command("rename")
    @guarded
    def rename(peer_id: str, name: str = typer.Option(..., "--name")):
        supported()
        valid_name(name)
        emit(server.rename_peer(["OpsKit", "wireguard"], peer_id=peer_id, name=name))

    @peer.command("remove")
    @guarded
    def remove(peer_id: str):
        supported()
        from core.prompt import confirm
        if not confirm(breadcrumb=["OpsKit", "wireguard"], prompt="Remove peer?"):
            fail("peer removal cancelled")
        emit(server.remove_peer(["OpsKit", "wireguard"], peer_id=peer_id))

    @peer.command("export")
    @guarded
    def export(peer_id: str, output_file: str = typer.Option(..., "--output-file")):
        supported()
        matches = [item for item in server._get_clients() if item.get("pubkey") == peer_id]
        if len(matches) != 1 or not matches[0].get("token"):
            fail("peer token not found")
        from core.cli_secrets import export_secret
        export_secret(output_file, {"token": matches[0]["token"]})
        emit({"exported": True})
