"""WireGuard PlatformDriver ABC + 工厂函数"""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod


class PlatformDriver(ABC):

    @abstractmethod
    def check_compat(self) -> None:
        """检查平台兼容性，不兼容时抛 InstallError"""
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
