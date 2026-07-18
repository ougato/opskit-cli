"""YAML 读写助手 — 供内部模块与插件 SDK 使用（插件禁止直接依赖第三方库）"""
from __future__ import annotations

from pathlib import Path

import yaml


def load_yaml(path: Path | str) -> dict:
    """读取 YAML 文件为 dict，文件不存在或内容非映射时返回空 dict"""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_yaml(path: Path | str, data: dict) -> None:
    """把 dict 写入 YAML 文件（UTF-8，保持键序，自动建父目录）"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
