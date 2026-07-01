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


class PrivilegeError(Exception):
    """需要 root 权限但无法提权（无 sudo / 提权被拒）时抛出。"""


def _has_sudo() -> bool:
    import shutil
    return shutil.which("sudo") is not None


def _sudo_passwordless() -> bool:
    """免密 sudo 可用（`sudo -n true` 成功）。"""
    try:
        return subprocess.run(
            ["sudo", "-n", "true"], capture_output=True
        ).returncode == 0
    except Exception:
        return False


def _prime_sudo() -> bool:
    """交互式预热 sudo 凭据（提示一次密码，缓存约 15 分钟），成功返回 True。

    预热后同一会话内的多次 run_as_root 不会在进度条中途再逐条弹密码。
    """
    try:
        return subprocess.run(["sudo", "-v"]).returncode == 0
    except Exception:
        return False


def ensure_root_for_action(instance, action: str = "") -> None:
    """特权闸门：在执行需要 root 的 install/uninstall/upgrade 前统一调用。

    - recipe 未声明 ``requires_root`` → 直接返回（用户态软件不受影响）。
    - 已是 root（或 Windows，另有提权机制）→ 返回。
    - 非 root Linux/macOS：
        免密 sudo 可用 → 返回（后续 ``run_as_root`` 全程无感）；
        需要密码 → 预热 sudo（仅提示一次密码）→ 成功返回，否则抛 :class:`PrivilegeError`；
        无 sudo → 抛 :class:`PrivilegeError`（提示用户切换到 root）。
    """
    if not getattr(type(instance), "requires_root", False):
        return
    if is_root() or sys.platform == "win32":
        return

    from core.i18n import t
    if not _has_sudo():
        raise PrivilegeError(t("privilege.need_root"))
    if _sudo_passwordless():
        return
    if _prime_sudo():
        return
    raise PrivilegeError(t("privilege.need_root"))


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


def write_root_file(path, content, mode: str = "0644") -> None:
    """以 root 权限写入系统文件（``/etc``、``/root`` 等）。

    - 已是 root / Windows：直接写入（自动创建父目录）。
    - 非 root Linux/macOS：写入临时文件 → ``run_as_root(["install", ...])`` 落位，
      避免用普通用户身份直接 ``write_text`` 系统路径导致的 Permission denied，
      同时保证落位文件属主为 root。

    Args:
        path: 目标路径（str 或 Path）。
        content: 文件内容（str 按 UTF-8 编码，或 bytes）。
        mode: 八进制权限字符串（如 ``"0644"`` / ``"0600"``）。
    """
    import tempfile
    from pathlib import Path

    p = Path(path)
    path = str(path)
    data = content.encode("utf-8") if isinstance(content, str) else content

    # 已是 root / Windows，或父目录当前用户可写（如用户态目录、临时目录）：直接写入。
    parent = p.parent
    if is_root() or sys.platform == "win32" or (parent.exists() and os.access(parent, os.W_OK)):
        parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        try:
            os.chmod(path, int(mode, 8))
        except OSError:
            pass
        return

    fd, tmp = tempfile.mkstemp()
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        parent = os.path.dirname(path)
        if parent:
            run_as_root(["mkdir", "-p", parent])
        run_as_root(["install", "-m", mode, tmp, path])
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
