"""OpsKit 插件 SDK — 外部插件唯一允许依赖的稳定 API 面

外部插件（plugins/ 目录下的 python 插件）只准 `from core.sdk import ...`，
禁止 import core 其他内部模块。此文件的导出集合与语义受 SDK_API_VERSION 保护：
不兼容变更（删除导出 / 改签名语义）必须递增大版本，并同步 docs/plugin-spec.md。
"""
from __future__ import annotations
from contextlib import contextmanager

# ─── API 版本（plugin.yaml 的 api_version 必须与此相等） ─────────────────────
from core.constants import PLUGIN_API_VERSION as SDK_API_VERSION

# ─── 协议 ─────────────────────────────────────────────────────────────────────
from core.module import ModuleInfo

# ─── i18n ─────────────────────────────────────────────────────────────────────
from core.i18n import t, current_lang, register_locale

# ─── 主题 / 输出 ──────────────────────────────────────────────────────────────
from core.theme import (
    get_color,
    get_icon,
    print_success,
    print_error,
    print_warning,
    print_info,
)

# ─── 交互 ─────────────────────────────────────────────────────────────────────
from core.prompt import (
    select as _select,
    select_input as _select_input,
    multi_select as _multi_select,
    paged_select,
    confirm,
    text_input,
    pause,
    clear_screen,
    print_header,
    UserCancel,
    console,
)


@contextmanager
def waiting(message: str = ""):
    """Show a transient wait indicator while the enclosed operation runs."""
    from core.installation_progress import WaitingDisplay
    display = WaitingDisplay()
    display.start(message)
    try:
        yield
    finally:
        display.close()


def select(breadcrumb: list[str], subtitle: str, choices: list[dict], theme_key: str = 'root',
           show_shortcuts: list[tuple[str, str, str]] | None = None, back_label: str = '',
           description: str = '', info_lines: list[str] | None = None) -> str | None:
    """Select a key; return None when the user goes back or presses Escape."""
    try:
        return _select(breadcrumb, subtitle, choices, theme_key, show_shortcuts, back_label,
                       description=description, info_lines=info_lines)
    except UserCancel:
        return None


def select_input(breadcrumb: list[str], subtitle: str, choices: list[dict], back_label: str = '',
                 description: str = '', default: str = '', info_lines: list[str] | None = None) -> str | None:
    """Read a menu value; 0 and Escape go back, while an empty line uses default."""
    try:
        value = _select_input(breadcrumb, subtitle, choices, back_label, description, default, info_lines)
    except UserCancel:
        return None
    return None if value == '0' else value


def multi_select(breadcrumb: list[str], subtitle: str, options: list[str], theme_key: str = 'root',
                 back_label: str = '', all_index: int | None = None) -> list[int] | None:
    """Return selected indexes, or None when the user goes back or presses Escape."""
    try:
        return _multi_select(breadcrumb, subtitle, options, theme_key, back_label, all_index)
    except UserCancel:
        return None

# ─── 插件间服务 ─────────────────────────────────────────────────────────────────
def get_service(name: str) -> object:
    """Return a service proxy in an isolated worker; access is checked on use."""
    raise PermissionError("service API requires an isolated plugin worker")


def request_plugin_install(provider: str) -> bool:
    """Request an approved, declared provider from an interactive plugin menu."""
    raise PermissionError("plugin installation requires an isolated plugin worker")


def open_service_menu(name: str, breadcrumb: list[str], context: dict[str, object] | None = None) -> bool:
    """Open an authorized provider's menu; never install a provider implicitly."""
    raise PermissionError("service API requires an isolated plugin worker")

# ─── 子进程执行 ───────────────────────────────────────────────────────────────
from core.runner import run, run_lines, which, cmd_ok

# ─── 路径 ─────────────────────────────────────────────────────────────────────
from core.paths import data_dir, cache_dir, log_dir, plugins_dir, plugin_data_dir

# ─── YAML 读写（插件配置 / 构建记录，禁止直接依赖第三方库）──────────────────
from core.yamlio import load_yaml, save_yaml

# ─── 日志 ─────────────────────────────────────────────────────────────────────
from core.logger import get_logger


