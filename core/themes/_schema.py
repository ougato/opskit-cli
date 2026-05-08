"""ThemeData 数据类定义 — 描述主题文件的结构"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ThemeData:
    meta: dict[str, str] = field(default_factory=dict)
    colors: dict[str, Any] = field(default_factory=dict)
    icons: dict[str, str] = field(default_factory=dict)
    banner: dict[str, Any] = field(default_factory=dict)
    panel: dict[str, Any] = field(default_factory=dict)
