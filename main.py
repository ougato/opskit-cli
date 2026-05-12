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
import click

from core.config import ensure_config
from core.i18n import init as i18n_init, t, switch as i18n_switch
from core.theme import init as theme_init, get_icon, switch_theme, list_themes, print_info
from core.loader import discover_modules
from core.prompt import select, console, UserCancel, set_auto_yes


# ── 预启动 locale 加载器（typer 帮助文本在 import 时求值，早于 _boot）────────
def _preboot_locale() -> dict[str, str]:
    """轻量级加载 cli.* 本地化文案，不依赖 _boot()"""
    from pathlib import Path as _P
    import yaml as _yaml

    from core.config import get_config_path, get_resource_dir
    from core.constants import DIR_LOCALE

    lang = "auto"
    cfg_path = get_config_path()
    if cfg_path.exists():
        try:
            with cfg_path.open("r", encoding="utf-8") as _f:
                _cfg = _yaml.safe_load(_f) or {}
            lang = _cfg.get("language", "auto")
        except Exception:
            pass
    if lang not in ("zh", "en"):
        from core.i18n import _detect_system_lang
        lang = _detect_system_lang()

    locale_path = get_resource_dir(DIR_LOCALE) / f"{lang}.yaml"
    if not locale_path.exists():
        locale_path = get_resource_dir(DIR_LOCALE) / "en.yaml"
    with locale_path.open("r", encoding="utf-8") as _f:
        data = _yaml.safe_load(_f) or {}
    cli_section = data.get("cli", {})
    return {k: str(v) for k, v in cli_section.items()}


_CLI = _preboot_locale()


def _ct(key: str) -> str:
    """读取 cli.* 本地化文案"""
    return _CLI.get(key, key)


# ── 覆盖 typer/click 内置 --help 文本 ────────────────────────────────────────
_HELP_TEXT = _ct("help_text")

_CONTEXT_SETTINGS = {
    "help_option_names": ["--help"],
}

app = typer.Typer(
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


@app.callback(invoke_without_command=True)
def _app_callback(
    ctx: typer.Context,
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help=_ct("yes_help"),
    ),
    version: bool = typer.Option(False, "--version", "-V", help=_ct("version_help")),
    theme: str = typer.Option("", "--theme", help=_ct("theme_help")),
    lang: str = typer.Option("", "--lang", help=_ct("lang_help")),
) -> None:
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

sw_app = typer.Typer(help=_ct("software"))
mon_app = typer.Typer(help=_ct("monitor"))
net_app = typer.Typer(help=_ct("network"))
app.add_typer(sw_app, name="software")
app.add_typer(mon_app, name="monitor")
app.add_typer(net_app, name="network")


# ─── software 子命令 ──────────────────────────────────────────────────────────

@sw_app.command("list", help=_ct("sw_list"))
def sw_list() -> None:
    _boot()
    _print_software_table()


@sw_app.command("search", help=_ct("sw_search"))
def sw_search(
    query: str = typer.Argument(None, help=_ct("sw_query")),
) -> None:
    _boot()
    if query:
        _print_software_table(query=query)
    else:
        from software.menu import show_search
        show_search()


@sw_app.command("installed", help=_ct("sw_installed"))
def sw_installed() -> None:
    _boot()
    _print_software_table(installed_only=True, include_hidden=True)


@sw_app.command("versions", help=_ct("sw_versions"))
def sw_versions(
    name: str = typer.Argument(..., help=_ct("sw_name")),
) -> None:
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
        console.print(t("cli.system_package_version", name=_recipe_name(cls)))
        return
    for item in versions:
        console.print(item)


@sw_app.command("install", help=_ct("sw_install"))
def sw_install(
    name: str = typer.Argument(None, help=_ct("sw_name_install")),
    token: str = typer.Option("", "--token", "-t", help=_ct("sw_token")),
    version: str = typer.Option("", "--version", help=_ct("sw_version")),
) -> None:
    _boot()
    if name:
        _sw_action_by_name(name, "install", token=token or None, version=version or None)
    else:
        from software.menu import show_install
        show_install()


