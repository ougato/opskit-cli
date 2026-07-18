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
# 登录地址轮询：up 超时后从 tailscale status --json 的 AuthURL 继续等地址
TAILSCALE_AUTH_URL_POLL_TIMEOUT_SECONDS = 30
TAILSCALE_AUTH_URL_POLL_INTERVAL_SECONDS = 1
TAILSCALE_BACKEND_RUNNING = "Running"
TAILSCALE_COMMAND_TIMEOUT_SECONDS = 20
TAILSCALE_INSTALL_TIMEOUT_SECONDS = 600
TAILSCALE_INSTALL_ERROR_TAIL_LINES = 8
TAILSCALE_LINUX_PLATFORMS = ("linux", "linux2")

SYSTEMCTL_COMMAND = "systemctl"
SYSCTL_COMMAND = "sysctl"
BASH_COMMAND = "bash"
INSTALL_COMMAND = "install"
RM_COMMAND = "rm"
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
TAILSCALE_EXIT_NODE_SYSCTL_FILE = Path("/etc/sysctl.d/99-tailscale-exit-node.conf")
TAILSCALE_EXIT_NODE_SCRIPT_FILE = Path("/usr/local/sbin/tailscale-exit-node-nat")
TAILSCALE_EXIT_NODE_SERVICE_FILE = Path("/etc/systemd/system/tailscale-exit-node-nat.service")
TAILSCALE_EXIT_NODE_SERVICE = "tailscale-exit-node-nat.service"
TAILSCALE_EXIT_NODE_TAILNET_CIDR = "100.64.0.0/10"
TAILSCALE_EXIT_NODE_OUTBOUND_INTERFACE = "eth0"
TAILSCALE_EXIT_NODE_ROUTES = ("0.0.0.0/0", "::/0")
TAILSCALE_IPV4_FORWARD_KEY = "net.ipv4.ip_forward"
TAILSCALE_IPV6_FORWARD_KEY = "net.ipv6.conf.all.forwarding"
