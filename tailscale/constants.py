"""Tailscale 自动化常量。"""
from __future__ import annotations

from pathlib import Path

TAILSCALE_COMMAND = "tailscale"
TAILSCALED_SERVICE = "tailscaled"
TAILSCALE_VERSION_LATEST = "latest"
TAILSCALE_INSTALLED_VERSION = "installed"
TAILSCALE_INSTALL_SCRIPT_URL = "https://tailscale.com/install.sh"
TAILSCALE_HOSTNAME = "opskit-tailscale"
TAILSCALE_UP_TIMEOUT_SECONDS = 12
TAILSCALE_COMMAND_TIMEOUT_SECONDS = 20
TAILSCALE_INSTALL_TIMEOUT_SECONDS = 600
TAILSCALE_LINUX_PLATFORMS = ("linux", "linux2")

SYSTEMCTL_COMMAND = "systemctl"
BASH_COMMAND = "bash"
APT_GET_COMMAND = "apt-get"
APT_PURGE_COMMAND = "purge"
APT_ASSUME_YES_ARG = "-y"
DEBIAN_FRONTEND_ENV = "DEBIAN_FRONTEND"
DEBIAN_FRONTEND_NONINTERACTIVE = "noninteractive"

TAILSCALE_PACKAGES = ["tailscale", "tailscale-archive-keyring"]
TAILSCALE_STATE_DIR = Path("/var/lib/tailscale")
TAILSCALE_RUN_DIR = Path("/run/tailscale")
TAILSCALE_REPO_FILE = Path("/etc/apt/sources.list.d/tailscale.list")
TAILSCALE_KEYRING_FILE = Path("/usr/share/keyrings/tailscale-archive-keyring.gpg")
