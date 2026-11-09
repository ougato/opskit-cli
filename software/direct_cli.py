"""Software CLI registration and interactive/direct dispatch.

The host parameter is an explicit compatibility boundary: it supplies runtime
callbacks, allows tests to replace them, and never imports the entry module.
"""
from __future__ import annotations

import sys
from typing import Any, Protocol

import typer
from core.cli import guarded

_EXIT_RUNTIME = 1
_EXIT_USAGE = 2


class SoftwareHost(Protocol):
    """Entry-provided callbacks resolved when each command runs."""

    console: Any
    t: Any
    _boot: Any
    _software_inventory: Any
    _print_software_table: Any
    _get_recipe_for_direct: Any
    _direct_fail: Any
    _recipe_name: Any
    _check_disk_or_exit: Any
    _resolve_deps_or_exit: Any
    _sw_action_by_name: Any
    _install_direct: Any
    _uninstall_direct: Any
    _upgrade_direct: Any
    _switch_direct: Any
    _diagnose_direct: Any


def register(sw_app: typer.Typer, translate, host: SoftwareHost):
    """Register commands in the existing order and return compatibility exports."""
    _ct = translate

    @sw_app.command("list", help=_ct("sw_list"))
    def sw_list(category: str = typer.Option("", "--category"), platform: str = typer.Option("", "--platform")) -> None:
        if host._software_inventory(category=category, platform=platform):
            return
        host._boot()
        host._print_software_table()


    @sw_app.command("search", help=_ct("sw_search"))
    def sw_search(
        query: str = typer.Argument(None, help=_ct("sw_query")),
        category: str = typer.Option("", "--category"),
        platform: str = typer.Option("", "--platform"),
    ) -> None:
        from core.cli import require_interaction
        if query is None:
            require_interaction()
        elif host._software_inventory(query=query, category=category, platform=platform):
            return
        host._boot()
        if query:
            host._print_software_table(query=query)
        else:
            from software.menu import show_search
            show_search()


    @sw_app.command("installed", help=_ct("sw_installed"))
    def sw_installed() -> None:
        if host._software_inventory(installed_only=True):
            return
        host._boot()
        host._print_software_table(installed_only=True, include_hidden=True)


    @sw_app.command("xui-snapshot", hidden=True)
    def sw_xui_snapshot() -> None:
        """供 systemd 定时器调用：把 x-ui 累计流量计数器写入历史库。"""
        from xui.traffic import take_snapshot
        take_snapshot()


    @sw_app.command("versions", help=_ct("sw_versions"))
    def sw_versions(
        name: str = typer.Argument(..., help=_ct("sw_name")),
    ) -> None:
        from core.cli import output_mode, emit
        if output_mode.get() != "json":
            host._boot()
        cls, instance = host._get_recipe_for_direct(name)
        if getattr(cls, "has_submenu", False):
            host._direct_fail(host.t("cli.error.submenu_recipe", name=name), _EXIT_USAGE)
        try:
            versions = instance.versions()
        except Exception as e:
            host._direct_fail(host.t("error.unknown", error=str(e)), _EXIT_RUNTIME)
        if not versions:
            host._direct_fail(host.t("software.no_versions"), _EXIT_RUNTIME)
        if not getattr(cls, "has_install_version_selection", True):
            if output_mode.get() == "json":
                emit({"name": cls.key, "version_selection": False, "versions": [], "source": "system_or_managed"})
                return
            host.console.print(host.t("cli.system_package_version", name=host._recipe_name(cls)))
            return
        if output_mode.get() == "json":
            emit({"name": cls.key, "version_selection": True, "versions": versions})
            return
        for item in versions:
            host.console.print(item)


    @sw_app.command("install", help=_ct("sw_install"))
    @guarded
    def sw_install(
        name: str = typer.Argument(None, help=_ct("sw_name_install")),
        token: str = typer.Option("", "--token", "-t", help=_ct("sw_token")),
        version: str = typer.Option("", "--version", "-v", help=_ct("sw_version")),
        token_file: str = typer.Option("", "--token-file", help="Read WireGuard token from file or - for stdin"),
        config_file: str = typer.Option("", "--config", help="Official installation YAML"),
    ) -> None:
        if config_file:
            from core.cli import emit, fail
            from software.install_config import load_options, install
            if token or token_file or version or not name:
                fail("--config conflicts with token/version or missing software name", 2)
            try:
                options = load_options(name, config_file)
            except (OSError, ValueError):
                fail("invalid installation config", 2)
            cls, instance = host._get_recipe_for_direct(name)
            host._check_disk_or_exit(instance)
            host._resolve_deps_or_exit(instance, ["OpsKit", "software"])
            from contextlib import redirect_stdout
            try:
                with redirect_stdout(sys.stderr):
                    result = install(name, options)
            except typer.Exit:
                raise
            except Exception as exc:
                fail(type(exc).__name__)
            emit(result)
            return
        if token_file:
            if token or name != "wg_client":
                from core.cli import fail
                fail("--token-file requires wg_client and conflicts with --token", 2)
            from core.cli_secrets import read_secret_file
            try:
                token = read_secret_file(token_file)
            except (OSError, ValueError):
                from core.cli import fail
                fail("unable to read token input", 2)
        if name:
            host._sw_action_by_name(name, "install", token=token or None, version=version or None)
        else:
            from core.cli import require_interaction
            require_interaction()
            host._boot()
            from software.menu import show_install
            show_install()


    @sw_app.command("uninstall", help=_ct("sw_uninstall"))
    @guarded
    def sw_uninstall(
        name: str = typer.Argument(None, help=_ct("sw_name")),
        version: str = typer.Option("", "--version", "-v", help=_ct("sw_version")),
        all_versions: bool = typer.Option(False, "--all", "-a", help=_ct("sw_all_versions")),
    ) -> None:
        if name:
            host._sw_action_by_name(name, "uninstall", version=version or None, all_versions=all_versions)
        else:
            from core.cli import require_interaction
            require_interaction()
            host._boot()
            from software.menu import show_uninstall
            show_uninstall()


    @sw_app.command("upgrade", help=_ct("sw_upgrade"))
    @guarded
    def sw_upgrade(
        name: str = typer.Argument(None, help=_ct("sw_name")),
        version: str = typer.Option("", "--version", "-v", help=_ct("sw_version")),
    ) -> None:
        if name:
            host._sw_action_by_name(name, "upgrade", version=version or None)
        else:
            from core.cli import require_interaction
            require_interaction()
            host._boot()
            from software.menu import show_upgrade
            show_upgrade()


    @sw_app.command("switch", help=_ct("sw_switch"))
    @guarded
    def sw_switch(
        name: str = typer.Argument(..., help=_ct("sw_name")),
        version: str = typer.Option(..., "--version", "-v", help=_ct("sw_version")),
    ) -> None:
        host._sw_action_by_name(name, "switch", version=version)


    @sw_app.command("diagnose", help=_ct("sw_diagnose"))
    @guarded
    def sw_diagnose(
        name: str = typer.Argument(..., help=_ct("sw_name_diagnose")),
        timeout: float = typer.Option(30, "--timeout", min=0.1, max=3600),
    ) -> None:
        host._sw_action_by_name(name, "diagnose", timeout=timeout)


    @sw_app.command("manage", help=_ct("sw_manage"))
    @guarded
    def sw_manage(
        name: str = typer.Argument(..., help=_ct("sw_name_diagnose")),
    ) -> None:
        from core.cli import require_interaction
        require_interaction()
        host._boot()
        host._sw_action_by_name(name, "manage")


    def _sw_action_by_name(
        name: str,
        action: str,
        token: str | None = None,
        version: str | None = None,
        all_versions: bool = False,
        timeout: float = 30,
    ) -> None:
        """按软件名执行指定操作

        Args:
            name: 软件 key
            action: 操作类型 (install/uninstall/upgrade/diagnose/manage)
            token: WireGuard 客户端令牌（非交互模式）
            version: 指定安装版本（非交互模式）
        """
        cls, instance = host._get_recipe_for_direct(name)
        breadcrumb = ["OpsKit", host.t("menu.software")]
        if action == "install":
            host._install_direct(breadcrumb, cls, instance, version=version, token=token)
        elif action == "uninstall":
            host._uninstall_direct(cls, instance, version=version, all_versions=all_versions)
        elif action == "upgrade":
            host._upgrade_direct(breadcrumb, cls, instance, version=version)
        elif action == "switch":
            host._switch_direct(cls, instance, version=version)
        elif action == "diagnose":
            if getattr(cls, "has_diagnose", False):
                host._diagnose_direct(cls, instance, timeout=timeout)
            else:
                host._direct_fail(host.t("software.not_supported"), _EXIT_USAGE)
        elif action == "manage":
            if getattr(cls, "has_manage", False):
                from software.menu import _do_manage
                _do_manage(breadcrumb, cls, instance)
            else:
                host._direct_fail(host.t("software.not_supported"), _EXIT_USAGE)


    def _software_inventory(**kwargs) -> bool:
        from core.cli import emit, fail, output_mode
        if kwargs.get("platform", "") not in {"", "linux", "windows", "darwin"}:
            fail("platform must be linux, windows or darwin", 2)
        if output_mode.get() == "json" or kwargs.get("category") or kwargs.get("platform"):
            from software.query import inventory
            emit(inventory(**kwargs))
            return True
        return False


    def _direct_fail(message: str, code: int = _EXIT_RUNTIME) -> None:
        from core.cli import output_mode, fail
        if output_mode.get() == "json":
            fail(message, code)
        from core.theme import print_error

        print_error(message)
        raise typer.Exit(code)


    def _recipe_name(cls: type) -> str:
        from core.recipe_utils import recipe_display_name
        return recipe_display_name(cls)


    def _get_recipe_for_direct(name: str):
        from software.registry import get as get_recipe
        from core.platform import get_platform

        cls = get_recipe(name)
        if cls is None:
            host._direct_fail(host.t("cli.error.software_not_found", name=name), _EXIT_USAGE)
        info = get_platform()
        if info.os_type not in getattr(cls, "platforms", []):
            host._direct_fail(host.t("cli.error.platform_not_supported", name=name, platform=info.os_type), _EXIT_USAGE)
        return cls, cls()


    def _print_software_table(
        query: str | None = None,
        installed_only: bool = False,
        include_hidden: bool = False,
    ) -> None:
        """脚本友好的软件列表输出：不进入选择器，不暂停。"""
        from rich import box as rich_box
        from rich.table import Table
        from software.registry import all_recipes
        from core.platform import get_platform
        from core.theme import get_color, get_icon

        info = get_platform()
        keyword = (query or "").strip().lower()
        rows: list[tuple[type, str | None]] = []
        for cls in all_recipes():
            if info.os_type not in getattr(cls, "platforms", []):
                continue
            if getattr(cls, "hidden", False) and not include_hidden:
                continue
            name = host._recipe_name(cls)
            if keyword and keyword not in cls.key.lower() and keyword not in name.lower() and keyword not in getattr(cls, "description", "").lower():
                continue
            try:
                version = cls().detect()
            except Exception:
                version = None
            if installed_only and not version:
                continue
            rows.append((cls, version))

        if not rows:
            host.console.print(host.t("software.none_installed") if installed_only else host.t("software.search_no_result", keyword=query or ""))
            return

        muted = get_color("muted")
        success = get_color("success")
        table = Table(title=host.t("software.list"), box=rich_box.ROUNDED)
        table.add_column("key")
        table.add_column(host.t("software.name"))
        table.add_column(host.t("software.status"))
        table.add_column(host.t("software.version"))
        table.add_column(host.t("software.platforms"))
        for cls, version in rows:
            status = f"[{success}]{host.t('software.installed')}[/{success}]" if version else f"[{muted}]{host.t('software.not_installed')}[/{muted}]"
            table.add_row(
                cls.key,
                f"{get_icon(cls.key)} {host._recipe_name(cls)}",
                status,
                version or "-",
                " / ".join(getattr(cls, "platforms", [])),
            )
        host.console.print(table)


    def _diagnose_direct(cls: type, instance, *, timeout=30) -> None:
        from core.cli import emit_diagnostics
        emit_diagnostics(instance.diagnostic_report(timeout=timeout))


    return (
        sw_list,
        sw_search,
        sw_installed,
        sw_xui_snapshot,
        sw_versions,
        sw_install,
        sw_uninstall,
        sw_upgrade,
        sw_switch,
        sw_diagnose,
        sw_manage,
        _sw_action_by_name,
        _software_inventory,
        _direct_fail,
        _recipe_name,
        _get_recipe_for_direct,
        _print_software_table,
        _diagnose_direct,
    )
