"""Assemble official CLI groups without importing the application entry point."""
from __future__ import annotations

from collections.abc import Callable

import typer


def create_groups(app: typer.Typer, settings: dict, translate: Callable[[str], str]):
    """Create groups in their established help and registration order."""
    groups = []
    for name in ("software", "monitor", "network", "plugin"):
        group = typer.Typer(help=translate(name), context_settings=settings)
        app.add_typer(group, name=name)
        groups.append(group)
    return tuple(groups)


def register_plugin_commands(plugin_app: typer.Typer, translate: Callable[[str], str]) -> None:
    """Register plugin lifecycle and permissions before the main run command."""
    def plugin_text(key: str) -> str:
        return translate("plugin_native_" + key)

    from plugin.lifecycle_cli import register as register_lifecycle
    register_lifecycle(plugin_app, translate=plugin_text)
    from plugin.capability_cli import register as register_capabilities
    register_capabilities(plugin_app, translate=plugin_text)


def register_builtin_commands(app: typer.Typer, sw_app: typer.Typer,
                              mon_app: typer.Typer, net_app: typer.Typer,
                              settings: dict) -> None:
    """Register existing command implementations in their established order."""
    from monitor.cli import register as register_monitor
    register_monitor(mon_app)
    from software.query import register as register_software_query
    register_software_query(sw_app)
    from core.preferences_cli import register as register_preferences
    register_preferences(app, settings)
    from tailscale.cli import register as register_tailscale
    register_tailscale(app, settings)
    from software.recipes.ohmyzsh.cli import register as register_ohmyzsh
    register_ohmyzsh(app, settings)
    from xui.cli import register as register_xui
    register_xui(app, settings)
    from wireguard.cli import register as register_wireguard
    register_wireguard(app, settings)
    from network.cli import register as register_network
    register_network(net_app)
    from core.update_cli import register as register_update
    register_update(app, settings)
