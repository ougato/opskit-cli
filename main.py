"""OpsKit 入口 — 初始化框架层，渲染主菜单，分发到各插件模块"""
from __future__ import annotations

import sys

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes

    _STD_OUTPUT_HANDLE = ctypes.wintypes.DWORD(-11 & 0xFFFFFFFF)
    _STD_ERROR_HANDLE = ctypes.wintypes.DWORD(-12 & 0xFFFFFFFF)
    _ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    _ENABLE_PROCESSED_OUTPUT = 0x0001
    _kernel32 = ctypes.windll.kernel32

    for _handle_id in (_STD_OUTPUT_HANDLE, _STD_ERROR_HANDLE):
        _handle = _kernel32.GetStdHandle(_handle_id)
        _mode = ctypes.wintypes.DWORD(0)
        if _kernel32.GetConsoleMode(_handle, ctypes.byref(_mode)):
            _kernel32.SetConsoleMode(
                _handle,
                _mode.value | _ENABLE_VIRTUAL_TERMINAL_PROCESSING | _ENABLE_PROCESSED_OUTPUT,
            )

    _kernel32.SetConsoleOutputCP(65001)
    _kernel32.SetConsoleCP(65001)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
else:
    import io as _io
    import os as _os
    if not _os.environ.get("PYTHONIOENCODING"):
        _os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if getattr(sys.stdout, "encoding", "ascii").lower().replace("-", "") != "utf8":
        sys.stdout = _io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True,
        )
        sys.stderr = _io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True,
        )

# ── venv 自举：必须在所有第三方 import 之前，确保后续 import 使用 venv 内的包 ──
# 打包模式（Nuitka __compiled__ / PyInstaller sys.frozen）跳过 venv bootstrap
_is_packed = getattr(sys, "frozen", False) or "__compiled__" in dir()
if sys.platform != "win32" and not _is_packed:
    from pathlib import Path as _Path
    from core.venv_bootstrap import ensure_venv as _ensure_venv
    _ensure_venv(_Path(__file__).parent.resolve())

import typer

from core.config import ensure_config
from core.i18n import init as i18n_init, t, switch as i18n_switch
from core.theme import init as theme_init, get_icon, switch_theme, list_themes, print_info
from core.loader import discover_modules
from core.prompt import select, console, UserCancel, set_auto_yes

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


