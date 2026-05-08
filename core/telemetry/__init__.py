"""core/telemetry — 错误上报门面层 + Provider 实现集合

业务代码只调此模块的三个公共函数，不直接接触任何 SDK。
切换上报平台：只改 config.yaml 中 telemetry.dsn，代码零改动。

Provider 分层：
  core/telemetry/__init__.py  ← 本文件（门面，永不改动）
  core/telemetry/base.py      ← TelemetryProvider 抽象接口
  core/telemetry/sentry_provider.py  ← Sentry SaaS / GlitchTip 共用实现
  core/telemetry/null_provider.py    ← DSN 为空或 enabled=false 时静默
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TelemetryProvider
from .null_provider import NullProvider
from .sentry_provider import SentryProvider

if TYPE_CHECKING:
    pass

__all__ = ["TelemetryProvider", "NullProvider", "SentryProvider", "init", "capture_error", "capture_message"]

_provider: TelemetryProvider | None = None


def init(cfg: dict | None = None) -> None:
    """初始化上报 Provider，在 main.py _boot() 中调用一次。

    三重防崩保证：
    1. init() 整体 try/except，失败静默
    2. SentryProvider.init() 内部 try/except
    3. NullProvider 始终可用作降级
    """
    global _provider
    try:
        if cfg is None:
            try:
                from core.config import load_config
                cfg = load_config()
            except Exception:
                cfg = {}

        t_cfg = cfg.get("telemetry", {})
        enabled = t_cfg.get("enabled", True)
        dsn = t_cfg.get("dsn", "").strip()

        if not enabled or not dsn:
            _provider = NullProvider()
            return

        try:
            from core.constants import APP_VERSION
            app_version = APP_VERSION
        except Exception:
            app_version = ""

        p = SentryProvider(dsn=dsn, app_version=app_version)
        p.init()
        _provider = p
    except Exception:
        try:
            _provider = NullProvider()
        except Exception:
            _provider = None


def capture_error(exc: Exception, **ctx) -> None:
    """上报异常，附带上下文标签（software/version/action 等）。

    任何情况下不会抛出异常，不影响主程序运行。
    """
    try:
        p = _provider
        if p is not None:
            p.capture_error(exc, **ctx)
    except Exception:
        pass


def capture_message(msg: str, level: str = "warning", **ctx) -> None:
    """上报关键事件（非异常，如下载超时降级、版本不支持等）。

    任何情况下不会抛出异常，不影响主程序运行。
    """
    try:
        p = _provider
        if p is not None:
            p.capture_message(msg, level=level, **ctx)
    except Exception:
        pass
