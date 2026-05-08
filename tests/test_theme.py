"""core/theme.py 单元测试"""
from __future__ import annotations

import pytest

from core import theme as th


def test_init_loads_catppuccin(tmp_path) -> None:
    th.init("catppuccin")
    assert th.current_theme() == "catppuccin"


def test_get_color_success(tmp_path) -> None:
    th.init("catppuccin")
    color = th.get_color("success")
    assert "a6e3a1" in color


def test_get_color_nested(tmp_path) -> None:
    th.init("catppuccin")
    color = th.get_color("modules.software.title")
    assert color != "white"


def test_get_color_fallback(tmp_path) -> None:
    th.init("catppuccin")
    assert th.get_color("nonexistent.token") == "white"


def test_get_icon_exists(tmp_path) -> None:
    th.init("catppuccin")
    assert th.get_icon("back") == "🔙"


def test_get_icon_fallback(tmp_path) -> None:
    th.init("catppuccin")
    assert th.get_icon("nonexistent") == "•"


def test_list_themes_contains_catppuccin(tmp_path) -> None:
    th.init("catppuccin")
    themes = th.list_themes()
    assert "catppuccin" in themes


def test_get_banner_config(tmp_path) -> None:
    th.init("catppuccin")
    cfg = th.get_banner_config()
    assert "gradient" in cfg
    assert "width" in cfg


def test_get_panel_config(tmp_path) -> None:
    th.init("catppuccin")
    cfg = th.get_panel_config()
    assert "box" in cfg
