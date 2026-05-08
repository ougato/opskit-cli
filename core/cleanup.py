"""信号处理 + 优雅退出 + 清理回调注册"""
from __future__ import annotations

import atexit
import signal
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from core.constants import TIMEOUT_CLEANUP

_callbacks: list[Callable[[], Any]] = []
_lock = threading.Lock()
_registered = False


def register_cleanup(fn: Callable[[], Any]) -> None:
    """注册退出清理回调，退出时按注册逆序执行"""
    with _lock:
        _callbacks.append(fn)


def _run_callbacks() -> None:
    """执行所有清理回调，总超时 TIMEOUT_CLEANUP 秒"""
    with _lock:
        cbs = list(reversed(_callbacks))

    deadline = time.monotonic() + TIMEOUT_CLEANUP
    for cb in cbs:
        if time.monotonic() > deadline:
            break
        try:
            cb()
        except Exception:
            pass


def _signal_handler(signum: int, frame: Any) -> None:
    if signum == signal.SIGINT:
        raise KeyboardInterrupt
    _run_callbacks()
    sys.exit(0)


def init() -> None:
    """
    初始化信号处理器（进程生命周期内只注册一次）：

    - Linux/macOS：SIGINT / SIGTERM / SIGHUP
    - Windows：SIGINT + SIGBREAK + SetConsoleCtrlHandler（Ctrl+C / 关闭窗口）
    - atexit：保证普通 sys.exit() 时也执行清理
    """
    global _registered
    if _registered:
        return
    _registered = True

    atexit.register(_run_callbacks)

    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
    except (OSError, ValueError):
        pass

    if sys.platform.startswith("linux") or sys.platform == "darwin":
        try:
            signal.signal(signal.SIGHUP, _signal_handler)
        except (OSError, AttributeError):
            pass

    if sys.platform == "win32":
        _init_windows()


def _init_windows() -> None:
    """Windows 额外注册 SIGBREAK + SetConsoleCtrlHandler"""
    try:
        signal.signal(signal.SIGBREAK, _signal_handler)  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        pass

    try:
        import ctypes
        import ctypes.wintypes

        HandlerRoutine = ctypes.WINFUNCTYPE(
            ctypes.wintypes.BOOL, ctypes.wintypes.DWORD
        )

        @HandlerRoutine
        def _ctrl_handler(ctrl_type: int) -> bool:
            if ctrl_type == 0:
                # Ctrl+C：返回 True 防止 CRT 默认的 ExitProcess 导致
                # ACCESS_VIOLATION 崩溃。同时注入 \x03 到 stdin 缓冲区，
                # 让 msvcrt.getwch() / _read_line() 立即收到并触发 UserCancel。
                try:
                    _inject_ctrl_c()
                except Exception:
                    pass
                return True
            # 1=Ctrl+Break  2=Close  5=Logoff  6=Shutdown → 执行清理后退出
            if ctrl_type in (1, 2, 5, 6):
                _run_callbacks()
                return False
            return False

        def _inject_ctrl_c() -> None:
            """向 stdin 注入 Ctrl+C 字符，唤醒 msvcrt.getwch()"""
            import ctypes.wintypes
            KEY_EVENT = 0x0001
            STD_INPUT_HANDLE = ctypes.wintypes.DWORD(-10 & 0xFFFFFFFF)

            class KEY_EVENT_RECORD(ctypes.Structure):
                _fields_ = [
                    ("bKeyDown", ctypes.wintypes.BOOL),
                    ("wRepeatCount", ctypes.wintypes.WORD),
                    ("wVirtualKeyCode", ctypes.wintypes.WORD),
                    ("wVirtualScanCode", ctypes.wintypes.WORD),
                    ("uChar", ctypes.c_wchar),
                    ("dwControlKeyState", ctypes.wintypes.DWORD),
                ]

            class INPUT_RECORD(ctypes.Structure):
                _fields_ = [
                    ("EventType", ctypes.wintypes.WORD),
                    ("Event", KEY_EVENT_RECORD),
                ]

            handle = ctypes.windll.kernel32.GetStdHandle(STD_INPUT_HANDLE)
            rec = INPUT_RECORD()
            rec.EventType = KEY_EVENT
            rec.Event.bKeyDown = True
            rec.Event.wRepeatCount = 1
            rec.Event.wVirtualKeyCode = 0x43  # 'C'
            rec.Event.wVirtualScanCode = 0
            rec.Event.uChar = '\x03'
            rec.Event.dwControlKeyState = 0x0008  # LEFT_CTRL_PRESSED
            written = ctypes.wintypes.DWORD(0)
            ctypes.windll.kernel32.WriteConsoleInputW(
                handle, ctypes.byref(rec), 1, ctypes.byref(written)
            )

        ctypes.windll.kernel32.SetConsoleCtrlHandler(_ctrl_handler, True)
    except Exception:
        pass
