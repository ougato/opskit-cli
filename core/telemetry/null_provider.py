"""NullProvider — 空操作实现，DSN 为空或 enabled=false 时使用"""
from __future__ import annotations

from .base import TelemetryProvider


class NullProvider(TelemetryProvider):
    """完全静默，不上报任何数据"""

    def init(self) -> None:
        pass

    def capture_error(self, exc: Exception, **ctx) -> None:
        pass

    def capture_message(self, msg: str, level: str = "warning", **ctx) -> None:
        pass
