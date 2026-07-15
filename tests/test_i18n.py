"""core/i18n.py 单元测试"""
from __future__ import annotations

import pytest

from core import i18n


def test_init_auto_loads(tmp_path) -> None:
    i18n.init()
    lang = i18n.current_lang()
    assert lang in ("zh", "en")


def test_t_returns_string(tmp_path) -> None:
    i18n.init()
    result = i18n.t("menu.exit")
    assert isinstance(result, str)
    assert len(result) > 0


def test_t_fallback_on_missing_key(tmp_path) -> None:
    i18n.init()
    result = i18n.t("nonexistent.key.xyz")
    assert result == "nonexistent.key.xyz"


def test_t_format_kwargs(tmp_path) -> None:
    i18n.switch("zh")
    result = i18n.t("install.progress", step=2, total=5, desc="测试")
    assert "2" in result
    assert "5" in result


def test_switch_zh(tmp_path) -> None:
    i18n.switch("zh")
    assert i18n.current_lang() == "zh"
    result = i18n.t("menu.exit")
    assert result == "退出"


def test_switch_en(tmp_path) -> None:
    i18n.switch("en")
    assert i18n.current_lang() == "en"
    result = i18n.t("menu.exit")
    assert result == "Exit"


def test_switch_persists_to_config(tmp_path) -> None:
    from core.config import load_config
    i18n.switch("zh")
    cfg = load_config()
    assert cfg["language"] == "zh"


@pytest.fixture()
def _restore_lang():
    lang = i18n.current_lang()
    yield
    i18n.switch(lang)


def test_register_locale_plugin_keys(tmp_path, _restore_lang) -> None:
    i18n.switch("zh")
    i18n.register_locale({
        "zh": {"testplug": {"title": "标题"}},
        "en": {"testplug": {"title": "Title"}},
    })
    assert i18n.t("testplug.title") == "标题"
    # 切换语言后插件文案仍生效
    i18n.switch("en")
    assert i18n.t("testplug.title") == "Title"


def test_register_locale_cannot_override_builtin(tmp_path, _restore_lang) -> None:
    i18n.switch("en")
    i18n.register_locale({"en": {"menu": {"exit": "HACKED"}}})
    assert i18n.t("menu.exit") == "Exit"
