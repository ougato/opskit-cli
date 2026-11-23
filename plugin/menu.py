"""插件工具菜单 — 常驻主菜单：插件管理（安装 / 更新 / 卸载）+ 已安装插件入口（热插拔）"""
from __future__ import annotations

from rich.markup import escape

from core.i18n import current_lang, t
from core.prompt import select, text_input, confirm, pause, clear_screen, print_header, UserCancel, console
from core.sdk import waiting
from core.theme import get_color, get_icon, print_success, print_error, print_info, print_warning

from plugin import commands
from core.plugin import PluginInventoryError

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


def _run_entry(pair, preload=None):
    from core.installation_progress import WaitingDisplay
    from core.prompt import screen_transition

    display = WaitingDisplay()
    display.start(t("plugin.opening"))
    try:
        with screen_transition(display.close):
            return _activate_entry(pair, preload)
    finally:
        display.close()


def _activate_entry(pair, preload):
    manifest, info = pair
    if preload is None:
        return info.entry()
    from core.plugin import _guard_entry
    def execute():
        if not preload.take():
            from core.plugin_sandbox import invoke
            invoke(manifest, "entry")
    return _guard_entry(manifest.name, execute)()


def entry() -> None:
    try:
        _entry()
    except PluginInventoryError as error:
        print_error(str(error))
        pause()


def _entry() -> None:
    """插件工具主循环：每次进入实时重新扫描插件目录（热插拔），同 group 插件聚合为一个入口"""
    crumb = [*_BREADCRUMB, t("menu.plugin")]
    while True:
        with waiting(t("plugin.loading_inventory")):
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
            choices.append({"key": str(i + 2), "label": f"{escape(icon)} {escape(label)}"})
        preload = None
        candidate = items[0][1] if items and items[0][0] == "plugin" else None
        def prepare():
            nonlocal preload
            if candidate is not None:
                from core.plugin_entry_preload import start
                preload = start(candidate[0])
                if preload is not None:
                    preload.prepare_caller_inspector()
        try:
            key = select(
                breadcrumb=crumb,
                subtitle="",
                choices=choices,
                theme_key=_THEME_KEY,
                back_label=f"{get_icon('back')} {t('plugin_native.back')}",
                on_ready=prepare,
            )
        except UserCancel:
            if preload is not None:
                preload.cancel()
            break
        except BaseException:
            if preload is not None:
                preload.cancel()
            raise
        if key is None:
            if preload is not None:
                preload.cancel()
            break
        try:
            if key == "1":
                if preload is not None:
                    preload.cancel()
                _manage()
            else:
                idx = int(key) - 2
                if 0 <= idx < len(items):
                    kind, payload = items[idx]
                    if kind == "plugin":
                        if preload is not None and payload is not candidate:
                            preload.cancel()
                        _run_entry(payload, preload if payload is candidate else None)
                    else:
                        if preload is not None:
                            preload.cancel()
                        _group_menu(payload, _group_display(groups[payload])[1])
        except (KeyboardInterrupt, UserCancel):
            pass
        finally:
            if preload is not None:
                preload.cancel()


def _group_menu(group_id: str, group_label: str) -> None:
    """分组子菜单：列出该 group 下全部插件（每轮循环重新扫描，热插拔）"""
    while True:
        crumb = [*_BREADCRUMB, t("menu.plugin"), group_label]
        with waiting(t("plugin.loading_inventory")):
            pairs = commands.loaded_plugins()
        members = [(m, info) for m, info in pairs if m.group == group_id]
        if not members:
            break
        choices = []
        for i, (_manifest, info) in enumerate(members):
            icon = info.icon or get_icon("plugin")
            label = info.label or info.key
            choices.append({"key": str(i + 1), "label": f"{escape(icon)} {escape(label)}"})
        try:
            key = select(
                breadcrumb=crumb,
                subtitle="",
                choices=choices,
                theme_key=_THEME_KEY,
                back_label=f"{get_icon('back')} {t('plugin_native.back')}",
            )
        except UserCancel:
            break
        if key is None:
            break
        try:
            idx = int(key) - 1
            if 0 <= idx < len(members):
                _run_entry(members[idx])
        except (KeyboardInterrupt, UserCancel):
            pass


