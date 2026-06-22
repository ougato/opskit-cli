"""x-ui 模块常量。"""
from __future__ import annotations

from pathlib import Path

XUI_SERVICE = "x-ui"
XUI_COMMAND = "x-ui"
XUI_BINARY_COMMAND = "/usr/local/x-ui/x-ui"
XUI_SETTING_SUBCOMMAND = "setting"
XUI_SETTING_USERNAME_ARG = "-username"
XUI_SETTING_PASSWORD_ARG = "-password"
XUI_SETTING_PORT_ARG = "-port"
XUI_SETTING_WEB_BASE_PATH_ARG = "-webBasePath"
XRAY_COMMAND = "xray"
XUI_VERSION_LATEST = "latest"
XUI_INSTALLED_VERSION = "installed"

XUI_INSTALL_SCRIPT_URL = "https://raw.githubusercontent.com/MHSanaei/3x-ui/master/install.sh"
XUI_INSTALL_SCRIPT_INPUT = "\n"
XUI_CONFIG_DIR = Path("/etc/x-ui")
XUI_STATE_FILE = XUI_CONFIG_DIR / "opskit-state.json"
XUI_PENDING_INBOUNDS_FILE = XUI_CONFIG_DIR / "opskit-inbounds.json"
XUI_LOG_LINES = "80"
LOOPBACK_HOST = "127.0.0.1"
HTTP_URL_TEMPLATE = "http://{host}:{port}{base_path}"
OPSKIT_USER_AGENT = "opskit/1.0"
HTTP_HEADER_CONTENT_TYPE = "Content-Type"
HTTP_HEADER_COOKIE = "Cookie"
HTTP_HEADER_USER_AGENT = "User-Agent"
HTTP_HEADER_X_REQUESTED_WITH = "X-Requested-With"
HTTP_HEADER_X_CSRF_TOKEN = "X-CSRF-Token"
HTTP_VALUE_XMLHTTPREQUEST = "XMLHttpRequest"
HTTP_CONTENT_TYPE_JSON = "application/json"
HTTP_CONTENT_TYPE_FORM = "application/x-www-form-urlencoded; charset=UTF-8"
XUI_CSRF_META_MARKER = 'name="csrf-token" content="'
XUI_COOKIE_SEPARATOR = "; "

LINUX_PLATFORMS = ("linux", "linux2")
APT_GET_COMMAND = "apt-get"
APT_UPDATE_COMMAND = "update"
APT_INSTALL_COMMAND = "install"
APT_ASSUME_YES_ARG = "-y"
YUM_COMMAND = "yum"
YUM_INSTALL_COMMAND = "install"
CURL_COMMAND = "curl"
SQLITE3_COMMAND = "sqlite3"
SQLITE_YUM_PACKAGE = "sqlite"
BASH_COMMAND = "bash"
DEBIAN_FRONTEND_ENV = "DEBIAN_FRONTEND"
DEBIAN_FRONTEND_NONINTERACTIVE = "noninteractive"
SYSTEMCTL_COMMAND = "systemctl"
SS_COMMAND = "ss"
SS_TCP_LISTEN_ARGS = "-tln"
JOURNALCTL_COMMAND = "journalctl"
JOURNAL_NO_PAGER_ARG = "--no-pager"

XUI_XRAY_CANDIDATES = [
    "xray",
    "/usr/local/bin/xray",
    "/usr/local/x-ui/bin/xray",
    "/usr/local/x-ui/bin/xray-linux-amd64",
    "/usr/local/x-ui/Xray-linux-amd64",
]

DEFAULT_PANEL_HOST = "127.0.0.1"
DEFAULT_PANEL_BASE_PATH = ""
DEFAULT_PANEL_PORT = 54321
DEFAULT_VLESS_PORT = 443
DEFAULT_TROJAN_PORT = 8443
DEFAULT_REALITY_SNI = "www.cloudflare.com"
DEFAULT_XHTTP_MODE = "auto"
DEFAULT_XHTTP_PATH_PREFIX = "/xhttp-"
DEFAULT_FINGERPRINT = "chrome"
DEFAULT_VLESS_REMARK = "opskit-vless-reality-xhttp"
DEFAULT_TROJAN_REMARK = "opskit-trojan"
DEFAULT_PANEL_USER = "opskit"
DEFAULT_TROJAN_ENABLE = False

SHORT_ID_BYTES = 8
XHTTP_PATH_SUFFIX_BYTES = 3
PASSWORD_BYTES = 18

XUI_API_LOGIN_PATH = "/login"
XUI_API_CSRF_PATH = "/csrf-token"
XUI_API_ADD_INBOUND_PATH = "/panel/api/inbounds/add"

HTTP_STATUS_OK = 200
HTTP_STATUS_REDIRECT_MIN = 300
HTTP_STATUS_REDIRECT_MAX = 399
HTTP_TIMEOUT_SECONDS = 10
SERVICE_RESTART_TIMEOUT = 20
PANEL_API_RETRY_COUNT = 10
PANEL_API_RETRY_DELAY_SECONDS = 1
INSTALL_SCRIPT_TIMEOUT = 600

TROJAN_PROTOCOL = "trojan"
VLESS_PROTOCOL = "vless"
REALITY_SECURITY = "reality"
TLS_SECURITY = "tls"
XHTTP_NETWORK = "xhttp"
TCP_NETWORK = "tcp"
CLIENT_FINGERPRINT = "chrome"
FREEDOM_PROTOCOL = "freedom"
VLESS_DECRYPTION = "none"
SNIFFING_DEST_OVERRIDE = ["http", "tls", "quic"]
TROJAN_SNIFFING_DEST_OVERRIDE = ["http", "tls"]

SENSITIVE_STATE_KEYS = frozenset(
    {
        "password",
        "panel_password",
        "private_key",
        "privateKey",
        "token",
        "secret",
        "cookie",
    }
)
REDACTED_VALUE = "<redacted>"
