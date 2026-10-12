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

import re
from typing import Protocol, runtime_checkable

from core.constants import APP_NAME
from core.logger import get_logger
from core.plugin import PluginManifest, list_manifests

_log = get_logger("opskit.services")


@runtime_checkable
class MenuService(Protocol):
    """带交互界面的服务对象协议"""

    def open_menu(self, breadcrumb: list[str], context: dict[str, object]) -> None: ...


# 服务名 → 已解析的服务对象（进程内缓存）
_cache: dict[str, object] = {}


def _find_provider(name: str) -> PluginManifest | None:
    """在已安装插件清单中查找声明 provides 包含 name 的提供方"""
    providers = [manifest for manifest in list_manifests() if name in manifest.provides]
    return providers[0] if len(providers) == 1 else None


class ServiceProxy:
    """JSON RPC proxy; no provider code or Python object enters the platform."""
    service_api_version = 2

    def __init__(self, name: str):
        self.name = name

    def open_menu(self, breadcrumb, context):
        return call_service(self.name, "open_menu", [breadcrumb, context])

    def __getattr__(self, method: str):
        if method.startswith("_"):
            raise AttributeError(method)
        return lambda *args, **kwargs: call_service(self.name, method, list(args), kwargs)


def call_service(name: str, method: str, args: list | None = None,
                 kwargs: dict | None = None, *, caller: str = "platform"):
    from core.plugin_sandbox import SandboxError, check_authorization, invoke
    if not re.fullmatch(r"[a-z][a-z0-9_]*", caller):
        raise SandboxError("invalid caller identity")
    if caller != "platform":
        callers = [m for m in list_manifests() if m.name == caller]
        if len(callers) != 1:
            raise SandboxError("caller identity is ambiguous")
        check_authorization(callers[0])
        if name not in {u["service"] for u in callers[0].uses}:
            raise SandboxError("service access not declared and authorized")
    manifest = _find_provider(name)
    if manifest is None:
        raise SandboxError("service provider unavailable")
    check_authorization(manifest)
    if not method.isidentifier() or method.startswith("_"):
        raise SandboxError("private service method denied")
    if method == "open_menu":
        args = list(args or [])
        context = dict(args[1] if len(args) > 1 else {})
        context["caller"] = caller
        args = [args[0], context]
    return invoke(manifest, "service.call", service=name, method=method,
                  args=args, kwargs=kwargs, caller=caller)


def get_service(name: str) -> object | None:
    """获取插件间服务对象；提供方未安装 / 未信任 / 加载失败返回 None"""
    manifest = _find_provider(name)
    if manifest is None:
        return None
    from core.plugin_sandbox import SandboxError, check_authorization
    try:
        check_authorization(manifest)
    except SandboxError:
        _cache.pop(name, None)
        return None
    return _cache.setdefault(name, ServiceProxy(name))


def invalidate_service_cache() -> None:
    """清空服务缓存（插件安装 / 更新 / 卸载后调用）"""
    _cache.clear()


def _guided_install(name: str, source: str) -> object | None:
    """提供方未安装时的引导安装：clone → 信任确认 → 返回服务对象"""
    from core.i18n import t
    from core.prompt import pause, print_header, clear_screen
    from core.theme import print_error, print_info

    if not source:
        return None
    from plugin import commands as plugin_commands
    from plugin.menu import _display_name, confirm_trust

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
        print_error(t("plugin.trust_declined", name=_display_name(manifest)))
        pause()
        return None
    svc = get_service(name)
    if svc is None:
        print_error(t("service.unavailable", service=name))
        pause()
    return svc


def open_service_menu(
    name: str,
    breadcrumb: list[str],
    context: dict[str, object] | None = None,
    source: str = "",
) -> bool:
    """打开某服务的交互界面（供业务插件的菜单项一行接入）。

    提供方未安装且给了 source 时自动安装（仅信任确认一次交互）。返回是否成功打开。
    """
    from core.i18n import t
    from core.prompt import pause
    from core.theme import print_error

    svc = get_service(name)
    if svc is None and source:
        svc = _guided_install(name, source)
        if svc is None:
            # 引导安装内部已展示具体失败原因并等待返回，不再叠加提示
            return False
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
