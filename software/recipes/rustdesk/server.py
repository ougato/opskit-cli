"""RustDesk Server 安装、卸载与诊断。"""
from __future__ import annotations

import json
import platform
import shlex
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from core.constants import PUBLIC_IP_APIS
from core.i18n import t
from core.privilege import run_as_root
from core.service import systemd_is_available
from core.theme import console, get_color, print_action_title, print_warning
from software.base import InstallError
from .constants import (
    RUSTDESK_ARCH_ASSETS,
    RUSTDESK_BINARIES,
    RUSTDESK_COMMAND_TIMEOUT_SECONDS,
    RUSTDESK_DATA_DIR,
    RUSTDESK_DOWNLOAD_TIMEOUT_SECONDS,
    RUSTDESK_GITHUB_API,
    RUSTDESK_HBBR_PID_FILE,
    RUSTDESK_HBBR_SERVICE,
    RUSTDESK_HBBR_SERVICE_FILE,
    RUSTDESK_HBBS_PID_FILE,
    RUSTDESK_HBBS_SERVICE,
    RUSTDESK_HBBS_SERVICE_FILE,
    RUSTDESK_ID_PORT,
    RUSTDESK_INSTALL_DIR,
    RUSTDESK_INSTALLED_VERSION,
    RUSTDESK_KEY_FILE,
    RUSTDESK_KEY_WAIT_RETRIES,
    RUSTDESK_LOG_DIR,
    RUSTDESK_NAT_PORT,
    RUSTDESK_PACKAGE_PREFIX,
    RUSTDESK_PUBLIC_IP_TIMEOUT_SECONDS,
    RUSTDESK_RELAY_PORT,
    RUSTDESK_RELEASE_DOWNLOAD,
    RUSTDESK_RELEASE_LATEST_DOWNLOAD,
    RUSTDESK_VERSION_LATEST,
    RUSTDESK_START_WAIT_SECONDS,
    RUSTDESK_STATE_DIR,
    RUSTDESK_STATE_FILE,
    RUSTDESK_WEB_PORTS,
)