def _manage() -> None:
    """插件管理主循环：安装 / 更新 / 卸载"""
    from core.plugin_entry_preload import cancel_pending
    crumb = [*_BREADCRUMB, t("menu.plugin"), t("plugin.manage")]
    destinations = {
        "1": "plugin.install", "2": "plugin.update", "3": "plugin.remove",
        "4": "plugin_native.permissions", "5": "plugin_native.maintenance",
    }
    while True:
        choices = [
            {"key": "1", "label": f"{get_icon('install')} {t('plugin.install')}"},
            {"key": "2", "label": f"{get_icon('update')} {t('plugin.update')}"},
            {"key": "3", "label": f"{get_icon('delete')} {t('plugin.remove')}"},
            {"key": "4", "label": f"{get_icon('plugin_permissions')} {t('plugin_native.permissions')}"},
            {"key": "5", "label": f"{get_icon('diagnose')} {t('plugin_native.maintenance')}"},
        ]
        try:
            key = select(
                breadcrumb=crumb,
                subtitle="",
                choices=choices,
                theme_key=_THEME_KEY,
                back_label=f"{get_icon('back')} {t('plugin_native.back')}",
            )
        except UserCancel:
            break
        if key is None:
            break
        try:
            if key not in destinations:
                continue
            clear_screen()
            print_header([*crumb, t(destinations[key])])
            # Discovery may have started in the root menu.
            with waiting(t("plugin_native.bootstrap.update_wait")):
                cancel_pending(wait=True)
            if key == "1":
                _install()
            elif key == "2":
                _update()
            elif key == "3":
                _remove()
            elif key == "4":
                from plugin.capability_menu import entry as permissions_entry
                permissions_entry()
            elif key == "5":
                _maintenance()
        except (KeyboardInterrupt, UserCancel):
            pass


def _maintenance() -> None:
    items = [
        ("restore", "update_recovery", "plugin.update_recovery_menu"),
        ("sandbox_cleanup", "profile_recovery", "plugin.native_profile_menu"),
        ("workspace_cleanup", "workspace_recovery", "plugin.workspace_recovery_menu"),
        ("cache_cleanup", "cache_cleanup", "plugin.cache_cleanup_menu"),
        ("disk", "disk", "plugin.disk_menu"),
    ]
    while True:
        try:
            key = select(
                breadcrumb=[*_BREADCRUMB, t("menu.plugin"), t("plugin.manage"), t("plugin_native.maintenance")],
                subtitle="",
                choices=[{"key": str(i + 1), "label": f"{get_icon(icon)} {t('plugin_native.' + name + '.menu')}"}
                         for i, (icon, name, _) in enumerate(items)],
                back_label=f"{get_icon('back')} {t('plugin_native.back')}", theme_key=_THEME_KEY,
            )
            if key is None:
                return
            if key.isdecimal() and 0 < int(key) <= len(items):
                import importlib
                importlib.import_module(items[int(key) - 1][2]).entry()
        except (KeyboardInterrupt, UserCancel):
            return


def _summary_lines(manifest) -> list[str]:
    """信任确认界面展示的插件概要（名称 / 版本 / 形态 / 权限声明）"""
    perms = ", ".join(manifest.permissions) if manifest.permissions else t("plugin.perm_none")
    return [
        f"{t('plugin.col_name')}: {escape(_display_name(manifest))}",
        f"{t('plugin.col_version')}: {escape(manifest.version)}",
        f"{t('plugin.col_kind')}: {escape(manifest.kind)}",
        f"{t('plugin.col_perms')}: {escape(perms)}",
    ]


def confirm_trust(manifest, source: str = "", branch: str | None = None) -> bool:
    """展示概要 + 用户确认信任，确认后记录指纹。"""
    from core.constants import PLUGIN_API_VERSION
    if manifest.api_version != PLUGIN_API_VERSION:
        print_error(t("plugin_native.errors.sdk_required"))
        return False
    crumb = [*_BREADCRUMB, t("menu.plugin"), t("plugin.manage")]
    clear_screen()
    print_header([*crumb, t("confirm_ui.trust")])
    with waiting(t("plugin_native.lifecycle.fingerprint_wait")):
        record = commands.trusted_record(manifest.name) or {}
        fingerprint = commands.compute_fingerprint(manifest.root)
    if not fingerprint:
        raise ValueError(t("plugin_native.lifecycle.fingerprint_mismatch"))
    shown_source = source or record.get("source", "")
    shown_branch = branch if branch is not None else record.get("branch")
    lines = _summary_lines(manifest)
    lines.append(t("confirm_ui.trust_detail"))
    if shown_source:
        lines.append(f"{t('plugin.col_source')}: {escape(shown_source)}")
    if shown_branch:
        lines.append(f"{t('plugin.col_branch')}: {escape(shown_branch)}")
    lines.append(f"{t('plugin.col_fingerprint')}: {fingerprint}")
    if not confirm(
        breadcrumb=crumb,
        prompt=t("confirm_ui.trust"),
        theme_key=_THEME_KEY,
        info_lines=lines,
        require_explicit=True,
    ):
        return False
    clear_screen()
    print_header([*crumb, t("confirm_ui.trust")])
    with waiting(t("plugin_native.lifecycle.trust_save_wait")):
        commands.grant_trust(manifest, source, branch, expected_fingerprint=fingerprint)
    return True


