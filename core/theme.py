"""主题引擎 — 加载、切换、查询 API"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.markup import escape

from core.constants import DIR_THEMES

console = Console()

_theme: dict[str, Any] = {}
_theme_name: str = "catppuccin"


# ─── 内部工具 ─────────────────────────────────────────────────────────────────

def _get_themes_dir() -> Path:
    from core.config import get_resource_dir
    return get_resource_dir(DIR_THEMES)


def _resolve(data: dict[str, Any], token: str) -> Any:
    """按点号路径查找嵌套 dict 中的值，找不到返回 None"""
    keys = token.split(".")
    node: Any = data
    for k in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(k)
    return node


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ─── 公开 API ─────────────────────────────────────────────────────────────────

def init(theme_name: str | None = None) -> None:
    """
    加载主题：
    1. theme_name 为 None → 读 config/common.yaml 中的 theme 字段
    2. 加载 core/themes/{theme_name}.yaml
    3. 解析到全局 _theme 字典
    """
    global _theme, _theme_name
    if theme_name is None:
        from core.config import load_config
        cfg = load_config()
        theme_name = cfg.get("theme", "catppuccin")

    theme_file = _get_themes_dir() / f"{theme_name}.yaml"
    if not theme_file.exists():
        theme_file = _get_themes_dir() / "catppuccin.yaml"
        theme_name = "catppuccin"

    data = _load_yaml(theme_file)

    parent_name = data.get("meta", {}).get("extends")
    if parent_name:
        parent_file = _get_themes_dir() / f"{parent_name}.yaml"
        if parent_file.exists():
            parent_data = _load_yaml(parent_file)
            data = _deep_merge(parent_data, data)

    _theme = data
    _theme_name = theme_name


def get_color(token: str) -> str:
    """
    获取颜色样式字符串。

    token 使用点号分隔路径：
      get_color('success')                → 'bold #a6e3a1'
      get_color('modules.software.title') → 'bold #89b4fa'

    找不到 → 返回 'white'（安全降级，不崩溃）
    """
    value = _resolve(_theme.get("colors", {}), token)
    if isinstance(value, str):
        return value
    return "white"


def get_icon(token: str) -> str:
    """
    获取图标字符串（纯 emoji）。

      get_icon('back')     → '🔙'
      get_icon('software') → '📦'

    找不到 → 返回 '•'（安全降级）
    """
    value = _theme.get("icons", {}).get(token)
    if isinstance(value, str):
        return value
    return "•"


def get_banner_config() -> dict[str, Any]:
    """返回 banner 配置（gradient / border / width）"""
    return _theme.get("banner", {})


def get_panel_config() -> dict[str, Any]:
    """返回 panel 配置（box / width / padding）"""
    return _theme.get("panel", {})


def list_themes() -> list[str]:
    """列出所有可用主题名（不含扩展名，不含 _schema）"""
    d = _get_themes_dir()
    if not d.exists():
        return ["catppuccin"]
    return sorted(
        p.stem for p in d.glob("*.yaml") if not p.stem.startswith("_")
    )


def switch_theme(name: str) -> None:
    """运行时切换主题（重新加载 YAML），同时持久化到配置文件"""
    from core.config import load_config, set_config_value
    cfg = load_config()
    init(name)
    set_config_value(cfg, "theme", _theme_name)


def current_theme() -> str:
    """返回当前主题名"""
    return _theme_name


# ─── 便捷 print_* 函数 ────────────────────────────────────────────────────────

def print_success(msg: str, elapsed: float | None = None) -> None:
    color = get_color("success")
    icon = get_icon("success")
    suffix = (
        f"  ([{get_color('muted')}]{elapsed:.1f}s[/{get_color('muted')}])"
        if elapsed is not None
        else ""
    )
    console.print()
    console.print(f"[{color}]{icon} {escape(msg)}[/{color}]{suffix}")


def print_error(msg: str) -> None:
    color = get_color("error")
    icon = get_icon("error")
    console.print()
    console.print(f"[{color}]{icon} {escape(msg)}[/{color}]")


def print_warning(msg: str) -> None:
    color = get_color("warning")
    icon = get_icon("warning")
    console.print()
    console.print(f"[{color}]{icon} {escape(msg)}[/{color}]")


def print_info(msg: str) -> None:
    color = get_color("info")
    icon = get_icon("info")
    console.print()
    console.print(f"[{color}]{icon} {escape(msg)}[/{color}]")


def print_action_title(breadcrumb: list[str]) -> None:
    """打印操作页面标题（Powerline 面包屑色块风格，与菜单标题完全一致）。

    用于安装、卸载、诊断等操作页面进度条开始前，让用户知道当前在做什么。

    Args:
        breadcrumb: 面包屑标签列表，例如 ["OpsKit", "WireGuard", "公网服务端", "安装"]

    用法：
        from core.theme import print_action_title
        print_action_title(["OpsKit", "WireGuard", "公网服务端", "安装"])
        with MultiStepProgress(descs) as sp:
            ...
    """
    from core.prompt import _render_header
    _render_header(breadcrumb)
    console.print()
