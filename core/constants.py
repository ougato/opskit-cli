"""全局常量定义 — 所有路径 / 超时 / 重试 / 网络配置统一管理"""
from __future__ import annotations

from pathlib import Path

# ─── 版本 ───────────────────────────────────────────────────────────────────
APP_NAME = "OpsKit"
APP_VERSION = 6

# ─── 目录名 ──────────────────────────────────────────────────────────────────
DIR_CONFIG = "config"
DIR_DATA = "data"
DIR_CACHE = "cache"
DIR_LOGS = "logs"
DIR_THEMES = "core/themes"
DIR_LOCALE = "core/locale"
DIR_MIRRORS = "core/mirrors"
DIR_PLUGINS = "plugins"
DIR_PLUGIN_DATA = "plugin-data"

# ─── 配置文件名 ──────────────────────────────────────────────────────────────
FILE_CONFIG = "common.yaml"
FILE_MIRROR_CACHE = "mirror_cache.yaml"
FILE_UPDATE_CACHE = "update_check.json"
FILE_LOCK = "opskit.lock"
FILE_PLUGIN_MANIFEST = "plugin.yaml"
FILE_PLUGIN_TRUST = "plugin_trust.yaml"

# ─── 插件 SDK API 版本（不兼容变更才递增，见 core/sdk.py 与 docs/plugin-spec.md） ──
PLUGIN_API_VERSION = 1

# ─── 默认配置模板（首次运行时写入） ─────────────────────────────────────────
DEFAULT_CONFIG: dict = {
    "language": "auto",
    "theme": "catppuccin",
    "modules": {},
    "update": {
        "enabled": True,
        "repo": "ougato/opskit-cli",
    },
    "mirror": {
        "region": "auto",
    },
    "wireguard": {
        "domain": "",
        "client": {
            "server_ip": "",
            "reality_pub": "",
            "wg_server_pub": "",
            "uuid": "",
            "short_id": "",
        },
    },
    "log": {
        "level": "WARNING",
    },
    "plugin": {
        "trusted_sources": [],
    },
    "telemetry": {
        "enabled": True,
        "dsn": "https://f1b67ed44be18705828ccf6d6f44e37c@o4511342957297664.ingest.us.sentry.io/4511342960574464",
    },
}

# ─── 网络超时（秒） ──────────────────────────────────────────────────────────
TIMEOUT_HTTP = 10
TIMEOUT_MIRROR_PROBE = 5
TIMEOUT_VERSION_FETCH = 3
TIMEOUT_DOWNLOAD_STALL = 10
TIMEOUT_UPDATE_CHECK = 5
TIMEOUT_CLEANUP = 5
TIMEOUT_SOURCE_MAX = 60
TIMEOUT_STALL_SECS = 30
DOWNLOAD_SLOW_WINDOW = 30
MIRROR_RACE_COUNT = 3

# ─── 重试次数 ────────────────────────────────────────────────────────────────
MAX_RETRY_DOWNLOAD = 5
MAX_RETRY_INSTALL = 2

# ─── 下载 ────────────────────────────────────────────────────────────────────
DOWNLOAD_CHUNK_SIZE = 65536
DOWNLOAD_MIN_SPEED_KBPS = 10
DOWNLOAD_CACHE_DIR = "opskit"
DOWNLOAD_RETRY_BASE_DELAY = 1       # 指数退避基数（秒），第 n 次失败后等 2^(n-1) 秒
TIMEOUT_DOWNLOAD_READ = 30          # 单块读取超时（秒），替换 read=None 防连接挂死
UPDATE_RATELIMIT_BACKOFF = 3600     # GitHub Rate Limit 后延迟检查时间（秒）

# ─── 日志轮转 ────────────────────────────────────────────────────────────────
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3
LOG_FORMAT = "[{asctime}] [{levelname}] [{name}] {message}"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ─── 镜像测速 ────────────────────────────────────────────────────────────────
MIRROR_PROBE_COUNT = 3
MIRROR_CACHE_TTL = 86400

