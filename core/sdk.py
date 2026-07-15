"""OpsKit 插件 SDK — 外部插件唯一允许依赖的稳定 API 面

外部插件（plugins/ 目录下的 python 插件）只准 `from core.sdk import ...`，
禁止 import core 其他内部模块。此文件的导出集合与语义受 SDK_API_VERSION 保护：
不兼容变更（删除导出 / 改签名语义）必须递增大版本，并同步 docs/plugin-spec.md。
"""
from __future__ import annotations

# ─── API 版本（plugin.yaml 的 api_version 必须与此相等） ─────────────────────
from core.constants import PLUGIN_API_VERSION as SDK_API_VERSION

# ─── 协议 ─────────────────────────────────────────────────────────────────────
from core.module import ModuleInfo

# ─── i18n ─────────────────────────────────────────────────────────────────────
from core.i18n import t, current_lang

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
    select,
    confirm,
    text_input,
    pause,
    clear_screen,
    UserCancel,
    console,
)

# ─── 子进程执行 ───────────────────────────────────────────────────────────────
from core.runner import run, run_lines, which, cmd_ok

# ─── 路径 ─────────────────────────────────────────────────────────────────────
from core.paths import data_dir, cache_dir, log_dir, plugins_dir

# ─── 日志 ─────────────────────────────────────────────────────────────────────
from core.logger import get_logger

__all__ = [
    "SDK_API_VERSION",
    "ModuleInfo",
    "t", "current_lang",
    "get_color", "get_icon",
    "print_success", "print_error", "print_warning", "print_info",
    "select", "confirm", "text_input", "pause", "clear_screen", "UserCancel", "console",
    "run", "run_lines", "which", "cmd_ok",
    "data_dir", "cache_dir", "log_dir", "plugins_dir",
    "get_logger",
]
