"""插件工具菜单 — 常驻主菜单：插件管理（安装 / 更新 / 卸载）+ 已安装插件入口（热插拔）"""
from __future__ import annotations

from core.i18n import current_lang, t
from core.prompt import select, text_input, confirm, pause, clear_screen, UserCancel, console
from core.theme import get_color, get_icon, print_success, print_error, print_info, print_warning

from plugin import commands

_THEME_KEY = "plugin"
_BREADCRUMB = ["OpsKit"]


def _grouped(pairs):
    """把已加载插件按清单 group 归组：返回 (未分组列表, {group_id: 组内列表})"""
    ungrouped = []
    groups: dict[str, list] = {}
    for pair in pairs:
        gid = pair[0].group
        if gid:
            groups.setdefault(gid, []).append(pair)
        else:
            ungrouped.append(pair)
    return ungrouped, groups


def _group_display(members) -> tuple[str, str]:
    """分组入口的图标与显示名（取组内首个声明者）"""
    lang = current_lang()
    icon = next((m.group_icon for m, _ in members if m.group_icon), None) or get_icon("plugin")
    label = next(
        (m.display_group_label(lang) for m, _ in members if m.display_group_label(lang)),
        members[0][0].group,
    )
    return icon, label


def entry() -> None:
    """插件工具主循环：每次进入实时重新扫描插件目录（热插拔），同 group 插件聚合为一个入口"""
    while True:
        pairs = commands.loaded_plugins()
        ungrouped, groups = _grouped(pairs)
        choices = [{"key": "1", "label": f"{get_icon('plugin_manage')} {t('plugin.manage')}"}]
        items: list[tuple[str, object]] = []          # ("plugin", pair) | ("group", gid)
        for pair in ungrouped:
            items.append(("plugin", pair))
        for gid in groups:
            items.append(("group", gid))
        for i, (kind, payload) in enumerate(items):
            if kind == "plugin":
                _manifest, info = payload
                icon = info.icon or get_icon("plugin")
                label = info.label or info.key
            else:
                icon, label = _group_display(groups[payload])
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
                if 0 <= idx < len(items):
                    kind, payload = items[idx]
                    if kind == "plugin":
                        payload[1].entry()
                    else:
                        _group_menu(payload)
        except (KeyboardInterrupt, UserCancel):
            pass


