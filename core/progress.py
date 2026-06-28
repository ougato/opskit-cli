"""安装/卸载进度管理器（样式走主题 Token）"""
from __future__ import annotations

import signal
import sys
import time
from contextlib import contextmanager
from typing import Iterator

from rich.console import Console
from rich.live import Live
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)
from rich.text import Text

from core.theme import get_color

console = Console()


# ─── 自定义秒数列（x.xxs 格式）─────────────────────────────────────────────────

class _SecondsElapsedColumn(ProgressColumn):
    """显示已用秒数，格式：x.xxs"""

    def render(self, task: Task) -> Text:
        elapsed = task.finished_time if task.finished else task.elapsed
        if elapsed is None:
            return Text("0.00s", style="dim")
        return Text(f"{elapsed:.2f}s", style="dim")


def _make_download_progress() -> Progress:
    """创建下载进度条（文件大小 + 速度显示）"""
    bar_color = get_color("progress.bar_complete")
    remaining_color = get_color("progress.bar_remaining")
    pct_color = get_color("progress.percentage")
    muted = get_color("muted")

    return Progress(
        SpinnerColumn(style=bar_color),
        TextColumn(f"[{muted}]{{task.description}}[/{muted}]"),
        BarColumn(
            complete_style=bar_color,
            finished_style=bar_color,
            style=remaining_color,
        ),
        DownloadColumn(),
        TransferSpeedColumn(),
        TextColumn(f"[{pct_color}]{{task.percentage:>3.0f}}%[/{pct_color}]"),
        console=console,
        transient=False,
    )


# ─── 多行步骤进度管理器（方案 B）───────────────────────────────────────────────

_GREEN = "#a6e3a1"
_RED   = "#f38ba8"
_MIN_DESC_WIDTH = 16
_BAR_WIDTH  = 20
_FILLED     = "█"
_EMPTY      = "░"
_PULSE_LEN  = 4


def _display_width(s: str) -> int:
    """计算字符串在终端的实际显示宽度（CJK 宽字符占 2 列）"""
    import unicodedata
    w = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        w += 2 if eaw in ("W", "F") else 1
    return w


def _pad_desc(desc: str, width: int) -> str:
    """将描述文本填充到指定列宽（考虑中文宽字符）"""
    dw = _display_width(desc)
    pad = max(0, width - dw)
    return desc + " " * pad


def _render_line(desc: str, pct: int, elapsed: float, color: str, width: int) -> str:
    """统一渲染一行进度条文本（完成/失败态）"""
    filled = int(_BAR_WIDTH * pct / 100)
    bar = _FILLED * filled + _EMPTY * (_BAR_WIDTH - filled)
    return f"[{color}]{_pad_desc(desc, width)} {bar}  {pct:>4d}%  {elapsed:.2f}s[/{color}]"


def _render_pulse(desc: str, frame: int, elapsed: float, color: str, width: int) -> str:
    """渲染脉冲动画行（高亮段在进度条内来回移动）"""
    total_frames = _BAR_WIDTH - _PULSE_LEN
    if total_frames <= 0:
        total_frames = 1
    cycle = frame % (total_frames * 2)
    if cycle < total_frames:
        pos = cycle
    else:
        pos = total_frames * 2 - cycle - 1
    bar = _EMPTY * pos + _FILLED * _PULSE_LEN + _EMPTY * (_BAR_WIDTH - pos - _PULSE_LEN)
    return f"[{color}]{_pad_desc(desc, width)} {bar}         {elapsed:.2f}s[/{color}]"


class _LiveTicker:
    """后台线程定时刷新 Live 显示脉冲动画；支持外部通过 set_pct() 切换为真实百分比"""

    def __init__(self, live: Live, desc: str, color: str, start: float, width: int) -> None:
        import threading
        self._live = live
        self._desc = desc
        self._color = color
        self._start = start
        self._width = width
        self._frame = 0
        self._pct: int = -1  # -1 表示脉冲模式，>=0 表示真实百分比模式
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def set_pct(self, pct: int) -> None:
        """外部调用：切换到真实百分比模式（0~100）"""
        with self._lock:
            self._pct = max(0, min(100, pct))

    def _run(self) -> None:
        while not self._stop_event.wait(0.1):
            elapsed = time.monotonic() - self._start
            with self._lock:
                pct = self._pct
            if pct >= 0:
                txt = Text.from_markup(
                    _render_line(self._desc, pct, elapsed, self._color, self._width)
                )
            else:
                txt = Text.from_markup(
                    _render_pulse(self._desc, self._frame, elapsed, self._color, self._width)
                )
            self._frame += 1
            try:
                self._live.update(txt)
            except Exception:
                break

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=1)


