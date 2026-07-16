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
    select,
    multi_select,
    paged_select,
    confirm,
    text_input,
    pause,
    clear_screen,
    print_header,
    UserCancel,
    console,
)

# ─── 插件间服务 ─────────────────────────────────────────────────────────────────
from core.plugin_services import get_service, open_service_menu

# ─── 子进程执行 ───────────────────────────────────────────────────────────────
from core.runner import run, run_lines, which, cmd_ok

# ─── 路径 ─────────────────────────────────────────────────────────────────────
from core.paths import data_dir, cache_dir, log_dir, plugins_dir, plugin_data_dir

# ─── YAML 读写（插件配置 / 构建记录，禁止直接依赖第三方库）──────────────────
from core.yamlio import load_yaml, save_yaml

# ─── 日志 ─────────────────────────────────────────────────────────────────────
from core.logger import get_logger


# ─── 软件安装（复用平台软件配方，插件不得自行实现安装器）───────────────
def ensure_software(key: str) -> bool:
    """检测并按需安装 OpsKit 自带软件配方（如 golang / docker / nodejs）。

    已装 → 静默返回 True；未装 → 以平台统一安装流程（进度条 + 统一反馈）
    安装推荐版本。只允许安装注册表内的配方（白名单），插件无法借此执行
    任意安装逻辑。调用前应已渲染阶段标题（clear_screen + print_header）。
    """
    from software.actions import ensure_installed
    return ensure_installed(key)


def software_bin(key: str, cmd: str) -> str | None:
    """返回已安装软件配方提供的可执行文件路径，未安装返回 None。

    插件无需关心平台把软件装在哪（系统 PATH / 私有目录 / shim）：平台
    会先激活该软件到当前进程 PATH，再解析 cmd，保证后续子进程可直接调用。
    仅限注册表内配方（白名单）。
    """
    from software.actions import resolve_bin
    return resolve_bin(key, cmd)


def ensure_python_package(package: str, import_name: str = "") -> bool:
    """检测并按需 pip 安装 Python 包到应用 venv（供插件声明运行时依赖）。

    已可导入 → 静默返回 True；否则用当前解释器 pip 安装，失败返回 False。
    """
    import importlib.util
    import subprocess
    import sys

    name = import_name or package
    if importlib.util.find_spec(name) is not None:
        return True
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", package],
            check=True,
        )
    except subprocess.CalledProcessError:
        return False
    importlib.invalidate_caches()
    return importlib.util.find_spec(name) is not None


__all__ = [
    "SDK_API_VERSION",
    "ModuleInfo",
    "t", "current_lang", "register_locale",
    "get_color", "get_icon",
    "print_success", "print_error", "print_warning", "print_info",
    "select", "multi_select", "paged_select", "confirm", "text_input", "pause", "clear_screen", "print_header", "UserCancel", "console",
    "get_service", "open_service_menu",
    "ensure_python_package",
    "run", "run_lines", "which", "cmd_ok",
    "data_dir", "cache_dir", "log_dir", "plugins_dir", "plugin_data_dir",
    "load_yaml", "save_yaml",
    "get_logger",
    "ensure_software",
    "software_bin",
]