@sw_app.command("uninstall", help=_ct("sw_uninstall"))
def sw_uninstall(
    name: str = typer.Argument(None, help=_ct("sw_name")),
    version: str = typer.Option("", "--version", help=_ct("sw_version")),
    all_versions: bool = typer.Option(False, "--all", help=_ct("sw_all_versions")),
) -> None:
    _boot()
    if name:
        _sw_action_by_name(name, "uninstall", version=version or None, all_versions=all_versions)
    else:
        from software.menu import show_uninstall
        show_uninstall()


@sw_app.command("upgrade", help=_ct("sw_upgrade"))
def sw_upgrade(
    name: str = typer.Argument(None, help=_ct("sw_name")),
    version: str = typer.Option("", "--version", help=_ct("sw_version")),
) -> None:
    _boot()
    if name:
        _sw_action_by_name(name, "upgrade", version=version or None)
    else:
        from software.menu import show_upgrade
        show_upgrade()


@sw_app.command("switch", help=_ct("sw_switch"))
def sw_switch(
    name: str = typer.Argument(..., help=_ct("sw_name")),
    version: str = typer.Option(..., "--version", help=_ct("sw_version")),
) -> None:
    _boot()
    _sw_action_by_name(name, "switch", version=version)


@sw_app.command("diagnose", help=_ct("sw_diagnose"))
def sw_diagnose(
    name: str = typer.Argument(..., help=_ct("sw_name_diagnose")),
) -> None:
    _boot()
    _sw_action_by_name(name, "diagnose")


@sw_app.command("manage", help=_ct("sw_manage"))
def sw_manage(
    name: str = typer.Argument(..., help=_ct("sw_name_diagnose")),
) -> None:
    _boot()
    _sw_action_by_name(name, "manage")


def _sw_action_by_name(
    name: str,
    action: str,
    token: str | None = None,
    version: str | None = None,
    all_versions: bool = False,
) -> None:
    """按软件名执行指定操作

    Args:
        name: 软件 key
        action: 操作类型 (install/uninstall/upgrade/diagnose/manage)
        token: WireGuard 客户端令牌（非交互模式）
        version: 指定安装版本（非交互模式）
    """
    from software.menu import _do_manage

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
            _diagnose_direct(cls, instance)
        else:
            _direct_fail(t("software.not_supported"), _EXIT_USAGE)
    elif action == "manage":
        if getattr(cls, "has_manage", False):
            _do_manage(breadcrumb, cls, instance)
        else:
            _direct_fail(t("software.not_supported"), _EXIT_USAGE)


_EXIT_RUNTIME = 1
_EXIT_USAGE = 2


def _direct_fail(message: str, code: int = _EXIT_RUNTIME) -> None:
    from core.theme import print_error

    print_error(message)
    raise typer.Exit(code)


def _recipe_name(cls: type) -> str:
    key = f"software.{cls.key}"
    value = t(key)
    return value if value != key else (getattr(cls, "description", "") or cls.key)


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


def _is_multi_version(cls: type, instance) -> bool:
    return bool(getattr(cls, "has_switch", False) and hasattr(instance, "installed_versions"))


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


def _resolve_deps_or_exit(instance, breadcrumb: list[str]) -> None:
    from software.base import InstallError
    from software.resolver import resolve_deps

    try:
        resolve_deps(instance, breadcrumb)
    except InstallError as e:
        _direct_fail(str(e), _EXIT_RUNTIME)
    except Exception as e:
        _direct_fail(t("error.unknown", error=str(e)), _EXIT_RUNTIME)


def _check_disk_or_exit() -> None:
    from core.platform import check_disk_space
    from core.constants import MIN_DISK_FREE_BYTES

    if not check_disk_space(MIN_DISK_FREE_BYTES):
        _direct_fail(
            t("error.disk_space", required=f"{MIN_DISK_FREE_BYTES // 1024 // 1024}MB", free=""),
            _EXIT_RUNTIME,
        )


