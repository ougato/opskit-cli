"""Official Oh My Zsh management commands."""
from contextlib import redirect_stdout
import sys
import typer
from core.cli import emit, fail, guarded, require_interaction
from software.recipes.ohmyzsh import impl


def register(app, context_settings):
    group = typer.Typer(context_settings=context_settings)
    p10k = typer.Typer(context_settings=context_settings)
    shell = typer.Typer(context_settings=context_settings)
    app.add_typer(group, name="ohmyzsh")
    group.add_typer(p10k, name="p10k")
    group.add_typer(shell, name="shell")

    @group.command("status")
    @guarded
    def status():
        impl._ensure_supported()
        emit({"installed": bool(impl.detect()), "p10k_installed": impl.p10k_installed()})

    @p10k.command("configure")
    @guarded
    def configure():
        require_interaction()
        impl._ensure_supported()
        impl._manage_p10k_configure(["OpsKit", "ohmyzsh"])

    @p10k.command("apply-preset")
    @guarded
    def apply_preset():
        impl._ensure_supported()
        with redirect_stdout(sys.stderr):
            impl._install_p10k()
            impl._apply_zshrc(with_p10k=True)
            impl._write_p10k_preset()
            fonts = impl._install_fonts()
        emit({"preset_applied": True, "font_installed": fonts})

    @shell.command("switch")
    @guarded
    def switch():
        impl._ensure_supported()
        if not impl._switch_default_shell():
            fail("default shell change failed")
        emit({"shell": "zsh", "changed": True})
