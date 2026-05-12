"""Nginx PlatformDriver ABC + 工厂函数"""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod


class PlatformDriver(ABC):

    @abstractmethod
    def detect(self) -> str | None:
        """检测已安装 nginx 版本，未安装返回 None"""
        ...

    @abstractmethod
    def install_pkg(self) -> None:
        """调用包管理器安装 nginx"""
        ...

    @abstractmethod
    def remove_pkg(self) -> None:
        """调用包管理器卸载 nginx"""
        ...

    @abstractmethod
    def enable_service(self) -> None:
        """启动并设置开机自启"""
        ...

    @abstractmethod
    def disable_service(self) -> None:
        """停止并禁用服务"""
        ...


def get_driver() -> PlatformDriver:
    if not sys.platform.startswith("linux"):
        raise RuntimeError(f"Nginx driver only supports Linux, got {sys.platform}")
    from .linux import LinuxDriver
    return LinuxDriver()
