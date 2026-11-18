"""信号处理 + 优雅退出 + 清理回调注册"""
from __future__ import annotations

import atexit
import logging
import signal
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from core.constants import TIMEOUT_CLEANUP

_callbacks: list[Callable[[], Any]] = []
_lock = threading.RLock()
_init_lock = threading.RLock()
_registered = False
_atexit_registered = False
_registered_signals: set[int] = set()
_running = False
_windows_ctrl_handler = None
_console_input_lock = threading.Lock()
_console_input_active = False
_console_input_interrupted = False
_log = logging.getLogger(__name__)


def add_exception_note(error: BaseException, message: str) -> None:
    """Retain secondary cleanup evidence without replacing the primary error.

    Python 3.10 keeps the same metadata for callers, although its native
    traceback formatter does not display exception notes.
    """
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(message)
    else:
        error.__notes__ = [*getattr(error, "__notes__", []), message]


def register_cleanup(fn: Callable[[], Any]) -> None:
    """注册退出清理回调，退出时按注册逆序执行"""
    with _lock:
        _callbacks.append(fn)


def _run_callbacks() -> None:
    """逆序执行一次；期限只限制是否开始下一项，不能中断同步回调。"""
    global _running
    with _lock:
        if _running:
            return
        _running = True
        cbs = list(reversed(_callbacks))
        _callbacks.clear()

    deadline = time.monotonic() + TIMEOUT_CLEANUP
    index = 0
    try:
        while index < len(cbs) and time.monotonic() <= deadline:
            cb = cbs[index]
            index += 1
            try:
                cb()
            except Exception as exc:
                _log.warning("cleanup callback failed (%s)", type(exc).__name__)
    finally:
        with _lock:
            # 未开始的回调仍可在下一次退出清理时执行；新注册项保持逆序优先。
            _callbacks[0:0] = reversed(cbs[index:])
            _running = False


def _signal_handler(signum: int, frame: Any) -> None:
    if signum == signal.SIGINT:
        raise KeyboardInterrupt
    _run_callbacks()
    sys.exit(0)


def _begin_console_input() -> bool:
    """Mark a main-thread console read so Ctrl+C can wake its polling loop."""
    global _console_input_active, _console_input_interrupted
    if threading.current_thread() is not threading.main_thread():
        return False
    with _console_input_lock:
        _console_input_active = True
        _console_input_interrupted = False
    return True


def _console_interrupt_pending() -> bool:
    with _console_input_lock:
        return _console_input_interrupted


def _end_console_input() -> bool:
    global _console_input_active, _console_input_interrupted
    with _console_input_lock:
        interrupted = _console_input_interrupted
        _console_input_active = False
        _console_input_interrupted = False
        return interrupted


def init() -> None:
    """
    初始化信号处理器（进程生命周期内只注册一次）：

    - Linux/macOS：SIGINT / SIGTERM / SIGHUP
    - Windows：SIGINT + SIGBREAK + SetConsoleCtrlHandler（Ctrl+C / 关闭窗口）
    - atexit：保证普通 sys.exit() 时也执行清理
    """
    global _atexit_registered, _registered
    with _init_lock:
        if not _atexit_registered:
            atexit.register(_run_callbacks)
            _atexit_registered = True

        # Python only allows signal handlers to be installed on the main
        # thread. A worker's early init must not prevent a later main-thread
        # call from completing registration.
        if threading.current_thread() is not threading.main_thread():
            return

        handlers = [(signal.SIGINT, signal.default_int_handler),
                    (signal.SIGTERM, _signal_handler)]
        if sys.platform.startswith("linux") or sys.platform == "darwin":
            handlers.append((signal.SIGHUP, _signal_handler))
        elif sys.platform == "win32":
            handlers.append((signal.SIGBREAK, _signal_handler))

        for signum, handler in handlers:
            if signum in _registered_signals:
                continue
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                continue
            _registered_signals.add(signum)
        _registered = all(signum in _registered_signals for signum, _ in handlers)

        # 如果首次注册失败，允许后续 init 重试；成功后不得重复注册。
        if sys.platform == "win32" and _windows_ctrl_handler is None:
            _init_windows()


def _init_windows() -> None:
    """Windows 额外注册 SetConsoleCtrlHandler。"""
    global _windows_ctrl_handler
    if _windows_ctrl_handler is not None:
        return
    try:
        import ctypes
        import ctypes.wintypes

        HandlerRoutine = ctypes.WINFUNCTYPE(
            ctypes.wintypes.BOOL, ctypes.wintypes.DWORD
        )

        @HandlerRoutine
        def _ctrl_handler(ctrl_type: int) -> bool:
            global _console_input_interrupted
            if ctrl_type == 0:
                # Only consume Ctrl+C while the main thread is reading a key.
                # Otherwise let Python's normal SIGINT handler interrupt work.
                with _console_input_lock:
                    if _console_input_active:
                        _console_input_interrupted = True
                        return True
                return False
            # 1=Ctrl+Break  2=Close  5=Logoff  6=Shutdown → 执行清理后退出
            if ctrl_type in (1, 2, 5, 6):
                _run_callbacks()
                return False
            return False

        if ctypes.windll.kernel32.SetConsoleCtrlHandler(_ctrl_handler, True):
            # Windows 保存原生函数指针，不持有 Python 回调对象。
            _windows_ctrl_handler = _ctrl_handler
        else:
            _log.warning("Windows console handler registration failed")
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        _log.warning("Windows console handler registration failed (%s)", type(exc).__name__)
