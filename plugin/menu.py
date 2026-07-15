"""插件管理菜单 — 列表 / 安装 / 更新 / 启停 / 卸载"""
from __future__ import annotations

from rich.table import Table

from core.i18n import t
from core.paths import plugins_dir
from core.prompt import select, text_input, confirm, pause, clear_screen, UserCancel, console
from core.theme import get_color, get_icon, print_success, print_error, print_info

from plugin import commands

_THEME_KEY = "plugin"
_BREADCRUMB = ["OpsKit"]


def entry() -> None:
    """插件管理主循环"""
    while True:
        choices = [
            {"key": "1", "label": f"{get_icon('list')} {t('plugin.list')}"},
            {"key": "2", "label": f"{get_icon('install')} {t('plugin.install')}"},
            {"key": "3", "label": f"{get_icon('update')} {t('plugin.update')}"},
            {"key": "4", "label": f"{get_icon('toggle_on')} {t('plugin.toggle')}"},
            {"key": "5", "label": f"{get_icon('trash')} {t('plugin.remove')}"},
        ]
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
                _show_list()
            elif key == "2":
                _install()
            elif key == "3":
                _update()
            elif key == "4":
                _toggle()
            elif key == "5":
                _remove()
        except (KeyboardInterrupt, UserCancel):
            pass


def _show_list() -> None:
    clear_screen()
    manifests = commands.manifests()
    print_info(t("plugin.dir_hint", path=str(plugins_dir())))
    if not manifests:
        console.print(t("plugin.empty"), style=get_color("muted"))
        pause()
        return
    table = Table(header_style=get_color("table.header"), border_style=get_color("table.border"))
    table.add_column(t("plugin.col_name"))
    table.add_column(t("plugin.col_version"))
    table.add_column(t("plugin.col_kind"))
    table.add_column(t("plugin.col_status"))
    for m in manifests:
        status = t("plugin.status_enabled") if commands.is_enabled(m.name) else t("plugin.status_disabled")
        table.add_row(m.name, m.version, m.kind, status)
    console.print(table)
    pause()


def _install() -> None:
    url = text_input(
        breadcrumb=[*_BREADCRUMB, t("menu.plugin"), t("plugin.install")],
        prompt=t("plugin.install_prompt"),
        hint=t("plugin.install_hint"),
        theme_key=_THEME_KEY,
    )
    if not url.strip():
        return
    ok, msg = commands.install(url)
    if ok:
        print_success(t("plugin.install_success", name=msg))
    elif msg.startswith("exists:"):
        print_error(t("plugin.install_exists", name=msg.split(":", 1)[1]))
    elif msg == "no_manifest":
        print_error(t("plugin.install_no_manifest"))
    else:
        print_error(t("plugin.install_failed", error=msg))
    pause()


def _pick(subtitle_key: str):
    """选择一个已安装插件，返回 manifest 或 None"""
    manifests = commands.manifests()
    if not manifests:
        clear_screen()
        console.print(t("plugin.empty"), style=get_color("muted"))
        pause()
        return None
    choices = [
        {"key": str(i + 1), "label": f"{m.icon or get_icon('plugin')} {m.name} {m.version}"}
        for i, m in enumerate(manifests)
    ]
    try:
        key = select(
            breadcrumb=[*_BREADCRUMB, t("menu.plugin"), t(subtitle_key)],
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
    ok, msg = commands.update(manifest)
    if ok:
        print_success(t("plugin.update_success", name=msg))
    elif msg == "not_git":
        print_error(t("plugin.update_not_git", name=manifest.name))
    else:
        print_error(t("plugin.update_failed", error=msg))
    pause()


def _toggle() -> None:
    manifest = _pick("plugin.toggle")
    if manifest is None:
        return
    enabled = not commands.is_enabled(manifest.name)
    commands.set_enabled(manifest.name, enabled)
    if enabled:
        print_success(t("plugin.toggled_on", name=manifest.name))
    else:
        print_success(t("plugin.toggled_off", name=manifest.name))
    pause()


def _remove() -> None:
    manifest = _pick("plugin.remove")
    if manifest is None:
        return
    if not confirm(
        breadcrumb=[*_BREADCRUMB, t("menu.plugin")],
        prompt=t("plugin.remove_confirm", name=manifest.name),
        theme_key=_THEME_KEY,
    ):
        return
    commands.remove(manifest)
    print_success(t("plugin.remove_success", name=manifest.name))
    pause()
