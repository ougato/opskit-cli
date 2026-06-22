"""RustDesk Server 常量。"""
from __future__ import annotations

from pathlib import Path

RUSTDESK_VERSION_LATEST = "latest"
RUSTDESK_INSTALLED_VERSION = "installed"
RUSTDESK_GITHUB_API = "https://api.github.com/repos/rustdesk/rustdesk-server/releases/latest"
RUSTDESK_RELEASE_DOWNLOAD = "https://github.com/rustdesk/rustdesk-server/releases/download/{version}/{asset}"
RUSTDESK_PACKAGE_PREFIX = "rustdesk-server-linux"
RUSTDESK_ARCH_ASSETS = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64v8",
    "arm64": "arm64v8",
    "armv7": "armv7",
    "armv7l": "armv7",
    "i386": "i386",
    "i686": "i386",
}
RUSTDESK_BINARIES = ("hbbs", "hbbr")
RUSTDESK_INSTALL_DIR = Path("/opt/opskit-rustdesk-server")
RUSTDESK_DATA_DIR = Path("/var/lib/opskit-rustdesk-server")
RUSTDESK_LOG_DIR = Path("/var/log/opskit-rustdesk-server")
RUSTDESK_STATE_DIR = Path("/etc/opskit/rustdesk-server")
RUSTDESK_STATE_FILE = RUSTDESK_STATE_DIR / "state.json"
RUSTDESK_HBBS_SERVICE = "opskit-rustdesk-hbbs.service"
RUSTDESK_HBBR_SERVICE = "opskit-rustdesk-hbbr.service"
RUSTDESK_HBBS_PID_FILE = RUSTDESK_DATA_DIR / "hbbs.pid"
RUSTDESK_HBBR_PID_FILE = RUSTDESK_DATA_DIR / "hbbr.pid"
RUSTDESK_KEY_FILE = RUSTDESK_DATA_DIR / "id_ed25519.pub"
RUSTDESK_ID_PORT = 21116
RUSTDESK_RELAY_PORT = 21117
RUSTDESK_NAT_PORT = 21115
RUSTDESK_WEB_PORTS = (21118, 21119)
RUSTDESK_DOWNLOAD_TIMEOUT_SECONDS = 600
RUSTDESK_COMMAND_TIMEOUT_SECONDS = 20
RUSTDESK_START_WAIT_SECONDS = 2
RUSTDESK_SUPPORTED_PLATFORMS = ("linux", "linux2")
RUSTDESK_PUBLIC_IP_TIMEOUT_SECONDS = 5
RUSTDESK_SYSTEMD_DIR = Path("/etc/systemd/system")
RUSTDESK_HBBS_SERVICE_FILE = RUSTDESK_SYSTEMD_DIR / RUSTDESK_HBBS_SERVICE
RUSTDESK_HBBR_SERVICE_FILE = RUSTDESK_SYSTEMD_DIR / RUSTDESK_HBBR_SERVICE
RUSTDESK_RELEASE_LATEST_DOWNLOAD = "https://github.com/rustdesk/rustdesk-server/releases/latest/download/{asset}"
RUSTDESK_KEY_WAIT_RETRIES = 10
