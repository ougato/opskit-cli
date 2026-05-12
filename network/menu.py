"""网络工具菜单"""
from __future__ import annotations

import ctypes
import signal
import sys
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from rich import box as rich_box
from rich.console import Console
from rich.live import Live
from rich.table import Table

from core.i18n import t
from core.prompt import select, text_input, pause, console, UserCancel, clear_screen, shield_ctrlc
from core.theme import get_color, get_icon, print_success, print_error, print_info, print_warning

console = Console()
_THEME_KEY = "network"

_COMMON_PORTS = [21, 22, 23, 25, 53, 80, 110, 143, 443, 445, 3306, 5432, 6379, 8080, 8443, 27017]


@contextmanager
def _ctrlc_guard(kill_ref: "list | None" = None, cancel_event: "threading.Event | None" = None) -> Iterator[bool]:
    """即时 Ctrl+C 响应上下文管理器。

    Ctrl+C 时：
    - 设置 cancel_event（如提供）
    - 调用 kill_ref[0].kill()（如提供且已填充）
    - 退出后 yield 值为 True 表示被中断

    用法：
        kill_ref, cancel_event = [], threading.Event()
        with _ctrlc_guard(kill_ref, cancel_event) as interrupted_ref:
            do_work(kill_ref=kill_ref, cancel_event=cancel_event)
        if interrupted_ref[0]:
            return
    """
    interrupted = [False]
    _handler_ref = None
    _old_sigint = signal.getsignal(signal.SIGINT)

    def _do_interrupt() -> None:
        interrupted[0] = True
        if cancel_event is not None:
            cancel_event.set()
        if kill_ref:
            try:
                kill_ref[0].kill()
            except Exception:
                pass

    if sys.platform == "win32":
        @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
        def _win_handler(event: int) -> int:
            if event == 0:
                _do_interrupt()
                return 1
            return 0
        _handler_ref = _win_handler
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_ref, True)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    else:
        def _unix_handler(signum: int, frame: object) -> None:
            _do_interrupt()
        signal.signal(signal.SIGINT, _unix_handler)

    try:
        yield interrupted
    finally:
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_ref, False)
        try:
            signal.signal(signal.SIGINT, _old_sigint)
        except (OSError, ValueError):
            pass


