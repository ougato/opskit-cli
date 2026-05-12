"""系统监控菜单 — 仪表盘 / 各细分页面 / 实时刷新"""
from __future__ import annotations

import os
import sys
import time

from rich import box as rich_box
from rich.columns import Columns
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from core.i18n import t
from core.prompt import select, pause, clear_screen, UserCancel, _kbhit, _read_key, console as base_console
from core.theme import get_color, get_icon, get_panel_config

console = Console()

_THEME_KEY = "monitor"
_REFRESH_INTERVAL = 2  # 秒


def _live_loop(render_fn) -> None:
    """通用实时刷新循环：screen=True 接管终端，退出后完整恢复，消除闪烁"""
    hint = Text(f"\n{t('prompt.pause')}", style=get_color('muted'))
    _stop = [False]

    def _grouped():
        return Group(render_fn(), hint)

    _handler_ref = None
    if os.name == 'nt':
        import ctypes
        _CTRL_C_EVENT = 0

        @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
        def _console_handler(ctrl_type):
            if ctrl_type == _CTRL_C_EVENT:
                _stop[0] = True
                return 1
            return 0

        _handler_ref = _console_handler
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_ref, True)
    else:
        import signal
        _old_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda s, f: _stop.__setitem__(0, True))

    _old_term = None
    _fd = -1
    if os.name != 'nt':
        import tty
        import termios
        _fd = sys.stdin.fileno()
        if os.isatty(_fd):
            try:
                _old_term = termios.tcgetattr(_fd)
            except Exception:
                _old_term = None

    try:
        with Live(_grouped(), refresh_per_second=1, screen=True) as live:
            if _old_term is not None:
                try:
                    tty.setcbreak(_fd)
                except Exception:
                    pass
            while True:
                if _stop[0]:
                    break
                for _ in range(20):
                    time.sleep(0.05)
                    if _stop[0]:
                        break
                    if _kbhit():
                        try:
                            sys.stdin.read(1)
                        except Exception:
                            pass
                        return
                else:
                    live.update(_grouped())
                    continue
                break
    except BaseException:
        pass
    finally:
        if _old_term is not None:
            import termios
            try:
                termios.tcsetattr(_fd, termios.TCSADRAIN, _old_term)
            except Exception:
                pass
        if os.name == 'nt':
            import ctypes
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_ref, False)
        else:
            import signal
            signal.signal(signal.SIGINT, _old_handler)


# ─── 菜单入口 ─────────────────────────────────────────────────────────────────