class MultiStepProgress:
    """
    多行步骤进度管理器。每步独占一行：
    - 进行中：主题色 █░ 进度条 50%，动态 elapsed（x.xxs）
    - 完成：整行变绿色 █ 100%
    - 失败：整行变红色 █░ 50%

    三态统一用 _render_line 渲染，字符完全一致。

    用法：
        with MultiStepProgress(["检测操作系统", "安装 WireGuard"]) as sp:
            sp.step("检测操作系统")
            do_check()
            sp.step("安装 WireGuard")
            do_install()
        # 异常由 __exit__ 自动捕获并将当前行标红

    也支持不传 descriptions（向后兼容），此时动态跟踪已见最大宽度。
    """

    def __init__(self, descriptions: list[str] | None = None) -> None:
        self._in_progress_color = get_color("progress.bar_active")
        self._live: Live | None = None
        self._ticker: _LiveTicker | None = None
        self._step_start: float = 0.0
        self._current_desc: str = ""
        self._start_time: float = 0.0
        self._interrupted: bool = False
        self._old_sigint = None
        self._win_handler_ref = None
        if descriptions:
            self._desc_width = max(_display_width(d) for d in descriptions) + 2
        else:
            self._desc_width = _MIN_DESC_WIDTH

    def __enter__(self) -> "MultiStepProgress":
        self._start_time = time.monotonic()
        self._install_sigint_shield()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self._restore_sigint()
        if self._live is not None:
            self._finish_current(failed=exc_type is not None)
        # 屏蔽期间收到过 Ctrl+C：正常退出时转为 KeyboardInterrupt，让上层中止并返回菜单
        if self._interrupted and exc_type is None:
            raise KeyboardInterrupt
        return False

    def _install_sigint_shield(self) -> None:
        """安装 SIGINT 屏蔽：Ctrl+C 只记录标志位，不在子进程读管道时崩溃渲染线程。

        前台进程组里的子进程仍会收到 SIGINT 而退出，因此 subprocess 会很快返回，
        随后在下一个 step()/complete() 边界抛出 KeyboardInterrupt，实现快速返回。
        """
        try:
            self._old_sigint = signal.getsignal(signal.SIGINT)
        except (OSError, ValueError):
            self._old_sigint = None
            return
        if sys.platform == "win32":
            try:
                import ctypes

                @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
                def _win_handler(event: int) -> int:
                    if event == 0:
                        self._interrupted = True
                        return 1
                    return 0

                self._win_handler_ref = _win_handler
                ctypes.windll.kernel32.SetConsoleCtrlHandler(_win_handler, True)
                signal.signal(signal.SIGINT, signal.SIG_IGN)
            except Exception:
                pass
        else:
            def _unix_handler(signum: int, frame: object) -> None:
                self._interrupted = True

            try:
                signal.signal(signal.SIGINT, _unix_handler)
            except (OSError, ValueError):
                pass

    def _restore_sigint(self) -> None:
        if sys.platform == "win32" and self._win_handler_ref is not None:
            try:
                import ctypes
                ctypes.windll.kernel32.SetConsoleCtrlHandler(self._win_handler_ref, False)
            except Exception:
                pass
        if self._old_sigint is not None:
            try:
                signal.signal(signal.SIGINT, self._old_sigint)
            except (OSError, ValueError):
                pass

    def _check_interrupt(self) -> None:
        if self._interrupted:
            raise KeyboardInterrupt

    def _start_live(self, desc: str) -> None:
        """启动当前步骤的 Live + 后台 ticker"""
        dw = _display_width(desc) + 2
        if dw > self._desc_width:
            self._desc_width = dw
        initial = Text.from_markup(
            _render_pulse(desc, 0, 0.0, self._in_progress_color, self._desc_width)
        )
        live = Live(initial, console=console, refresh_per_second=10, transient=True)
        live.start()
        self._live = live
        self._step_start = time.monotonic()
        self._current_desc = desc
        self._ticker = _LiveTicker(live, desc, self._in_progress_color, self._step_start, self._desc_width)

    def _finish_current(self, failed: bool) -> None:
        """停止 ticker → 关闭 Live → print 固定行"""
        if self._ticker is not None:
            self._ticker.stop()
            self._ticker = None
        if self._live is None:
            return
        elapsed = time.monotonic() - self._step_start
        self._live.stop()
        self._live = None

        if failed:
            line = _render_line(self._current_desc, 50, elapsed, _RED, self._desc_width)
        else:
            line = _render_line(self._current_desc, 100, elapsed, _GREEN, self._desc_width)
        console.print(line)

    def step(self, desc: str) -> None:
        """开始新一步：将上一步标绿，启动新步骤 Live"""
        self._check_interrupt()
        if self._live is not None:
            self._finish_current(failed=False)
        self._start_live(desc)

    def set_step_pct(self, pct: int) -> None:
        """更新当前步骤的百分比（0~100），切换为真实百分比渲染模式"""
        if self._ticker is not None:
            self._ticker.set_pct(pct)

    def complete(self) -> None:
        """手动标记当前步骤完成（__exit__ 正常退出时也会自动调用）"""
        if self._live is not None:
            self._finish_current(failed=False)
        self._check_interrupt()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start_time


