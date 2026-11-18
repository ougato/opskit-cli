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

from collections import deque
import threading
from typing import TYPE_CHECKING

from .base import TelemetryProvider
from .null_provider import NullProvider
from .sentry_provider import SentryProvider

if TYPE_CHECKING:
    pass

__all__ = ["TelemetryProvider", "NullProvider", "SentryProvider", "init", "capture_error", "capture_message"]

_provider: TelemetryProvider | None = None
_provider_lock = threading.Lock()
# sentry-sdk uses a process-global client; hold this across both SDK
# initialization and capture so a provider cannot send to a newer DSN.
_provider_init_lock = threading.RLock()
_startup_updates = deque(maxlen=32)
_buffer_startup_updates = False
_init_generation = 0
_active_dsn = None
_prepared_dsn = None


def prepare_startup_updates(cfg: dict) -> None:
    """Buffer only sanitized update event names when reporting is enabled."""
    global _provider, _buffer_startup_updates, _init_generation, _active_dsn, _prepared_dsn
    section = cfg.get("telemetry", {})
    enabled = (isinstance(section, dict) and section.get("enabled") is True
               and isinstance(section.get("dsn"), str) and bool(section["dsn"].strip()))
    dsn = section["dsn"].strip() if enabled else None
    with _provider_lock:
        if enabled and _prepared_dsn == dsn:
            return
        _prepared_dsn = dsn
        _startup_updates.clear()
        if not enabled or _active_dsn != dsn:
            # A stale asynchronous initialization or provider must not send
            # a newly opted-in event to a previously configured endpoint.
            _init_generation += 1
            _provider = None if enabled else NullProvider()
            _active_dsn = None
        _buffer_startup_updates = enabled and (_provider is None or isinstance(_provider, NullProvider))


def defer_startup_update(event: str, level: str) -> bool:
    """Queue an already-sanitized event name without exception or context data."""
    with _provider_lock:
        if not _buffer_startup_updates:
            return False
        _startup_updates.append((event, level if level in {"info", "warning", "error"} else "warning"))
        return True


def _provider_is_authorized(provider: TelemetryProvider) -> bool:
    """Reject SDK events from an earlier or disabled provider."""
    with _provider_lock:
        return _provider is provider


def init(cfg: dict | None = None) -> None:
    """初始化上报 Provider，在 main.py _boot() 中调用一次。

    三重防崩保证：
    1. init() 整体 try/except，失败静默
    2. SentryProvider.init() 内部 try/except
    3. NullProvider 始终可用作降级
    """
    global _provider, _buffer_startup_updates, _init_generation, _active_dsn, _prepared_dsn
    with _provider_lock:
        _init_generation += 1
        generation = _init_generation
        # Sentry uses a process-global SDK. Do not keep sending through an old
        # provider while an initialization can change the SDK destination.
        _provider = None
        _active_dsn = None
    dsn = None
    try:
        if cfg is None:
            try:
                from core.config import load_config
                cfg = load_config()
            except Exception:
                cfg = {}

        t_cfg = cfg.get("telemetry", {})
        enabled = t_cfg.get("enabled") is True
        dsn = t_cfg.get("dsn", "").strip()

        if not enabled or not dsn:
            p = NullProvider()
        else:
            try:
                from core.constants import APP_VERSION
                app_version = APP_VERSION
            except Exception:
                app_version = ""

            p = SentryProvider(dsn=dsn, app_version=app_version)
            with _provider_init_lock:
                with _provider_lock:
                    if generation != _init_generation:
                        return
                p.init()
    except Exception:
        try:
            p = NullProvider()
        except Exception:
            p = None
    with _provider_lock:
        # A newer configuration decision must not be undone by a slower init.
        if generation != _init_generation:
            return
        _provider = p
        _active_dsn = dsn if p is not None and not isinstance(p, NullProvider) else None
        pending = (list(_startup_updates) if _active_dsn is not None and _active_dsn == _prepared_dsn else [])
        _startup_updates.clear()
        _buffer_startup_updates = False
        _prepared_dsn = _active_dsn
    for event, level in pending:
        with _provider_init_lock:
            with _provider_lock:
                if generation != _init_generation or _provider is not p:
                    break
            try:
                p.capture_message(event, level=level, component="updater")
            except Exception:
                pass


def capture_error(exc: Exception, **ctx) -> None:
    """上报异常，附带上下文标签（software/version/action 等）。

    任何情况下不会抛出异常，不影响主程序运行。
    """
    if not _provider_init_lock.acquire(blocking=False):
        return
    try:
        with _provider_lock:
            p = _provider
        if p is not None:
            p.capture_error(exc, **ctx)
    except Exception:
        pass
    finally:
        _provider_init_lock.release()


def capture_message(msg: str, level: str = "warning", **ctx) -> None:
    """上报关键事件（非异常，如下载超时降级、版本不支持等）。

    任何情况下不会抛出异常，不影响主程序运行。
    """
    if not _provider_init_lock.acquire(blocking=False):
        return
    try:
        with _provider_lock:
            p = _provider
        if p is not None:
            p.capture_message(msg, level=level, **ctx)
    except Exception:
        pass
    finally:
        _provider_init_lock.release()