def _install() -> None:
    raw = text_input(
        breadcrumb=[*_BREADCRUMB, t("menu.plugin"), t("plugin.manage"), t("plugin.install")],
        prompt=t("plugin.install_prompt"),
        hint=t("plugin.install_hint"),
        theme_key=_THEME_KEY,
    )
    url, branch, alias = commands.parse_install_input(raw)
    if not url:
        return
    if not commands.is_trusted_source(url):
        print_warning(t("plugin.source_warning"))
    manifest, err = _install_with_progress(url, branch, alias)
    if manifest is None:
        if err.startswith("exists:"):
            print_error(t("plugin.install_exists", name=err.split(":", 1)[1]))
        elif err.startswith("bad_alias:"):
            print_error(t("plugin.install_bad_alias", name=err.split(":", 1)[1]))
        elif err.startswith("manifest_name_exists:"):
            _prefix, name, directory = err.split(":", 2)
            print_error(t("plugin.install_manifest_name_exists", name=name, dir=directory))
        elif err == "no_manifest":
            print_error(t("plugin.install_no_manifest"))
        else:
            print_error(t("plugin.install_failed", error=err))
        pause()
        return
    try:
        accepted = _confirm_install(manifest, source=url.strip(), branch=branch)
    except (OSError, ValueError, RuntimeError) as exc:
        print_error(t("plugin.install_failed", error=str(exc)))
        pause()
        return
    if not accepted:
        print_error(t("plugin.trust_declined", name=_display_name(manifest)))
        pause()
        return
    print_success(t("plugin.install_success", name=_display_name(manifest)))
    pause()


def _confirm_install(manifest, source, branch=None):
    accepted = False
    try:
        accepted = confirm_trust(manifest, source=source, branch=branch)
        return accepted
    finally:
        if not accepted:
            commands.rollback_install(manifest)
            from core.plugin_trust import invalidate
            invalidate(manifest.name)


def _install_with_progress(url, branch=None, alias=None):
    """Install the source the user just submitted in the install menu."""
    from core.installation_progress import InstallationProgress
    options = _fetch_options(url, user_entered=True)
    with InstallationProgress() as progress:
        if "fetch_interaction" in options:
            options["fetch_interaction"] = progress.interaction(options["fetch_interaction"])
        return commands.install(url, branch, alias, automatic=True, progress=progress, **options)


def _fetch_options(source, *, user_entered=False):
    """Defer exceptional authentication/network prompts until transport needs them."""
    from core.native_acquisition import source_identity
    if not source:
        return {}
    try:
        canonical, local = source_identity(source)
        if local:
            return {}
    except ValueError:
        print_error(t("plugin_native.lifecycle.repository_invalid"))
        raise UserCancel() from None
    options = {"fetch_interaction": _fetch_interaction}
    if user_entered:
        options["allow_private"] = True
    return options


def _fetch_interaction(source, code):
    crumb = [*_BREADCRUMB, t("menu.plugin"), t("plugin.manage")]
    if code == "fetch_private_denied":
        return confirm(breadcrumb=crumb, prompt=t("confirm_ui.private_connection"),
                       info_lines=[escape(source), t("confirm_ui.private_detail")], theme_key=_THEME_KEY, require_explicit=True)
    profile = text_input(breadcrumb=crumb, prompt=t("plugin_native.fetch_profile_prompt"),
                         hint=t("plugin_native.fetch_credentials_hint"), theme_key=_THEME_KEY).strip()
    if not profile:
        raise UserCancel()
    return profile


def _update_fetch_options(manifest):
    record = commands.trusted_record(manifest.name) or {}
    return _fetch_options(record.get("source", ""))


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
    return f"{escape(m.icon or get_icon('plugin'))} {escape(m.display_label(lang) or m.name)}"


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
            back_label=f"{get_icon('back')} {t('plugin_native.back')}",
        )
    except UserCancel:
        return None
    if key is None:
        return None
    idx = int(key) - 1
    if 0 <= idx < len(members):
        return members[idx]
    return None


_ALL = object()  # 「更新全部」哨兵（永远第一项）


