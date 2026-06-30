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
XUI_SETTING_SHOW_ARG = "-show"
XUI_SETTING_PORT_KEY = "port"
XUI_SETTING_WEB_BASE_PATH_KEY = "webBasePath"
XUI_SETTING_KV_SEPARATOR = ":"
XRAY_COMMAND = "xray"
XUI_VERSION_LATEST = "latest"
XUI_INSTALLED_VERSION = "installed"

XUI_INSTALL_SCRIPT_URL = "https://raw.githubusercontent.com/MHSanaei/3x-ui/master/install.sh"
XUI_INSTALL_SCRIPT_INPUT = "\n"
XUI_CONFIG_DIR = Path("/etc/x-ui")
XUI_DATABASE_FILE = XUI_CONFIG_DIR / "x-ui.db"
XUI_STATE_FILE = XUI_CONFIG_DIR / "opskit-state.json"
XUI_PENDING_INBOUNDS_FILE = XUI_CONFIG_DIR / "opskit-inbounds.json"
XUI_TRAFFIC_HISTORY_FILE = XUI_CONFIG_DIR / "opskit-traffic.db"
XUI_INSTALL_DIR = Path("/usr/local/x-ui")
XUI_ARTIFACT_DIRS = [XUI_INSTALL_DIR, XUI_CONFIG_DIR]
XUI_ARTIFACT_FILES = [
    Path("/etc/systemd/system/x-ui.service"),
    Path("/usr/bin/x-ui"),
    Path("/usr/local/bin/x-ui"),
]
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
WSL_CONF_FILE = Path("/etc/wsl.conf")
WSL_BOOT_HEADER = "[boot]"
WSL_SYSTEMD_LINE = "systemd=true"
WSL_SYSTEMD_LINE_PATTERN = r"^\s*systemd\s*=.*$"
WSL_EXE_COMMAND = "wsl.exe"
WSL_SHUTDOWN_ARG = "--shutdown"
WSL_OSRELEASE_FILE = Path("/proc/sys/kernel/osrelease")
WSL_MARKER = "microsoft"
WSL_DISTRO_NAME_ENV = "WSL_DISTRO_NAME"
SYSTEMCTL_ENABLE_ARG = "enable"
SYSTEMCTL_START_ARG = "start"
SYSTEMCTL_STOP_ARG = "stop"
SYSTEMCTL_DAEMON_RELOAD_ARG = "daemon-reload"
SYSTEMCTL_ENABLE_NOW_ARG = "--now"

SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")
TRAFFIC_TIMER_UNIT = "opskit-xui-traffic.timer"
TRAFFIC_SERVICE_UNIT = "opskit-xui-traffic.service"
TRAFFIC_SERVICE_UNIT_FILE = SYSTEMD_UNIT_DIR / TRAFFIC_SERVICE_UNIT
TRAFFIC_TIMER_UNIT_FILE = SYSTEMD_UNIT_DIR / TRAFFIC_TIMER_UNIT
TRAFFIC_SNAPSHOT_CLI_ARGS = "software xui-snapshot"
TRAFFIC_SERVICE_UNIT_CONTENT = (
    "[Unit]\n"
    "Description=OpsKit x-ui traffic snapshot\n\n"
    "[Service]\n"
    "Type=oneshot\n"
    "ExecStart={exec_start}\n"
)
TRAFFIC_TIMER_UNIT_CONTENT = (
    "[Unit]\n"
    "Description=OpsKit x-ui traffic snapshot timer\n\n"
    "[Timer]\n"
    "OnCalendar=hourly\n"
    "Persistent=true\n\n"
    "[Install]\n"
    "WantedBy=timers.target\n"
)

TRAFFIC_HISTORY_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS traffic_snapshots ("
    "ts INTEGER NOT NULL, inbound_id INTEGER NOT NULL, "
    "remark TEXT, up INTEGER NOT NULL, down INTEGER NOT NULL)"
)
TRAFFIC_PERIOD_TODAY = "today"
TRAFFIC_PERIOD_WEEK = "week"
TRAFFIC_PERIOD_MONTH = "month"
TRAFFIC_BYTE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")
TRAFFIC_BYTE_STEP = 1024.0

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
DEFAULT_REALITY_SNI = "www.tesla.com"
DEFAULT_FINGERPRINT = "chrome"
DEFAULT_VLESS_REMARK = "opskit-vless-reality-tcp"
DEFAULT_PANEL_USER = "opskit"

SHORT_ID_BYTES = 8
PASSWORD_BYTES = 18
CLIENT_TOTAL_GB = 1024**4
CLIENT_EXPIRY_DAYS = 365
SECONDS_PER_DAY = 86400

XUI_API_LOGIN_PATH = "/login"
XUI_API_CSRF_PATH = "/csrf-token"
XUI_API_ADD_INBOUND_PATH = "/panel/api/inbounds/add"

HTTP_STATUS_OK = 200
HTTP_STATUS_REDIRECT_MIN = 300
HTTP_STATUS_REDIRECT_MAX = 399
HTTP_TIMEOUT_SECONDS = 10
PUBLIC_HOST_DETECT_TIMEOUT = 3
SERVICE_RESTART_TIMEOUT = 20
PANEL_API_RETRY_COUNT = 10
PANEL_API_RETRY_DELAY_SECONDS = 1
INSTALL_SCRIPT_TIMEOUT = 600

VLESS_PROTOCOL = "vless"
VLESS_FLOW = "xtls-rprx-vision"
REALITY_SECURITY = "reality"
TCP_NETWORK = "tcp"
CLIENT_FINGERPRINT = "chrome"
FREEDOM_PROTOCOL = "freedom"
VLESS_DECRYPTION = "none"
SNIFFING_DEST_OVERRIDE = ["http", "tls", "quic"]

XUI_SERVER_RECIPE_KEY = "xui_server"
SYSCTL_COMMAND = "sysctl"
SYSCTL_WRITE_ARG = "-w"
MODPROBE_COMMAND = "modprobe"
BBR_KERNEL_MODULE = "tcp_bbr"
BBR_SYSPARAMS = {
    "net.core.default_qdisc": "fq",
    "net.ipv4.tcp_congestion_control": "bbr",
}
BBR_SYSCTL_FILE = Path("/etc/sysctl.d/99-xui-bbr.conf")
BBR_SYSCTL_FILE_CONTENT = "".join(f"{key} = {value}\n" for key, value in BBR_SYSPARAMS.items())

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
