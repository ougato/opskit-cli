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
if __name__ == "__main__" and sys.platform != "win32" and not _is_packed:
    from pathlib import Path as _Path
    from core.venv_bootstrap import ensure_venv as _ensure_venv
    _ensure_venv(_Path(__file__).parent.resolve())

import typer
import click

from core.config import ensure_config
from core.i18n import init as i18n_init, t, switch as i18n_switch
from core.theme import init as theme_init, get_icon, switch_theme, list_themes, print_info
from core.loader import discover_modules
from core.prompt import select, console, UserCancel, set_auto_yes


# ── 预启动 locale 加载器（typer 帮助文本在 import 时求值，早于 _boot）────────
def _preboot_locale() -> dict[str, str]:
    """轻量级加载 cli.* 本地化文案，不依赖 _boot()"""
    from core.config import load_config
    from core.i18n import _load_lang

    lang = "auto"
    try:
        lang = load_config().get("language", "auto")
    except (OSError, ValueError):
        pass
    if lang not in ("zh", "en"):
        from core.i18n import _detect_system_lang
        lang = _detect_system_lang()

    data = _load_lang(lang)
    cli_section = data.get("cli", {})
    return {k: str(v) for k, v in cli_section.items()}


_CLI = _preboot_locale()


def _ct(key: str) -> str:
    """读取 cli.* 本地化文案"""
    return _CLI.get(key, key)


# ── 覆盖 typer/click 内置 --help 文本 ────────────────────────────────────────
_HELP_TEXT = _ct("help_text")

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

# 猴子补丁：覆盖 click 内置 --help 的 "Show this message and exit." 文本
import click.decorators as _click_dec
_click_orig_gettext = getattr(_click_dec, "_", str)

def _click_i18n_gettext(msg: str) -> str:
    if msg == "Show this message and exit.":
        return _HELP_TEXT
    return _click_orig_gettext(msg)

_click_dec._ = _click_i18n_gettext

# 同时覆盖 click.core 中的内置标签
import click.core as _click_core
_click_core_orig_gettext = getattr(_click_core, "_", str)

_CLICK_LABEL_MAP = {
    "Show this message and exit.": _HELP_TEXT,
    "required": _ct("label_required"),
    "default": _ct("label_default"),
    "Options": _ct("label_options"),
    "Commands": _ct("label_commands"),
    "Arguments": _ct("label_arguments"),
    "Usage": _ct("label_usage").rstrip(":"),
    "Usage:": _ct("label_usage"),
}

def _click_core_i18n_gettext(msg: str) -> str:
    return _CLICK_LABEL_MAP.get(msg, _click_core_orig_gettext(msg))

_click_core._ = _click_core_i18n_gettext

# 覆盖 typer rich 渲染中的英文标签
import typer.rich_utils as _typer_ru
_typer_ru.REQUIRED_LONG_STRING = f"[{_ct('label_required')}]"
_typer_ru.OPTIONS_PANEL_TITLE = _ct("label_options")
_typer_ru.COMMANDS_PANEL_TITLE = _ct("label_commands")
_typer_ru.ARGUMENTS_PANEL_TITLE = _ct("label_arguments")
_typer_ru.STYLE_USAGE_COMMAND = "bold"
_typer_ru_orig_gettext = getattr(_typer_ru, "_", str)

_TYPER_LABEL_MAP = {
    "[required]": f"[{_ct('label_required')}]",
    "[default: {default}]": f"[{_ct('label_default')}: {{default}}]",
    "Usage": _ct("label_usage").rstrip(":"),
    "Arguments": _ct("label_arguments"),
    "Options": _ct("label_options"),
    "Commands": _ct("label_commands"),
    "Show this message and exit.": _HELP_TEXT,
}

def _typer_ru_i18n_gettext(msg: str) -> str:
    return _TYPER_LABEL_MAP.get(msg, _typer_ru_orig_gettext(msg))

_typer_ru._ = _typer_ru_i18n_gettext

# 覆盖 click HelpFormatter.write_usage 的 "Usage:" 前缀
_orig_write_usage = click.HelpFormatter.write_usage

