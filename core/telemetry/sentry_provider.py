"""SentryProvider — 基于 sentry-sdk 的上报实现

兼容所有 Sentry SDK 协议的平台：
- Sentry SaaS (sentry.io)
- GlitchTip (自建)
- 其他 Sentry 协议兼容平台

切换平台只需修改 DSN，本文件代码不变。
"""
from __future__ import annotations

import sys

from .base import TelemetryProvider


class SentryProvider(TelemetryProvider):
    """使用 sentry-sdk 上报，支持任意 Sentry 协议兼容平台"""

    def __init__(self, dsn: str, app_version: int | str = "") -> None:
        self._dsn = dsn
        self._app_version = str(app_version)
        self._ready = False
        self._base_tags: dict = {}
        self._base_contexts: dict = {}

    def init(self) -> None:
        try:
            import sentry_sdk
            sentry_sdk.init(
                dsn=self._dsn,
                release=self._app_version,
                default_integrations=False,
                send_default_pii=False,
                traces_sample_rate=0.0,
                server_name="",
            )
            self._collect_env()
            self._ready = True
        except Exception:
            pass

    def _collect_env(self) -> None:
        """收集运行环境 + 网络信息，存入实例变量，每次上报时一并传入"""
        try:
            from core.platform import get_platform
            info = get_platform()

            try:
                import core.mirror as _mm
                region = _mm._region or ""
                if not region:
                    region = _mm._load_cache().get("region", "unknown")
            except Exception:
                region = "unknown"

            try:
                import httpx as _httpx
                _r = _httpx.head("https://github.com", timeout=4, follow_redirects=False)
                github_reachable = "yes" if _r.status_code < 500 else "no"
            except Exception:
                github_reachable = "no"

            self._base_tags = {
                "os":               f"{info.os_name} {info.os_version}".strip(),
                "arch":             info.arch,
                "python":           info.python_version,
                "is_root":          str(info.is_root).lower(),
                "region":           region,
                "github_reachable": github_reachable,
            }
            self._base_contexts = {
                "runtime_env": {
                    "os_type":        info.os_type,
                    "os_name":        info.os_name,
                    "os_version":     info.os_version,
                    "arch":           info.arch,
                    "python_version": info.python_version,
                    "is_root":        info.is_root,
                    "pkg_manager":    info.pkg_manager,
                    "init_system":    info.init_system,
                },
                "network": {
                    "region":           region,
                    "github_reachable": github_reachable,
                },
            }
        except Exception:
            pass

    def capture_error(self, exc: Exception, **ctx) -> None:
        if not self._ready:
            return
        try:
            import sentry_sdk
            tags = {**self._base_tags, **self._build_ctx_tags(ctx)}
            contexts = dict(self._base_contexts)
            with sentry_sdk.new_scope() as scope:
                for k, v in tags.items():
                    scope.set_tag(k, v)
                for name, data in contexts.items():
                    scope.set_context(name, data)
                sentry_sdk.capture_exception(exc)
        except Exception:
            pass

    def capture_message(self, msg: str, level: str = "warning", **ctx) -> None:
        if not self._ready:
            return
        try:
            import sentry_sdk
            tags = {**self._base_tags, **self._build_ctx_tags(ctx)}
            contexts = dict(self._base_contexts)
            with sentry_sdk.new_scope() as scope:
                for k, v in tags.items():
                    scope.set_tag(k, v)
                for name, data in contexts.items():
                    scope.set_context(name, data)
                sentry_sdk.capture_message(msg, level=level)
        except Exception:
            pass

    def _build_ctx_tags(self, ctx: dict) -> dict:
        """将 capture_error() 调用时传入的 ctx 转为 tag 字典"""
        tags: dict = {}
        try:
            for key, val in ctx.items():
                if val is not None:
                    tags[key] = str(val)
        except Exception:
            pass
        return tags