def _group_menu(group_id: str) -> None:
    """分组子菜单：列出该 group 下全部插件（每轮循环重新扫描，热插拔）"""
    while True:
        pairs = commands.loaded_plugins()
        members = [(m, info) for m, info in pairs if m.group == group_id]
        if not members:
            break
        _icon, group_label = _group_display(members)
        choices = []
        for i, (_manifest, info) in enumerate(members):
            icon = info.icon or get_icon("plugin")
            label = info.label or info.key
            choices.append({"key": str(i + 1), "label": f"{icon} {label}"})
        try:
            key = select(
                breadcrumb=[*_BREADCRUMB, t("menu.plugin"), group_label],
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
            idx = int(key) - 1
            if 0 <= idx < len(members):
                members[idx][1].entry()
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


def _summary_lines(manifest) -> list[str]:
    """信任确认界面展示的插件概要（名称 / 版本 / 形态 / 权限声明）"""
    perms = ", ".join(manifest.permissions) if manifest.permissions else t("plugin.perm_none")
    return [
        f"{t('plugin.col_name')}: {_display_name(manifest)}",
        f"{t('plugin.col_version')}: {manifest.version}",
        f"{t('plugin.col_kind')}: {manifest.kind}",
        f"{t('plugin.col_perms')}: {perms}",
    ]


def _confirm_trust(manifest, source: str = "") -> bool:
    """展示概要 + 用户确认信任，确认后记录指纹"""
    if not confirm(
        breadcrumb=[*_BREADCRUMB, t("menu.plugin"), t("plugin.manage")],
        prompt=t("plugin.trust_confirm", name=_display_name(manifest)),
        theme_key=_THEME_KEY,
        info_lines=_summary_lines(manifest),
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
        print_error(t("plugin.trust_declined", name=_display_name(manifest)))
        pause()
        return
    print_success(t("plugin.install_success", name=_display_name(manifest)))
    pause()


def _manifest_grouped(manifests):
    """把已安装插件清单按 group 归组：返回 (未分组列表, {group_id: 组内列表})"""
    ungrouped = []
    groups: dict[str, list] = {}
    for m in manifests:
        if m.group:
            groups.setdefault(m.group, []).append(m)
        else:
            ungrouped.append(m)
    return ungrouped, groups


def _manifest_group_display(members) -> tuple[str, str]:
    """分组入口的图标与显示名（取组内首个声明者）"""
    lang = current_lang()
    icon = next((m.group_icon for m in members if m.group_icon), None) or get_icon("plugin")
    label = next(
        (m.display_group_label(lang) for m in members if m.display_group_label(lang)),
        members[0].group,
    )
    return icon, label


def _manifest_item(m) -> str:
    lang = current_lang()
    return f"{m.icon or get_icon('plugin')} {m.display_label(lang) or m.name}"


def _display_name(m) -> str:
    """提示文案用显示名：分组名 + 插件显示名（如 “Insight Flow 服务端”），无则降级 name"""
    lang = current_lang()
    label = m.display_label(lang) or m.name
    group_label = m.display_group_label(lang)
    return f"{group_label} {label}" if group_label else label


def _pick_member(subtitle_key: str, members):
    """在分组内选择一个插件（显示插件显示名），返回 manifest 或 None"""
    _icon, group_label = _manifest_group_display(members)
    choices = [
        {"key": str(i + 1), "label": _manifest_item(m)}
        for i, m in enumerate(members)
    ]
    try:
        key = select(
            breadcrumb=[*_BREADCRUMB, t("menu.plugin"), t("plugin.manage"), t(subtitle_key), group_label],
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
    if 0 <= idx < len(members):
        return members[idx]
    return None


def _pick(subtitle_key: str):
    """选择一个已安装插件：与插件工具入口一致的分组结构 —
    先显示入口名（group），进入后再显示组内插件显示名；未分组插件直接显示"""
    manifests = commands.manifests()
    if not manifests:
        clear_screen()
        console.print(t("plugin.empty"), style=get_color("muted"))
        pause()
        return None
    while True:
        ungrouped, groups = _manifest_grouped(manifests)
        items: list[tuple[str, object]] = []          # ("plugin", manifest) | ("group", gid)
        choices = []
        for m in ungrouped:
            items.append(("plugin", m))
        for gid in groups:
            items.append(("group", gid))
        for i, (kind, payload) in enumerate(items):
            if kind == "plugin":
                label = _manifest_item(payload)
            else:
                icon, name = _manifest_group_display(groups[payload])
                label = f"{icon} {name}"
            choices.append({"key": str(i + 1), "label": label})
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
        if not (0 <= idx < len(items)):
            continue
        kind, payload = items[idx]
        if kind == "plugin":
            return payload
        picked = _pick_member(subtitle_key, groups[payload])
        if picked is not None:
            return picked


def _update() -> None:
    manifest = _pick("plugin.update")
    if manifest is None:
        return
    # 手动 clone / 内容变化后未信任的插件：先走信任确认，确认后继续更新
    if commands.trust_status(manifest) != commands.TRUST_OK:
        if not _confirm_trust(manifest):
            print_error(t("plugin.trust_declined", name=_display_name(manifest)))
            pause()
            return
    ok, msg = commands.update(manifest)
    if not ok:
        if msg == "not_git":
            print_error(t("plugin.update_not_git", name=_display_name(manifest)))
        else:
            print_error(t("plugin.update_failed", error=msg))
        pause()
        return
    refreshed = next((m for m in commands.manifests() if m.name == manifest.name), None)
    if refreshed is None:
        print_error(t("plugin.install_no_manifest"))
        pause()
        return
    if msg == "unchanged":
        print_info(t("plugin.update_latest", name=_display_name(refreshed)))
        pause()
        return
    if commands.trust_status(refreshed) == commands.TRUST_OK or _confirm_trust(refreshed):
        commands.reload(refreshed)
        print_success(t("plugin.update_success", name=_display_name(refreshed)))
    else:
        print_error(t("plugin.trust_needed", name=_display_name(refreshed)))
    pause()


def _remove() -> None:
    manifest = _pick("plugin.remove")
    if manifest is None:
        return
    if not confirm(
        breadcrumb=[*_BREADCRUMB, t("menu.plugin"), t("plugin.manage")],
        prompt=t("plugin.remove_confirm", name=_display_name(manifest)),
        theme_key=_THEME_KEY,
    ):
        return
    commands.remove(manifest)
    print_success(t("plugin.remove_success", name=_display_name(manifest)))
    pause()