def _install_direct(
    breadcrumb: list[str],
    cls: type,
    instance,
    version: str | None = None,
    token: str | None = None,
) -> None:
    """非交互式安装：参数不足或能力不支持时返回用法错误。"""
    from software.base import InstallError
    from core.theme import print_success

    name = _recipe_name(cls)
    if token and cls.key != "wg_client":
        _direct_fail(t("cli.error.token_only_wg_client"), _EXIT_USAGE)
    if getattr(cls, "has_submenu", False):
        _direct_fail(t("cli.error.submenu_recipe", name=cls.key), _EXIT_USAGE)
    if cls.key == "wg_client" and token:
        _resolve_deps_or_exit(instance, breadcrumb)
        try:
            from wireguard.client import install_client
            install_client(token=token)
            detected = instance.detect() or "installed"
            print_success(t("install.success", name=name, version=detected, elapsed=0))
            return
        except InstallError as e:
            _direct_fail(t("install.failed", name=name, error=str(e)), _EXIT_RUNTIME)
        except Exception as e:
            _direct_fail(t("error.unknown", error=str(e)), _EXIT_RUNTIME)
    if getattr(cls, "has_wizard", False):
        _direct_fail(t("cli.error.wizard_requires_interactive", name=name), _EXIT_USAGE)
    if version and not getattr(cls, "has_install_version_selection", True):
        _direct_fail(t("cli.error.version_not_supported", name=name), _EXIT_USAGE)
    if not version:
        if getattr(cls, "has_install_version_selection", True):
            _direct_fail(t("cli.error.version_required", name=name), _EXIT_USAGE)
        version = "latest"

    _resolve_deps_or_exit(instance, breadcrumb)
    _check_disk_or_exit()

    import time as _time
    start = _time.monotonic()
    try:
        instance.install(version)
        installed_version = instance.detect() or version
        print_success(t("install.success", name=name, version=installed_version, elapsed=_time.monotonic() - start))
    except InstallError as e:
        _direct_fail(t("install.failed", name=name, error=str(e)), _EXIT_RUNTIME)
    except Exception as e:
        _direct_fail(t("error.unknown", error=str(e)), _EXIT_RUNTIME)


def _uninstall_direct(
    cls: type,
    instance,
    version: str | None = None,
    all_versions: bool = False,
) -> None:
    from software.base import UninstallError
    from core.theme import print_success

    name = _recipe_name(cls)
    if getattr(cls, "has_submenu", False):
        _direct_fail(t("cli.error.submenu_recipe", name=cls.key), _EXIT_USAGE)
    if version and all_versions:
        _direct_fail(t("cli.error.version_all_conflict"), _EXIT_USAGE)

    try:
        if _is_multi_version(cls, instance):
            installed = instance.installed_versions()
            if not installed:
                _direct_fail(t("software.not_installed_hint", name=name), _EXIT_RUNTIME)
            if not version and not all_versions:
                _direct_fail(t("cli.error.uninstall_version_required", name=name), _EXIT_USAGE)
            if version and version not in installed:
                _direct_fail(t("software.not_installed_hint", name=f"{name} {version}"), _EXIT_RUNTIME)
            instance.uninstall(None if all_versions else version)
        else:
            if version or all_versions:
                _direct_fail(t("cli.error.version_not_supported", name=name), _EXIT_USAGE)
            if not instance.detect():
                _direct_fail(t("software.not_installed_hint", name=name), _EXIT_RUNTIME)
            instance.uninstall()
        print_success(t("uninstall.success", name=name))
    except UninstallError as e:
        _direct_fail(t("uninstall.failed", name=name, error=str(e)), _EXIT_RUNTIME)
    except typer.Exit:
        raise
    except Exception as e:
        _direct_fail(t("error.unknown", error=str(e)), _EXIT_RUNTIME)


def _upgrade_direct(
    breadcrumb: list[str],
    cls: type,
    instance,
    version: str | None,
) -> None:
    from software.base import InstallError
    from core.theme import print_success

    name = _recipe_name(cls)
    if not getattr(cls, "has_upgrade", True):
        _direct_fail(t("software.not_supported"), _EXIT_USAGE)
    if not version:
        _direct_fail(t("cli.error.version_required", name=name), _EXIT_USAGE)
    if not instance.detect():
        _direct_fail(t("software.not_installed_hint", name=name), _EXIT_RUNTIME)

    _resolve_deps_or_exit(instance, breadcrumb)

    import time as _time
    start = _time.monotonic()
    try:
        if _is_multi_version(cls, instance) and version in instance.installed_versions() and hasattr(instance, "switch"):
            instance.switch(version)
            print_success(t("software.switch_success", name=name, version=version))
        else:
            instance.upgrade(version)
            print_success(t("upgrade.success", name=name, elapsed=_time.monotonic() - start))
    except InstallError as e:
        _direct_fail(t("upgrade.failed", name=name, error=str(e)), _EXIT_RUNTIME)
    except Exception as e:
        _direct_fail(t("error.unknown", error=str(e)), _EXIT_RUNTIME)