def entry() -> None:
    while True:
        choices = [
            {"key": "1", "label": f"{get_icon('ping')} {t('network.ping')}"},
            {"key": "2", "label": f"{get_icon('traceroute')} {t('network.traceroute')}"},
            {"key": "3", "label": f"{get_icon('dns')} {t('network.dns')}"},
            {"key": "4", "label": f"{get_icon('port_scan')} {t('network.port_scan')}"},
            {"key": "5", "label": f"{get_icon('speed_test')} {t('network.speed_test')}"},
            {"key": "6", "label": f"{get_icon('public_ip')} {t('network.public_ip')}"},
        ]
        try:
            key = select(
                breadcrumb=["OpsKit", t("menu.network")],
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
                show_ping()
            elif key == "2":
                show_traceroute()
            elif key == "3":
                show_dns()
            elif key == "4":
                show_port_scan()
            elif key == "5":
                show_speed_test()
            elif key == "6":
                show_public_ip()
        except KeyboardInterrupt:
            pass


def show_ping(host: str | None = None, pause_after: bool = True) -> None:
    from network.commands import ping

    if not host:
        try:
            host = text_input(
                breadcrumb=["OpsKit", t("menu.network"), t("network.ping")],
                prompt=t("network.enter_host"),
                default="8.8.8.8",
            )
        except UserCancel:
            return
    if not host:
        return

    clear_screen()
    from core.progress import spinner
    kill_ref: list = []
    result_ref: list = []

    with _ctrlc_guard(kill_ref=kill_ref) as interrupted:
        with spinner(f"ping {host}...", raw=True):
            result_ref.append(ping(host, count=4, kill_ref=kill_ref))

    if interrupted[0]:
        return

    result = result_ref[0] if result_ref else None
    if result is None:
        return

    title_color = get_color(f"modules.{_THEME_KEY}.title")
    muted = get_color("muted")
    success = get_color("success")
    error_c = get_color("error")

    status_color = success if result.reachable else error_c
    console.print(f"\n[{title_color}]{t('network.ping')}: {host}[/{title_color}]")
    console.print(f"  IP: [{muted}]{result.ip}[/{muted}]")
    console.print(f"  [{status_color}]{result.packets_recv}/{result.packets_sent} packets received[/{status_color}]")
    if result.reachable:
        console.print(f"  min/avg/max: [{success}]{result.min_ms:.1f}/{result.avg_ms:.1f}/{result.max_ms:.1f} ms[/{success}]")
    if pause_after:
        pause()


def show_traceroute(host: str | None = None, pause_after: bool = True) -> None:
    from network.commands import traceroute

    if not host:
        try:
            host = text_input(
                breadcrumb=["OpsKit", t("menu.network"), t("network.traceroute")],
                prompt=t("network.enter_host"),
                default="8.8.8.8",
            )
        except UserCancel:
            return
    if not host:
        return

    clear_screen()
    title_color = get_color(f"modules.{_THEME_KEY}.title")
    muted = get_color("muted")
    console.print(f"\n[{title_color}]{t('network.traceroute')}: {host}[/{title_color}]\n")

    kill_ref: list = []

    with _ctrlc_guard(kill_ref=kill_ref) as interrupted:
        for line in traceroute(host, kill_ref=kill_ref):
            console.print(f"[{muted}]{line}[/{muted}]")

    if interrupted[0]:
        return
    if pause_after:
        pause()


def show_dns(host: str | None = None, pause_after: bool = True) -> None:
    from network.commands import dns_lookup, dns_reverse

    title_color = get_color(f"modules.{_THEME_KEY}.title")
    success = get_color("success")
    error_c = get_color("error")
    muted = get_color("muted")

    # 非交互模式：host 已传入，自动判断正查 / 反查
    if host:
        import re as _re
        _is_ip = bool(_re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host))
        clear_screen()
        from core.progress import spinner
        if _is_ip:
            with spinner(f"PTR: {host}"):
                hostname = dns_reverse(host)
            if hostname:
                console.print(f"\n[{success}]{host}[/{success}] → [{title_color}]{hostname}[/{title_color}]")
            else:
                print_error(t("network.dns_not_found"))
        else:
            with spinner(f"{t('network.dns')}: {host}"):
                result = dns_lookup(host)
            if result.addresses:
                console.print(f"\n[{title_color}]{host}[/{title_color}]")
                for addr in result.addresses:
                    console.print(f"  [{success}]{addr}[/{success}]")
            else:
                print_error(t("network.dns_not_found"))
        if pause_after:
            pause()
        return

    # 交互模式：选择正查 / 反查
    choices = [
        {"key": "1", "label": t("network.dns_forward")},
        {"key": "2", "label": t("network.dns_reverse")},
    ]
    try:
        key = select(
            breadcrumb=["OpsKit", t("menu.network"), t("network.dns")],
            subtitle=t("prompt.select"),
            choices=choices,
            theme_key=_THEME_KEY,
            back_label=f"{get_icon('back')} {t('menu.back')}",
        )
    except UserCancel:
        return
    if not key:
        return

    if key == "1":
        try:
            host = text_input(
                breadcrumb=["OpsKit", t("menu.network"), t("network.dns")],
                prompt=t("network.enter_host"),
            )
        except UserCancel:
            return
        if not host:
            return
        clear_screen()
        from core.progress import spinner
        with spinner(f"{t('network.dns')}: {host}"):
            result = dns_lookup(host)
        if result.addresses:
            console.print(f"\n[{title_color}]{host}[/{title_color}]")
            for addr in result.addresses:
                console.print(f"  [{success}]{addr}[/{success}]")
        else:
            print_error(t("network.dns_not_found"))
    else:
        try:
            ip = text_input(
                breadcrumb=["OpsKit", t("menu.network"), t("network.dns")],
                prompt=t("network.enter_ip"),
            )
        except UserCancel:
            return
        if not ip:
            return
        clear_screen()
        from core.progress import spinner
        with spinner(f"PTR: {ip}"):
            hostname = dns_reverse(ip)
        if hostname:
            console.print(f"\n[{success}]{ip}[/{success}] → [{title_color}]{hostname}[/{title_color}]")
        else:
            print_error(t("network.dns_not_found"))
    if pause_after:
        pause()