# ─── 版本缓存 ────────────────────────────────────────────────────────────────
FILE_INSTALL_SNAPSHOTS = "install_snapshots.json"
FILE_VERSION_CACHE = "version_cache.yaml"
SNAPSHOT_JSON_INDENT = 2            # 多版本安装快照 JSON 缩进
VERSION_CACHE_TTL = 3600            # 1h 内不重新获取
VERSION_CACHE_STALE_TTL = 86400     # 超过 24h 视为完全过期
VERSION_FETCH_TIMEOUT = 10          # 后台获取超时（宽松）
VERSION_FETCH_INTERVAL = 500        # 后台串行获取间隔 ms

# ─── Bootstrap（OpsKit 自更新动态控制面 / version manifest）─────────────────
# 并发拉取，取最快返回；GitHub raw 为主，ghproxy 镜像作兜底
BOOTSTRAP_URLS = [
    "https://raw.githubusercontent.com/ougato/opskit-cli/main/bootstrap.json",
    "https://mirror.ghproxy.com/https://raw.githubusercontent.com/ougato/opskit-cli/main/bootstrap.json",
]
BOOTSTRAP_TIMEOUT = 5
FILE_BOOTSTRAP_CACHE = "bootstrap_cache.json"
BOOTSTRAP_SCHEMA_VERSION = 2        # 控制面 manifest schema 版本

# ─── 灰度发布（rollout）─────────────────────────────────────────────────────
UPDATE_ROLLOUT_FULL = 100           # 100 表示全量放量
FILE_MACHINE_ID = "machine_id"      # 灰度分桶用的稳定机器指纹

# ─── 崩溃回滚健康探针 ────────────────────────────────────────────────────────
FILE_UPDATE_HEALTH = "update_health.json"
HEALTH_CONFIRM_DELAY = 15           # 新版本启动 N 秒内未崩溃则确认健康（秒）
MAX_HEALTH_FAILS = 2                # 未确认健康的启动达到此次数则回滚

# ─── GitHub ──────────────────────────────────────────────────────────────────
GITHUB_BASE = "https://github.com"
GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_RELEASES = "https://api.github.com/repos/{repo}/releases/latest"
GITHUB_API_RELEASES_LIST = "https://api.github.com/repos/{repo}/releases?per_page=10"
GITHUB_API_TAGS = "https://api.github.com/repos/{repo}/tags?per_page=10"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com"

# ─── ghproxy 镜像加速 ────────────────────────────────────────────────────────
GHPROXY_BASE = "https://mirror.ghproxy.com"

# ─── 公网 IP 探测 API ────────────────────────────────────────────────────────
PUBLIC_IP_APIS = [
    "https://ip.sb",
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
    "https://4.ipw.cn",
]

# ─── 地区探测 API ────────────────────────────────────────────────────────────
REGION_DETECT_APIS = [
    ("https://ipinfo.io/json",               lambda d: d.get("country", "")),
    ("https://ipapi.co/json/",               lambda d: d.get("country_code", "")),
    ("https://ip-api.com/json/?fields=countryCode",
                                             lambda d: d.get("countryCode", "")),
]

# ─── 第三方 endoflife API（通用） ────────────────────────────────────────────
ENDOFLIFE_API = "https://endoflife.date/api/{product}.json"

# ─── 网络测速 ───────────────────────────────────────────────────────────────
SPEED_TEST_URL = "https://speed.cloudflare.com/__down?bytes=5000000"

# ─── 安装前磁盘空间保留（字节） ──────────────────────────────────────────────
MIN_DISK_FREE_BYTES = 512 * 1024 * 1024

# ─── 依赖解析 ────────────────────────────────────────────────────────────────
MAX_DEP_DEPTH = 5             # 依赖链最大递归深度，防止循环依赖死循环

# ─── 日志分析 ────────────────────────────────────────────────────────────────
LOG_ANALYSIS_TAIL_LINES = 200
LOG_ANALYSIS_MAX_LINES = 500
