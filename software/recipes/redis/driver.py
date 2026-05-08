"""Redis PlatformDriver ABC + 工厂函数（对齐 MongoDB/MySQL 模式）"""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from pathlib import Path


class PlatformDriver(ABC):

    @abstractmethod
    def install_binary(self, version: str, src: Path) -> str:
        """安装二进制（解压 deb/zip/编译源码）到版本目录，返回 bin 目录路径字符串"""
        ...

    @abstractmethod
    def install_shim(self, fallback_bin: str) -> None:
        """安装 shim 脚本并注入 PATH"""
        ...

    @abstractmethod
    def remove_shim(self) -> None:
        """删除 shim 文件并清理 PATH 注入"""
        ...

    @abstractmethod
    def shim_active(self) -> bool:
        """检查 shim 目录是否在当前 PATH 中"""
        ...

    @abstractmethod
    def apply_version_link(self, bin_dir: str) -> None:
        """创建/更新 active 符号链接或 junction，使版本切换立即生效"""
        ...

    @abstractmethod
    def restore_original(self) -> None:
        """卸载时清理全局链接/PATH 注入"""
        ...

    @abstractmethod
    def detect_active(self) -> str | None:
        """检测当前激活的 Redis 版本"""
        ...

    @abstractmethod
    def snapshot_pre_install(self) -> dict:
        """安装前需要保存到 snapshot 的平台特有数据"""
        ...


def get_driver() -> PlatformDriver:
    if sys.platform == "win32":
        from .windows import WindowsDriver
        return WindowsDriver()
    if sys.platform == "darwin":
        from .darwin import DarwinDriver
        return DarwinDriver()
    from .linux import LinuxDriver
    return LinuxDriver()
