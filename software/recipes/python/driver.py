"""PlatformDriver 抽象接口：所有平台驱动必须实现此接口"""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod


class PlatformDriver(ABC):
    """平台驱动接口，负责所有平台差异化操作"""

    @abstractmethod
    def ensure_uv(self) -> str:
        """确保 uv 可执行，返回 uv 可执行路径"""
        ...

    @abstractmethod
    def install_shim(self, fallback_bin: str) -> None:
        """安装 shim（python/python3 wrapper）并注入 PATH"""
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
    def apply_version_link(self, new_bin: str) -> None:
        """创建/更新系统 python3 指向（symlink 或 shim 路由）"""
        ...

    @abstractmethod
    def restore_original(
        self,
        symlink_path: str,
        original_target: str | None,
        had_local_bin_path: bool,
    ) -> None:
        """卸载时恢复系统原始 python3 状态"""
        ...

    @abstractmethod
    def detect_active(self) -> str | None:
        """检测当前活跃 Python 版本字符串，如 '3.13.13'"""
        ...

    @abstractmethod
    def snapshot_pre_install(self) -> dict:
        """
        记录安装前状态，返回写入快照的额外字段。
        Linux/macOS 记录 symlink_path / original_target / had_local_bin_path。
        Windows 返回空 dict（无需 symlink 信息）。
        """
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
