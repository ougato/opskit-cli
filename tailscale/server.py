"""Tailscale 安装、卸载、诊断与管理。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from rich.console import Console

from core.http import get_bytes
from core.i18n import t
from core.progress import MultiStepProgress
from core.prompt import UserCancel, clear_screen, pause, select
from core.theme import get_color, get_icon, print_action_title, print_success, print_warning
from software.base import InstallError, UninstallError
from tailscale.constants import (
    APT_ASSUME_YES_ARG,
    APT_GET_COMMAND,
    APT_PURGE_COMMAND,
    BASH_COMMAND,
    DEBIAN_FRONTEND_ENV,
    DEBIAN_FRONTEND_NONINTERACTIVE,
    INSTALL_COMMAND,
    RM_COMMAND,
    TAILSCALE_INSTALL_ERROR_TAIL_LINES,
    SYSTEMCTL_COMMAND,
    SYSCTL_COMMAND,
    TAILSCALE_COMMAND,
    TAILSCALE_COMMAND_TIMEOUT_SECONDS,
    TAILSCALE_EXIT_NODE_OUTBOUND_INTERFACE,
    TAILSCALE_EXIT_NODE_ROUTES,
    TAILSCALE_EXIT_NODE_SCRIPT_FILE,
    TAILSCALE_EXIT_NODE_SERVICE,
    TAILSCALE_EXIT_NODE_SERVICE_FILE,
    TAILSCALE_EXIT_NODE_SYSCTL_FILE,
    TAILSCALE_EXIT_NODE_TAILNET_CIDR,
    TAILSCALE_HOSTNAME,
    TAILSCALE_INSTALL_SCRIPT_URL,
    TAILSCALE_INSTALL_TIMEOUT_SECONDS,
    TAILSCALE_IPV4_FORWARD_KEY,
    TAILSCALE_IPV6_FORWARD_KEY,
    TAILSCALE_KEYRING_FILE,
    TAILSCALE_LINUX_PLATFORMS,
    TAILSCALE_PACKAGES,
    TAILSCALE_REPO_FILE,
    TAILSCALE_RUN_DIR,
    TAILSCALE_STATE_DIR,
    TAILSCALE_UP_TIMEOUT_SECONDS,
    TAILSCALED_SERVICE,
)

console = Console()


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def _ensure_linux() -> None:
    if sys.platform not in TAILSCALE_LINUX_PLATFORMS:
        raise InstallError(t("tailscale.error.unsupported_os"))


def _run(command: list[str], check: bool = True, timeout: int = TAILSCALE_COMMAND_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=check, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _run_root(command: list[str], check: bool = True, timeout: int = TAILSCALE_COMMAND_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    from core.privilege import run_as_root

    return run_as_root(command, check=check, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _write_root_file(path: Path, content: str, mode: str) -> None:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as file:
        file.write(content)
        temp_path = Path(file.name)
    try:
        _run_root([INSTALL_COMMAND, "-m", mode, str(temp_path), str(path)])
    finally:
        temp_path.unlink(missing_ok=True)


def _exit_node_sysctl_content() -> str:
    return "\n".join(
        [
            f"{TAILSCALE_IPV4_FORWARD_KEY} = 1",
            f"{TAILSCALE_IPV6_FORWARD_KEY} = 1",
            "",
        ]
    )


def _exit_node_nat_script_content() -> str:
    interface = TAILSCALE_EXIT_NODE_OUTBOUND_INTERFACE
    cidr = TAILSCALE_EXIT_NODE_TAILNET_CIDR
    return f"""#!/bin/sh
set -eu
while iptables -D FORWARD -i tailscale0 -o {interface} -j ACCEPT 2>/dev/null; do :; done
while iptables -D FORWARD -i {interface} -o tailscale0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; do :; done
while iptables -t nat -D POSTROUTING -s {cidr} -o {interface} -j MASQUERADE 2>/dev/null; do :; done
while iptables -t mangle -D FORWARD -i tailscale0 -o {interface} -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null; do :; done
while iptables -t mangle -D FORWARD -i {interface} -o tailscale0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null; do :; done
while ip6tables -D FORWARD -i tailscale0 -j REJECT 2>/dev/null; do :; done
if [ "${{1:-}}" = "clean" ]; then
    exit 0
