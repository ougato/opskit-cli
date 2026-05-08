"""权限检测 + 自动提权（sudo / runas）"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Sequence


def is_root() -> bool:
    """检测当前是否具有 root / admin 权限"""
    from core.platform import get_platform
    return get_platform().is_root


def require_root(message: str = "") -> None:
    """
    若非 root / admin 则尝试自动提权重启，失败则打印错误退出。

    - Linux/macOS: 用 sudo 重新执行当前命令
    - Windows: 用 ShellExecute runas 提权重启
    """
    if is_root():
        return

    from core.i18n import t
    from core.theme import print_error, get_icon
    msg = message or t("error.permission")

    os_type = sys.platform
    if os_type == "win32":
        _elevate_windows()
    else:
        _elevate_unix(msg)


def _elevate_unix(msg: str) -> None:
    """Linux/macOS 使用 sudo 重新执行当前进程"""
    import shutil
    if not shutil.which("sudo"):
        from core.theme import print_error
        print_error(msg)
        sys.exit(1)

    cmd: list[str] = ["sudo", sys.executable] + sys.argv
    try:
        os.execvp("sudo", cmd)
    except Exception as e:
        from core.theme import print_error
        print_error(str(e))
        sys.exit(1)


def _elevate_windows() -> None:
    """Windows 使用 ShellExecute 以 runas 提权重新启动"""
    try:
        import ctypes
        params = " ".join(f'"{a}"' for a in sys.argv)
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1
        )
        if ret > 32:
            sys.exit(0)
        else:
            from core.theme import print_error
            from core.i18n import t
            print_error(t("error.permission"))
            sys.exit(1)
    except Exception as e:
        from core.theme import print_error
        print_error(str(e))
        sys.exit(1)


def run_as_root(cmd: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
    """
    以 root / admin 权限执行命令。

    - 已是 root：直接执行
    - Linux/macOS：sudo {cmd}
    - Windows：直接执行（需调用方已确保权限）
    """
    if is_root() or sys.platform == "win32":
        return subprocess.run(list(cmd), **kwargs)

    import shutil
    if shutil.which("sudo"):
        return subprocess.run(["sudo"] + list(cmd), **kwargs)

    return subprocess.run(list(cmd), **kwargs)
