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
import hmac
from functools import wraps
from typing import Protocol, runtime_checkable

from core.constants import APP_NAME
from core.logger import get_logger
from core.plugin import PluginManifest

_log = get_logger("opskit.services")


@runtime_checkable
class MenuService(Protocol):
    """带交互界面的服务对象协议"""

    def open_menu(self, breadcrumb: list[str], context: dict[str, object]) -> None: ...


# 服务名 → 已解析的服务对象（进程内缓存）
_cache: dict[str, object] = {}


def _service_budget(function):
    @wraps(function)
    def run(*args, **kwargs):
        from core.plugin_sandbox import _execution_budget, _invocations
        if getattr(_invocations, "inspection_scope", False):
            return function(*args, **kwargs)
        with _execution_budget():
            return function(*args, **kwargs)
    return run


def _selected_manifests(select, value):
    from core.paths import plugins_dir
    from core.plugin_sandbox import _read_inventory
    return _read_inventory(plugins_dir(), select=select, value=value)


@_service_budget
def authorize_service(caller: str, service: str, caller_fingerprint: str, provider_fingerprint: str):
    """Approve exactly the caller's declared contract using current immutable identities."""
    from core.config import configuration_transaction
    from core.plugin_trust import authorization_transaction, grant_service, revoke_service
    from core.plugin_sandbox import SandboxError, check_authorization, compute_fingerprint
    if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in (caller_fingerprint, provider_fingerprint)):
        raise ValueError("invalid service approval fingerprints")
    with configuration_transaction(), authorization_transaction():
        callers = _selected_manifests("name", caller)
        provider = _find_provider(service)
        if len(callers) != 1 or provider is None:
            raise ValueError("service approval identities unavailable or ambiguous")
        caller_manifest = callers[0]
        requirement = next((item for item in caller_manifest.uses if item["service"] == service), None)
        contract = provider.service_contracts.get(service)
        if (not requirement or not contract or requirement.get("provider") != provider.name
                or requirement.get("api_version") != contract["api_version"]
                or not isinstance(requirement.get("methods"), list)
                or not set(requirement["methods"]).issubset(contract["methods"])):
            raise ValueError("service requirements do not match provider contract; migrate manifests")
        actual_caller = check_authorization(caller_manifest)
        actual_provider = check_authorization(provider)
        if not hmac.compare_digest(actual_caller, caller_fingerprint) or not hmac.compare_digest(actual_provider, provider_fingerprint):
            raise ValueError("fingerprint does not match current service identities")
        grant_service(caller, actual_caller, service, provider.name, actual_provider,
                      requirement["api_version"], requirement["methods"])
        if compute_fingerprint(caller_manifest.root) != actual_caller or compute_fingerprint(provider.root) != actual_provider:
            revoke_service(caller, service)
            raise SandboxError("plugin changed during service authorization")
        return {"caller": caller, "service": service, "provider": provider.name,
                "api_version": requirement["api_version"], "methods": requirement["methods"], "authorized": True}


@_service_budget
def _find_provider(name: str) -> PluginManifest | None:
    """在已安装插件清单中查找声明 provides 包含 name 的提供方"""
    providers = _selected_manifests("service", name)
    return providers[0] if len(providers) == 1 else None


class ServiceProxy:
    """JSON RPC proxy; no provider code or Python object enters the platform."""

    @property
    def service_api_version(self):
        return describe_service(self.name)["service_api_version"]

    def __init__(self, name: str):
        self.name = name

    def open_menu(self, breadcrumb, context):
        return call_service(self.name, "open_menu", [breadcrumb, context])

    def __getattr__(self, method: str):
        if method.startswith("_"):
            raise AttributeError(method)
        return lambda *args, **kwargs: call_service(self.name, method, list(args), kwargs)


@_service_budget
def call_service(name: str, method: str, args: list | None = None,
                 kwargs: dict | None = None, *, caller: str = "platform"):
    from core.plugin_sandbox import SandboxError, check_authorization, invoke
    if not re.fullmatch(r"[a-z][a-z0-9_]*", caller):
        raise SandboxError("invalid caller identity")
    manifest = _find_provider(name)
    if manifest is None:
        raise SandboxError("service provider unavailable")
    check_authorization(manifest)
    if not method.isidentifier() or method.startswith("_"):
        raise SandboxError("private service method denied")
    resolve_service_access(manifest, name, method, caller)
    if method == "open_menu":
        args = list(args or [])
        context = dict(args[1] if len(args) > 1 else {})
        context["caller"] = caller
        args = [args[0], context]
    return invoke(manifest, "service.call", service=name, method=method,
                  args=args, kwargs=kwargs, caller=caller)


@_service_budget
def resolve_service_access(provider, service, method, caller):
    """Validate declared contracts and return content-bound live grant checks."""
    from core.plugin_sandbox import SandboxError, check_authorization, _check_live_authorizations
    if not isinstance(caller, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", caller):
        raise SandboxError("invalid caller identity")
    contract = provider.service_contracts.get(service)
    if not contract:
        raise SandboxError("service contract missing; migrate provider manifest")
    if method and method not in contract["methods"]:
        raise SandboxError("service method not declared by provider")
    if caller == "platform":
        return ()
    callers = _selected_manifests("name", caller)
    if len(callers) != 1:
        raise SandboxError("caller identity is ambiguous")
    caller_manifest = callers[0]
    requirement = next((item for item in caller_manifest.uses if item["service"] == service), None)
    if (not requirement or requirement.get("provider") != provider.name
            or requirement.get("api_version") != contract["api_version"]
            or not isinstance(requirement.get("methods"), list)
            or not set(requirement["methods"]).issubset(contract["methods"])):
        raise SandboxError("service access not declared and authorized; migrate caller manifest")
    if method and method not in requirement["methods"]:
        raise SandboxError("service method not declared by caller")
    caller_digest = check_authorization(caller_manifest)
    provider_digest = check_authorization(provider)
    methods = [method] if method else requirement["methods"]
    edges = tuple((caller, caller_digest, service, provider.name, provider_digest, contract["api_version"], value)
                  for value in methods)
    _check_live_authorizations(extra=((caller, caller_digest), (provider.name, provider_digest)), extra_edges=edges)
    return edges


@_service_budget
def describe_service(name: str, *, caller="platform"):
    from core.plugin_sandbox import SandboxError, invoke
    manifest = _find_provider(name)
    if manifest is None:
        raise SandboxError("service provider unavailable")
    resolve_service_access(manifest, name, "", caller)
    return invoke(manifest, "service.describe", service=name, caller=caller)


@_service_budget
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
        try:
            plugin_commands.rollback_install(manifest)
        except (OSError, ValueError, RuntimeError) as exc:
            print_error(t("plugin.install_failed", error=str(exc)))
            pause()
            return None
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
