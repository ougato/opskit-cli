"""PlatformDriver 抽象接口：所有平台驱动必须实现此接口"""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod


class PlatformDriver(ABC):
    """平台驱动接口，负责所有平台差异化操作"""

    @abstractmethod
    def install_tarball(self, version: str, tarball: "Path") -> str:
        """
        解压 tarball 到版本目录，返回 bin 目录路径字符串。
        Linux/macOS: tar -C install_dir -xzf tarball
        Windows: zipfile 解压到版本目录
        """
        ...

    @abstractmethod
    def install_shim(self, fallback_bin: str) -> None:
        """安装 shim（go/gofmt wrapper）并注入 PATH"""
        ...

    @abstractmethod
    def remove_shim(self) -> None:
        """卸载 shim 文件并清理 PATH 注入"""
        ...

    @abstractmethod
    def shim_active(self) -> bool:
        """检测 shims 目录是否已在当前进程 PATH 中"""
        ...

    @abstractmethod
    def apply_version_link(self, bin_dir: str) -> None:
        """创建/更新系统 go 指向（symlink 或 shim 路由）"""
        ...

    @abstractmethod
    def restore_original(self) -> None:
        """卸载时清理系统 go 相关 PATH/symlink"""
        ...

    @abstractmethod
    def detect_active(self) -> str | None:
        """检测当前活跃 Go 版本字符串，如 '1.23.4'"""
        ...

    @abstractmethod
    def snapshot_pre_install(self) -> dict:
        """记录安装前状态，返回写入快照的额外字段"""
        ...


def get_driver() -> PlatformDriver:
    """工厂函数：根据当前平台返回对应驱动实例"""
    if sys.platform == "win32":
        from .windows import WindowsDriver
        return WindowsDriver()
    elif sys.platform == "darwin":
        from .darwin import DarwinDriver
        return DarwinDriver()
    else:
        from .linux import LinuxDriver
        return LinuxDriver()
