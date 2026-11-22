"""Official XUI management with explicit sensitive export."""
import typer
from core.cli import emit, fail, guarded
from xui import utils, traffic
from xui.journal import service_logs


def register(app, context_settings):
    group = typer.Typer(context_settings=context_settings)
    app.add_typer(group, name="xui")

    def supported():
        from core.platform import get_platform
        if get_platform().os_type != "linux":
            fail("XUI requires Linux", 2)

    def state():
        supported()
        value = utils.load_state()
        if not value:
            fail("XUI state not found")
        return value

    @group.command("status")
    @guarded
    def status():
        emit(utils.redact_state(state()))

    @group.command("diagnose")
    @guarded
    def diagnose(timeout: float = typer.Option(30, "--timeout", min=0.1, max=3600)):
        supported()
        from xui.diagnostics import report
        from core.cli import emit_diagnostics
        emit_diagnostics(report(timeout=timeout))

    @group.command("traffic")
    @guarded
    def show_traffic():
        supported()
        emit(traffic.compute_stats())

    @group.command("links")
    @guarded
    def links(
        output_file: str = typer.Option(..., "--output-file"),
        raw: bool = typer.Option(False, "--raw"),
        mihomo: bool = typer.Option(False, "--mihomo"),
    ):
        if raw and mihomo:
            from core.i18n import t
            fail(t("xui.error.link_format_conflict"), 2)
        value = state()
        from core.cli_secrets import export_secret
        link = value.get("vless", {}).get("link")
        if not link:
            fail("XUI link not found")
        if raw or mihomo:
            from pathlib import Path
            from core.private_export import publish_private_file
            from xui.links import mihomo_proxy_yaml
            try:
                payload = mihomo_proxy_yaml(link) if mihomo else (link + "\n").encode("utf-8")
            except ValueError:
                from core.i18n import t
                fail(t("xui.error.invalid_link"), 2)
            publish_private_file(Path(output_file), payload)
        else:
            export_secret(output_file, {"vless_link": link})
        emit({"exported": True})

    @group.command("logs")
    @guarded
    def logs(lines: int = typer.Option(80, "--lines", min=1, max=10000), follow: bool = typer.Option(False, "--follow")):
        supported()
        from contextlib import closing
        with closing(service_logs(lines, follow=follow)) as entries:
            for line in entries:
                emit({"line": line})