def _patched_write_usage(self, prog, args="", prefix=None):
    if prefix is None:
        prefix = _CLICK_LABEL_MAP.get("Usage:", "Usage:") + " "
    return _orig_write_usage(self, prog, args, prefix=prefix)

click.HelpFormatter.write_usage = _patched_write_usage

# 修改 typer rich highlighter regex 使其匹配本地化的 "用法:"
_new_highlights = []
for _h in _typer_ru.OptionHighlighter.highlights:
    _new_highlights.append(_h.replace("Usage: ", _CLICK_LABEL_MAP.get("Usage:", "Usage:") + " "))
_typer_ru.OptionHighlighter.highlights = _new_highlights

# 帮助输出统一小写：选项/参数的类型占位符（TEXT / INTEGER 等）一律小写，
# 避免大写字母在中文帮助里突兀。typer 的 _print_options_panel 在两处渲染类型：
# 选项走 make_metavar() → self.type.name.upper()，参数/兜底走 param.type.name.upper()，
# 都是对 click 类型名（本就是小写的 text / integer …）再 .upper()。
# 这里把这些类型名换成一个 .upper() 返回小写的字符串子类，两条路径同时生效。
# 唯独 boolean 保持原样：typer 靠 name.upper() == "BOOLEAN" 来隐藏布尔开关的类型列。
# 注意：typer 0.26 内置了自己的 click（typer._click），实际参数类型来自那里，
# 因此两套 ParamType 层级都要打补丁。
import click.types as _click_types


class _LowerName(str):
    """click 类型名：显示/比较同普通字符串，但 .upper() 返回小写，供帮助渲染用。"""

    def upper(self) -> str:  # noqa: D401
        return self.lower()


def _lowercase_type_names(root: type) -> None:
    for sub in root.__subclasses__():
        name = getattr(sub, "name", None)
        if isinstance(name, str) and not isinstance(name, _LowerName) and name != "boolean":
            sub.name = _LowerName(name)
        _lowercase_type_names(sub)


_lowercase_type_names(_click_types.ParamType)
try:
    import typer._click.types as _typer_click_types

    _lowercase_type_names(_typer_click_types.ParamType)
except Exception:
    pass

# usage 行的占位符（[OPTIONS] / COMMAND [ARGS]... / [NAME]）同样改小写。
# 片段分散在几处 collect_usage_pieces：typer._click.core.Command（选项 + 参数），
# typer.core.TyperGroup（额外的 COMMAND [ARGS]... 子命令占位符）。逐个包一层小写。
def _install_lower_usage(cls) -> None:
    if "collect_usage_pieces" not in cls.__dict__:
        return
    _orig = cls.collect_usage_pieces

    def _patched(self, ctx, _orig=_orig):
        return [piece.lower() for piece in _orig(self, ctx)]

    cls.collect_usage_pieces = _patched


try:
    import typer._click.core as _typer_click_core
    import typer.core as _typer_core

    _install_lower_usage(_typer_click_core.Command)
    _install_lower_usage(_typer_core.TyperGroup)
    _install_lower_usage(_typer_core.TyperCommand)
except Exception:
    pass


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
    output: str = typer.Option("text", "--output", help="Output format: text or json"),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Reject prompts and menus"),
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
        cfg = _boot()
        if theme:
            switch_theme(theme)
        if lang:
            i18n_switch(lang)
        _main_menu(cfg)
    else:
        # Named commands need readable messages, without boot's config/update writes.
        i18n_init()


def _boot() -> dict:
    """启动初始化：配置 → 日志 → 清理 → 主题 → i18n → 预检 → pending 更新检测。

    联网/重型后台任务（测速 / 版本刷新 / 自更新 / 遥测）不在此启动，
    而是延迟到主菜单首屏渲染后由 _start_background_workers() 触发。
    """
    cfg = ensure_config()

    import core.logger as _logger
    _logger.init(cfg.get("log", {}).get("level", "WARNING"))

    import core.cleanup as _cleanup
    _cleanup.init()

    theme_init()
    i18n_init()

    # 启动预检：要紧问题（无包管理器 / 磁盘不足）在进入主菜单前显示并暂停，
    # 保证用户看得清（主菜单渲染会清屏）；「非 root」这类普通用户常态告警
    # 不打断流程，仅写入日志。
    from core.platform import preflight_check
    from core.theme import print_warning
    from core.prompt import pause
    _SILENT_ISSUE_CODES = {"non_root"}
    _shown = False
    for issue in preflight_check():
        if issue.code in _SILENT_ISSUE_CODES:
            _logger.warning("%s（%s）", issue.message, issue.suggestion)
            continue
        print_warning(str(issue.message))
        if issue.suggestion:
            console.print(f"  [dim]↳ {issue.suggestion}[/dim]")
        _shown = True
    if _shown:
        pause()

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

    return cfg


