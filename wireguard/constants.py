"""WireGuard 部署常量"""
from __future__ import annotations

# ─── 默认端口 ─────────────────────────────────────────────────────────────────
XRAY_REALITY_PORT = 443
WG_UDP_PORT = 3002
CLIENT_XRAY_LOCAL_PORT = 4000

# ─── VPN 子网（默认值，服务端安装时可按第三段独立配置）────────────────────────
VPN_SUBNET_TPL   = "10.10.{octet3}.0/24"
VPN_GW_TPL       = "10.10.{octet3}.1"
VPN_SUBNET       = "10.10.10.0/24"
VPN_SERVER_IP    = "10.10.10.1"
VPN_CLIENT_IP_START = 2
VPN_PEER_IP_START   = 2
VPN_CLIENT_IP_MAX   = 254
VPN_DEFAULT_OCTET3  = 10

# ─── WireGuard 客户端默认值 ──────────────────────────────────────────────────
WG_CLIENT_MTU = 1280
WG_KEEPALIVE = 25

# ─── 客户端默认值 ─────────────────────────────────────────────────────────────
DEFAULT_ROUTER_IP = "192.168.88.1"

# ─── 文件路径（通过 core/paths.py 获取，禁止直接硬编码）────────────────────
from core.paths import (
    wg_config_dir as _wg_config_dir,
    wg_config_file as _wg_config_file,
    xray_config_dir as _xray_config_dir,
    xray_config_file as _xray_config_file,
    xray_binary as _xray_binary,
    nginx_conf_dir as _nginx_conf_dir,
    nginx_ssl_dir as _nginx_ssl_dir,
)

WG_CONFIG_DIR = str(_wg_config_dir())
WG_CONFIG_FILE = str(_wg_config_file())
WG_STATE_FILE = str(_wg_config_dir() / ".opskit-state.json")
WG_CLIENT_STATE_FILE = str(_wg_config_dir() / ".opskit-client-state.json")
XRAY_CONFIG_DIR = str(_xray_config_dir())
XRAY_CONFIG_FILE = str(_xray_config_file())
XRAY_BINARY = str(_xray_binary())
NGINX_STREAM_CONF = str(_nginx_conf_dir() / "stream-sni.conf")
NGINX_STEAL_CONF = str(_nginx_conf_dir() / "steal-oneself.conf")
NGINX_VLESS_WS_CONF = str(_nginx_conf_dir() / "vless-ws.conf")
XRAY_WS_PATH = "/vless-ws"
XRAY_WS_PORT = 2443
ACME_CERT_DIR = str(_nginx_ssl_dir())
ACME_DEFAULT_EMAIL = "acme@opskit.local"

# ─── SNI 伪装域名白名单（VLESS+WS+TLS 用，从中随机选取 tlsSettings.serverName）──
SNI_WHITELIST = [
    "www.microsoft.com",
    "www.apple.com",
    "update.microsoft.com",
    "www.amazon.com",
    "www.samsung.com",
    "www.nvidia.com",
    "www.intel.com",
    "addons.mozilla.org",
    "www.mozilla.org",
    "www.hp.com",
    "www.dell.com",
]

# ─── 多隧道客户端端口分配范围 ────────────────────────────────────────────────
CLIENT_XRAY_LOCAL_PORT_MIN = 4000
CLIENT_XRAY_LOCAL_PORT_MAX = 4099

# ─── 服务名 ───────────────────────────────────────────────────────────────────
WG_SERVICE = "wg-quick@wg0"
XRAY_SERVICE = "xray"

# ─── Xray-core 下载源 ────────────────────────────────────────────────────────
XRAY_REPO              = "XTLS/Xray-core"
XRAY_DOC_URL           = "https://github.com/xtls"
XRAY_DOWNLOAD_ZIP      = "Xray-linux-64.zip"
XRAY_API_LATEST        = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"
XRAY_API_LATEST_GHPROXY = "https://mirror.ghproxy.com/https://api.github.com/repos/XTLS/Xray-core/releases/latest"

# ─── dnsmasq WG DNS 常量 ─────────────────────────────────────────────────────
DNSMASQ_CONF_PATH    = "/etc/dnsmasq.d/opskit-wg.conf"
DNSMASQ_UPSTREAM_DNS = ["8.8.8.8", "1.1.1.1"]

# ─── acme.sh 安装源（按优先级排列） ─────────────────────────────────────────
ACME_INSTALL_MIRRORS = [
    "https://gitee.com/neilpang/acme.sh/raw/master/acme.sh",
    "https://get.acme.sh",
    "https://raw.githubusercontent.com/acmesh-official/acme.sh/master/acme.sh",
]
