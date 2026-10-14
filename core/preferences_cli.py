"""Explicit preference commands; read-only queries never initialize config."""
import typer

from core.cli import emit, fail, guarded
from core.config import load_config


def register(app, context_settings):
    preferences = typer.Typer(context_settings=context_settings)
    theme = typer.Typer(context_settings=context_settings)
    language = typer.Typer(context_settings=context_settings)
    app.add_typer(preferences, name="preferences")
    app.add_typer(theme, name="theme")
    app.add_typer(language, name="language")

    @preferences.command("show")
    @guarded
    def show():
        cfg = load_config()
        # Do not dump unrelated configuration or credentials.
        emit({"theme": cfg.get("theme"), "language": cfg.get("language")})

    @theme.command("list")
    def themes():
        from core.theme import list_themes
        emit(list_themes())

    @theme.command("set")
    @guarded
    def set_theme(name: str):
        from core.theme import list_themes, switch_theme
        if name not in list_themes():
            fail("unknown theme", 2)
        switch_theme(name)
        emit({"theme": name})

    @language.command("list")
    def languages():
        emit(["auto", "zh", "en"])

    @language.command("set")
    @guarded
    def set_language(name: str):
        if name not in {"auto", "zh", "en"}:
            fail("unknown language", 2)
        from core.i18n import switch
        switch(name)
        emit({"language": name})
