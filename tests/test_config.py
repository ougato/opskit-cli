"""core/config.py 单元测试"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.config import (
    ensure_config,
    get_config_path,
    get_data_dir,
    load_config,
    save_config,
    set_config_value,
)
from core.constants import DEFAULT_CONFIG


def test_get_data_dir_uses_env(tmp_path: Path) -> None:
    assert get_data_dir() == tmp_path


def test_ensure_config_creates_file(tmp_path: Path) -> None:
    cfg = ensure_config()
    path = get_config_path()
    assert path.exists()
    assert cfg["language"] == DEFAULT_CONFIG["language"]


def test_ensure_config_idempotent(tmp_path: Path) -> None:
    ensure_config()
    ensure_config()
    path = get_config_path()
    assert path.exists()


def test_save_and_load_config(tmp_path: Path) -> None:
    cfg = {"language": "zh", "theme": "catppuccin"}
    get_config_path().parent.mkdir(parents=True, exist_ok=True)
    save_config(cfg)
    loaded = load_config()
    assert loaded["language"] == "zh"


def test_set_config_value_nested(tmp_path: Path) -> None:
    cfg = ensure_config()
    cfg = set_config_value(cfg, "update.enabled", False)
    assert cfg["update"]["enabled"] is False
    reloaded = load_config()
    assert reloaded["update"]["enabled"] is False


def test_set_config_value_top_level(tmp_path: Path) -> None:
    cfg = ensure_config()
    cfg = set_config_value(cfg, "theme", "nord")
    assert cfg["theme"] == "nord"


def test_load_config_merges_defaults(tmp_path: Path) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump({"language": "zh"}, f)
    cfg = load_config()
    assert cfg["language"] == "zh"
    assert "update" in cfg
