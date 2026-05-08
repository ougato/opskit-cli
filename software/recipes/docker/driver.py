"""Docker PlatformDriver ABC + 工厂函数"""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod


class PlatformDriver(ABC):

    @abstractmethod
    def ensure_deps(self) -> None:
        """安装前置依赖（repo 初始化等）"""
        ...

    @abstractmethod
    def pkg_name(self, version: str) -> str:
        """返回包管理器安装时的包名"""
        ...

    @abstractmethod
    def install_pkg(self, pkg: str) -> None:
        """调用包管理器安装"""
        ...

    @abstractmethod
    def remove_pkg(self) -> None:
        """调用包管理器卸载"""
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
    if sys.platform == "win32":
        from .windows import WindowsDriver
        return WindowsDriver()
    elif sys.platform == "darwin":
        from .darwin import DarwinDriver
        return DarwinDriver()
    else:
        from .linux import LinuxDriver
        return LinuxDriver()
