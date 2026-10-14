"""Official XUI management with explicit sensitive export."""
import typer
from core.cli import emit, fail, guarded
from xui import utils, traffic, constants


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
    def links(output_file: str = typer.Option(..., "--output-file")):
        value = state()
        from core.cli_secrets import export_secret
        link = value.get("vless", {}).get("link")
        if not link:
            fail("XUI link not found")
        export_secret(output_file, {"vless_link": link})
        emit({"exported": True})

    @group.command("logs")
    @guarded
    def logs(lines: int = typer.Option(80, "--lines", min=1, max=10000), follow: bool = typer.Option(False, "--follow")):
        supported()
        from core.process import stream
        from core.secrets import redact
        command = [constants.JOURNALCTL_COMMAND, "-u", constants.XUI_SERVICE, "-n", str(lines), constants.JOURNAL_NO_PAGER_ARG]
        if follow:
            command.append("-f")
        iterator = stream(command, timeout=None if follow else 30)
        try:
            while True:
                try:
                    line = next(iterator)
                except StopIteration as done:
                    if done.value:
                        fail("journal query failed")
                    break
                emit({"line": redact(line)})
        finally:
            iterator.close()