def _switch_direct(cls: type, instance, version: str | None) -> None:
    from software.base import InstallError
    from core.theme import print_success

    name = _recipe_name(cls)
    if not getattr(cls, "has_switch", False) or not hasattr(instance, "switch"):
        _direct_fail(t("software.not_supported"), _EXIT_USAGE)
    if not version:
        _direct_fail(t("cli.error.version_required", name=name), _EXIT_USAGE)
    installed = instance.installed_versions() if hasattr(instance, "installed_versions") else []
    if version not in installed:
        _direct_fail(t("software.not_installed_hint", name=f"{name} {version}"), _EXIT_RUNTIME)
    try:
        instance.switch(version)
        print_success(t("software.switch_success", name=name, version=version))
    except InstallError as e:
        _direct_fail(t("install.failed", name=name, error=str(e)), _EXIT_RUNTIME)
    except Exception as e:
        _direct_fail(t("error.unknown", error=str(e)), _EXIT_RUNTIME)


def _diagnose_direct(cls: type, instance) -> None:
    try:
        instance.diagnose()
    except Exception as e:
        _direct_fail(t("error.unknown", error=str(e)), _EXIT_RUNTIME)


# ─── monitor 子命令 ───────────────────────────────────────────────────────────

@mon_app.command("dashboard", help=_ct("mon_dashboard"))
def mon_dashboard() -> None:
    _boot()
    from monitor.menu import show_dashboard
    show_dashboard()


@mon_app.command("cpu", help=_ct("mon_cpu"))
def mon_cpu() -> None:
    _boot()
    from monitor.menu import show_cpu_detail
    show_cpu_detail()


@mon_app.command("memory", help=_ct("mon_memory"))
def mon_memory() -> None:
    _boot()
    from monitor.menu import show_memory_detail
    show_memory_detail()


@mon_app.command("disk", help=_ct("mon_disk"))
def mon_disk() -> None:
    _boot()
    from monitor.menu import show_disk_detail
    show_disk_detail(pause_after=False)


@mon_app.command("network", help=_ct("mon_network"))
def mon_network() -> None:
    _boot()
    from monitor.menu import show_network_detail
    show_network_detail()


@mon_app.command("processes", help=_ct("mon_processes"))
def mon_processes() -> None:
    _boot()
    from monitor.menu import show_processes
    show_processes()


# ─── network 子命令 ───────────────────────────────────────────────────────────

@net_app.command("ping", help=_ct("net_ping"))
def net_ping(
    host: str = typer.Argument(None, help=_ct("net_host")),
) -> None:
    _boot()
    from network.menu import show_ping
    show_ping(host=host, pause_after=host is None)


@net_app.command("traceroute", help=_ct("net_traceroute"))
def net_traceroute(
    host: str = typer.Argument(None, help=_ct("net_host")),
) -> None:
    _boot()
    from network.menu import show_traceroute
    show_traceroute(host=host, pause_after=host is None)


@net_app.command("dns", help=_ct("net_dns"))
def net_dns(
    host: str = typer.Argument(None, help=_ct("net_dns_host")),
) -> None:
    _boot()
    from network.menu import show_dns
    show_dns(host=host, pause_after=host is None)


@net_app.command("port-scan", help=_ct("net_port_scan"))
def net_port_scan(
    host: str = typer.Argument(None, help=_ct("net_host")),
) -> None:
    _boot()
    from network.menu import show_port_scan
    show_port_scan(host=host, pause_after=host is None)


@net_app.command("speed-test", help=_ct("net_speed_test"))
def net_speed_test() -> None:
    _boot()
    from network.menu import show_speed_test
    show_speed_test(pause_after=False)


@net_app.command("public-ip", help=_ct("net_public_ip"))
def net_public_ip() -> None:
    _boot()
    from network.menu import show_public_ip
    show_public_ip(pause_after=False)


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
