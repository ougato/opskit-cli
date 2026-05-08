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
from core.prompt import select, console, UserCancel

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


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


@app.command()
def run(
    version: bool = typer.Option(False, "--version", "-v", help="Show version"),
    theme: str = typer.Option("", "--theme", help="Override theme"),
    lang: str = typer.Option("", "--lang", help="Override language (zh/en)"),
) -> None:
    """OpsKit — 跨平台运维面板"""
    if version:
        from core.constants import APP_VERSION
        console.print(f"v{APP_VERSION}")
        raise typer.Exit()

    cfg = _boot()

    if theme:
        switch_theme(theme)
    if lang:
        i18n_switch(lang)

    _main_menu(cfg)


if __name__ == "__main__":
    app()
