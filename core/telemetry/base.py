"""TelemetryProvider 抽象基类 — 定义上报接口契约"""
from __future__ import annotations

from abc import ABC, abstractmethod


class TelemetryProvider(ABC):
    """所有上报后端必须实现此接口，业务代码只依赖此抽象层"""

    @abstractmethod
    def init(self) -> None:
        """初始化 SDK / 连接"""
        ...

    @abstractmethod
    def capture_error(self, exc: Exception, **ctx) -> None:
        """上报异常，ctx 可含 software / version / action / platform 等标签"""
        ...

    @abstractmethod
    def capture_message(self, msg: str, level: str = "warning", **ctx) -> None:
        """上报非异常关键事件"""
        ...