def _pick(subtitle_key: str, all_label: str = ""):
    """选择一个已安装插件：与插件工具入口一致的分组结构 —
    先显示入口名（group），进入后再显示组内插件显示名；未分组插件直接显示。
    all_label 非空时列表第一项为该文案，选中返回哨兵 _ALL"""
    with waiting(t("plugin.loading_inventory")):
        manifests = commands.manifests()
    if not manifests:
        clear_screen()
        print_header([*_BREADCRUMB, t("menu.plugin"), t("plugin.manage"), t(subtitle_key)])
        console.print(t("plugin.empty"), style=get_color("muted"))
        pause()
        return None
    while True:
        ungrouped, groups = _manifest_grouped(manifests)
        items: list[tuple[str, object]] = []          # ("all", None) | ("plugin", manifest) | ("group", gid)
        choices = []
        if all_label:
            items.append(("all", None))
        for m in ungrouped:
            items.append(("plugin", m))
        for gid in groups:
            items.append(("group", gid))
        for i, (kind, payload) in enumerate(items):
            if kind == "all":
                label = all_label
            elif kind == "plugin":
                label = _manifest_item(payload)
            else:
                icon, name = _manifest_group_display(groups[payload])
                label = f"{escape(icon)} {escape(name)}"
            choices.append({"key": str(i + 1), "label": label})
        try:
            key = select(
                breadcrumb=[*_BREADCRUMB, t("menu.plugin"), t("plugin.manage"), t(subtitle_key)],
                subtitle=t("plugin.select"),
                choices=choices,
                theme_key=_THEME_KEY,
                back_label=f"{get_icon('back')} {t('plugin_native.back')}",
            )
        except UserCancel:
            return None
        if key is None:
            return None
        idx = int(key) - 1
        if not (0 <= idx < len(items)):
            continue
        kind, payload = items[idx]
        if kind == "all":
            return _ALL
        if kind == "plugin":
            return payload
        picked = _pick_member(subtitle_key, groups[payload])
        if picked is not None:
            return picked


def _update_all() -> None:
    """逐个更新插件，每个结果确认后继续"""
    crumb = [*_BREADCRUMB, t("menu.plugin"), t("plugin.update"), t("plugin.update_all")]
    clear_screen()
    print_header(crumb)
    with waiting(t("plugin.loading_inventory")):
        manifests = commands.manifests()
    for manifest in manifests:
        _update_one(manifest, [*crumb, _display_name(manifest)])
        pause()


def _update_one(manifest, crumb) -> None:
    clear_screen()
    print_header(crumb)
    from core.installation_progress import InstallationProgress
    try:
        with InstallationProgress() as progress:
            progress("update_wait")
            options = _update_fetch_options(manifest)
            if "fetch_interaction" in options:
                options["fetch_interaction"] = progress.interaction(options["fetch_interaction"])
            ok, msg = commands.update(manifest, automatic=True, progress=progress, **options)
            if ok:
                progress("loading_inventory")
                refreshed = next((item for item in commands.manifests() if item.name == manifest.name), None)
    except UserCancel:
        print_info(t("plugin.update_cancelled", name=_display_name(manifest)))
        return
    if not ok:
        if msg == "not_git":
            print_error(t("plugin.update_not_git", name=_display_name(manifest)))
        else:
            print_error(t("plugin.update_failed", error=msg))
        return
    if refreshed is None:
        print_error(t("plugin.install_no_manifest"))
        return
    name = _display_name(refreshed)
    if msg == "unchanged":
        print_info(t("plugin.update_latest", name=name))
    else:
        print_success(t("plugin.update_success", name=name, version=refreshed.version))


def _update() -> None:
    manifest = _pick("plugin.update", all_label=f"{get_icon('update')} {t('plugin.update_all')}")
    if manifest is None:
        return
    if manifest is _ALL:
        _update_all()
        return
    crumb = [*_BREADCRUMB, t("menu.plugin"), t("plugin.update"), _display_name(manifest)]
    _update_one(manifest, crumb)
    pause()


def _remove() -> None:
    manifest = _pick("plugin.remove")
    if manifest is None:
        return
    if not confirm(
        breadcrumb=[*_BREADCRUMB, t("menu.plugin"), t("plugin.manage")],
        prompt=t("confirm_ui.uninstall"),
        theme_key=_THEME_KEY,
        info_lines=[escape(_display_name(manifest)), t("confirm_ui.plugin_delete")],
    ):
        return
    from core.resource_lock import ResourceBusyError
    try:
        clear_screen()
        print_header([*_BREADCRUMB, t("menu.plugin"), t("plugin.manage"), t("plugin.remove"),
                      _display_name(manifest)])
        with waiting(t("confirm_ui.uninstall")):
            commands.remove(manifest)
    except ResourceBusyError:
        print_error(t("plugin_native.lifecycle.plugin_busy"))
        pause()
        return
    print_success(t("plugin.remove_success", name=_display_name(manifest)))
    pause()
