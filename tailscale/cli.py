"""Tailscale management without menu or implicit exit-node authorization."""
import time
import typer
from core.cli import emit, fail, guarded
from tailscale import server


def register(app, context_settings):
    group = typer.Typer(context_settings=context_settings)
    app.add_typer(group, name="tailscale")

    def supported():
        server._ensure_supported()
        if not server.command_exists(server.TAILSCALE_COMMAND):
            fail("Tailscale is not installed")

    @group.command("status")
    @guarded
    def status():
        supported()
        data = server._status_json()
        if not data:
            fail("Tailscale status unavailable")
        emit({"backend_state": data.get("BackendState"), "addresses": data.get("TailscaleIPs", []), "service_active": server.is_service_active()})

    @group.command("diagnose")
    @guarded
    def diagnose(timeout: float = typer.Option(30, "--timeout", min=0.1, max=3600)):
        supported()
        from tailscale.diagnostics import report
        from core.cli import emit_diagnostics
        emit_diagnostics(report(timeout=timeout))

    @group.command("down")
    @guarded
    def down():
        supported()
        server._run_root([server.TAILSCALE_COMMAND, "down"], check=True)
        emit({"disconnected": True})

    @group.command("restart")
    @guarded
    def restart():
        supported()
        server.restart_service()
        if not server.is_service_active():
            fail("Tailscale service restart failed")
        emit({"service_active": True})

    @group.command("login")
    @guarded
    def login(wait: bool = typer.Option(False, "--wait"), timeout: float = typer.Option(60, "--timeout", min=1, max=3600), advertise_exit_node: bool = typer.Option(False, "--advertise-exit-node")):
        supported()
        if advertise_exit_node and server._is_darwin():
            fail("exit-node advertisement is not supported by this macOS integration", 2)
        deadline = time.monotonic() + timeout

        def remaining():
            budget = deadline - time.monotonic()
            if budget <= 0:
                fail("Tailscale authorization deadline exceeded")
            return budget

        output = server.start_login(advertise_exit_node=advertise_exit_node,
                                    timeout=min(remaining(), server.TAILSCALE_UP_TIMEOUT_SECONDS))
        data = server._status_json(timeout=remaining())
        remaining()
        url = server._extract_auth_url(output) or data.get("AuthURL", "")
        logged_in = data.get("BackendState") == server.TAILSCALE_BACKEND_RUNNING
        if not url and not logged_in:
            fail("Tailscale did not provide an authorization URL")
        if wait:
            while not logged_in:
                time.sleep(min(0.5, remaining()))
                data = server._status_json(timeout=remaining())
                remaining()
                logged_in = data.get("BackendState") == server.TAILSCALE_BACKEND_RUNNING
        emit({"auth_url": url, "logged_in": logged_in, "advertise_exit_node": advertise_exit_node})
