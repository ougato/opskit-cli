"""Tailscale 安装、卸载、诊断与管理。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from rich.console import Console

from core.i18n import t
from core.progress import MultiStepProgress
from core.prompt import UserCancel, pause, select
from core.theme import print_error, print_info, print_success
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
    return subprocess.run(command, check=check, capture_output=True, text=True, timeout=timeout)


def _run_root(command: list[str], check: bool = True, timeout: int = TAILSCALE_COMMAND_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    from core.privilege import run_as_root

    return run_as_root(command, check=check, capture_output=True, text=True, timeout=timeout)


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
        with urllib.request.urlopen(TAILSCALE_INSTALL_SCRIPT_URL, timeout=TAILSCALE_COMMAND_TIMEOUT_SECONDS) as resp:
            script_path.write_bytes(resp.read())
        script_path.chmod(0o700)
        _run_root(
            [BASH_COMMAND, str(script_path)],
            timeout=TAILSCALE_INSTALL_TIMEOUT_SECONDS,
        )
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

    print_success(t("tailscale.output.install_done"))
    if login_output:
        console.print(login_output)
    ip = tailscale_ip()
    if ip:
        console.print(f"{t('tailscale.output.ip')}: {ip}")
    pause()


def uninstall_client() -> None:
    try:
        cleanup_exit_node()
        if command_exists(TAILSCALE_COMMAND):
            _run_root([TAILSCALE_COMMAND, "down"], check=False)
        _run_root([SYSTEMCTL_COMMAND, "disable", "--now", TAILSCALED_SERVICE], check=False)
        if command_exists(APT_GET_COMMAND):
            env = {**os.environ, DEBIAN_FRONTEND_ENV: DEBIAN_FRONTEND_NONINTERACTIVE}
            subprocess.run(
                [APT_GET_COMMAND, APT_PURGE_COMMAND, APT_ASSUME_YES_ARG, *TAILSCALE_PACKAGES],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=TAILSCALE_INSTALL_TIMEOUT_SECONDS,
            )
        remove_tailscale_artifacts()
    except Exception as exc:
        raise UninstallError(str(exc)) from exc
    print_success(t("tailscale.output.uninstall_done"))
    pause()


def remove_tailscale_artifacts() -> None:
    for path in (TAILSCALE_STATE_DIR, TAILSCALE_RUN_DIR):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    for path in (TAILSCALE_REPO_FILE, TAILSCALE_KEYRING_FILE):
        path.unlink(missing_ok=True)


def diagnose_client() -> None:
    print_info(t("tailscale.diagnose.title"))
    console.print(f"{t('tailscale.diagnose.installed')}: {detect_tailscale_version() or False}")
    console.print(f"{t('tailscale.diagnose.service')}: {is_service_active()}")
    ip = tailscale_ip()
    console.print(f"{t('tailscale.output.ip')}: {ip or '-'}")
    result = _run([TAILSCALE_COMMAND, "status"], check=False) if command_exists(TAILSCALE_COMMAND) else None
    if result and result.stdout:
        console.print(result.stdout.strip())
    pause()


def manage_client() -> None:
    while True:
        try:
            key = select(
                breadcrumb=["OpsKit", t("menu.software"), t("software.tailscale"), t("software.manage")],
                subtitle=t("prompt.select"),
                choices=[
                    {"key": "1", "label": t("tailscale.manage.status")},
                    {"key": "2", "label": t("tailscale.manage.login")},
                    {"key": "3", "label": t("tailscale.manage.down")},
                    {"key": "4", "label": t("tailscale.manage.restart")},
                ],
                theme_key="software",
            )
        except UserCancel:
            return
        if key is None:
            return
        if key == "1":
            diagnose_client()
        elif key == "2":
            output = start_login()
            if output:
                console.print(output)
            pause()
        elif key == "3":
            _run_root([TAILSCALE_COMMAND, "down"], check=False)
            print_success(t("tailscale.output.down_done"))
            pause()
        elif key == "4":
            _run_root([SYSTEMCTL_COMMAND, "restart", TAILSCALED_SERVICE], check=False)
            print_success(t("tailscale.output.restart_done"))
            pause()