@app.callback(invoke_without_command=True)
def _app_callback(
    ctx: typer.Context,
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Non-interactive mode: skip all confirmation prompts (like apt -y)",
    ),
    version: bool = typer.Option(False, "--version", "-V", help="Show version"),
    theme: str = typer.Option("", "--theme", help="Override theme"),
    lang: str = typer.Option("", "--lang", help="Override language (zh/en)"),
) -> None:
    """OpsKit — 跨平台运维工具箱"""
    import os
    if yes or os.environ.get("OPSKIT_YES", "").strip() in ("1", "true", "yes"):
        set_auto_yes(True)
    if version:
        from core.constants import APP_VERSION
        console.print(f"v{APP_VERSION}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        cfg = _boot()
        if theme:
            switch_theme(theme)
        if lang:
            i18n_switch(lang)
        _main_menu(cfg)


def _boot() -> dict:
    """启动初始化：配置 → 日志 → 清理 → 主题 → i18n → 预检 → 后台线程"""
    cfg = ensure_config()

    import core.logger as _logger
    _logger.init(cfg.get("log", {}).get("level", "WARNING"))

    import core.cleanup as _cleanup
    _cleanup.init()

    theme_init()
    i18n_init()

    from core.platform import preflight_check
    from core.theme import print_warning
    for issue in preflight_check():
        print_warning(str(issue.message))

    # ── 启动时 pending 检测（上次下载但未替换的更新）──
    try:
        from core.updater import check_and_apply_pending, pending_version
        if check_and_apply_pending():
            ver = pending_version()
            ver_str = f"v{ver}" if ver else ""
            print_info(t("update.applying", version=ver_str))
            # 打包模式：exec 重启到新版本，开发模式跳过
            import os
            if getattr(sys, "frozen", False) or "__compiled__" in globals():
                new_argv = [a for a in sys.argv if a != "--post-update"] + ["--post-update"]
                os.execv(sys.executable, [sys.executable] + new_argv)
    except Exception:
        pass

    # ── 后台线程 1：源管理层初始化（IP 检测 + 测速）──
    import threading
    def _mirror_init_worker():
        try:
            from core.mirror import init as mirror_init
            mirror_init()
        except Exception:
            pass
    threading.Thread(target=_mirror_init_worker, daemon=True, name="opskit-mirror").start()

    # ── 后台线程 2：版本缓存刷新（等待源管理层就绪后开始）──
    def _version_cache_worker():
        try:
            from core.version_cache import refresh_all_background
            refresh_all_background()
        except Exception:
            pass
    threading.Thread(target=_version_cache_worker, daemon=True, name="opskit-vcache").start()

    # ── 后台线程 3：OpsKit 自更新检测（下载新版本到 pending）──
    try:
        from core.updater import check_update_background
        check_update_background(cfg)
    except Exception:
        pass

    # ── 遥测初始化（错误上报，DSN 为空则静默）──
    try:
        import core.telemetry as _telemetry
        _telemetry.init(cfg)
    except Exception:
        pass

    return cfg


def _main_menu(cfg: dict) -> None:
    """主菜单循环"""
    modules = discover_modules(cfg)

    while True:
        choices = [
            {
                "key": str(i + 1),
                "label": f"{get_icon(m.key)} {t(f'menu.{m.key}')}",
            }
            for i, m in enumerate(modules)
        ]
        pref_key = str(len(modules) + 1)
        choices.append({
            "key": pref_key,
            "label": f"{get_icon('preferences')} {t('menu.preferences')}",
        })

        try:
            key = select(
                breadcrumb=["OpsKit"],
                subtitle=t("prompt.select"),
                choices=choices,
                theme_key="root",
                back_label=f"{get_icon('exit')} {t('menu.exit')}",
            )
        except UserCancel:
            _on_exit(cfg)
            break

        if key is None:
            _on_exit(cfg)
            break

        if key == pref_key:
            _handle_preferences(cfg)
            modules = discover_modules(cfg)
            continue

        idx = int(key) - 1
        if 0 <= idx < len(modules):
            try:
                modules[idx].entry()
            except (KeyboardInterrupt, UserCancel):
                pass
            except Exception as _e:
                try:
                    import core.telemetry as _tel
                    _tel.capture_error(_e, action="module.entry", software=modules[idx].key)
                except Exception:
                    pass
                from core.theme import print_error as _pe
                _pe(t("error.unknown", error=str(_e)))
                from core.prompt import pause as _pause
                _pause()


def _handle_preferences(cfg: dict) -> None:
    """偏好设置子菜单"""
    while True:
        pref_choices = [
            {"key": "1", "label": f"{get_icon('theme')} {t('menu.theme')}"},
            {"key": "2", "label": f"{get_icon('language')} {t('menu.language')}"},
        ]
        try:
            key = select(
                breadcrumb=["OpsKit", t("menu.preferences")],
                subtitle=t("prompt.select"),
                choices=pref_choices,
                theme_key="root",
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            break
        if key is None:
            break
        elif key == "1":
            _handle_theme(cfg)
        elif key == "2":
            _handle_language(cfg)


def _handle_theme(cfg: dict) -> None:
    """主题切换子菜单"""
    themes = list_themes()
    choices = [{"key": str(i + 1), "label": name} for i, name in enumerate(themes)]

    try:
        key = select(
            breadcrumb=["OpsKit", t("menu.theme")],
            subtitle=t("prompt.select"),
            choices=choices,
            theme_key="root",
            back_label=f"{get_icon('back')} {t('menu.back')}",
        )
    except UserCancel:
        return
    if key:
        idx = int(key) - 1
        if 0 <= idx < len(themes):
            switch_theme(themes[idx])


def _handle_language(cfg: dict) -> None:
    """语言切换子菜单"""
    lang_choices = [
        {"key": "1", "label": t("language.zh")},
        {"key": "2", "label": t("language.en")},
        {"key": "3", "label": t("language.auto")},
    ]
    lang_map = {"1": "zh", "2": "en", "3": "auto"}

    try:
        key = select(
            breadcrumb=["OpsKit", t("menu.language")],
            subtitle=t("language.select"),
            choices=lang_choices,
            theme_key="root",
            back_label=f"{get_icon('back')} {t('menu.back')}",
        )
    except UserCancel:
        return
    if key and key in lang_map:
        i18n_switch(lang_map[key])


def _on_exit(cfg: dict) -> None:
    """退出前检查是否有待应用的更新"""
    try:
        from core.updater import apply_pending_update
        apply_pending_update()
    except Exception:
        pass


# ─── 子命令组 ─────────────────────────────────────────────────────────────────

sw_app = typer.Typer(help="软件管理 / Software management")
mon_app = typer.Typer(help="系统监控 / System monitor")
net_app = typer.Typer(help="网络工具 / Network tools")
app.add_typer(sw_app, name="software")
app.add_typer(mon_app, name="monitor")
app.add_typer(net_app, name="network")


# ─── software 子命令 ──────────────────────────────────────────────────────────

@sw_app.command("list")
def sw_list() -> None:
    """显示所有可用软件及安装状态"""
    _boot()
    from software.menu import show_list
    show_list()


@sw_app.command("search")
def sw_search() -> None:
    """搜索软件"""
    _boot()
    from software.menu import show_search
    show_search()


@sw_app.command("installed")
def sw_installed() -> None:
    """已安装软件列表"""
    _boot()
    from software.menu import show_installed
    show_installed()


@sw_app.command("install")
def sw_install(
    name: str = typer.Argument(None, help="软件名 (docker/nginx/mysql/redis/wireguard/wg_client/wg_server/...)"),
    token: str = typer.Option("", "--token", "-t", help="WireGuard 客户端连接令牌（跳过交互输入）"),
    version: str = typer.Option("", "--version", help="指定安装版本（跳过版本选择器）"),
) -> None:
    """安装软件（交互式或指定名称）"""
    _boot()
    if name:
        _sw_action_by_name(name, "install", token=token or None, version=version or None)
    else:
        from software.menu import show_install
        show_install()


@sw_app.command("uninstall")
def sw_uninstall(
    name: str = typer.Argument(None, help="软件名"),
) -> None:
    """卸载软件（交互式或指定名称）"""
    _boot()
    if name:
        _sw_action_by_name(name, "uninstall")
    else:
        from software.menu import show_uninstall
        show_uninstall()


@sw_app.command("upgrade")
def sw_upgrade(
    name: str = typer.Argument(None, help="软件名"),
) -> None:
    """升级软件（交互式或指定名称）"""
    _boot()
    if name:
        _sw_action_by_name(name, "upgrade")
    else:
        from software.menu import show_upgrade
        show_upgrade()


@sw_app.command("diagnose")
def sw_diagnose(
    name: str = typer.Argument(..., help="软件名 (wg_server/wg_client)"),
) -> None:
    """运行软件诊断"""
    _boot()
    _sw_action_by_name(name, "diagnose")


@sw_app.command("manage")
def sw_manage(
    name: str = typer.Argument(..., help="软件名 (wg_server/wg_client)"),
) -> None:
    """进入软件管理界面"""
    _boot()
    _sw_action_by_name(name, "manage")


def _sw_action_by_name(
    name: str,
    action: str,
    token: str | None = None,
    version: str | None = None,
) -> None:
    """按软件名执行指定操作

    Args:
        name: 软件 key
        action: 操作类型 (install/uninstall/upgrade/diagnose/manage)
        token: WireGuard 客户端令牌（非交互模式）
        version: 指定安装版本（非交互模式）
    """
    from software.registry import get as get_recipe
    from software.menu import (
        _do_install, _do_uninstall, _do_upgrade,
        _do_diagnose, _do_manage, _show_submenu, show_actions,
    )
    cls = get_recipe(name)
    if cls is None:
        from core.theme import print_error
        print_error(f"未找到软件: {name}")
        raise typer.Exit(1)
    instance = cls()
    breadcrumb = ["OpsKit", t("menu.software")]
    if action == "install":
        # WireGuard 客户端：--token 直达安装
        if name == "wg_client" and token:
            from wireguard.client import install_client
            install_client(token=token)
            return
        # 多版本软件：--version 直达安装
        if version and not getattr(cls, "has_wizard", False):
            _do_install_direct(breadcrumb, cls, instance, version)
            return
        if getattr(cls, "has_submenu", False):
            _show_submenu(breadcrumb=breadcrumb, cls=cls)
        else:
            _do_install(breadcrumb, cls, instance)
    elif action == "uninstall":
        _do_uninstall(breadcrumb, cls, instance)
    elif action == "upgrade":
        _do_upgrade(breadcrumb, cls, instance)
    elif action == "diagnose":
        if getattr(cls, "has_diagnose", False):
            _do_diagnose(breadcrumb, cls, instance)
        else:
            from core.theme import print_warning
            print_warning(t("software.not_supported"))
    elif action == "manage":
        if getattr(cls, "has_manage", False):
            _do_manage(breadcrumb, cls, instance)
        else:
            from core.theme import print_warning
            print_warning(t("software.not_supported"))


def _do_install_direct(
    breadcrumb: list[str], cls: type, instance, version: str,
) -> None:
    """非交互式直接安装指定版本（跳过版本选择器和确认弹窗）"""
    from software.base import InstallError
    from software.resolver import resolve_deps
    from core.platform import check_disk_space
    from core.constants import MIN_DISK_FREE_BYTES
    from core.theme import print_error, print_success, print_header
    from core.prompt import clear_screen
    from rich.console import Console as _Con

    _name = t(f"software.{cls.key}") if f"software.{cls.key}" else cls.description

    try:
        resolve_deps(instance, breadcrumb)
    except (InstallError, Exception) as e:
        print_error(str(e))
        return

    if not check_disk_space(MIN_DISK_FREE_BYTES):
        print_error(t("error.disk_space", required=f"{MIN_DISK_FREE_BYTES // 1024 // 1024}MB", free=""))
        return

    clear_screen()
    print_header([*breadcrumb, t("software.install")])
    _Con().print()
    import time as _time
    _t0 = _time.monotonic()
    try:
        instance.install(version)
        print_success(t("install.success", name=_name, version=version, elapsed=_time.monotonic() - _t0))
    except InstallError as e:
        print_error(t("install.failed", name=_name, error=str(e)))
    except Exception as e:
        print_error(t("error.unknown", error=str(e)))


# ─── monitor 子命令 ───────────────────────────────────────────────────────────

@mon_app.command("dashboard")
def mon_dashboard() -> None:
    """实时概览仪表盘"""
    _boot()
    from monitor.menu import show_dashboard
    show_dashboard()


@mon_app.command("cpu")
def mon_cpu() -> None:
    """CPU 详情"""
    _boot()
    from monitor.menu import show_cpu_detail
    show_cpu_detail()


@mon_app.command("memory")
def mon_memory() -> None:
    """内存详情"""
    _boot()
    from monitor.menu import show_memory_detail
    show_memory_detail()


@mon_app.command("disk")
def mon_disk() -> None:
    """磁盘详情"""
    _boot()
    from monitor.menu import show_disk_detail
    show_disk_detail()


@mon_app.command("network")
def mon_network() -> None:
    """网络流量"""
    _boot()
    from monitor.menu import show_network_detail
    show_network_detail()


@mon_app.command("processes")
def mon_processes() -> None:
    """进程列表"""
    _boot()
    from monitor.menu import show_processes
    show_processes()


# ─── network 子命令 ───────────────────────────────────────────────────────────

@net_app.command("ping")
def net_ping(
    host: str = typer.Argument(None, help="目标主机名或 IP（不填则交互输入）"),
) -> None:
    """Ping 测试"""
    _boot()
    from network.menu import show_ping
    show_ping(host=host)


@net_app.command("traceroute")
def net_traceroute(
    host: str = typer.Argument(None, help="目标主机名或 IP（不填则交互输入）"),
) -> None:
    """路由追踪"""
    _boot()
    from network.menu import show_traceroute
    show_traceroute(host=host)


@net_app.command("dns")
def net_dns(
    host: str = typer.Argument(None, help="域名或 IP（不填则交互输入）"),
) -> None:
    """DNS 查询"""
    _boot()
    from network.menu import show_dns
    show_dns(host=host)


@net_app.command("port-scan")
def net_port_scan(
    host: str = typer.Argument(None, help="目标主机名或 IP（不填则交互输入）"),
) -> None:
    """端口扫描"""
    _boot()
    from network.menu import show_port_scan
    show_port_scan(host=host)


@net_app.command("speed-test")
def net_speed_test() -> None:
    """下载测速"""
    _boot()
    from network.menu import show_speed_test
    show_speed_test()


@net_app.command("public-ip")
def net_public_ip() -> None:
    """公网 IP 查询"""
    _boot()
    from network.menu import show_public_ip
    show_public_ip()


if __name__ == "__main__":
    try:
        app()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as _fatal:
        try:
            import core.telemetry as _tel_fallback
            _tel_fallback.capture_error(_fatal, action="top_level_crash")
        except Exception:
            pass
        raise