fi
iptables -I FORWARD 1 -i tailscale0 -o {interface} -j ACCEPT
iptables -I FORWARD 2 -i {interface} -o tailscale0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
iptables -t nat -I POSTROUTING 1 -s {cidr} -o {interface} -j MASQUERADE
iptables -t mangle -I FORWARD 1 -i tailscale0 -o {interface} -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
iptables -t mangle -I FORWARD 2 -i {interface} -o tailscale0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
ip6tables -I FORWARD 1 -i tailscale0 -j REJECT
"""


def _exit_node_service_content() -> str:
    return f"""[Unit]
Description=Tailscale exit node forwarding rules
After={TAILSCALED_SERVICE} network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={TAILSCALE_EXIT_NODE_SCRIPT_FILE}
ExecStop={TAILSCALE_EXIT_NODE_SCRIPT_FILE} clean
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""


def detect_tailscale_version() -> str | None:
    if not command_exists(TAILSCALE_COMMAND):
        return None
    result = _run([TAILSCALE_COMMAND, "version"], check=False)
    if result.returncode != 0:
        return None
    first = result.stdout.splitlines()[0].strip() if result.stdout else ""
    return first or None


def tailscale_ip() -> str:
    if not command_exists(TAILSCALE_COMMAND):
        return ""
    result = _run([TAILSCALE_COMMAND, "ip", "-4"], check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def is_service_active() -> bool:
    result = _run([SYSTEMCTL_COMMAND, "is-active", TAILSCALED_SERVICE], check=False)
    return result.stdout.strip() == "active"


def _install_script() -> None:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as script:
        script_path = Path(script.name)
    try:
        content = get_bytes(TAILSCALE_INSTALL_SCRIPT_URL, timeout=TAILSCALE_COMMAND_TIMEOUT_SECONDS)
        if content is None:
            raise InstallError(t("tailscale.error.download_failed"))
        script_path.write_bytes(content)
        script_path.chmod(0o700)
        from core.privilege import run_as_root

        env = {**os.environ, DEBIAN_FRONTEND_ENV: DEBIAN_FRONTEND_NONINTERACTIVE}
        result = run_as_root(
            [BASH_COMMAND, str(script_path)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=TAILSCALE_INSTALL_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            tail = "\n".join(detail.splitlines()[-TAILSCALE_INSTALL_ERROR_TAIL_LINES:])
            raise InstallError(tail or f"exit {result.returncode}")
    finally:
        script_path.unlink(missing_ok=True)


def start_login() -> str:
    command = [
        TAILSCALE_COMMAND,
        "up",
        "--hostname",
        TAILSCALE_HOSTNAME,
        "--advertise-exit-node",
    ]
    try:
        result = _run_root(command, check=False, timeout=TAILSCALE_UP_TIMEOUT_SECONDS)
        return (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired as exc:
        output = ""
        if exc.stdout:
            output += exc.stdout.decode("utf-8", errors="ignore") if isinstance(exc.stdout, bytes) else exc.stdout
        if exc.stderr:
            output += exc.stderr.decode("utf-8", errors="ignore") if isinstance(exc.stderr, bytes) else exc.stderr
        return output.strip()


def configure_exit_node() -> None:
    _write_root_file(TAILSCALE_EXIT_NODE_SYSCTL_FILE, _exit_node_sysctl_content(), "0644")
    _run_root([SYSCTL_COMMAND, "-p", str(TAILSCALE_EXIT_NODE_SYSCTL_FILE)], check=False)
    _write_root_file(TAILSCALE_EXIT_NODE_SCRIPT_FILE, _exit_node_nat_script_content(), "0755")
    _write_root_file(TAILSCALE_EXIT_NODE_SERVICE_FILE, _exit_node_service_content(), "0644")
    _run_root([SYSTEMCTL_COMMAND, "daemon-reload"], check=False)
    _run_root([SYSTEMCTL_COMMAND, "enable", "--now", TAILSCALE_EXIT_NODE_SERVICE], check=False)


def cleanup_exit_node() -> None:
    if TAILSCALE_EXIT_NODE_SCRIPT_FILE.exists():
        _run_root([str(TAILSCALE_EXIT_NODE_SCRIPT_FILE), "clean"], check=False)
    _run_root([SYSTEMCTL_COMMAND, "disable", "--now", TAILSCALE_EXIT_NODE_SERVICE], check=False)
    for path in (
        TAILSCALE_EXIT_NODE_SCRIPT_FILE,
        TAILSCALE_EXIT_NODE_SERVICE_FILE,
        TAILSCALE_EXIT_NODE_SYSCTL_FILE,
    ):
        _run_root([RM_COMMAND, "-f", str(path)], check=False)
    _run_root([SYSTEMCTL_COMMAND, "daemon-reload"], check=False)


def install_client() -> None:
    breadcrumb = ["OpsKit", t("menu.software"), t("software.tailscale"), t("software.install")]
    clear_screen()
    print_action_title(breadcrumb)
    step_descs = [
        t("tailscale.step.check_os"),
        t("tailscale.step.install"),
        t("tailscale.step.start"),
        t("tailscale.step.exit_node"),
        t("tailscale.step.login"),
    ]
    with MultiStepProgress(step_descs) as sp:
        sp.step(t("tailscale.step.check_os"))
        _ensure_linux()

        sp.step(t("tailscale.step.install"))
        if not command_exists(TAILSCALE_COMMAND):
            _install_script()

        sp.step(t("tailscale.step.start"))
        _run_root([SYSTEMCTL_COMMAND, "enable", "--now", TAILSCALED_SERVICE], check=False)

        sp.step(t("tailscale.step.exit_node"))
        configure_exit_node()

        sp.step(t("tailscale.step.login"))
        login_output = start_login()

    console.print()
    _render_install_panel(login_output)
    pause()


def uninstall_client() -> None:
    breadcrumb = ["OpsKit", t("menu.software"), t("software.tailscale"), t("software.uninstall")]
    descs = [
        t("software.step.stop_service"),
        t("software.step.remove_files"),
        t("software.step.cleanup"),
    ]
    clear_screen()
    print_action_title(breadcrumb)
    try:
        with MultiStepProgress(descs) as sp:
            sp.step(descs[0])
            cleanup_exit_node()
            if command_exists(TAILSCALE_COMMAND):
                _run_root([TAILSCALE_COMMAND, "down"], check=False)
            _run_root([SYSTEMCTL_COMMAND, "disable", "--now", TAILSCALED_SERVICE], check=False)

            sp.step(descs[1])
            if command_exists(APT_GET_COMMAND):
                env = {**os.environ, DEBIAN_FRONTEND_ENV: DEBIAN_FRONTEND_NONINTERACTIVE}
                subprocess.run(
                    [APT_GET_COMMAND, APT_PURGE_COMMAND, APT_ASSUME_YES_ARG, *TAILSCALE_PACKAGES],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    timeout=TAILSCALE_INSTALL_TIMEOUT_SECONDS,
                )
            remove_tailscale_artifacts()

            sp.step(descs[2])
            _run_root([SYSTEMCTL_COMMAND, "daemon-reload"], check=False)
            sp.complete()
    except Exception as exc:
        raise UninstallError(str(exc)) from exc
    print_success(t("tailscale.output.uninstall_done"))


def remove_tailscale_artifacts() -> None:
    # 这些路径均为 root 所有（/var/lib、/etc/apt/sources.list.d 等），
    # 必须以 root 删除，否则普通用户会触发 PermissionError。
    paths = (TAILSCALE_STATE_DIR, TAILSCALE_RUN_DIR, TAILSCALE_REPO_FILE, TAILSCALE_KEYRING_FILE)
    _run_root([RM_COMMAND, "-rf", *(str(p) for p in paths)], check=False)


def _extract_auth_url(output: str) -> str:
    """从 tailscale up 输出里提取登录授权地址，忽略 UDP GRO 等无关警告。"""
    import re

    match = re.search(r"https?://login\.tailscale\.com/\S+", output or "")
    return match.group(0) if match else ""


def _render_panel(title: str, rows: list[str]) -> None:
    """统一的信息面板（参考 RustDesk）：标题 + 若干行内容。"""
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    success = get_color("success")
    value_style = get_color("text")

    tbl = Table.grid(padding=(0, 1))
    tbl.add_column(no_wrap=False)
    for row in rows:
        tbl.add_row(Text(row, style=value_style))
    console.print(Panel(
        tbl,
        title=f"[{success}]{title}[/{success}]",
        border_style=success,
        padding=(1, 2),
        expand=False,
    ))


def _render_install_panel(login_output: str) -> None:
    """安装完成后用面板输出关键信息：Tailscale IP + 登录地址。"""
    rows: list[str] = []
    ip = tailscale_ip()
    if ip:
        rows.append(f"{t('tailscale.output.ip')}: {ip}")
    auth_url = _extract_auth_url(login_output)
    if auth_url:
        rows.append(f"{t('tailscale.output.auth_url')}: {auth_url}")
    _render_panel(t("tailscale.output.install_done"), rows)


def _render_login_panel(auth_url: str) -> None:
    """登录授权地址用面板展示，过滤 UDP GRO 等无关输出。"""
    _render_panel(t("tailscale.output.login_title"), [f"{t('tailscale.output.auth_url')}: {auth_url}"])


def _render_status_panel() -> None:
    """以面板表格输出 Tailscale 状态（参考 WireGuard 诊断展示）。"""
    _render_panel(t("tailscale.diagnose.title"), [
        f"{t('tailscale.diagnose.installed')}: {detect_tailscale_version() or '-'}",
        f"{t('tailscale.diagnose.service')}: {is_service_active()}",
        f"{t('tailscale.output.ip')}: {tailscale_ip() or '-'}",
    ])
    result = _run([TAILSCALE_COMMAND, "status"], check=False) if command_exists(TAILSCALE_COMMAND) else None
    if result and result.stdout:
        auth_url = _extract_auth_url(result.stdout)
        console.print()
        if auth_url:
            _render_login_panel(auth_url)
        else:
            console.print(result.stdout.strip())


def diagnose_client() -> None:
    breadcrumb = ["OpsKit", t("menu.software"), t("software.tailscale"), t("software.diagnose")]
    clear_screen()
    print_action_title(breadcrumb)
    _render_status_panel()
    pause()


def manage_client() -> None:
    breadcrumb = ["OpsKit", t("menu.software"), t("software.tailscale"), t("software.manage")]
    if not command_exists(TAILSCALE_COMMAND):
        clear_screen()
        print_action_title(breadcrumb, trailing_blank=False)
        print_warning(t("software.not_installed_hint", name=t("software.tailscale")))
        pause()
        return
    while True:
        try:
            key = select(
                breadcrumb=breadcrumb,
                subtitle=t("prompt.select"),
                choices=[
                    {"key": "1", "label": f"{get_icon('state')} {t('tailscale.manage.status')}"},
                    {"key": "2", "label": f"{get_icon('link')} {t('tailscale.manage.login')}"},
                    {"key": "3", "label": f"{get_icon('stop')} {t('tailscale.manage.down')}"},
                    {"key": "4", "label": f"{get_icon('restart')} {t('tailscale.manage.restart')}"},
                ],
                theme_key="software",
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            return
        if key is None:
            return
        clear_screen()
        if key == "1":
            print_action_title(breadcrumb)
            _render_status_panel()
            pause()
        elif key == "2":
            output = start_login()
            auth_url = _extract_auth_url(output)
            if auth_url:
                print_action_title(breadcrumb)
                _render_login_panel(auth_url)
            else:
                print_action_title(breadcrumb, trailing_blank=False)
                print_success(t("tailscale.output.login_done"))
            pause()
        elif key == "3":
            print_action_title(breadcrumb, trailing_blank=False)
            _run_root([TAILSCALE_COMMAND, "down"], check=False)
            print_success(t("tailscale.output.down_done"))
            pause()
        elif key == "4":
            print_action_title(breadcrumb, trailing_blank=False)
            _run_root([SYSTEMCTL_COMMAND, "restart", TAILSCALED_SERVICE], check=False)
            print_success(t("tailscale.output.restart_done"))
            pause()