def ensure_python_package(package: str, import_name: str = "") -> bool:
    """Only inspect dependencies baked into the plugin's immutable runtime.

    This API never installs into the platform environment. Prepare dependencies
    through scripts/build_plugin_runtime.py before execution.
    """
    import importlib.util
    return importlib.util.find_spec(import_name or package) is not None


__all__ = [
    "read_host_file",
    "compare_write_host_file",
    "create_host_file",
    "host_file_status",
    "list_host_files",
    "artifact_info",
    "host_execute", "host_artifacts", "host_path_info", "host_find_executable", "host_file_version", "host_asar_directories",
    "https_request",
    "upload_file",
    "SDK_API_VERSION",
    "ModuleInfo",
    "t", "current_lang", "register_locale",
    "get_color", "get_icon",
    "print_success", "print_error", "print_warning", "print_info",
    "select", "select_input", "multi_select", "paged_select", "confirm", "text_input", "pause", "clear_screen", "print_header", "waiting", "UserCancel", "console",
    "get_service", "open_service_menu", "request_plugin_install",
    "ensure_python_package",
    "run", "run_lines", "which", "cmd_ok",
    "data_dir", "cache_dir", "log_dir", "plugins_dir", "plugin_data_dir",
    "load_yaml", "save_yaml",
    "get_logger",
]


def read_host_file(directory: str, relative: str) -> bytes:
    """Read up to 1MiB after host-owned directory authorization."""
    raise PermissionError("host file API requires an isolated plugin worker")


def compare_write_host_file(directory: str, relative: str, expected_sha256: str, content: bytes) -> dict:
    """Replace an unchanged bounded host file after exact write authorization."""
    raise PermissionError("host file API requires an isolated plugin worker")


def create_host_file(directory: str, relative: str, content: bytes) -> dict:
    """Create a new bounded host file with exact authorization; never overwrite."""
    raise PermissionError("host file API requires an isolated plugin worker")


def host_file_status(directory: str, relative: str) -> dict:
    """Read a bounded ordinary file's existence and digest after authorization."""
    raise PermissionError("host file API requires an isolated plugin worker")


def host_asar_directories(directory: str, relative: str, member: str) -> list[str]:
    """List bounded immediate ASAR directories after host directory authorization."""
    raise PermissionError("host archive API requires an isolated plugin worker")


def list_host_files(directory: str, *, missing_ok: bool = False) -> list[str]:
    """List ordinary files under an authorized host directory, or allow an absent directory."""
    raise PermissionError("host file listing requires an isolated plugin worker")


def artifact_info(directory: str, relative: str) -> dict:
    """Return bytes/SHA256 for an authorized ordinary host file, up to 512MiB."""
    raise PermissionError("artifact API requires an isolated plugin worker")


def host_execute(executable: str, arguments: list[str], directory: str, options: dict | None = None) -> dict:
    """Execute an approved host tool with bounded input, output and execution time."""
    raise PermissionError("host tool API requires an isolated plugin worker")


def host_artifacts(action: str, parameters: dict) -> dict | list:
    """Commit/list/collect identity-bound outputs owned by the current plugin."""
    raise PermissionError("artifact receipt API requires an isolated plugin worker")


def host_path_info(path: str, directory: bool = False, write: bool = False, execute: bool = False) -> dict:
    """Validate a host path and explicitly request the required directory access."""
    raise PermissionError("host path API requires an isolated plugin worker")


def host_find_executable(name: str, preferred: str = "") -> str:
    """Find an ordinary host executable and authorize its directory, or return an empty string."""
    raise PermissionError("host tool API requires an isolated plugin worker")


def host_file_version(path: str) -> dict:
    """Inspect Windows fixed file/product versions after directory authorization."""
    raise PermissionError("host version API requires an isolated plugin worker")


def https_request(url: str, method: str = "GET", headers: dict | None = None,
                  body: bytes = b"") -> dict:
    """Bounded HTTPS request; returns status and body bytes, requires network permission."""
    raise PermissionError("network API requires an isolated plugin worker")


def upload_file(directory: str, relative: str, url: str, sha256: str,
                headers: dict | None = None) -> dict:
    """PUT a checksum-bound file (up to 512MiB); requires network and directory consent."""
    raise PermissionError("upload API requires an isolated plugin worker")
