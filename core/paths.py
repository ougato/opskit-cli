"""跨平台应用路径统一入口

所有路径均从此模块获取，禁止在其他文件中硬编码任何系统路径。

优先级：
  1. 环境变量覆盖（OPSKIT_DATA_DIR 等）
  2. platformdirs 标准路径（遵循 XDG / Win / macOS 规范）
  3. root 模式 Linux 使用 FHS 系统级目录
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


# ─── 懒加载 platformdirs（打包环境已内置，开发环境按需 import）────────────────
def _get_platformdirs():
    try:
        import platformdirs
        return platformdirs
    except ImportError:
        return None


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False) or "__compiled__" in globals()


def _is_root() -> bool:
    if sys.platform == "win32":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


# ─── OpsKit 自身数据目录 ──────────────────────────────────────────────────────

def data_dir() -> Path:
    """
    OpsKit 运行数据目录（config/cache/log/pending 均放此目录下）。

    优先级：
      1. 环境变量 OPSKIT_DATA_DIR
      2. 开发模式 → 项目根目录
      3. Linux root → /var/lib/opskit（FHS 合规）
      4. 其他 → platformdirs.user_data_dir("opskit")
         Windows 7/10/11：%LOCALAPPDATA%/opskit
         macOS：          ~/Library/Application Support/opskit
         Linux 非 root：  ~/.local/share/opskit（XDG_DATA_HOME）
    """
    env = os.environ.get("OPSKIT_DATA_DIR")
    if env:
        return Path(env)

    if not _is_frozen():
        return Path(__file__).resolve().parent.parent

    if sys.platform == "linux" and _is_root():
        return Path("/var/lib/opskit")

    pd = _get_platformdirs()
    if pd:
        return Path(pd.user_data_dir("opskit", appauthor=False))

    # platformdirs 不可用时的硬兜底（不应触发，仅防御性）
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "opskit"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "opskit"
    return Path.home() / ".local" / "share" / "opskit"


def config_dir() -> Path:
    """配置文件目录（config/common.yaml 所在）"""
    env = os.environ.get("OPSKIT_DATA_DIR")
    if env:
        return Path(env) / "config"
    if not _is_frozen():
        return Path(__file__).resolve().parent.parent / "config"
    return data_dir() / "config"


def cache_dir() -> Path:
    """下载缓存目录"""
    env = os.environ.get("OPSKIT_DATA_DIR")
    if env:
        return Path(env) / "cache"
    if not _is_frozen():
        return Path(__file__).resolve().parent.parent / "cache"

    pd = _get_platformdirs()
    if pd and not (sys.platform == "linux" and _is_root()):
        return Path(pd.user_cache_dir("opskit", appauthor=False))
    return data_dir() / "cache"


def log_dir() -> Path:
    """日志目录"""
    if sys.platform == "linux" and _is_frozen() and _is_root():
        return Path("/var/log/opskit")
    return data_dir() / "logs"


# ─── Xray 安装路径（Linux 专用，遵循 FHS）────────────────────────────────────

def xray_bin_dir() -> Path:
    """xray 可执行文件目录"""
    return Path("/usr/local/bin")


def xray_config_dir() -> Path:
    """xray 配置目录"""
    return Path("/usr/local/etc/xray")


def xray_data_dir() -> Path:
    """xray geo 数据目录（geoip.dat / geosite.dat）"""
    return Path("/usr/local/share/xray")


def xray_log_dir() -> Path:
    """xray 日志目录"""
    return Path("/var/log/xray")


def xray_config_file() -> Path:
    return xray_config_dir() / "config.json"


def xray_binary() -> Path:
    return xray_bin_dir() / "xray"


# ─── Nginx 路径（发行版差异集中处理）────────────────────────────────────────

def nginx_webroot() -> Path:
    """
    Nginx 静态文件根目录（acme.sh HTTP-01 验证用）。

    Debian/Ubuntu：/var/www/html
    CentOS/RHEL/Alpine：/usr/share/nginx/html
    """
    if Path("/var/www/html").exists():
        return Path("/var/www/html")
    return Path("/usr/share/nginx/html")


def nginx_conf_dir() -> Path:
    return Path("/etc/nginx/conf.d")


def nginx_ssl_dir() -> Path:
    return Path("/etc/nginx/ssl")


def nginx_sites_enabled_dir() -> Path:
    return Path("/etc/nginx/sites-enabled")


def nginx_base_dir() -> Path:
    return Path("/etc/nginx")


# ─── WireGuard 路径 ───────────────────────────────────────────────────────────

def wg_config_dir() -> Path:
    return Path("/etc/wireguard")


def wg_config_file() -> Path:
    return wg_config_dir() / "wg0.conf"