_bg_started = False


def _start_background_workers(cfg: dict) -> None:
    """启动所有联网/重型后台任务（测速 / 版本刷新 / 自更新 / 遥测）。

    这些任务全部延迟到主菜单首屏渲染完成后再启动：先把菜单画出来，
    再让网络探测和重型 import 在后台进行，避免它们在单核 + 慢网下与
    主线程抢占 CPU/GIL，拖慢"进入主界面"的时间。
    """
    global _bg_started
    if _bg_started:
        return
    _bg_started = True

    import threading

    # ── 后台线程 1：源管理层初始化（IP 检测 + 测速）──
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

    # ── 后台线程 4：遥测初始化（错误上报，DSN 为空则静默）──
    # 必须放后台线程：SentryProvider.init() 会同步探测 github 可达性
    # （httpx.head 带数秒超时），放主线程会卡住菜单交互。
    def _telemetry_worker():
        try:
            import core.telemetry as _telemetry
            _telemetry.init(cfg)
        except Exception:
            pass
    threading.Thread(target=_telemetry_worker, daemon=True, name="opskit-telemetry").start()

    # ── 后台线程 5：健康确认（平稳运行一段时间后确认新版本健康，否则下次启动回滚）──
    def _health_confirm_worker():
        try:
            from core.constants import HEALTH_CONFIRM_DELAY
            from core.updater import confirm_health_after
            confirm_health_after(HEALTH_CONFIRM_DELAY)
        except Exception:
            pass
    threading.Thread(target=_health_confirm_worker, daemon=True, name="opskit-health").start()


