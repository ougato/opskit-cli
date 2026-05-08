"""PostgreSQL PlatformDriver ABC + 工厂函数（对齐 mongodb/driver.py）"""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from pathlib import Path


class PlatformDriver(ABC):
    """平台驱动接口，负责所有平台差异化操作"""

    @abstractmethod
    def install_tarball(self, version: str, tarball: Path) -> str:
        """解压 tarball/zip 到版本目录，返回 bin 目录路径字符串"""
        ...

    @abstractmethod
    def install_shim(self, fallback_bin: str) -> None:
        """安装 shim（psql/pg_ctl wrapper）并注入 PATH"""
        ...

    @abstractmethod
    def remove_shim(self) -> None:
        """卸载 shim 文件并清理 PATH 注入"""
        ...

    @abstractmethod
    def apply_version_link(self, bin_dir: str) -> None:
        """创建/更新 active symlink/junction 指向激活版本 bin 目录"""
        ...

    @abstractmethod
    def restore_original(self) -> None:
        """卸载时清理 active link / PATH 注入"""
        ...

    @abstractmethod
    def detect(self) -> str | None:
        """检测当前活跃 PostgreSQL 版本字符串，如 '17.2'"""
        ...


def get_driver() -> PlatformDriver:
    """工厂函数：根据当前平台返回对应驱动实例"""
    if sys.platform == "win32":
        from .windows import WindowsDriver
        return WindowsDriver()
    if sys.platform == "darwin":
        from .darwin import DarwinDriver
        return DarwinDriver()
    from .linux import LinuxDriver
    return LinuxDriver()