def show_port_scan(host: str | None = None, pause_after: bool = True) -> None:
    from network.commands import scan_ports

    if not host:
        try:
            host = text_input(
                breadcrumb=["OpsKit", t("menu.network"), t("network.port_scan")],
                prompt=t("network.enter_host"),
                default="127.0.0.1",
            )
        except UserCancel:
            return
    if not host:
        return

    clear_screen()
    console.print(f"\n{t('network.scanning_ports')}: {', '.join(map(str, _COMMON_PORTS))}\n")

    from core.progress import spinner
    with spinner(t("network.scanning")):
        results = scan_ports(host, _COMMON_PORTS, timeout=0.5)

    title_color = get_color(f"modules.{_THEME_KEY}.title")
    success = get_color("success")
    muted = get_color("muted")
    error_c = get_color("error")

    tbl = Table(
        title=f"[{title_color}]{t('network.port_scan')}: {host}[/{title_color}]",
        box=rich_box.ROUNDED,
        border_style=get_color(f"modules.{_THEME_KEY}.border"),
        header_style=get_color("table.header"),
    )
    tbl.add_column(t("network.port"), width=8)
    tbl.add_column(t("network.status"), width=12)
    tbl.add_column(t("network.latency"), width=12)

    for r in results:
        if r.open:
            tbl.add_row(
                str(r.port),
                f"[{success}]{t('network.open')}[/{success}]",
                f"[{muted}]{r.latency_ms:.1f}ms[/{muted}]",
            )
        else:
            tbl.add_row(
                str(r.port),
                f"[{muted}]{t('network.closed')}[/{muted}]",
                f"[{muted}]─[/{muted}]",
            )

    console.print(tbl)
    if pause_after:
        pause()


def show_speed_test(pause_after: bool = True) -> None:
    clear_screen()
    from network.commands import speed_test_download
    from core.progress import spinner
    from core.utils import fmt_bytes

    from core.constants import SPEED_TEST_URL
    test_url = SPEED_TEST_URL

    cancel_event = threading.Event()
    speed_ref: list = []

    with _ctrlc_guard(cancel_event=cancel_event) as interrupted:
        with spinner(t("network.testing"), raw=True):
            speed_ref.append(speed_test_download(test_url, timeout=15, cancel_event=cancel_event))

    if interrupted[0]:
        return

    success = get_color("success")
    console.print(f"\n[{success}]↓ {fmt_bytes(speed_ref[0] if speed_ref else 0)}/s[/{success}]")
    if pause_after:
        pause()


def show_public_ip(pause_after: bool = True) -> None:
    clear_screen()
    from network.commands import get_public_ip, get_local_ip
    from core.progress import spinner

    cancel_event = threading.Event()
    local_ref: list = []
    public_ref: list = []

    with _ctrlc_guard(cancel_event=cancel_event) as interrupted:
        with spinner(t("network.detecting_ip"), raw=True):
            local_ref.append(get_local_ip())
            public_ref.append(get_public_ip(cancel_event=cancel_event))

    if interrupted[0]:
        return

    success = get_color("success")
    muted = get_color("muted")
    title_color = get_color(f"modules.{_THEME_KEY}.title")
    local = local_ref[0] if local_ref else "127.0.0.1"
    public = public_ref[0] if public_ref else None
    console.print(f"\n[{title_color}]{t('network.local_ip')}:[/{title_color}] [{success}]{local}[/{success}]")
    if public:
        console.print(f"[{title_color}]{t('network.public_ip')}:[/{title_color}] [{success}]{public}[/{success}]")
    else:
        console.print(f"[{muted}]{t('network.public_ip_failed')}[/{muted}]")
    if pause_after:
        pause()