def detect_version() -> str | None:
    hbbs = RUSTDESK_INSTALL_DIR / "hbbs"
    if not hbbs.exists():
        return None
    try:
        result = subprocess.run(
            [str(hbbs), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=RUSTDESK_COMMAND_TIMEOUT_SECONDS,
        )
        text = (result.stdout or result.stderr).strip()
        for part in text.split():
            if part and part[0].isdigit():
                return part
    except Exception:
        pass
    state = load_state()
    version = state.get("version")
    return str(version) if version else RUSTDESK_INSTALLED_VERSION


def latest_version() -> str:
    try:
        req = urllib.request.Request(RUSTDESK_GITHUB_API, headers={"User-Agent": "opskit"})
        with urllib.request.urlopen(req, timeout=RUSTDESK_PUBLIC_IP_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = str(data.get("tag_name") or "").lstrip("v")
        return tag or RUSTDESK_VERSION_LATEST
    except Exception:
        return RUSTDESK_VERSION_LATEST


def install_server(version: str = RUSTDESK_VERSION_LATEST) -> None:
    from core.platform import get_platform
    from core.progress import MultiStepProgress

    info = get_platform()
    descs = [
        t("software.step.check"),
        t("rustdesk.step.download"),
        t("rustdesk.step.install_binaries"),
        t("rustdesk.step.configure_service"),
        t("rustdesk.step.start_service"),
        t("rustdesk.step.verify"),
    ]
    with MultiStepProgress(descs) as sp:
        sp.step(descs[0])
        if info.os_type != "linux":
            raise InstallError(t("rustdesk.error.platform_not_supported", platform=info.os_type))

        sp.step(descs[1])
        resolved_version = latest_version() if version in (RUSTDESK_VERSION_LATEST, "") else version
        archive = download_release(resolved_version)

        sp.step(descs[2])
        install_archive(archive)
        resolved_version = detect_version() or resolved_version

        sp.step(descs[3])
        host = detect_public_host()
        write_services(host)

        sp.step(descs[4])
        start_services()

        sp.step(descs[5])
        key = wait_for_key()
        if not key:
            raise InstallError(t("rustdesk.error.key_missing"))
        state = build_state(resolved_version, host, key)
        save_state(state)
        if not verify_running():
            raise InstallError(t("rustdesk.error.verify_failed"))
        sp.complete()

    # 连接信息在进度条结束后单独输出（避免与进度条交错）
    print_connection_info(state)


def uninstall_server() -> None:
    from core.progress import MultiStepProgress

    descs = [
        t("software.step.stop_service"),
        t("software.step.remove_files"),
        t("software.step.cleanup"),
    ]
    with MultiStepProgress(descs) as sp:
        sp.step(descs[0])
        stop_services()

        sp.step(descs[1])
        remove_artifacts()

        sp.step(descs[2])
        systemd_daemon_reload()
        sp.complete()


def diagnose_server() -> None:
    from core.prompt import clear_screen, pause

    breadcrumb = ["OpsKit", t("menu.software"), t("software.rustdesk"), t("software.diagnose")]
    clear_screen()
    print_action_title(breadcrumb)

    version = detect_version()
    if not version:
        print_warning(t("software.not_installed_hint", name=t("software.rustdesk")))
        pause()
        return
    state = load_state()
    key = read_key()
    if key:
        state = {**state, **build_state(version, str(state.get("host") or detect_public_host()), key)}
        save_state(state)
    rows = [
        t("rustdesk.info.version", version=version),
        t("rustdesk.info.hbbs", active=str(is_process_active(RUSTDESK_HBBS_SERVICE, RUSTDESK_HBBS_PID_FILE))),
        t("rustdesk.info.hbbr", active=str(is_process_active(RUSTDESK_HBBR_SERVICE, RUSTDESK_HBBR_PID_FILE))),
    ]
    _render_info_panel(state, extra_rows=rows)
    pause()


def download_release(version: str) -> Path:
    arch = RUSTDESK_ARCH_ASSETS.get(platform.machine().lower())
    if not arch:
        raise InstallError(t("rustdesk.error.arch_not_supported", arch=platform.machine()))
    asset = f"{RUSTDESK_PACKAGE_PREFIX}-{arch}.zip"
    if version == RUSTDESK_VERSION_LATEST:
        url = RUSTDESK_RELEASE_LATEST_DOWNLOAD.format(asset=asset)
    else:
        url = RUSTDESK_RELEASE_DOWNLOAD.format(version=version, asset=asset)
    tmpdir = Path(tempfile.mkdtemp(prefix="opskit-rustdesk-"))
    archive = tmpdir / asset
    try:
        urllib.request.urlretrieve(url, archive)
    except Exception as exc:
        raise InstallError(t("rustdesk.error.download_failed", error=str(exc))) from exc
    return archive


def install_archive(archive: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="opskit-rustdesk-extract-") as tmp:
        extract_dir = Path(tmp)
        try:
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(extract_dir)
        except Exception as exc:
            raise InstallError(t("rustdesk.error.extract_failed", error=str(exc))) from exc

        run_root(["install", "-d", "-m", "0755", str(RUSTDESK_INSTALL_DIR)])
        run_root(["install", "-d", "-m", "0755", str(RUSTDESK_DATA_DIR)])
        run_root(["install", "-d", "-m", "0755", str(RUSTDESK_LOG_DIR)])
        run_root(["install", "-d", "-m", "0755", str(RUSTDESK_STATE_DIR)])
        for name in RUSTDESK_BINARIES:
            src = next(extract_dir.rglob(name), None)
            if src is None:
                raise InstallError(t("rustdesk.error.binary_missing", binary=name))
            run_root(["install", "-m", "0755", str(src), str(RUSTDESK_INSTALL_DIR / name)])


def write_services(host: str) -> None:
    if systemd_is_available():
        hbbr = f"""[Unit]
Description=OpsKit RustDesk Relay Server
After=network.target

[Service]
Type=simple
WorkingDirectory={RUSTDESK_DATA_DIR}
ExecStart={RUSTDESK_INSTALL_DIR / 'hbbr'} -p {RUSTDESK_RELAY_PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""
        hbbs = f"""[Unit]
Description=OpsKit RustDesk ID Server
After=network.target {RUSTDESK_HBBR_SERVICE}
Requires={RUSTDESK_HBBR_SERVICE}

[Service]
Type=simple
WorkingDirectory={RUSTDESK_DATA_DIR}
ExecStart={RUSTDESK_INSTALL_DIR / 'hbbs'} -p {RUSTDESK_ID_PORT} -r {host}:{RUSTDESK_RELAY_PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""
        write_root_file(RUSTDESK_HBBR_SERVICE_FILE, hbbr, "0644")
        write_root_file(RUSTDESK_HBBS_SERVICE_FILE, hbbs, "0644")
        systemd_daemon_reload()


def start_services() -> None:
    stop_services()
    if systemd_is_available():
        run_root(["systemctl", "enable", "--now", RUSTDESK_HBBR_SERVICE])
        run_root(["systemctl", "enable", "--now", RUSTDESK_HBBS_SERVICE])
        time.sleep(RUSTDESK_START_WAIT_SECONDS)
        return
    start_process("hbbr", [str(RUSTDESK_INSTALL_DIR / "hbbr"), "-p", str(RUSTDESK_RELAY_PORT)], RUSTDESK_HBBR_PID_FILE)
    host = detect_public_host()
    start_process("hbbs", [str(RUSTDESK_INSTALL_DIR / "hbbs"), "-p", str(RUSTDESK_ID_PORT), "-r", f"{host}:{RUSTDESK_RELAY_PORT}"], RUSTDESK_HBBS_PID_FILE)
    time.sleep(RUSTDESK_START_WAIT_SECONDS)


def stop_services() -> None:
    if systemd_is_available():
        run_as_root(["systemctl", "disable", "--now", RUSTDESK_HBBS_SERVICE], capture_output=True, text=True, check=False)
        run_as_root(["systemctl", "disable", "--now", RUSTDESK_HBBR_SERVICE], capture_output=True, text=True, check=False)
    stop_process(RUSTDESK_HBBS_PID_FILE)
    stop_process(RUSTDESK_HBBR_PID_FILE)


def start_process(name: str, cmd: list[str], pid_file: Path) -> None:
    log_file = RUSTDESK_LOG_DIR / f"{name}.log"
    quoted_cmd = " ".join(shlex.quote(part) for part in cmd)
    shell_cmd = (
        f"cd {shlex.quote(str(RUSTDESK_DATA_DIR))} && "
        f"nohup {quoted_cmd} >> {shlex.quote(str(log_file))} 2>&1 & "
        f"echo $! > {shlex.quote(str(pid_file))}"
    )
    run_root(["sh", "-c", shell_cmd])


def stop_process(pid_file: Path) -> None:
    if not pid_file.exists():
        return
    try:
        pid = pid_file.read_text(encoding="utf-8").strip()
        if pid:
            run_as_root(["kill", pid], capture_output=True, text=True, check=False)
    except Exception:
        pass
    try:
        pid_file.unlink(missing_ok=True)
    except Exception:
        pass


def verify_running() -> bool:
    return is_process_active(RUSTDESK_HBBS_SERVICE, RUSTDESK_HBBS_PID_FILE) and is_process_active(RUSTDESK_HBBR_SERVICE, RUSTDESK_HBBR_PID_FILE)


def is_process_active(service: str, pid_file: Path) -> bool:
    if systemd_is_available():
        result = subprocess.run(["systemctl", "is-active", service], capture_output=True, text=True, check=False)
        return result.stdout.strip() == "active"
    if not pid_file.exists():
        return False
    try:
        pid = pid_file.read_text(encoding="utf-8").strip()
    except Exception:
        return False
    return bool(pid) and subprocess.run(["kill", "-0", pid], capture_output=True, check=False).returncode == 0


def wait_for_key() -> str:
    for _ in range(RUSTDESK_KEY_WAIT_RETRIES):
        key = read_key()
        if key:
            return key
        time.sleep(1)
    return ""


def read_key() -> str:
    try:
        return RUSTDESK_KEY_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def build_state(version: str, host: str, key: str) -> dict[str, Any]:
    return {
        "version": version,
        "host": host,
        "id_server": host,
        "relay_server": f"{host}:{RUSTDESK_RELAY_PORT}",
        "key": key,
        "ports": {
            "nat_test_tcp": RUSTDESK_NAT_PORT,
            "id_tcp_udp": RUSTDESK_ID_PORT,
            "relay_tcp": RUSTDESK_RELAY_PORT,
            "web_tcp": list(RUSTDESK_WEB_PORTS),
        },
    }


def print_connection_info(state: dict[str, Any]) -> None:
    if not state:
        return
    _render_info_panel(state)


def _render_info_panel(state: dict[str, Any], extra_rows: list[str] | None = None) -> None:
    """以面板表格输出连接信息（参考 WireGuard 安装结果展示）。"""
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    success = get_color("success")
    muted = get_color("muted")
    value_style = get_color("text")

    tbl = Table.grid(padding=(0, 1))
    tbl.add_column(no_wrap=False)
    for line in extra_rows or []:
        tbl.add_row(Text(line, style=value_style))
    tbl.add_row(Text(t("rustdesk.info.id_server", value=state.get("id_server", "")), style=value_style))
    tbl.add_row(Text(t("rustdesk.info.relay_server", value=state.get("relay_server", "")), style=value_style))
    tbl.add_row(Text(t("rustdesk.info.key", value=state.get("key", "")), style=value_style))
    tbl.add_row(Text(t("rustdesk.info.client_hint"), style=muted))
    console.print(Panel(
        tbl,
        title=f"[{success}]{t('rustdesk.info.done')}[/{success}]",
        border_style=success,
        padding=(1, 2),
    ))


def save_state(state: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
        tmp = Path(f.name)
    run_root(["install", "-d", "-m", "0755", str(RUSTDESK_STATE_DIR)])
    run_root(["install", "-m", "0600", str(tmp), str(RUSTDESK_STATE_FILE)])
    tmp.unlink(missing_ok=True)


def load_state() -> dict[str, Any]:
    try:
        return json.loads(RUSTDESK_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def detect_public_host() -> str:
    for url in PUBLIC_IP_APIS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "opskit"})
            with urllib.request.urlopen(req, timeout=RUSTDESK_PUBLIC_IP_TIMEOUT_SECONDS) as resp:
                text = resp.read().decode("utf-8").strip()
            if text:
                return text
        except Exception:
            continue
    return "127.0.0.1"


def remove_artifacts() -> None:
    for path in [RUSTDESK_INSTALL_DIR, RUSTDESK_DATA_DIR, RUSTDESK_LOG_DIR, RUSTDESK_STATE_DIR]:
        run_as_root(["rm", "-rf", str(path)], capture_output=True, text=True, check=False)
    for path in [RUSTDESK_HBBS_SERVICE_FILE, RUSTDESK_HBBR_SERVICE_FILE]:
        run_as_root(["rm", "-f", str(path)], capture_output=True, text=True, check=False)


def systemd_daemon_reload() -> None:
    if systemd_is_available():
        run_as_root(["systemctl", "daemon-reload"], capture_output=True, text=True, check=False)


def write_root_file(path: Path, content: str, mode: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
        f.write(content)
        tmp = Path(f.name)
    run_root(["install", "-m", mode, str(tmp), str(path)])
    tmp.unlink(missing_ok=True)


def run_root(cmd: list[str]) -> None:
    result = run_as_root(cmd, capture_output=True, text=True, check=False, timeout=RUSTDESK_DOWNLOAD_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise InstallError(result.stderr.strip() or result.stdout.strip() or " ".join(cmd))