# ─── 向后兼容：StepProgress 内部转发给 MultiStepProgress ──────────────────────

class StepProgress:
    """
    向后兼容包装，内部使用 MultiStepProgress。
    新代码请直接使用 MultiStepProgress。

    用法：
        with StepProgress(total=5) as sp:
            sp.step("配置仓库源")
            sp.step("下载安装包")
            sp.complete()
    """

    def __init__(self, total: int, description: str = "") -> None:
        self._total = total
        self._description = description
        self._impl = MultiStepProgress()

    def __enter__(self) -> "StepProgress":
        self._impl.__enter__()
        return self

    def __exit__(self, *args) -> bool:
        return self._impl.__exit__(*args)

    def step(self, desc: str) -> None:
        self._impl.step(desc)

    def complete(self) -> None:
        self._impl.complete()

    @property
    def elapsed(self) -> float:
        return self._impl.elapsed


# ─── 下载进度上下文管理器 ─────────────────────────────────────────────────────

class DownloadProgress:
    """
    下载进度管理器，支持字节级进度更新。

    用法：
        with DownloadProgress("nginx-1.25.4.tar.gz", total=15_000_000) as dp:
            for chunk in response.iter_bytes():
                dp.update(len(chunk))
    """

    def __init__(self, description: str, total: int | None = None) -> None:
        self._description = description
        self._total = total
        self._progress = _make_download_progress()
        self._task: TaskID | None = None
        self._start_time: float = 0.0

    def __enter__(self) -> "DownloadProgress":
        self._progress.start()
        self._task = self._progress.add_task(
            self._description,
            total=self._total,
        )
        self._start_time = time.monotonic()
        return self

    def __exit__(self, *_) -> None:
        self._progress.stop()

    def update(self, bytes_downloaded: int) -> None:
        """更新已下载字节数"""
        if self._task is not None:
            self._progress.update(self._task, advance=bytes_downloaded)

    def switch_source(self, new_description: str) -> None:
        """切换源时更新描述（不重置进度）"""
        if self._task is not None:
            self._progress.update(self._task, description=new_description)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start_time


# ─── 简单 spinner ──────────────────────────────────────────────────────────────

@contextmanager
def spinner(msg: str, raw: bool = False) -> Iterator[None]:
    """简单旋转等待（用于耗时操作但无进度数据时）

    通过 shield_ctrlc() 在 spinner 生命周期内屏蔽 Ctrl+C，
    退出后若收到过 Ctrl+C 则安全 raise KeyboardInterrupt。

    raw=True 时跳过 shield_ctrlc()，由外层调用方自行管理信号
    （例如与 _ctrlc_guard 配合使用，避免双层 handler 冲突）。
    """
    bar_color = get_color("progress.bar_complete")
    muted = get_color("muted")

    def _inner():
        with Progress(
            SpinnerColumn(style=bar_color),
            TextColumn(f"[{muted}]{msg}[/{muted}]"),
            console=console,
            transient=True,
        ) as p:
            p.add_task("", total=None)
            yield

    if raw:
        yield from _inner()
    else:
        from core.prompt import shield_ctrlc
        with shield_ctrlc():
            yield from _inner()
