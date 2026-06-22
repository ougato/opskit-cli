"""软件管理菜单 — 搜索 / 开发工具 / 运维工具分类"""
from __future__ import annotations

from rich import box as rich_box
from rich.console import Console
from rich.table import Table

from core.i18n import t
from core.prompt import select, confirm, pause, clear_screen, print_header, text_input, UserCancel, console as base_console
from core.theme import get_color, get_icon, print_success, print_error, print_warning, print_info

console = Console()

_THEME_KEY = "software"


def _report(exc: Exception, **ctx) -> None:
    """上报异常到遥测系统，失败静默"""
    try:
        import core.telemetry as _tel
        _tel.capture_error(exc, **ctx)
    except Exception:
        pass


def entry() -> None:
    """软件管理模块入口 — 搜索 + 两个分类"""
    while True:
        choices = [
            {"key": "1", "label": f"{get_icon('search')} {t('software.search')}"},
            {"key": "2", "label": f"{get_icon('installed')} {t('software.installed_list')}"},
            {"key": "3", "label": f"{get_icon('devtools')} {t('software.category.devtools')}"},
            {"key": "4", "label": f"{get_icon('devops')} {t('software.category.devops')}"},
        ]
        try:
            key = select(
                breadcrumb=["OpsKit", t("menu.software")],
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
                show_search()
            elif key == "2":
                show_installed()
            elif key == "3":
                show_category("devtools")
            elif key == "4":
                show_category("devops")
        except (KeyboardInterrupt, UserCancel):
            pass


# ─── 搜索 ─────────────────────────────────────────────────────────────────────

def show_search() -> None:
    """搜索软件：输入关键词 → 匹配 key + locale 显示名 → 结果列表 → 操作菜单"""
    from software.registry import all_recipes
    from core.platform import get_platform

    try:
        keyword = text_input(
            breadcrumb=["OpsKit", t("menu.software"), t("software.search")],
            prompt=t("software.search_prompt"),
            theme_key=_THEME_KEY,
        )
    except UserCancel:
        return
    if not keyword:
        return

    kw = keyword.strip().lower()
    info = get_platform()
    all_cls = [r for r in all_recipes() if info.os_type in r.platforms and not getattr(r, "hidden", False)]
    matched = [
        cls for cls in all_cls
        if kw in cls.key.lower() or kw in t(f"software.{cls.key}").lower()
    ]

    if not matched:
        print_warning(t("software.search_no_result", keyword=keyword))
        pause()
        return

    _pick_and_act(
        breadcrumb=["OpsKit", t("menu.software"), t("software.search")],
        recipes=matched,
    )


# ─── 分类浏览 ──────────────────────────────────────────────────────────────────

def show_category(category: str) -> None:
    """列出指定分类下的所有 recipes → 选择一个 → 操作菜单"""
    from software.registry import all_recipes
    from core.platform import get_platform

    cat_label = t(f"software.category.{category}")
    info = get_platform()
    recipes = [
        r for r in all_recipes()
        if getattr(r, "category", "devops") == category
        and info.os_type in r.platforms
        and not getattr(r, "hidden", False)
    ]

    if not recipes:
        print_warning(t("software.category_empty", category=cat_label))
        pause()
        return

    _pick_and_act(
        breadcrumb=["OpsKit", t("menu.software"), cat_label],
        recipes=recipes,
    )


# ─── 共用：选择一个 recipe → 操作菜单 ────────────────────────────────────────

def _pick_and_act(breadcrumb: list[str], recipes: list) -> None:
    """从 recipes 列表中让用户选择一个，再进入操作子菜单"""
    from core.progress import spinner

    muted = get_color("muted")
    success_c = get_color("success")

    hints: dict[str, str] = {}
    with spinner(t("software.detecting")):
        for cls in recipes:
            ver = cls().detect()
            hints[cls.key] = ver or ""

    total = len(recipes)
    total_pages = (total + _PAGE_SIZE - 1) // _PAGE_SIZE
    page = 0

    while True:
        start = page * _PAGE_SIZE
        end = min(start + _PAGE_SIZE, total)
        page_items = recipes[start:end]
        choices = [
            {
                "key": str(i + 1),
                "label": f"{get_icon(cls.key)} {t(f'software.{cls.key}') if _has_i18n(f'software.{cls.key}') else cls.key}",
                "hint": hints.get(cls.key, ""),
            }
            for i, cls in enumerate(page_items)
        ]
        if page > 0:
            choices.append({"key": "p", "label": t("software.prev_page")})
        if page < total_pages - 1:
            choices.append({"key": "n", "label": t("software.next_page")})

        subtitle = (
            f"{t('prompt.select')}  [{page + 1}/{total_pages}]"
            if total_pages > 1
            else t("prompt.select")
        )
        try:
            key = select(
                breadcrumb=breadcrumb,
                subtitle=subtitle,
                choices=choices,
                theme_key=_THEME_KEY,
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            return
        if key is None:
            return
        if key == "n":
            page += 1
            continue
        if key == "p":
            page -= 1
            continue

        cls = page_items[int(key) - 1]
        if getattr(cls, "has_submenu", False):
            _show_submenu(breadcrumb=breadcrumb, cls=cls)
        else:
            show_actions(breadcrumb=breadcrumb, cls=cls)
        return


def _show_submenu(breadcrumb: list[str], cls: type) -> None:
    """显示子菜单（如 WireGuard → 公网服务端 / 内网客户端）"""
    from software.registry import get as get_recipe
    name = t(f"software.{cls.key}") if _has_i18n(f"software.{cls.key}") else cls.key
    sub_breadcrumb = [*breadcrumb, name]
    instance = cls()
    items = instance.submenu_items()
    if not items:
        show_actions(breadcrumb=breadcrumb, cls=cls)
        return

    while True:
        choices = [
            {
                "key": str(i + 1),
                "label": f"{get_icon(item['key']) if _has_icon(item['key']) else get_icon('software')} {t(item['label_key']) if _has_i18n(item['label_key']) else item['key']}",
            }
            for i, item in enumerate(items)
        ]
        try:
            key = select(
                breadcrumb=sub_breadcrumb,
                subtitle=t("prompt.select"),
                choices=choices,
                theme_key=_THEME_KEY,
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            break
        if key is None:
            break

        selected = items[int(key) - 1]
        sub_cls = get_recipe(selected["key"])
        if sub_cls:
            show_actions(breadcrumb=sub_breadcrumb, cls=sub_cls)
        else:
            print_warning(t("software.coming_soon"))
            pause()


def _has_icon(key: str) -> bool:
    """检测 icon token 是否存在（get_icon 对未知 token 返回 '•'）"""
    return get_icon(key) != "•"


def _has_i18n(key: str) -> bool:
    """检测 i18n key 是否存在（避免返回 key 本身）"""
    val = t(key)
    return val != key


# ─── 操作子菜单（统一菜单：固定 1-3 + 扩展 4+）─────────────────────────────

def show_actions(breadcrumb: list[str], cls: type) -> None:
    """软件操作菜单：安装(1) / 卸载(2) / 升级(3) + 扩展操作"""
    name = t(f"software.{cls.key}") if _has_i18n(f"software.{cls.key}") else cls.key
    sub_breadcrumb = [*breadcrumb, name]
    instance = cls()
    muted = get_color("muted")

    while True:
        choices = [
            {"key": "1", "label": f"{get_icon('install')} {t('software.install')}"},
            {"key": "2", "label": f"{get_icon('uninstall')} {t('software.uninstall')}"},
        ]
        if getattr(cls, "has_upgrade", True):
            choices.append({"key": "3", "label": f"{get_icon('upgrade')} {t('software.upgrade')}"})
        else:
            choices.append({"key": "3", "label": f"[{muted}]{get_icon('upgrade')} {t('software.upgrade')}[/{muted}]", "disabled": True})

        action_map: dict[str, str] = {"1": "install", "2": "uninstall", "3": "upgrade"}
        idx = 4
        # 切换菜单项：仅当 has_switch=True 且程序管理了至少一个已安装版本时才显示
        _has_managed_versions = (
            getattr(cls, "has_switch", False)
            and hasattr(instance, "installed_versions")
            and bool(instance.installed_versions())
        )
        if _has_managed_versions:
            choices.append({"key": str(idx), "label": f"{get_icon('switch') if _has_icon('switch') else '⇄'} {t('software.switch')}"})
            action_map[str(idx)] = "switch"
            idx += 1
        if getattr(cls, "has_diagnose", False):
            choices.append({"key": str(idx), "label": f"{get_icon('diagnose') if _has_icon('diagnose') else '🔧'} {t('software.diagnose')}"})
            action_map[str(idx)] = "diagnose"
            idx += 1
        if getattr(cls, "has_manage", False):
            choices.append({"key": str(idx), "label": f"{get_icon('manage') if _has_icon('manage') else get_icon('list')} {t('software.manage')}"})
            action_map[str(idx)] = "manage"
            idx += 1

        try:
            key = select(
                breadcrumb=sub_breadcrumb,
                subtitle=t("prompt.select"),
                choices=choices,
                theme_key=_THEME_KEY,
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            break
        if key is None:
            break

        action = action_map.get(key)
        if action == "install":
            _do_install(sub_breadcrumb, cls, instance)
        elif action == "uninstall":
            _do_uninstall(sub_breadcrumb, cls, instance)
        elif action == "upgrade":
            if not getattr(cls, "has_upgrade", True):
                print_info(t("software.not_supported"))
                pause()
            else:
                _do_upgrade(sub_breadcrumb, cls, instance)
        elif action == "switch":
            _do_switch(sub_breadcrumb, cls, instance)
        elif action == "diagnose":
            _do_diagnose(sub_breadcrumb, cls, instance)
        elif action == "manage":
            _do_manage(sub_breadcrumb, cls, instance)


def _do_install(breadcrumb: list[str], cls: type, instance) -> None:
    """执行安装流程"""
    from core.platform import check_disk_space
    from core.constants import MIN_DISK_FREE_BYTES
    from software.base import InstallError
    from software.resolver import resolve_deps

    _name = t(f"software.{cls.key}") if _has_i18n(f"software.{cls.key}") else cls.description

    try:
        resolve_deps(instance, breadcrumb)
    except InstallError as e:
        _report(e, software=cls.key, action="install.resolve_deps")
        print_error(t("install.failed", name=_name, error=str(e)))
        pause()
        return
    except Exception as e:
        _report(e, software=cls.key, action="install.resolve_deps")
        print_error(t("error.unknown", error=str(e)))
        pause()
        return

    if getattr(cls, "has_version_picker", False):
        _do_install_version_picker(breadcrumb, cls, instance)
        return

    existing = instance.detect()
    if existing:
        print_warning(t("install.already", name=_name, version=existing))
        try:
            if not confirm(breadcrumb=breadcrumb, prompt=t("software.reinstall_confirm")):
                return
        except UserCancel:
            return

    if getattr(cls, "has_wizard", False):
        clear_screen()
        try:
            instance.install("latest")
        except InstallError as e:
            _report(e, software=cls.key, action="install")
            print_error(t("install.failed", name=_name, error=str(e)))
            pause()
        except Exception as e:
            _report(e, software=cls.key, action="install")
            print_error(t("error.unknown", error=str(e)))
            pause()
        return

    if getattr(cls, "has_install_version_selection", True):
        from core.progress import spinner
        with spinner(t("software.fetching_versions")):
            try:
                versions = instance.versions()
            except Exception:
                versions = []

        if not versions:
            print_error(t("software.no_versions"))
            pause()
            return

        ver_choices = [
            {"key": str(i + 1), "label": v, "hint": t("software.recommended") if i == 0 else ""}
            for i, v in enumerate(versions)
        ]
        try:
            ver_key = select(
                breadcrumb=[*breadcrumb, t("software.install")],
                subtitle=t("software.select_version"),
                choices=ver_choices,
                theme_key=_THEME_KEY,
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            return
        if not ver_key:
            return

        version = versions[int(ver_key) - 1]
    else:
        version = "latest"

    if not check_disk_space(MIN_DISK_FREE_BYTES):
        print_error(t("error.disk_space", required=f"{MIN_DISK_FREE_BYTES // 1024 // 1024}MB", free=""))
        pause()
        return

    if getattr(cls, "confirm_before_install", True):
        try:
            if not confirm(breadcrumb=breadcrumb, prompt=t("install.confirm", name=_name, version=version)):
                return
        except UserCancel:
            return

    clear_screen()
    print_header([*breadcrumb, t("software.install")])
    base_console.print()
    import time as _time
    _t0 = _time.monotonic()
    try:
        instance.install(version)
        installed_version = instance.detect() or version
        print_success(t("install.success", name=_name, version=installed_version, elapsed=_time.monotonic() - _t0))
    except InstallError as e:
        _report(e, software=cls.key, version=version, action="install")
        print_error(t("install.failed", name=_name, error=str(e)))
    except Exception as e:
        _report(e, software=cls.key, version=version, action="install")
        print_error(t("error.unknown", error=str(e)))
    pause()


def _do_install_version_picker(breadcrumb: list[str], cls: type, instance) -> None:
    """版本选择器安装流程：subtitle 显示已安装版本，直接列出版本，选中即装，无二次确认"""
    from core.platform import check_disk_space
    from core.constants import MIN_DISK_FREE_BYTES
    from software.base import InstallError
    from core.progress import spinner

    _name = t(f"software.{cls.key}") if _has_i18n(f"software.{cls.key}") else cls.description

    existing = instance.detect()
    installed_set: set[str] = (
        set(instance.installed_versions())
        if hasattr(instance, "installed_versions")
        else ({existing} if existing else set())
    )

    with spinner(t("software.fetching_versions")):
        try:
            versions = instance.versions()
        except Exception:
            versions = []

    if not versions:
        print_error(t("software.no_versions"))
        pause()
        return

    total = len(versions)
    total_pages = (total + _PAGE_SIZE - 1) // _PAGE_SIZE
    page = 0
    version: str | None = None

    while version is None:
        start = page * _PAGE_SIZE
        end = min(start + _PAGE_SIZE, total)
        page_items = versions[start:end]

        subtitle = (
            f"{t('software.installed')}: {existing}  [{page + 1}/{total_pages}]"
            if existing
            else f"{t('software.select_version')}  [{page + 1}/{total_pages}]"
        )

        choices = [
            {"key": str(i + 1), "label": f"[dim]{v}[/dim]" if v in installed_set else v}
            for i, v in enumerate(page_items)
        ]
        if page > 0:
            choices.append({"key": "p", "label": t("software.prev_page")})
        if page < total_pages - 1:
            choices.append({"key": "n", "label": t("software.next_page")})

        try:
            ver_key = select(
                breadcrumb=breadcrumb,
                subtitle=subtitle,
                choices=choices,
                theme_key=_THEME_KEY,
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            return
        if not ver_key:
            return

        if ver_key == "n":
            page += 1
            continue
        if ver_key == "p":
            page -= 1
            continue

        version = page_items[int(ver_key) - 1]

    # 已安装版本选中时弹确认
    if version in installed_set:
        try:
            if not confirm(breadcrumb=breadcrumb, prompt=t("software.reinstall_confirm")):
                return
        except UserCancel:
            return

    if not check_disk_space(MIN_DISK_FREE_BYTES):
        print_error(t("error.disk_space", required=f"{MIN_DISK_FREE_BYTES // 1024 // 1024}MB", free=""))
        pause()
        return

    clear_screen()
    print_header([*breadcrumb, t("software.install")])
    base_console.print()
    import time as _time
    _t0 = _time.monotonic()
    try:
        instance.install(version)
        print_success(t("install.success", name=_name, version=version, elapsed=_time.monotonic() - _t0))
    except InstallError as e:
        _report(e, software=cls.key, version=version, action="install")
        print_error(t("install.failed", name=_name, error=str(e)))
    except Exception as e:
        _report(e, software=cls.key, version=version, action="install")
        print_error(t("error.unknown", error=str(e)))
    pause()


def _do_uninstall(breadcrumb: list[str], cls: type, instance) -> None:
    """执行卸载流程：支持多版本选择卸载"""
    from software.base import UninstallError
    from core.progress import spinner

    _uname = t(f"software.{cls.key}") if _has_i18n(f"software.{cls.key}") else cls.description

    # 检查是否支持多版本（has_switch 的 recipe 有 installed_versions 方法）
    if getattr(cls, "has_switch", False) and hasattr(instance, "installed_versions"):
        installed = instance.installed_versions()
        active = instance._active_version() if hasattr(instance, "_active_version") else None

        if not installed:
            print_warning(t("software.not_installed_hint", name=_uname))
            pause()
            return

        # 构建版本选择列表
        choices = []
        for i, v in enumerate(installed):
            hint = t("software.current") if v == active else ""
            choices.append({"key": str(i + 1), "label": v, "hint": hint})
        choices.append({"key": str(len(installed) + 1), "label": t("software.uninstall_all")})

        try:
            ver_key = select(
                breadcrumb=[*breadcrumb, t("software.uninstall")],
                subtitle=t("software.select_uninstall_version"),
                choices=choices,
                theme_key=_THEME_KEY,
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            return
        if not ver_key:
            return

        key_int = int(ver_key)
        if key_int == len(installed) + 1:
            version_to_remove = None  # 卸载全部
            confirm_text = t("uninstall.confirm_all", name=_uname)
        else:
            version_to_remove = installed[key_int - 1]
            confirm_text = t("uninstall.confirm_version", name=_uname, version=version_to_remove)

        try:
            if not confirm(breadcrumb=breadcrumb, prompt=confirm_text):
                return
        except UserCancel:
            return

        clear_screen()
        print_header([*breadcrumb, t("software.uninstall")])
        base_console.print()
        try:
            instance.uninstall(version_to_remove)
            print_success(t("uninstall.success", name=_uname))
        except UninstallError as e:
            _report(e, software=cls.key, version=str(version_to_remove), action="uninstall")
            print_error(t("uninstall.failed", name=_uname, error=str(e)))
        except Exception as e:
            _report(e, software=cls.key, version=str(version_to_remove), action="uninstall")
            print_error(t("error.unknown", error=str(e)))
        pause()
        return

    # 普通卸载（无多版本支持）
    existing = instance.detect()
    if not existing:
        print_warning(t("software.not_installed_hint", name=_uname))
        pause()
        return

    if not getattr(cls, "has_wizard", False):
        try:
            if not confirm(breadcrumb=breadcrumb, prompt=t("uninstall.confirm", name=_uname)):
                return
        except UserCancel:
            return

    clear_screen()
    if not getattr(cls, "has_wizard", False):
        print_header([*breadcrumb, t("software.uninstall")])
        base_console.print()
    try:
        instance.uninstall()
    except UninstallError as e:
        _report(e, software=cls.key, action="uninstall")
        print_error(t("uninstall.failed", name=_uname, error=str(e)))
    except Exception as e:
        _report(e, software=cls.key, action="uninstall")
        print_error(t("error.unknown", error=str(e)))
    pause()


def _do_switch(breadcrumb: list[str], cls: type, instance) -> None:
    """执行版本切换流程"""
    from software.base import InstallError
    from core.progress import spinner

    _name = t(f"software.{cls.key}") if _has_i18n(f"software.{cls.key}") else cls.description

    if not hasattr(instance, "installed_versions"):
        print_info(t("software.not_supported"))
        pause()
        return

    installed = instance.installed_versions()
    active = instance._active_version() if hasattr(instance, "_active_version") else None

    if not installed:
        print_error(t("software.no_versions"))
        pause()
        return

    # 只显示已安装版本，切换仅限本地已有版本
    all_items: list[dict] = []
    for v in installed:
        hint = t("software.current") if v == active else t("software.installed_mark")
        label = f"[dim]{v}[/dim]" if v == active else v
        all_items.append({"label": label, "hint": hint, "version": v})

    if not all_items:
        print_error(t("software.no_versions"))
        pause()
        return

    total = len(all_items)
    total_pages = (total + _PAGE_SIZE - 1) // _PAGE_SIZE
    page = 0
    selected_item: dict | None = None
    switch_breadcrumb = [*breadcrumb, t("software.switch")]

    while selected_item is None:
        start = page * _PAGE_SIZE
        end = min(start + _PAGE_SIZE, total)
        page_items = all_items[start:end]

        subtitle = (
            f"{t('software.current_version')}: {active}  [{page + 1}/{total_pages}]"
            if active
            else f"{t('software.select_version')}  [{page + 1}/{total_pages}]"
        )

        choices = [
            {"key": str(i + 1), "label": item["label"], "hint": item["hint"]}
            for i, item in enumerate(page_items)
        ]
        if page > 0:
            choices.append({"key": "p", "label": t("software.prev_page")})
        if page < total_pages - 1:
            choices.append({"key": "n", "label": t("software.next_page")})

        try:
            ver_key = select(
                breadcrumb=switch_breadcrumb,
                subtitle=subtitle,
                choices=choices,
                theme_key=_THEME_KEY,
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            return
        if not ver_key:
            return

        if ver_key == "n":
            page += 1
            continue
        if ver_key == "p":
            page -= 1
            continue

        selected_item = page_items[int(ver_key) - 1]

    selected_version = selected_item["version"]

    if selected_version == active:
        print_info(t("software.current_version") + f": {selected_version}")
        pause()
        return

    clear_screen()
    print_header([*breadcrumb, t("software.switch")])
    try:
        instance.switch(selected_version)
        print_success(t("software.switch_success", name=_name, version=selected_version))
    except InstallError as e:
        _report(e, software=cls.key, version=selected_version, action="switch")
        print_error(t("install.failed", name=_name, error=str(e)))
    except Exception as e:
        _report(e, software=cls.key, version=selected_version, action="switch")
        print_error(t("error.unknown", error=str(e)))
    pause()


def _do_upgrade(breadcrumb: list[str], cls: type, instance) -> None:
    """执行升级流程"""
    from software.base import InstallError
    from core.progress import spinner
    from software.resolver import resolve_deps

    _name = t(f"software.{cls.key}") if _has_i18n(f"software.{cls.key}") else cls.description

    try:
        resolve_deps(instance, breadcrumb)
    except InstallError as e:
        _report(e, software=cls.key, action="upgrade.resolve_deps")
        print_error(t("install.failed", name=_name, error=str(e)))
        pause()
        return
    except Exception as e:
        _report(e, software=cls.key, action="upgrade.resolve_deps")
        print_error(t("error.unknown", error=str(e)))
        pause()
        return

    existing = instance.detect()
    if not existing:
        print_warning(t("software.not_installed_hint", name=_name))
        pause()
        return

    with spinner(t("software.fetching_versions")):
        try:
            versions = instance.versions()
        except Exception:
            versions = []

    if not versions:
        print_error(t("software.no_versions"))
        pause()
        return

    def _ver_tuple(v: str) -> tuple:
        return tuple(int(x) for x in v.split(".") if x.isdigit())

    # 只保留比当前版本更新的版本
    try:
        cur_tuple = _ver_tuple(existing)
        versions = [v for v in versions if _ver_tuple(v) > cur_tuple]
    except Exception:
        pass

    if not versions:
        print_info(t("software.already_latest", version=existing))
        pause()
        return

    total = len(versions)
    total_pages = (total + _PAGE_SIZE - 1) // _PAGE_SIZE
    page = 0
    new_version: str | None = None
    upg_breadcrumb = [*breadcrumb, t("software.upgrade")]
    upg_installed_set: set[str] = (
        set(instance.installed_versions())
        if hasattr(instance, "installed_versions")
        else ({existing} if existing else set())
    )

    while new_version is None:
        start = page * _PAGE_SIZE
        end = min(start + _PAGE_SIZE, total)
        page_items = versions[start:end]

        subtitle = f"{t('software.select_version')}  [{page + 1}/{total_pages}]"

        choices = [
            {"key": str(i + 1), "label": f"[dim]{v}[/dim]" if v in upg_installed_set else v}
            for i, v in enumerate(page_items)
        ]
        if page > 0:
            choices.append({"key": "p", "label": t("software.prev_page")})
        if page < total_pages - 1:
            choices.append({"key": "n", "label": t("software.next_page")})

        try:
            ver_key = select(
                breadcrumb=upg_breadcrumb,
                subtitle=subtitle,
                choices=choices,
                theme_key=_THEME_KEY,
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            return
        if not ver_key:
            return

        if ver_key == "n":
            page += 1
            continue
        if ver_key == "p":
            page -= 1
            continue

        new_version = page_items[int(ver_key) - 1]

    clear_screen()
    print_header([*breadcrumb, t("software.upgrade")])
    import time as _time
    _t0 = _time.monotonic()
    try:
        if new_version in upg_installed_set:
            # 已安装 → 直接切换，无需重新下载
            instance.switch(new_version)
            print_success(t("software.switch_success", name=_name, version=new_version))
        else:
            # 未安装 → 走安装升级流程
            base_console.print()
            instance.upgrade(new_version)
            print_success(t("upgrade.success", name=_name, elapsed=_time.monotonic() - _t0))
    except InstallError as e:
        _report(e, software=cls.key, version=new_version, action="upgrade")
        print_error(t("upgrade.failed", name=_name, error=str(e)))
    except Exception as e:
        _report(e, software=cls.key, version=new_version, action="upgrade")
        print_error(t("error.unknown", error=str(e)))
    pause()


def _do_diagnose(breadcrumb: list[str], cls: type, instance) -> None:
    """执行诊断"""
    clear_screen()
    try:
        instance.diagnose()
    except Exception as e:
        _report(e, software=cls.key, action="diagnose")
        print_error(t("error.unknown", error=str(e)))
        pause()


def _do_manage(breadcrumb: list[str], cls: type, instance) -> None:
    """执行管理界面"""
    try:
        instance.manage()
    except Exception as e:
        _report(e, software=cls.key, action="manage")
        print_error(t("error.unknown", error=str(e)))
        pause()


# ─── 软件列表 ─────────────────────────────────────────────────────────────────

def show_list() -> None:
    """显示所有可用软件及安装状态"""
    clear_screen()
    from software.registry import all_recipes
    from core.platform import get_platform
    from core.progress import spinner

    title_color = get_color(f"modules.{_THEME_KEY}.title")
    muted = get_color("muted")
    success = get_color("success")
    error_c = get_color("error")

    info = get_platform()
    recipes = [r for r in all_recipes() if info.os_type in r.platforms]

    tbl = Table(
        title=f"[{title_color}]{t('software.list')}[/{title_color}]",
        box=rich_box.ROUNDED,
        border_style=get_color(f"modules.{_THEME_KEY}.border"),
        header_style=get_color("table.header"),
    )
    tbl.add_column(t("software.name"), width=14)
    tbl.add_column(t("software.status"), width=14)
    tbl.add_column(t("software.version"), width=12)
    tbl.add_column(t("software.platforms"), width=28)

    with spinner(t("software.detecting")):
        for cls in recipes:
            instance = cls()
            ver = instance.detect()
            if ver:
                status_str = f"[{success}]{get_icon('success')} {t('software.installed')}[/{success}]"
                ver_str = f"[{success}]{ver}[/{success}]"
            else:
                status_str = f"[{muted}]─ {t('software.not_installed')}[/{muted}]"
                ver_str = f"[{muted}]─[/{muted}]"

            platforms_str = " / ".join(cls.platforms)
            tbl.add_row(
                f"{get_icon(cls.key)} {cls.key}",
                status_str,
                ver_str,
                f"[{muted}]{platforms_str}[/{muted}]",
            )

    console.print(tbl)
    pause()


# ─── 安装 ─────────────────────────────────────────────────────────────────────

def show_install() -> None:
    """选择软件 → 选择版本 → 确认 → 安装"""
    from software.registry import all_recipes, get as get_recipe
    from core.platform import get_platform, check_disk_space
    from core.constants import MIN_DISK_FREE_BYTES
    from software.base import InstallError

    info = get_platform()
    recipes = [r for r in all_recipes() if info.os_type in r.platforms]

    choices = [
        {"key": str(i + 1), "label": f"{get_icon(cls.key)} {cls.key}",
         "hint": cls().detect() or ""}
        for i, cls in enumerate(recipes)
    ]
    try:
        key = select(
            breadcrumb=["OpsKit", t("menu.software"), t("software.install")],
            subtitle=t("prompt.select"),
            choices=choices,
            theme_key=_THEME_KEY,
            back_label=f"{get_icon('back')} {t('menu.back')}",
        )
    except UserCancel:
        return
    if not key:
        return

    cls = recipes[int(key) - 1]
    instance = cls()

    _sname = t(f"software.{cls.key}") if _has_i18n(f"software.{cls.key}") else cls.description

    # 检查是否已安装
    existing = instance.detect()
    if existing:
        print_warning(t("install.already", name=_sname, version=existing))
        try:
            if not confirm(
                breadcrumb=["OpsKit", t("menu.software"), t("software.install")],
                prompt=t("software.reinstall_confirm"),
            ):
                return
        except UserCancel:
            return

    # 版本选择
    from core.progress import spinner
    with spinner(t("software.fetching_versions")):
        try:
            versions = instance.versions()
        except Exception:
            versions = []

    if not versions:
        print_error(t("software.no_versions"))
        pause()
        return

    ver_choices = [
        {"key": str(i + 1), "label": v, "hint": t("software.recommended") if i == 0 else ""}
        for i, v in enumerate(versions)
    ]
    try:
        ver_key = select(
            breadcrumb=["OpsKit", t("menu.software"), t("software.install"), cls.key],
            subtitle=t("software.select_version"),
            choices=ver_choices,
            theme_key=_THEME_KEY,
            back_label=f"{get_icon('back')} {t('menu.back')}",
        )
    except UserCancel:
        return
    if not ver_key:
        return

    version = versions[int(ver_key) - 1]

    # 磁盘空间检查
    if not check_disk_space(MIN_DISK_FREE_BYTES):
        print_error(t("error.disk_space",
                      required=f"{MIN_DISK_FREE_BYTES // 1024 // 1024}MB",
                      free=""))
        pause()
        return

    # 确认安装
    try:
        if not confirm(
            breadcrumb=["OpsKit", t("menu.software"), t("software.install")],
            prompt=t("install.confirm", name=_sname, version=version),
        ):
            return
    except UserCancel:
        return

    # 执行安装
    clear_screen()
    _iname = t(f"software.{cls.key}") if _has_i18n(f"software.{cls.key}") else cls.description
    print_header(["OpsKit", t("menu.software"), t("software.install")])
    base_console.print()
    import time as _time
    _t0 = _time.monotonic()
    try:
        instance.install(version)
        _vers = getattr(instance, "installed_versions", lambda: [])() or []
        _ver = _vers[-1] if _vers else version
        print_success(t("install.success", name=_iname, version=_ver, elapsed=_time.monotonic() - _t0))
    except InstallError as e:
        print_error(t("install.failed", name=_iname, error=str(e)))
    except Exception as e:
        print_error(t("error.unknown", error=str(e)))
    pause()


# ─── 已装软件 ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

_PAGE_SIZE = 9


def _parent_of(child_key: str, all_cls: list) -> type | None:
    """查找 child_key 所属的父级 recipe（通过 submenu_items 查找）"""
    for cls in all_cls:
        if not getattr(cls, "has_submenu", False):
            continue
        try:
            items = cls().submenu_items()
            if any(item.get("key") == child_key for item in items):
                return cls
        except Exception:
            pass
    return None


def show_installed() -> None:
    """已装软件：显示通过本工具安装过的软件，支持翻页。
    hidden 子软件（如 wg_client/wg_server）展示为父级（如 wireguard），相同父级去重。
    """
    from software.registry import all_recipes
    from core.platform import get_platform
    from core.progress import spinner

    info = get_platform()
    all_cls = [r for r in all_recipes() if info.os_type in r.platforms]

    installed: list[tuple] = []
    seen_keys: set[str] = set()
    with spinner(t("software.detecting")):
        for cls in all_cls:
            instance = cls()
            ver = instance.detect()
            if not ver:
                continue
            if getattr(cls, "hidden", False):
                parent_cls = _parent_of(cls.key, all_cls)
                if parent_cls is not None:
                    if parent_cls.key not in seen_keys:
                        seen_keys.add(parent_cls.key)
                        installed.append((parent_cls, parent_cls(), ver))
                    continue
            if cls.key not in seen_keys:
                seen_keys.add(cls.key)
                installed.append((cls, instance, ver))

    if not installed:
        print_warning(t("software.none_installed"))
        pause()
        return

    breadcrumb = ["OpsKit", t("menu.software"), t("software.installed_list")]
    page = 0
    total = len(installed)
    total_pages = (total + _PAGE_SIZE - 1) // _PAGE_SIZE

    while True:
        start = page * _PAGE_SIZE
        end = min(start + _PAGE_SIZE, total)
        page_items = installed[start:end]

        choices = [
            {"key": str(i + 1),
             "label": f"{get_icon(cls.key)} {t(f'software.{cls.key}')}",
             "hint": ver}
            for i, (cls, _, ver) in enumerate(page_items)
        ]
        if page > 0:
            choices.append({"key": "p", "label": t("software.prev_page")})
        if page < total_pages - 1:
            choices.append({"key": "n", "label": t("software.next_page")})

        try:
            key = select(
                breadcrumb=breadcrumb,
                subtitle=t("prompt.select"),
                choices=choices,
                theme_key=_THEME_KEY,
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            return
        if key is None:
            return

        if key == "n":
            page += 1
            continue
        if key == "p":
            page -= 1
            continue

        idx = int(key) - 1
        cls, instance, ver = page_items[idx]
        if getattr(cls, "has_submenu", False):
            _show_submenu(breadcrumb=breadcrumb, cls=cls)
        else:
            show_actions(breadcrumb=breadcrumb, cls=cls)


# ─── 卸载 ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def show_uninstall() -> None:
    """选择已安装软件 → 确认 → 卸载"""
    from software.registry import all_recipes
    from core.platform import get_platform
    from core.progress import spinner

    info = get_platform()
    recipes = [r for r in all_recipes() if info.os_type in r.platforms]

    installed: list[tuple] = []
    with spinner(t("software.detecting")):
        for cls in recipes:
            instance = cls()
            ver = instance.detect()
            if ver:
                installed.append((cls, instance, ver))

    if not installed:
        print_warning(t("software.none_installed"))
        pause()
        return

    choices = [
        {"key": str(i + 1), "label": f"{get_icon(cls.key)} {cls.key}",
         "hint": ver}
        for i, (cls, _, ver) in enumerate(installed)
    ]
    try:
        key = select(
            breadcrumb=["OpsKit", t("menu.software"), t("software.uninstall")],
            subtitle=t("prompt.select"),
            choices=choices,
            theme_key=_THEME_KEY,
            back_label=f"{get_icon('back')} {t('menu.back')}",
        )
    except UserCancel:
        return
    if not key:
        return

    cls, instance, ver = installed[int(key) - 1]

    _usname = t(f"software.{cls.key}") if _has_i18n(f"software.{cls.key}") else cls.description

    try:
        if not confirm(
            breadcrumb=["OpsKit", t("menu.software"), t("software.uninstall")],
            prompt=t("uninstall.confirm", name=_usname),
        ):
            return
    except UserCancel:
        return

    clear_screen()
    print_header(["OpsKit", t("menu.software"), t("software.uninstall")])
    base_console.print()
    try:
        instance.uninstall()
    except UninstallError as e:
        print_error(t("uninstall.failed", name=_usname, error=str(e)))
    except Exception as e:
        print_error(t("error.unknown", error=str(e)))

    pause()


# ─── 升级 ─────────────────────────────────────────────────────────────────────

def show_upgrade() -> None:
    """选择已安装软件 → 选择版本 → 确认 → 升级"""
    from software.registry import all_recipes
    from core.platform import get_platform
    from software.base import InstallError
    from core.progress import spinner

    info = get_platform()
    recipes = [r for r in all_recipes() if info.os_type in r.platforms]

    installed: list[tuple] = []
    with spinner(t("software.detecting")):
        for cls in recipes:
            instance = cls()
            ver = instance.detect()
            if ver:
                installed.append((cls, instance, ver))

    if not installed:
        print_warning(t("software.none_installed"))
        pause()
        return

    choices = [
        {"key": str(i + 1), "label": f"{get_icon(cls.key)} {cls.key}",
         "hint": ver}
        for i, (cls, _, ver) in enumerate(installed)
    ]
    try:
        key = select(
            breadcrumb=["OpsKit", t("menu.software"), t("software.upgrade")],
            subtitle=t("prompt.select"),
            choices=choices,
            theme_key=_THEME_KEY,
            back_label=f"{get_icon('back')} {t('menu.back')}",
        )
    except UserCancel:
        return
    if not key:
        return

    cls, instance, current_ver = installed[int(key) - 1]
    _upgname = t(f"software.{cls.key}") if _has_i18n(f"software.{cls.key}") else cls.description

    with spinner(t("software.fetching_versions")):
        try:
            versions = instance.versions()
        except Exception:
            versions = []

    if not versions:
        print_error(t("software.no_versions"))
        pause()
        return

    ver_choices = [
        {"key": str(i + 1), "label": v,
         "hint": t("software.current") if v == current_ver else ""}
        for i, v in enumerate(versions)
    ]
    try:
        ver_key = select(
            breadcrumb=["OpsKit", t("menu.software"), t("software.upgrade"), cls.key],
            subtitle=t("software.select_version"),
            choices=ver_choices,
            theme_key=_THEME_KEY,
            back_label=f"{get_icon('back')} {t('menu.back')}",
        )
    except UserCancel:
        return
    if not ver_key:
        return

    new_version = versions[int(ver_key) - 1]

    try:
        if not confirm(
            breadcrumb=["OpsKit", t("menu.software"), t("software.upgrade")],
            prompt=t("upgrade.available", name=_upgname, version=new_version),
        ):
            return
    except UserCancel:
        return

    clear_screen()
    print_header(["OpsKit", t("menu.software"), t("software.upgrade")])
    base_console.print()
    try:
        instance.upgrade(new_version)
        print_success(t("upgrade.success", name=_upgname, elapsed=0))
    except InstallError as e:
        print_error(t("upgrade.failed", name=_upgname, error=str(e)))
    except Exception as e:
        print_error(t("error.unknown", error=str(e)))

    pause()
