"""Host-provided root menu, preferences, and exit orchestration."""
from __future__ import annotations


def _main_menu(host, cfg: dict) -> None:
    """保证 Ctrl+C / EOF 从任何子菜单退出时也执行正常退出流程。"""
    exit_request = None
    try:
        host._main_menu_loop(cfg)
    except (host.UserExit, KeyboardInterrupt) as exc:
        exit_request = exc

    # Keep exit work outside the exception handler's protected region so a
    # second interruption or cleanup failure cannot repeat the update step.
    from core.plugin_entry_preload import cancel_pending
    cancel_pending(wait=True)
    host._on_exit(cfg)
    if isinstance(exit_request, KeyboardInterrupt):
        raise host.UserExit from None
    if exit_request is not None:
        raise exit_request


def _main_menu_loop(host, cfg: dict) -> None:
    """主菜单循环"""
    modules = host.discover_modules(cfg)

    # 首屏菜单渲染完成后再启动联网/重型后台任务（测速 / 版本刷新 / 自更新 /
    # 遥测）；已成功启动的任务不会重复，失败的任务可在下一次绘制时重试。

    while True:
        choices = [
            {
                "key": str(i + 1),
                "label": f"{m.icon or host.get_icon(m.key)} {m.label or host.t(f'menu.{m.key}')}",
            }
            for i, m in enumerate(modules)
        ]
        pref_key = str(len(modules) + 1)
        choices.append({
            "key": pref_key,
            "label": f"{host.get_icon('preferences')} {host.t('menu.preferences')}",
        })

        def _on_ready():
            try:
                host._start_background_workers(cfg)
            except Exception as exc:
                host._log_startup_failure("background_start", exc)
            try:
                from core.plugin_entry_preload import start_discovery
                start_discovery()
            except Exception as exc:
                host._log_startup_failure("plugin_discovery", exc)

        failures = getattr(modules, "failures", ())
        info_lines = []
        if failures:
            names = []
            for failure in failures:
                if failure == "_registry":
                    names.append(host.t("startup.registry"))
                else:
                    translated = host.t(f"menu.{failure}")
                    names.append(failure if translated == f"menu.{failure}" else translated)
            info_lines.append(host.t("startup.modules_failed", modules=", ".join(names)))

        try:
            key = host.select(
                breadcrumb=["OpsKit"],
                subtitle="",
                choices=choices,
                theme_key="root",
                back_label=f"{host.get_icon('exit')} {host.t('menu.exit')}",
                on_ready=_on_ready,
                info_lines=info_lines,
            )
        except host.UserCancel:
            break

        if key is None:
            break

        if key == pref_key:
            from core.plugin_entry_preload import cancel_pending
            cancel_pending()
            host._handle_preferences(cfg)
            modules = host.discover_modules(cfg)
            continue

        idx = int(key) - 1
        if 0 <= idx < len(modules):
            if modules[idx].key != "plugin":
                from core.plugin_entry_preload import cancel_pending
                cancel_pending()
            try:
                modules[idx].entry()
            except (KeyboardInterrupt, host.UserCancel):
                pass
            except Exception as _e:
                try:
                    import core.telemetry as _tel
                    _tel.capture_error(_e, action="module.entry", software=modules[idx].key)
                except Exception:
                    pass
                from core.theme import print_error as _pe
                _pe(host.t("error.unknown", error=str(_e)))
                from core.prompt import pause as _pause
                _pause()


def _handle_preferences(host, cfg: dict) -> None:
    """偏好设置子菜单"""
    while True:
        pref_choices = [
            {"key": "1", "label": f"{host.get_icon('theme')} {host.t('menu.theme')}"},
            {"key": "2", "label": f"{host.get_icon('language')} {host.t('menu.language')}"},
        ]
        try:
            key = host.select(
                breadcrumb=["OpsKit", host.t("menu.preferences")],
                subtitle="",
                choices=pref_choices,
                theme_key="root",
                back_label=f"{host.get_icon('back')} {host.t('menu.back')}",
            )
        except host.UserCancel:
            break
        if key is None:
            break
        elif key == "1":
            host._handle_theme(cfg)
        elif key == "2":
            host._handle_language(cfg)


def _handle_theme(host, cfg: dict) -> None:
    """主题切换子菜单"""
    themes = host.list_themes()
    choices = [{"key": str(i + 1), "label": name} for i, name in enumerate(themes)]

    try:
        key = host.select(
            breadcrumb=["OpsKit", host.t("menu.theme")],
            subtitle="",
            choices=choices,
            theme_key="root",
            back_label=f"{host.get_icon('back')} {host.t('menu.back')}",
        )
    except host.UserCancel:
        return
    if key:
        idx = int(key) - 1
        if 0 <= idx < len(themes):
            host.switch_theme(themes[idx])


def _handle_language(host, cfg: dict) -> None:
    """语言切换子菜单"""
    lang_choices = [
        {"key": "1", "label": host.t("language.zh")},
        {"key": "2", "label": host.t("language.en")},
        {"key": "3", "label": host.t("language.auto")},
    ]
    lang_map = {"1": "zh", "2": "en", "3": "auto"}

    try:
        key = host.select(
            breadcrumb=["OpsKit", host.t("menu.language")],
            subtitle=host.t("language.select"),
            choices=lang_choices,
            theme_key="root",
            back_label=f"{host.get_icon('back')} {host.t('menu.back')}",
        )
    except host.UserCancel:
        return
    if key and key in lang_map:
        host.i18n_switch(lang_map[key])


def _on_exit(host, cfg: dict) -> None:
    """退出前检查是否有待应用的更新"""
    try:
        from core.updater import apply_pending_update, has_pending_update, pending_version
        if has_pending_update():
            ver = pending_version()
            host.print_info(host.t("update.pending_ready", version=f"v{ver}" if ver else ""))
        apply_pending_update()
    except Exception as exc:
        host._log_startup_failure("exit_update", exc)
