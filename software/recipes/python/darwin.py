"""macOS 平台驱动：继承 LinuxDriver，暂无覆写（差异极小）"""
from __future__ import annotations

from .linux import LinuxDriver


class DarwinDriver(LinuxDriver):
    """macOS 驱动，逻辑与 Linux 一致，预留覆写入口"""
    pass
