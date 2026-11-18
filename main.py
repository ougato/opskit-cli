"""OpsKit 入口 — 初始化框架层，渲染主菜单，分发到各插件模块"""
from __future__ import annotations

import sys

if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()

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
    # The installed console script imports main:app rather than executing main.py.
    from core.console_bootstrap import configure_standard_streams as _configure_standard_streams
    _configure_standard_streams()

# ── venv 自举：必须在所有第三方 import 之前，确保后续 import 使用 venv 内的包 ──
# 打包模式（Nuitka __compiled__ / PyInstaller sys.frozen）跳过 venv bootstrap
_is_packed = getattr(sys, "frozen", False) or "__compiled__" in dir()
if __name__ == "__main__" and sys.platform == "win32" and not _is_packed:
    from pathlib import Path as _Path
    from core.venv_bootstrap import reenter_existing_windows_venv as _reenter_windows_venv
    _reenter_windows_venv(_Path(__file__).parent.resolve())
if __name__ == "__main__" and sys.platform != "win32" and not _is_packed:
    from pathlib import Path as _Path
    from core.venv_bootstrap import ensure_venv as _ensure_venv
    _ensure_venv(_Path(__file__).parent.resolve())
if __name__ == "__main__" and sys.platform == "win32" and not _is_packed:
    from core.worker_bootstrap import configure_main_spawn as _configure_main_spawn
    _configure_main_spawn()

import typer
import click
try:
    from typer._click.globals import get_current_context as _typer_current_context
    from typer._click.exceptions import ClickException as _TyperClickException
except ImportError:
    from click import get_current_context as _typer_current_context  # noqa: F401 - Typer fallback
    from click import ClickException as _TyperClickException

if _TyperClickException is click.ClickException:
    _StartupClickException = click.ClickException
else:
    class _StartupClickException(_TyperClickException, click.ClickException):
        """A startup error recognized by both Typer and Click callers."""

from core.config import ensure_config as ensure_config
from core.i18n import init as i18n_init, t as t, switch as i18n_switch
from core.theme import init as theme_init, get_icon as get_icon, switch_theme, list_themes as list_themes, print_info as print_info
from core.loader import discover_modules as discover_modules
from core.prompt import select as select, console, UserCancel as UserCancel, UserExit as UserExit, set_auto_yes


# ── 预启动 locale 加载器（typer 帮助文本在 import 时求值，早于 _boot）────────
def _preboot_locale(arguments=()) -> dict[str, str]:
    """Load help translations before Typer evaluates the root callback."""
    from core.config import load_config
    from core.i18n import _flatten, _load_lang

    # Only inspect root options. A plugin's own --lang must not change host help.
    lang = None
    index = 0
    while index < len(arguments):
        flag = arguments[index].lower()
        if not flag.startswith("-") or flag == "--":
            break
        if flag.startswith("--lang="):
            lang = arguments[index].split("=", 1)[1]
        elif flag in ("--lang", "-l"):
            index += 1
            if index < len(arguments):
                lang = arguments[index]
        elif flag.startswith("-l") and len(flag) > 2:
            lang = arguments[index][2:]
        elif flag in ("--theme", "-t", "--output"):
            index += 1
        index += 1
    if lang not in ("zh", "en"):
        try:
            lang = load_config().get("language", "auto")
        except (OSError, ValueError):
            lang = "auto"
    if lang not in ("zh", "en"):
        from core.i18n import _detect_system_lang
        lang = _detect_system_lang()

    data = _load_lang(lang)
    cli_section = data.get("cli", {})
    result = {k: str(v) for k, v in cli_section.items()}
    result.update({"plugin_native_" + key: value for key, value in
                   _flatten(data.get("plugin_native", {})).items()})
    return result


_CLI = _preboot_locale(sys.argv[1:])


def _ct(key: str) -> str:
    """读取 cli.* 本地化文案"""
    return _CLI.get(key, key)


_CONTEXT_SETTINGS = {
    # -h / --help 均可触发帮助；token_normalize_func 把所有选项/命令 token
    # 归一为小写，使大小写不敏感（-V/-v、-H/-h、software/SOFTWARE 等价）。
    "help_option_names": ["-h", "--help"],
    "token_normalize_func": lambda token: token.lower(),
}

from core.cli import ContractGroup

app = typer.Typer(
    cls=ContractGroup,
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings=_CONTEXT_SETTINGS,
    help=_ct("desc"),
)

from core.cli_help import install as _install_cli_help
_install_cli_help(_CLI)


