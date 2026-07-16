"""插件间服务 — 插件向其他插件提供可复用能力（如通用云存储）

协议：
  - 提供方在 plugin.yaml 声明 ``provides: [storage]``，其 python entry 包
    暴露模块级函数 ``provide_service(name: str) -> object | None``。
  - 服务对象约定携带 ``service_api_version: int`` 属性；带交互界面的服务
    暴露 ``open_menu(breadcrumb: list[str], context: dict) -> None``。
  - 调用方在 plugin.yaml 声明 ``uses: [{service: storage, source: <git url>}]``，
    通过 SDK 的 ``get_service`` / ``open_service_menu`` 使用，无需依赖提供方包名。

安全模型与插件加载一致：提供方必须已通过信任确认与完整性校验才会被加载。
"""
from __future__ import annotations

import importlib
import sys
from typing import Protocol, runtime_checkable

from core.constants import APP_NAME
from core.logger import get_logger
from core.plugin import PluginManifest, list_manifests
from core.plugin_integrity import CHECK_MISMATCH, verify_checksums
from core.plugin_trust import compute_fingerprint, is_trusted

_log = get_logger("opskit.services")


@runtime_checkable
class MenuService(Protocol):
    """带交互界面的服务对象协议"""

    def open_menu(self, breadcrumb: list[str], context: dict[str, object]) -> None: ...


# 服务名 → 已解析的服务对象（进程内缓存）
_cache: dict[str, object] = {}


def _find_provider(name: str) -> PluginManifest | None:
    """在已安装插件清单中查找声明 provides 包含 name 的提供方"""
    for manifest in list_manifests():
        if name in manifest.provides:
            return manifest
    return None


def _resolve(manifest: PluginManifest, name: str) -> object | None:
    """加载提供方插件包并调用 provide_service(name)，失败返回 None（写日志）"""
    if verify_checksums(manifest.root) == CHECK_MISMATCH:
        _log.error("service %s: provider %s integrity check failed", name, manifest.name)
        return None
    if not is_trusted(manifest.name, compute_fingerprint(manifest.root)):
        _log.warning("service %s: provider %s not trusted", name, manifest.name)
        return None
    root_str = str(manifest.root.resolve())
    if root_str not in sys.path:
        sys.path.append(root_str)
    try:
        mod = importlib.import_module(str(manifest.entry))
        try:
            provide = mod.provide_service
        except AttributeError:
            _log.warning("service %s: provider %s has no provide_service()", name, manifest.name)
            return None
        return provide(name)
    except (Exception, SystemExit) as e:
        _log.warning("service %s: provider %s load failed: %r", name, manifest.name, e)
        return None


def get_service(name: str) -> object | None:
    """获取插件间服务对象；提供方未安装 / 未信任 / 加载失败返回 None"""
    if name in _cache:
        return _cache[name]
    manifest = _find_provider(name)
    if manifest is None:
        return None
    svc = _resolve(manifest, name)
    if svc is not None:
        _cache[name] = svc
    return svc


def invalidate_service_cache() -> None:
    """清空服务缓存（插件安装 / 更新 / 卸载后调用）"""
    _cache.clear()


def _guided_install(name: str, source: str) -> object | None:
    """提供方未安装时的引导安装：确认 → clone → 信任确认 → 返回服务对象"""
    from core.i18n import t
    from core.prompt import confirm, pause, print_header, clear_screen
    from core.theme import print_error, print_info

    if not source:
        return None
    from plugin import commands as plugin_commands
    from plugin.menu import confirm_trust

    if not confirm(
        breadcrumb=[APP_NAME],
        prompt=t("service.install_confirm", service=name),
        info_lines=[source],
    ):
        return None
    clear_screen()
    print_header([APP_NAME, t("service.installing", service=name)])
    print_info(t("plugin.cloning"))
    manifest, err = plugin_commands.install(source)
    if manifest is None:
        print_error(t("plugin.install_failed", error=err))
        pause()
        return None
    if not confirm_trust(manifest, source=source):
        plugin_commands.rollback_install(manifest)
        print_error(t("plugin.trust_declined", name=manifest.name))
        pause()
        return None
    return get_service(name)


def open_service_menu(
    name: str,
    breadcrumb: list[str],
    context: dict[str, object] | None = None,
    source: str = "",
) -> bool:
    """打开某服务的交互界面（供业务插件的菜单项一行接入）。

    提供方未安装且给了 source 时先引导安装。返回是否成功打开。
    """
    from core.i18n import t
    from core.prompt import pause
    from core.theme import print_error

    svc = get_service(name)
    if svc is None:
        svc = _guided_install(name, source)
    if svc is None:
        print_error(t("service.unavailable", service=name))
        pause()
        return False
    if not isinstance(svc, MenuService):
        _log.warning("service %s: no open_menu()", name)
        print_error(t("service.unavailable", service=name))
        pause()
        return False
    svc.open_menu(breadcrumb, dict(context or {}))
    return True
