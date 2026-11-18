"""Localized Click/Typer help compatibility, installed once per process.

The CLI constructs its command tree at import time.  This module contains the
third-party rendering patches so the entry point only supplies the catalogue.
"""
from __future__ import annotations

import threading
from collections.abc import Mapping

import click
import click.core as click_core
import click.decorators as click_decorators
import click.types as click_types
import typer.rich_utils as typer_rich

_lock = threading.RLock()
_installed = False
_click_labels: dict[str, str] = {}
_typer_labels: dict[str, str] = {}
_highlights = getattr(typer_rich.OptionHighlighter, "_opskit_original_highlights",
                      tuple(typer_rich.OptionHighlighter.highlights))


def _original(function):
    return getattr(function, "_opskit_original", function)


_original_decorators_gettext = _original(getattr(click_decorators, "_", str))
_original_core_gettext = _original(getattr(click_core, "_", str))
_original_rich_gettext = _original(getattr(typer_rich, "_", str))
_original_write_usage = _original(click.HelpFormatter.write_usage)
try:
    import typer._click.decorators as typer_click_decorators
except ImportError:
    typer_click_decorators = None
_original_typer_help_option = (
    _original(typer_click_decorators.help_option) if typer_click_decorators else None
)


class _LowerName(str):
    """Keep Click type names lowercase when Rich requests their uppercase form."""

    def upper(self) -> str:
        return self.lower()


def _lowercase_type_names(root: type) -> None:
    for sub in root.__subclasses__():
        name = getattr(sub, "name", None)
        if isinstance(name, str) and not isinstance(name, _LowerName) and name != "boolean":
            sub.name = _LowerName(name)
        _lowercase_type_names(sub)


def _install_lower_usage(cls: type) -> None:
    if "collect_usage_pieces" not in cls.__dict__:
        return
    original = cls.collect_usage_pieces
    if getattr(original, "_opskit_lower_usage", False):
        return

    def patched(self, ctx):
        return [piece.lower() for piece in original(self, ctx)]

    patched._opskit_lower_usage = True
    cls.collect_usage_pieces = patched


def _decorators_gettext(message: str) -> str:
    if message == "Show this message and exit.":
        return _click_labels[message]
    return _original_decorators_gettext(message)



def _typer_help_option(*args, **kwargs):
    """Localize Typer's bundled Click, whose help text is a literal."""
    decorate = _original_typer_help_option(*args, **kwargs)

    def localized(command):
        result = decorate(command)
        result.params[-1].help = _click_labels["Show this message and exit."]
        return result

    return localized


def _core_gettext(message: str) -> str:
    if message in _click_labels:
        return _click_labels[message]
    return _original_core_gettext(message)


def _rich_gettext(message: str) -> str:
    if message in _typer_labels:
        return _typer_labels[message]
    return _original_rich_gettext(message)


def _write_usage(self, prog, args="", prefix=None):
    if prefix is None:
        prefix = _click_labels.get("Usage:", "Usage:") + " "
    return _original_write_usage(self, prog, args, prefix=prefix)


def install(catalogue: Mapping[str, str]) -> None:
    """Install compatible help formatting or update its language in place."""
    global _installed, _click_labels, _typer_labels
    text = lambda key: catalogue.get(key, key)
    help_text = text("help_text")
    labels = {
        "Show this message and exit.": help_text,
        "required": text("label_required"),
        "default": text("label_default"),
        "Options": text("label_options"),
        "Commands": text("label_commands"),
        "Arguments": text("label_arguments"),
        "Usage": text("label_usage").rstrip(":"),
        "Usage:": text("label_usage"),
    }
    typer_labels = {
        "[required]": f"[{text('label_required')}]",
        "[default: {default}]": f"[{text('label_default')}: {{default}}]",
        "Usage": labels["Usage"],
        "Arguments": labels["Arguments"],
        "Options": labels["Options"],
        "Commands": labels["Commands"],
        "Show this message and exit.": help_text,
    }
    with _lock:
        _click_labels = labels
        _typer_labels = typer_labels
        typer_rich.REQUIRED_LONG_STRING = typer_labels["[required]"]
        typer_rich.OPTIONS_PANEL_TITLE = labels["Options"]
        typer_rich.COMMANDS_PANEL_TITLE = labels["Commands"]
        typer_rich.ARGUMENTS_PANEL_TITLE = labels["Arguments"]
        typer_rich.STYLE_USAGE_COMMAND = "bold"
        typer_rich.OptionHighlighter._opskit_original_highlights = _highlights
        typer_rich.OptionHighlighter.highlights = [
            item.replace("Usage: ", labels["Usage:"] + " ") for item in _highlights
        ]
        if _installed:
            return
        _decorators_gettext._opskit_original = _original_decorators_gettext
        _core_gettext._opskit_original = _original_core_gettext
        _rich_gettext._opskit_original = _original_rich_gettext
        _write_usage._opskit_original = _original_write_usage
        click_decorators._ = _decorators_gettext
        if typer_click_decorators is not None:
            _typer_help_option._opskit_original = _original_typer_help_option
            typer_click_decorators.help_option = _typer_help_option
        click_core._ = _core_gettext
        typer_rich._ = _rich_gettext
        click.HelpFormatter.write_usage = _write_usage
        _lowercase_type_names(click_types.ParamType)
        try:
            import typer._click.types as typer_click_types
        except ImportError:
            pass
        else:
            _lowercase_type_names(typer_click_types.ParamType)
        try:
            import typer._click.core as typer_click_core
            import typer.core as typer_core
        except ImportError:
            pass
        else:
            _install_lower_usage(typer_click_core.Command)
            _install_lower_usage(typer_core.TyperGroup)
            _install_lower_usage(typer_core.TyperCommand)
        _installed = True