@app.callback(invoke_without_command=True)
def _app_callback(
    ctx: typer.Context,
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help=_ct("yes_help"),
    ),
    version: bool = typer.Option(False, "--version", "-v", help=_ct("version_help")),
    theme: str = typer.Option("", "--theme", "-t", help=_ct("theme_help")),
    lang: str = typer.Option("", "--lang", "-l", help=_ct("lang_help")),
    output: str = typer.Option("text", "--output", help=_ct("output_help")),
    non_interactive: bool = typer.Option(False, "--non-interactive", help=_ct("non_interactive_help")),
    post_update: bool = typer.Option(False, "--post-update", hidden=True),
) -> None:
    import os
    from core.cli import configure, require_interaction
    configure(output, non_interactive)
    set_auto_yes(yes or os.environ.get("OPSKIT_YES", "").strip().lower() in ("1", "true", "yes"))
    if version:
        from core.constants import APP_VERSION
        from core.cli import emit, output_mode
        if output_mode.get() == "json":
            emit({"version": APP_VERSION})
        else:
            console.print(f"v{APP_VERSION}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        require_interaction()
        cfg = _boot(post_update=True) if post_update else _boot()
        if theme:
            switch_theme(theme)
        if lang:
            i18n_switch(lang)
        _main_menu(cfg)
    else:
        # Named commands need readable messages, without boot's config/update writes.
        i18n_init(language=lang or None)
        theme_init(theme or None)



def _restart_arguments(arguments: list[str]) -> list[str]:
    """Remove duplicate root flags while preserving subcommand arguments."""
    result = []
    index = 0
    while index < len(arguments):
        value = arguments[index]
        flag = value.lower()
        if not flag.startswith("-") or flag == "--":
            result.extend(arguments[index:])
            break
        if flag != "--post-update":
            result.append(value)
        if flag in {"--lang", "-l", "--theme", "-t", "--output"}:
            index += 1
            if index < len(arguments):
                result.append(arguments[index])
        index += 1
    return result


def _boot(*, post_update: bool | None = None) -> dict:
    """Initialize the application using the shared startup orchestration."""
    from core.startup import boot
    return boot(sys.modules[__name__], post_update=post_update,
                compiled="__compiled__" in globals())


import logging as _logging
import threading as _threading

_startup_log = _logging.getLogger("opskit.startup")
_bg_lock = _threading.Lock()
_bg_started: set[str] = set()


def _log_startup_failure(task: str, exc: Exception) -> None:
    # 启动异常可能包含路径或凭据；只记录固定任务名及异常类型。
    try:
        _startup_log.warning("startup task failed: task=%s error=%s", task, type(exc).__name__)
    except Exception:
        # 日志路径不可写不能阻止其他启动任务。
        pass


def _start_background_workers(cfg: dict) -> None:
    """启动所有联网/重型后台任务（测速 / 版本刷新 / 自更新 / 遥测）。

    这些任务全部延迟到主菜单首屏渲染完成后再启动：先把菜单画出来，
    再让网络探测和重型 import 在后台进行，避免它们在单核 + 慢网下与
    主线程抢占 CPU/GIL，拖慢"进入主界面"的时间。
    """
    def mirror():
        from core.mirror import init as mirror_init
        mirror_init()

    def version_cache():
        from core.version_cache import refresh_all_background
        refresh_all_background()

    def update():
        from core.updater import check_update_background
        check_update_background(cfg)

    def telemetry():
        import core.telemetry as _telemetry
        _telemetry.init(cfg)

    def health():
        from core.constants import HEALTH_CONFIRM_DELAY
        from core.updater import confirm_health_after
        confirm_health_after(HEALTH_CONFIRM_DELAY)

    tasks = (("mirror", mirror), ("vcache", version_cache), ("updater", update),
             ("telemetry", telemetry), ("health", health))
    for name, task in tasks:
        with _bg_lock:
            if name in _bg_started:
                continue
            _bg_started.add(name)

        def run(task=task, name=name):
            try:
                task()
            except Exception as exc:
                _log_startup_failure(name, exc)
                with _bg_lock:
                    _bg_started.discard(name)

        try:
            _threading.Thread(target=run, daemon=True, name=f"opskit-{name}").start()
        except Exception as exc:
            with _bg_lock:
                _bg_started.discard(name)
            _log_startup_failure(name, exc)


from core import main_menu as _entry_menu


def _main_menu(cfg: dict) -> None:
    return _entry_menu._main_menu(sys.modules[__name__], cfg)


def _main_menu_loop(cfg: dict) -> None:
    return _entry_menu._main_menu_loop(sys.modules[__name__], cfg)


def _handle_preferences(cfg: dict) -> None:
    return _entry_menu._handle_preferences(sys.modules[__name__], cfg)


def _handle_theme(cfg: dict) -> None:
    return _entry_menu._handle_theme(sys.modules[__name__], cfg)


def _handle_language(cfg: dict) -> None:
    return _entry_menu._handle_language(sys.modules[__name__], cfg)


def _on_exit(cfg: dict) -> None:
    return _entry_menu._on_exit(sys.modules[__name__], cfg)


# ─── 子命令组 ─────────────────────────────────────────────────────────────────

from core.command_registration import create_groups, register_builtin_commands, register_plugin_commands

sw_app, mon_app, net_app, plugin_app = create_groups(app, _CONTEXT_SETTINGS, _ct)
register_plugin_commands(plugin_app, _ct)


@plugin_app.command("run", context_settings={"allow_extra_args": True, "ignore_unknown_options": True, "help_option_names": [], "token_normalize_func": None}, help=_ct("plugin_native_run_help"))
def plugin_run(ctx: typer.Context, name: str = typer.Argument(...)) -> None:
    from plugin.cli import run_plugin
    from core.plugin import PluginInventoryError
    from core.plugin_sandbox import SandboxError
    from core.native_sandbox import NativeSandboxError
    try:
        code = run_plugin(name, list(ctx.args))
    except (SandboxError, PluginInventoryError, NativeSandboxError) as exc:
        _direct_fail(str(exc), _EXIT_RUNTIME)
        return
    except KeyboardInterrupt:
        raise typer.Exit(130)
    raise typer.Exit(code)


# Software CLI implementations are registered without importing this entry module.
from software.lifecycle_cli import (
    _resolve_deps_or_exit as _resolve_deps_or_exit, _check_disk_or_exit as _check_disk_or_exit,
    _install_direct as _install_direct, _uninstall_direct as _uninstall_direct, _upgrade_direct as _upgrade_direct, _switch_direct as _switch_direct,
)
from software.direct_cli import register as _register_software_cli, _EXIT_RUNTIME, _EXIT_USAGE as _EXIT_USAGE

(
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
) = _register_software_cli(sw_app, _ct, sys.modules[__name__])


register_builtin_commands(app, sw_app, mon_app, net_app, _CONTEXT_SETTINGS)


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
