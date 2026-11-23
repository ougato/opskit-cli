"""网络工具菜单"""
from __future__ import annotations

import ctypes
import signal
import sys
import threading
from contextlib import closing, contextmanager
from typing import Iterator

from rich import box as rich_box
from rich.console import Console
from rich.table import Table

from core.i18n import t
from core.prompt import select, text_input, pause, UserCancel, clear_screen, print_header
from core.sdk import activity_step, progress_step
from core.theme import get_color, get_icon, print_error
from network.commands import COMMON_TCP_PORTS

console = Console()
_THEME_KEY = "network"



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
                subtitle="",
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
    print_header(["OpsKit", t("menu.network"), t("network.ping")])
    kill_ref: list = []
    try:
        with _ctrlc_guard(kill_ref=kill_ref) as interrupted, activity_step(t("network.ping"), t("progress.failed")):
            result = ping(host, count=4, kill_ref=kill_ref)
            if interrupted[0]:
                raise UserCancel
            if not result.reachable:
                raise LookupError
    except UserCancel:
        return
    except LookupError:
        pass

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
    from core.theme import console as progress_console

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
    print_header(["OpsKit", t("menu.network"), t("network.traceroute")])
    kill_ref: list = []
    with _ctrlc_guard(kill_ref=kill_ref) as interrupted, closing(traceroute(host, kill_ref=kill_ref)) as lines:
        try:
            with activity_step(t("network.traceroute"), t("progress.failed")):
                while not interrupted[0]:
                    try:
                        line = next(lines)
                    except StopIteration as done:
                        if done.value and done.value["reached"]:
                            break
                        raise LookupError from None
                    progress_console.print(line, markup=False, highlight=False)
                if interrupted[0]:
                    raise UserCancel
        except UserCancel:
            return
        except LookupError:
            if interrupted[0]:
                return
    if pause_after:
        pause()


def show_dns(host: str | None = None, pause_after: bool = True) -> None:
    from network.commands import dns_lookup, dns_reverse

    title_color = get_color(f"modules.{_THEME_KEY}.title")
    success = get_color("success")

    breadcrumb = ["OpsKit", t("menu.network"), t("network.dns")]
    if host:
        from ipaddress import ip_address
        try:
            ip_address(host)
        except ValueError:
            reverse = False
        else:
            reverse = True
    else:
        choices = [
            {"key": "1", "label": t("network.dns_forward")},
            {"key": "2", "label": t("network.dns_reverse")},
        ]
        try:
            key = select(breadcrumb=breadcrumb, subtitle="", choices=choices, theme_key=_THEME_KEY,
                         back_label=f"{get_icon('back')} {t('menu.back')}")
            if not key:
                return
            reverse = key == "2"
            host = text_input(breadcrumb=breadcrumb,
                              prompt=t("network.enter_ip" if reverse else "network.enter_host"))
        except UserCancel:
            return
        if not host:
            return

    clear_screen()
    print_header(breadcrumb)
    action = t("network.dns_reverse" if reverse else "network.dns_forward")
    try:
        with activity_step(action, t("progress.failed")):
            addresses = [dns_reverse(host)] if reverse else dns_lookup(host).addresses
            if not addresses or not addresses[0]:
                raise LookupError
    except (LookupError, ValueError):
        print_error(t("network.dns_not_found"))
    else:
        console.print(f"\n[{title_color}]{host}[/{title_color}]")
        for address in addresses:
            console.print(f"  [{success}]{address}[/{success}]")
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
    print_header(["OpsKit", t("menu.network"), t("network.port_scan")])
    with progress_step(t("network.scanning"), t("progress.failed"), total=len(COMMON_TCP_PORTS)) as update:
        results = scan_ports(host, COMMON_TCP_PORTS, timeout=0.5, on_progress=update)

    title_color = get_color(f"modules.{_THEME_KEY}.title")
    success = get_color("success")
    muted = get_color("muted")

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
    from core.utils import fmt_bytes

    from core.constants import SPEED_TEST_URL
    print_header(["OpsKit", t("menu.network"), t("network.speed_test")])
    cancel_event = threading.Event()
    try:
        with _ctrlc_guard(cancel_event=cancel_event), activity_step(t("network.testing"), t("progress.failed")):
            speed = speed_test_download(SPEED_TEST_URL, timeout=15, cancel_event=cancel_event)
            if cancel_event.is_set():
                raise UserCancel
            if speed <= 0:
                raise ValueError
    except UserCancel:
        return
    except ValueError:
        pass
    else:
        success = get_color("success")
        console.print(f"\n[{success}]↓ {fmt_bytes(speed)}/s[/{success}]")
    if pause_after:
        pause()


def show_public_ip(pause_after: bool = True) -> None:
    clear_screen()
    from network.commands import get_public_ip, get_local_ip
    print_header(["OpsKit", t("menu.network"), t("network.public_ip")])
    cancel_event = threading.Event()
    local = get_local_ip()
    try:
        with _ctrlc_guard(cancel_event=cancel_event), activity_step(t("network.detecting_ip"), t("progress.failed")):
            public = get_public_ip(cancel_event=cancel_event)
            if cancel_event.is_set():
                raise UserCancel
            if public is None:
                raise ValueError
    except UserCancel:
        return
    except ValueError:
        public = None

    success = get_color("success")
    muted = get_color("muted")
    title_color = get_color(f"modules.{_THEME_KEY}.title")
    console.print(f"\n[{title_color}]{t('network.local_ip')}:[/{title_color}] [{success}]{local}[/{success}]")
    if public:
        console.print(f"[{title_color}]{t('network.public_ip')}:[/{title_color}] [{success}]{public}[/{success}]")
    else:
        console.print(f"[{muted}]{t('network.public_ip_failed')}[/{muted}]")
    if pause_after:
        pause()
