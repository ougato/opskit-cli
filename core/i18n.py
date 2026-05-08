"""国际化引擎 — 语言检测、文案加载、切换与持久化"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from core.constants import DIR_LOCALE

_current: dict[str, Any] = {}
_lang: str = "en"


# ─── 内部工具 ─────────────────────────────────────────────────────────────────

def _get_locale_dir() -> Path:
    from core.config import get_resource_dir
    return get_resource_dir(DIR_LOCALE)


def _load_lang(lang: str) -> dict[str, Any]:
    path = _get_locale_dir() / f"{lang}.yaml"
    if not path.exists():
        path = _get_locale_dir() / "en.yaml"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _detect_system_lang() -> str:
    """
    检测系统语言，返回 'zh' 或 'en'。

    优先级：
    1. Linux: LANG / LC_ALL 环境变量
    2. Windows: ctypes.windll.kernel32.GetUserDefaultUILanguage()
    3. macOS: defaults read -g AppleLanguages（子进程）
    4. 兜底 → 'en'
    """
    import os

    platform = sys.platform

    if platform in ("linux", "linux2", "darwin"):
        for var in ("LANG", "LC_ALL", "LC_MESSAGES", "LANGUAGE"):
            val = os.environ.get(var, "")
            if val.startswith("zh"):
                return "zh"
        if platform == "darwin":
            try:
                import subprocess
                result = subprocess.run(
                    ["defaults", "read", "-g", "AppleLanguages"],
                    capture_output=True, text=True, timeout=2
                )
                if "zh" in result.stdout:
                    return "zh"
            except Exception:
                pass
    elif platform == "win32":
        try:
            import ctypes
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            # 0x0804 = zh-CN, 0x0404 = zh-TW, 0x0804 系列
            if (lang_id & 0xFF) == 0x04:
                return "zh"
        except Exception:
            pass

    return "en"


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """递归展平嵌套 dict 为点号路径 key → value"""
    result: dict[str, str] = {}
    for k, v in data.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            result.update(_flatten(v, full_key))
        else:
            result[full_key] = str(v)
    return result


# ─── 公开 API ─────────────────────────────────────────────────────────────────

def init() -> None:
    """
    语言决策优先级（从高到低）：

    1. config/common.yaml → language 字段
       - 值为 'zh' / 'en' → 直接使用
       - 值为 'auto' 或字段不存在 → 进入第 2 步
    2. 检测系统语言
    3. 兜底默认 → 'en'
    """
    global _current, _lang

    from core.config import load_config
    cfg = load_config()
    lang_cfg = cfg.get("language", "auto")

    if lang_cfg in ("zh", "en"):
        _lang = lang_cfg
    else:
        _lang = _detect_system_lang()

    _current = _flatten(_load_lang(_lang))


def t(key: str, **kwargs: Any) -> str:
    """
    翻译函数。

    t('install.progress', step=2, total=5) → '[2/5] 安装中...'
    找不到 key → 原样返回 key（安全降级）
    """
    template = _current.get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, ValueError):
            return template
    return template


def switch(lang: str) -> None:
    """
    切换语言并持久化：
    1. 重新加载 locale/{lang}.yaml
    2. 写入 config/common.yaml → language: {lang}
    3. 下次启动时 init() 读到 language: zh/en → 直接使用，不再自动检测
    """
    global _current, _lang
    if lang not in ("zh", "en", "auto"):
        return
    if lang == "auto":
        _lang = _detect_system_lang()
    else:
        _lang = lang
    _current = _flatten(_load_lang(_lang))

    from core.config import load_config, set_config_value
    cfg = load_config()
    set_config_value(cfg, "language", lang)


def current_lang() -> str:
    """返回当前语言代码：'zh' / 'en'"""
    return _lang
