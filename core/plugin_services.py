"""插件间服务 — 插件向其他插件提供可复用能力（如通用云存储）

协议：
  - 提供方在 plugin.yaml 声明 ``provides: [storage]``，其 python entry 包
    暴露模块级函数 ``provide_service(name: str) -> object | None``。
  - 服务对象约定携带 ``service_api_version: int`` 属性；带交互界面的服务
    暴露 ``open_menu(breadcrumb: list[str], context: dict) -> None``。
  - 调用方在 plugin.yaml 声明提供方身份、服务版本及允许的方法，
    通过 SDK 的 ``get_service`` / ``open_service_menu`` 使用。

安全模型与插件加载一致：提供方必须已通过信任确认与完整性校验才会被加载。
"""
from __future__ import annotations

import re
import hmac
from functools import wraps
from core.plugin import PluginManifest

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