def entry() -> None:
    """系统监控模块入口"""
    while True:
        choices = [
            {"key": "1", "label": f"{get_icon('monitor')} {t('monitor.overview')}"},
            {"key": "2", "label": f"{get_icon('cpu')} {t('monitor.cpu')}"},
            {"key": "3", "label": f"{get_icon('memory')} {t('monitor.memory')}"},
            {"key": "4", "label": f"{get_icon('disk')} {t('monitor.disk')}"},
            {"key": "5", "label": f"{get_icon('network')} {t('monitor.network')}"},
            {"key": "6", "label": f"{get_icon('processes')} {t('monitor.processes')}"},
        ]
        try:
            key = select(
                breadcrumb=["OpsKit", t("menu.monitor")],
                subtitle=t("prompt.select"),
                choices=choices,
                theme_key=_THEME_KEY,
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            break
        if key is None:
            break

        try:
            if key == "1":
                show_dashboard()
            elif key == "2":
                show_cpu_detail()
            elif key == "3":
                show_memory_detail()
            elif key == "4":
                show_disk_detail()
            elif key == "5":
                show_network_detail()
            elif key == "6":
                show_processes()
        except KeyboardInterrupt:
            pass


# ─── 仪表盘（实时刷新） ───────────────────────────────────────────────────────

def _build_dashboard() -> Table:
    """构建一屏仪表盘 Table"""
    from monitor.commands import get_snapshot, fmt_bytes, fmt_uptime, fmt_percent_bar

    snap = get_snapshot()
    title_color = get_color(f"modules.{_THEME_KEY}.title")
    muted = get_color("muted")
    success = get_color("success")
    warning = get_color("warning")
    error = get_color("error")

    def _pct_color(pct: float) -> str:
        if pct >= 90:
            return error
        if pct >= 70:
            return warning
        return success

    grid = Table.grid(padding=(0, 2))
    grid.add_column(min_width=26)
    grid.add_column(min_width=26)

    # CPU
    cpu = snap.cpu
    cpu_bar = fmt_percent_bar(cpu.percent, 18)
    cpu_color = _pct_color(cpu.percent)
    cpu_panel = Panel(
        f"[{cpu_color}]{cpu_bar}[/{cpu_color}]\n"
        f"[{muted}]{cpu.count_physical}C/{cpu.count_logical}T  {cpu.freq_mhz:.0f} MHz[/{muted}]",
        title=f"[{title_color}]CPU[/{title_color}]",
        border_style=get_color(f"modules.{_THEME_KEY}.border"),
    )

    # 内存
    mem = snap.mem
    mem_bar = fmt_percent_bar(mem.percent, 18)
    mem_color = _pct_color(mem.percent)
    mem_panel = Panel(
        f"[{mem_color}]{mem_bar}[/{mem_color}]\n"
        f"[{muted}]{fmt_bytes(mem.used)} / {fmt_bytes(mem.total)}[/{muted}]",
        title=f"[{title_color}]{t('monitor.memory')}[/{title_color}]",
        border_style=get_color(f"modules.{_THEME_KEY}.border"),
    )

    # 磁盘（取第一个分区）
    if snap.disks:
        d = snap.disks[0]
        disk_bar = fmt_percent_bar(d.percent, 18)
        disk_color = _pct_color(d.percent)
        disk_panel = Panel(
            f"[{disk_color}]{disk_bar}[/{disk_color}]\n"
            f"[{muted}]{fmt_bytes(d.used)} / {fmt_bytes(d.total)}  {d.mountpoint}[/{muted}]",
            title=f"[{title_color}]{t('monitor.disk')}[/{title_color}]",
            border_style=get_color(f"modules.{_THEME_KEY}.border"),
        )
    else:
        disk_panel = Panel("[dim]N/A[/dim]", title=t("monitor.disk"))

    # 网络（取第一个接口）
    if snap.net:
        n = snap.net[0]
        speed_color = get_color("info")
        net_panel = Panel(
            f"[{speed_color}]↑ {fmt_bytes(n.speed_send)}/s[/{speed_color}]\n"
            f"[{success}]↓ {fmt_bytes(n.speed_recv)}/s[/{success}]\n"
            f"[{muted}]{n.name}[/{muted}]",
            title=f"[{title_color}]{t('monitor.network')}[/{title_color}]",
            border_style=get_color(f"modules.{_THEME_KEY}.border"),
        )
    else:
        net_panel = Panel("[dim]N/A[/dim]", title=t("monitor.network"))

    # 系统运行时间
    uptime_str = fmt_uptime(snap.uptime_seconds)
    load_str = ""
    if snap.load_avg:
        load_str = f"  Load: {snap.load_avg[0]:.2f} {snap.load_avg[1]:.2f} {snap.load_avg[2]:.2f}"
    uptime_panel = Panel(
        f"[{success}]{uptime_str}[/{success}]{load_str}",
        title=f"[{title_color}]{t('monitor.uptime')}[/{title_color}]",
        border_style=get_color(f"modules.{_THEME_KEY}.border"),
    )

    grid.add_row(cpu_panel, mem_panel)
    grid.add_row(disk_panel, net_panel)
    grid.add_row(uptime_panel, "")
    return grid


def _titled(content, title: str) -> Panel:
    """给监控内容包裹统一标题面板"""
    cfg = get_panel_config()
    border_style = get_color(f"modules.{_THEME_KEY}.border")
    title_color = get_color(f"modules.{_THEME_KEY}.title")
    import rich.box as _rb
    box_name = cfg.get("box", "ROUNDED")
    box_obj = getattr(_rb, box_name, rich_box.ROUNDED)
    return Panel(
        content,
        title=f"[{title_color}]{title}[/{title_color}]",
        border_style=border_style,
        box=box_obj,
        padding=tuple(cfg.get("padding", [0, 1])),
    )


def show_dashboard() -> None:
    """实时刷新仪表盘，按任意键返回"""
    title = f"{t('menu.monitor')} — {t('monitor.overview')}"

    def _render():
        return _titled(_build_dashboard(), title)

    _live_loop(_render)


# ─── CPU 详情 ─────────────────────────────────────────────────────────────────

def show_cpu_detail() -> None:
    """CPU 详细信息（每核使用率）"""
    from monitor.commands import get_cpu, fmt_percent_bar

    muted = get_color("muted")
    success = get_color("success")
    warning = get_color("warning")
    error_c = get_color("error")
    title = f"{t('menu.monitor')} — {t('monitor.cpu')}"

    def _render():
        cpu = get_cpu()
        tbl = Table(
            box=rich_box.SIMPLE,
            show_header=True,
            header_style=get_color("table.header"),
            show_edge=False,
        )
        tbl.add_column(t("monitor.core"), style=muted, width=10)
        tbl.add_column(t("monitor.usage"), width=32)

        total_bar = fmt_percent_bar(cpu.percent, 24)
        color = error_c if cpu.percent >= 90 else (warning if cpu.percent >= 70 else success)
        tbl.add_row(f"[{muted}]{t('monitor.total')}[/{muted}]", f"[{color}]{total_bar}[/{color}]")

        for i, pct in enumerate(cpu.per_core):
            bar = fmt_percent_bar(pct, 24)
            c = error_c if pct >= 90 else (warning if pct >= 70 else success)
            tbl.add_row(f"Core {i}", f"[{c}]{bar}[/{c}]")
        return _titled(tbl, title)

    _live_loop(_render)


# ─── 内存详情 ─────────────────────────────────────────────────────────────────

def show_memory_detail() -> None:
    """内存 + Swap 详细信息"""
    from monitor.commands import get_mem, fmt_bytes, fmt_percent_bar

    muted = get_color("muted")
    success = get_color("success")
    warning = get_color("warning")
    error_c = get_color("error")
    title = f"{t('menu.monitor')} — {t('monitor.memory')}"

    def _render():
        mem = get_mem()

        def _row_color(pct):
            return error_c if pct >= 90 else (warning if pct >= 70 else success)

        tbl = Table(
            box=rich_box.SIMPLE,
            show_header=True,
            header_style=get_color("table.header"),
            show_edge=False,
        )
        tbl.add_column(t("monitor.type"), style=muted, width=10)
        tbl.add_column(t("monitor.used"), width=14)
        tbl.add_column(t("monitor.total"), width=14)
        tbl.add_column(t("monitor.usage"), width=28)

        c = _row_color(mem.percent)
        tbl.add_row(
            t("monitor.memory"),
            f"[{c}]{fmt_bytes(mem.used)}[/{c}]",
            fmt_bytes(mem.total),
            f"[{c}]{fmt_percent_bar(mem.percent, 20)}[/{c}]",
        )
        if mem.swap_total > 0:
            c2 = _row_color(mem.swap_percent)
            tbl.add_row(
                "Swap",
                f"[{c2}]{fmt_bytes(mem.swap_used)}[/{c2}]",
                fmt_bytes(mem.swap_total),
                f"[{c2}]{fmt_percent_bar(mem.swap_percent, 20)}[/{c2}]",
            )
        return _titled(tbl, title)

    _live_loop(_render)


# ─── 磁盘详情 ─────────────────────────────────────────────────────────────────

def show_disk_detail(pause_after: bool = True) -> None:
    """磁盘分区使用情况（静态，按任意键返回）"""
    from monitor.commands import get_disks, fmt_bytes, fmt_percent_bar

    muted = get_color("muted")
    success = get_color("success")
    warning = get_color("warning")
    error_c = get_color("error")
    title = f"{t('menu.monitor')} — {t('monitor.disk')}"

    disks = get_disks()
    tbl = Table(
        box=rich_box.SIMPLE,
        show_header=True,
        header_style=get_color("table.header"),
        show_edge=False,
    )
    tbl.add_column(t("monitor.mount"), style=muted, width=16)
    tbl.add_column(t("monitor.device"), width=18)
    tbl.add_column(t("monitor.fstype"), width=8)
    tbl.add_column(t("monitor.used"), width=10)
    tbl.add_column(t("monitor.free"), width=10)
    tbl.add_column(t("monitor.total"), width=10)
    tbl.add_column(t("monitor.usage"), width=28)

    for d in disks:
        c = error_c if d.percent >= 90 else (warning if d.percent >= 70 else success)
        tbl.add_row(
            d.mountpoint,
            d.device,
            d.fstype,
            fmt_bytes(d.used),
            fmt_bytes(d.free),
            fmt_bytes(d.total),
            f"[{c}]{fmt_percent_bar(d.percent, 20)}[/{c}]",
        )

    clear_screen()
    base_console.print(_titled(tbl, title))
    if pause_after:
        pause()


# ─── 网络详情 ─────────────────────────────────────────────────────────────────

def show_network_detail() -> None:
    """网络接口实时流量"""
    from monitor.commands import get_net, fmt_bytes

    muted = get_color("muted")
    info_c = get_color("info")
    success = get_color("success")
    title = f"{t('menu.monitor')} — {t('monitor.network')}"

    def _render():
        interfaces = get_net()
        tbl = Table(
            box=rich_box.SIMPLE,
            show_header=True,
            header_style=get_color("table.header"),
            show_edge=False,
        )
        tbl.add_column(t("monitor.interface"), style=muted, width=14)
        tbl.add_column("↑ " + t("monitor.send_speed"), width=14)
        tbl.add_column("↓ " + t("monitor.recv_speed"), width=14)
        tbl.add_column("↑ " + t("monitor.total_sent"), width=12)
        tbl.add_column("↓ " + t("monitor.total_recv"), width=12)

        for n in interfaces:
            tbl.add_row(
                n.name,
                f"[{info_c}]{fmt_bytes(n.speed_send)}/s[/{info_c}]",
                f"[{success}]{fmt_bytes(n.speed_recv)}/s[/{success}]",
                fmt_bytes(n.bytes_sent),
                fmt_bytes(n.bytes_recv),
            )
        return _titled(tbl, title)

    _live_loop(_render)


# ─── 进程列表 ─────────────────────────────────────────────────────────────────

def show_processes() -> None:
    """Top 进程列表（按 CPU 排序，实时刷新）"""
    from monitor.commands import get_top_processes

    muted = get_color("muted")
    error_c = get_color("error")
    warning = get_color("warning")
    success = get_color("success")
    title = f"{t('menu.monitor')} — {t('monitor.processes')} (Top 15)"

    def _render():
        procs = get_top_processes(n=15, sort_by="cpu")
        tbl = Table(
            box=rich_box.SIMPLE,
            show_header=True,
            header_style=get_color("table.header"),
            show_edge=False,
        )
        tbl.add_column("PID", style=muted, width=8)
        tbl.add_column(t("monitor.name"), width=22)
        tbl.add_column("CPU%", width=10)
        tbl.add_column("MEM%", width=10)
        tbl.add_column(t("monitor.status"), style=muted, width=12)

        for p in procs:
            cpu_c = error_c if p.cpu_percent >= 80 else (warning if p.cpu_percent >= 50 else success)
            mem_c = error_c if p.mem_percent >= 50 else (warning if p.mem_percent >= 30 else success)
            tbl.add_row(
                str(p.pid),
                p.name[:22],
                f"[{cpu_c}]{p.cpu_percent:.1f}[/{cpu_c}]",
                f"[{mem_c}]{p.mem_percent:.1f}[/{mem_c}]",
                p.status,
            )
        return _titled(tbl, title)

    _live_loop(_render)