def _main_menu(cfg: dict) -> None:
    """主菜单循环"""
    modules = discover_modules(cfg)

    # 首屏菜单渲染完成后再启动联网/重型后台任务（测速 / 版本刷新 / 自更新 /
    # 遥测），保证"进入主界面"接近瞬时；只在第一次循环触发一次。
    _first_render = True

    while True:
        choices = [
            {
                "key": str(i + 1),
                "label": f"{m.icon or get_icon(m.key)} {m.label or t(f'menu.{m.key}')}",
            }
            for i, m in enumerate(modules)
        ]
        pref_key = str(len(modules) + 1)
        choices.append({
            "key": pref_key,
            "label": f"{get_icon('preferences')} {t('menu.preferences')}",
        })

        _on_ready = (lambda: _start_background_workers(cfg)) if _first_render else None
        _first_render = False

        try:
            key = select(
                breadcrumb=["OpsKit"],
                subtitle=t("prompt.select"),
                choices=choices,
                theme_key="root",
                back_label=f"{get_icon('exit')} {t('menu.exit')}",
                on_ready=_on_ready,
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
        from core.updater import apply_pending_update, has_pending_update, pending_version
        if has_pending_update():
            ver = pending_version()
            print_info(t("update.pending_ready", version=f"v{ver}" if ver else ""))
        apply_pending_update()
    except Exception:
        pass


# ─── 子命令组 ─────────────────────────────────────────────────────────────────

sw_app = typer.Typer(help=_ct("software"), context_settings=_CONTEXT_SETTINGS)
mon_app = typer.Typer(help=_ct("monitor"), context_settings=_CONTEXT_SETTINGS)
net_app = typer.Typer(help=_ct("network"), context_settings=_CONTEXT_SETTINGS)
plugin_app = typer.Typer(help=_ct("plugin"), context_settings=_CONTEXT_SETTINGS)
app.add_typer(sw_app, name="software")
app.add_typer(mon_app, name="monitor")
app.add_typer(net_app, name="network")
app.add_typer(plugin_app, name="plugin")
from plugin.lifecycle_cli import register as register_plugin_lifecycle
register_plugin_lifecycle(plugin_app)


@plugin_app.command("run", context_settings={"allow_extra_args": True, "ignore_unknown_options": True, "help_option_names": [], "token_normalize_func": None}, help="Run a plugin's own CLI: plugin run NAME -- ARGS")
def plugin_run(ctx: typer.Context, name: str = typer.Argument(...)) -> None:
    from plugin.cli import run_plugin
    from core.plugin import PluginInventoryError
    from core.plugin_sandbox import SandboxError
    try:
        code = run_plugin(name, list(ctx.args))
    except (SandboxError, PluginInventoryError) as exc:
        _direct_fail(str(exc), _EXIT_RUNTIME)
        return
    except KeyboardInterrupt:
        raise typer.Exit(130)
    raise typer.Exit(code)


# ─── software 子命令 ──────────────────────────────────────────────────────────

@sw_app.command("list", help=_ct("sw_list"))
def sw_list(category: str = typer.Option("", "--category"), platform: str = typer.Option("", "--platform")) -> None:
    if _software_inventory(category=category, platform=platform):
        return
    _boot()
    _print_software_table()


@sw_app.command("search", help=_ct("sw_search"))
def sw_search(
    query: str = typer.Argument(None, help=_ct("sw_query")),
    category: str = typer.Option("", "--category"),
    platform: str = typer.Option("", "--platform"),
) -> None:
    from core.cli import require_interaction
    if query is None:
        require_interaction()
    elif _software_inventory(query=query, category=category, platform=platform):
        return
    _boot()
    if query:
        _print_software_table(query=query)
    else:
        from software.menu import show_search
        show_search()


@sw_app.command("installed", help=_ct("sw_installed"))
def sw_installed() -> None:
    if _software_inventory(installed_only=True):
        return
    _boot()
    _print_software_table(installed_only=True, include_hidden=True)


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
        _boot()
    cls, instance = _get_recipe_for_direct(name)
    if getattr(cls, "has_submenu", False):
        _direct_fail(t("cli.error.submenu_recipe", name=name), _EXIT_USAGE)
    try:
        versions = instance.versions()
    except Exception as e:
        _direct_fail(t("error.unknown", error=str(e)), _EXIT_RUNTIME)
    if not versions:
        _direct_fail(t("software.no_versions"), _EXIT_RUNTIME)
    if not getattr(cls, "has_install_version_selection", True):
        if output_mode.get() == "json":
            emit({"name": cls.key, "version_selection": False, "versions": [], "source": "system_or_managed"})
            return
        console.print(t("cli.system_package_version", name=_recipe_name(cls)))
        return
    if output_mode.get() == "json":
        emit({"name": cls.key, "version_selection": True, "versions": versions})
        return
    for item in versions:
        console.print(item)


from core.cli import guarded


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
        cls, instance = _get_recipe_for_direct(name)
        _check_disk_or_exit()
        _resolve_deps_or_exit(instance, ["OpsKit", "software"])
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
        _sw_action_by_name(name, "install", token=token or None, version=version or None)
    else:
        from core.cli import require_interaction
        require_interaction()
        _boot()
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
        _sw_action_by_name(name, "uninstall", version=version or None, all_versions=all_versions)
    else:
        from core.cli import require_interaction
        require_interaction()
        _boot()
        from software.menu import show_uninstall
        show_uninstall()


@sw_app.command("upgrade", help=_ct("sw_upgrade"))
@guarded
def sw_upgrade(
    name: str = typer.Argument(None, help=_ct("sw_name")),
    version: str = typer.Option("", "--version", "-v", help=_ct("sw_version")),
) -> None:
    if name:
        _sw_action_by_name(name, "upgrade", version=version or None)
    else:
        from core.cli import require_interaction
        require_interaction()
        _boot()
        from software.menu import show_upgrade
        show_upgrade()


@sw_app.command("switch", help=_ct("sw_switch"))
@guarded
def sw_switch(
    name: str = typer.Argument(..., help=_ct("sw_name")),
    version: str = typer.Option(..., "--version", "-v", help=_ct("sw_version")),
) -> None:
    _sw_action_by_name(name, "switch", version=version)


@sw_app.command("diagnose", help=_ct("sw_diagnose"))
@guarded
def sw_diagnose(
    name: str = typer.Argument(..., help=_ct("sw_name_diagnose")),
    timeout: float = typer.Option(30, "--timeout", min=0.1, max=3600),
) -> None:
    _sw_action_by_name(name, "diagnose", timeout=timeout)


@sw_app.command("manage", help=_ct("sw_manage"))
@guarded
def sw_manage(
    name: str = typer.Argument(..., help=_ct("sw_name_diagnose")),
) -> None:
    from core.cli import require_interaction
    require_interaction()
    _boot()
    _sw_action_by_name(name, "manage")


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
    cls, instance = _get_recipe_for_direct(name)
    breadcrumb = ["OpsKit", t("menu.software")]
    if action == "install":
        _install_direct(breadcrumb, cls, instance, version=version, token=token)
    elif action == "uninstall":
        _uninstall_direct(cls, instance, version=version, all_versions=all_versions)
    elif action == "upgrade":
        _upgrade_direct(breadcrumb, cls, instance, version=version)
    elif action == "switch":
        _switch_direct(cls, instance, version=version)
    elif action == "diagnose":
        if getattr(cls, "has_diagnose", False):
            _diagnose_direct(cls, instance, timeout=timeout)
        else:
            _direct_fail(t("software.not_supported"), _EXIT_USAGE)
    elif action == "manage":
        if getattr(cls, "has_manage", False):
            from software.menu import _do_manage
            _do_manage(breadcrumb, cls, instance)
        else:
            _direct_fail(t("software.not_supported"), _EXIT_USAGE)


_EXIT_RUNTIME = 1
_EXIT_USAGE = 2


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
        _direct_fail(t("cli.error.software_not_found", name=name), _EXIT_USAGE)
    info = get_platform()
    if info.os_type not in getattr(cls, "platforms", []):
        _direct_fail(t("cli.error.platform_not_supported", name=name, platform=info.os_type), _EXIT_USAGE)
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
        name = _recipe_name(cls)
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
        console.print(t("software.none_installed") if installed_only else t("software.search_no_result", keyword=query or ""))
        return

    muted = get_color("muted")
    success = get_color("success")
    table = Table(title=t("software.list"), box=rich_box.ROUNDED)
    table.add_column("key")
    table.add_column(t("software.name"))
    table.add_column(t("software.status"))
    table.add_column(t("software.version"))
    table.add_column(t("software.platforms"))
    for cls, version in rows:
        status = f"[{success}]{t('software.installed')}[/{success}]" if version else f"[{muted}]{t('software.not_installed')}[/{muted}]"
        table.add_row(
            cls.key,
            f"{get_icon(cls.key)} {_recipe_name(cls)}",
            status,
            version or "-",
            " / ".join(getattr(cls, "platforms", [])),
        )
    console.print(table)


from software.lifecycle_cli import (
    _resolve_deps_or_exit, _check_disk_or_exit,
    _install_direct, _uninstall_direct, _upgrade_direct, _switch_direct,
)


def _diagnose_direct(cls: type, instance, *, timeout=30) -> None:
    from core.cli import emit_diagnostics
    emit_diagnostics(instance.diagnostic_report(timeout=timeout))


from monitor.cli import register as register_monitor_cli
register_monitor_cli(mon_app)
from software.query import register as register_software_query
register_software_query(sw_app)
from core.preferences_cli import register as register_preferences_cli
register_preferences_cli(app, _CONTEXT_SETTINGS)
from tailscale.cli import register as register_tailscale_cli
register_tailscale_cli(app, _CONTEXT_SETTINGS)
from software.recipes.ohmyzsh.cli import register as register_ohmyzsh_cli
register_ohmyzsh_cli(app, _CONTEXT_SETTINGS)
from xui.cli import register as register_xui_cli
register_xui_cli(app, _CONTEXT_SETTINGS)
from wireguard.cli import register as register_wireguard_cli
register_wireguard_cli(app, _CONTEXT_SETTINGS)


from network.cli import register as register_network_cli
register_network_cli(net_app)
from core.update_cli import register as register_update_cli
register_update_cli(app, _CONTEXT_SETTINGS)


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
