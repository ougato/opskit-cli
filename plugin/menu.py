"""插件工具菜单 — 常驻主菜单：插件管理（安装 / 更新 / 卸载）+ 已安装插件入口（热插拔）"""
from __future__ import annotations

from core.i18n import t
from core.prompt import select, text_input, confirm, pause, clear_screen, UserCancel, console
from core.theme import get_color, get_icon, print_success, print_error, print_info, print_warning

from plugin import commands

_THEME_KEY = "plugin"
_BREADCRUMB = ["OpsKit"]


def entry() -> None:
    """插件工具主循环：每次进入实时重新扫描插件目录（热插拔）"""
    while True:
        pairs = commands.loaded_plugins()
        choices = [{"key": "1", "label": f"{get_icon('plugin_manage')} {t('plugin.manage')}"}]
        for i, (_manifest, info) in enumerate(pairs):
            icon = info.icon or get_icon("plugin")
            label = info.label or info.key
            choices.append({"key": str(i + 2), "label": f"{icon} {label}"})
        try:
            key = select(
                breadcrumb=[*_BREADCRUMB, t("menu.plugin")],
                subtitle=t("prompt.select"),
                choices=choices,
                theme_key=_THEME_KEY,
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            break
        if key is None:
            break
        try:
            if key == "1":
                _manage()
            else:
                idx = int(key) - 2
                if 0 <= idx < len(pairs):
                    pairs[idx][1].entry()
        except (KeyboardInterrupt, UserCancel):
            pass


def _manage() -> None:
    """插件管理主循环：安装 / 更新 / 卸载"""
    while True:
        choices = [
            {"key": "1", "label": f"{get_icon('install')} {t('plugin.install')}"},
            {"key": "2", "label": f"{get_icon('update')} {t('plugin.update')}"},
            {"key": "3", "label": f"{get_icon('trash')} {t('plugin.remove')}"},
        ]
        try:
            key = select(
                breadcrumb=[*_BREADCRUMB, t("menu.plugin"), t("plugin.manage")],
                subtitle=t("prompt.select"),
                choices=choices,
                theme_key=_THEME_KEY,
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            break
        if key is None:
            break
        try:
            if key == "1":
                _install()
            elif key == "2":
                _update()
            elif key == "3":
                _remove()
        except (KeyboardInterrupt, UserCancel):
            pass


def _show_summary(manifest) -> None:
    """信任确认前展示插件概要（名称 / 版本 / 形态 / 权限声明）"""
    perms = ", ".join(manifest.permissions) if manifest.permissions else t("plugin.perm_none")
    console.print(f"{t('plugin.col_name')}: {manifest.name}", style=get_color("info"))
    console.print(f"{t('plugin.col_version')}: {manifest.version}", style=get_color("info"))
    console.print(f"{t('plugin.col_kind')}: {manifest.kind}", style=get_color("info"))
    console.print(f"{t('plugin.col_perms')}: {perms}", style=get_color("info"))


def _confirm_trust(manifest, source: str = "") -> bool:
    """展示概要 + 用户确认信任，确认后记录指纹"""
    clear_screen()
    _show_summary(manifest)
    if not confirm(
        breadcrumb=[*_BREADCRUMB, t("menu.plugin"), t("plugin.manage")],
        prompt=t("plugin.trust_confirm", name=manifest.name),
        theme_key=_THEME_KEY,
    ):
        return False
    commands.grant_trust(manifest, source)
    return True


def _install() -> None:
    url = text_input(
        breadcrumb=[*_BREADCRUMB, t("menu.plugin"), t("plugin.manage"), t("plugin.install")],
        prompt=t("plugin.install_prompt"),
        hint=t("plugin.install_hint"),
        theme_key=_THEME_KEY,
    )
    if not url.strip():
        return
    if not commands.is_trusted_source(url):
        print_warning(t("plugin.source_warning"))
    print_info(t("plugin.cloning"))
    manifest, err = commands.install(url)
    if manifest is None:
        if err.startswith("exists:"):
            print_error(t("plugin.install_exists", name=err.split(":", 1)[1]))
        elif err == "no_manifest":
            print_error(t("plugin.install_no_manifest"))
        else:
            print_error(t("plugin.install_failed", error=err))
        pause()
        return
    if not _confirm_trust(manifest, source=url.strip()):
        commands.rollback_install(manifest)
        print_error(t("plugin.trust_declined", name=manifest.name))
        pause()
        return
    print_success(t("plugin.install_success", name=manifest.name))
    pause()


def _pick(subtitle_key: str):
    """选择一个已安装插件（仅显示名称），返回 manifest 或 None"""
    manifests = commands.manifests()
    if not manifests:
        clear_screen()
        console.print(t("plugin.empty"), style=get_color("muted"))
        pause()
        return None
    choices = [
        {"key": str(i + 1), "label": f"{m.icon or get_icon('plugin')} {m.name}"}
        for i, m in enumerate(manifests)
    ]
    try:
        key = select(
            breadcrumb=[*_BREADCRUMB, t("menu.plugin"), t("plugin.manage"), t(subtitle_key)],
            subtitle=t("plugin.select"),
            choices=choices,
            theme_key=_THEME_KEY,
            back_label=f"{get_icon('back')} {t('menu.back')}",
        )
    except UserCancel:
        return None
    if key is None:
        return None
    idx = int(key) - 1
    if 0 <= idx < len(manifests):
        return manifests[idx]
    return None


def _update() -> None:
    manifest = _pick("plugin.update")
    if manifest is None:
        return
    # 手动 clone / 内容变化后未信任的插件：选中即走信任确认
    if commands.trust_status(manifest) != commands.TRUST_OK:
        if _confirm_trust(manifest):
            print_success(t("plugin.trust_granted", name=manifest.name))
        else:
            print_error(t("plugin.trust_declined", name=manifest.name))
        pause()
        return
    ok, msg = commands.update(manifest)
    if not ok:
        if msg == "not_git":
            print_error(t("plugin.update_not_git", name=manifest.name))
        else:
            print_error(t("plugin.update_failed", error=msg))
        pause()
        return
    refreshed = next((m for m in commands.manifests() if m.name == manifest.name), None)
    if refreshed is None:
        print_error(t("plugin.install_no_manifest"))
        pause()
        return
    if commands.trust_status(refreshed) == commands.TRUST_OK:
        print_info(t("plugin.update_latest", name=refreshed.name))
        pause()
        return
    if _confirm_trust(refreshed):
        commands.reload(refreshed)
        print_success(t("plugin.update_success", name=refreshed.name))
    else:
        print_error(t("plugin.trust_needed", name=refreshed.name))
    pause()


def _remove() -> None:
    manifest = _pick("plugin.remove")
    if manifest is None:
        return
    if not confirm(
        breadcrumb=[*_BREADCRUMB, t("menu.plugin"), t("plugin.manage")],
        prompt=t("plugin.remove_confirm", name=manifest.name),
        theme_key=_THEME_KEY,
    ):
        return
    commands.remove(manifest)
    print_success(t("plugin.remove_success", name=manifest.name))
    pause()
