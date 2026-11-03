import pytest

from core import i18n
from plugin import tool_config_menu as menu


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("approved", [False, True])
def test_saving_tool_requires_review_without_authorization(monkeypatch, language, approved):
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    monkeypatch.setattr(menu.tools, "creators", lambda: {})
    monkeypatch.setattr(menu, "select", lambda *args: "1")
    answers = iter(["creator-3-8", "C:/reviewed/Creator.exe"])
    monkeypatch.setattr(menu, "text_input", lambda *args: next(answers))
    def confirm(breadcrumb, title, **kwargs):
        assert title == i18n.t("plugin_native.tool_config.confirm")
        assert "creator-3-8" in kwargs["info_lines"][0]
        return approved
    monkeypatch.setattr(menu, "confirm", confirm)
    monkeypatch.setattr(menu, "pause", lambda: None)
    saved, messages = [], []
    monkeypatch.setattr(menu.tools, "configure_creator", lambda *args: saved.append(args))
    monkeypatch.setattr(menu, "print_success", messages.append)
    menu.entry()
    assert saved == ([("creator-3-8", "C:/reviewed/Creator.exe")] if approved else [])
    assert messages == ([i18n.t("plugin_native.tool_config.saved")] if approved else [])


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("approved", [False, True])
def test_removal_selects_existing_tool_and_reviews_exact_path(monkeypatch, language, approved):
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    values = {"creator-3-8": "C:/reviewed/Creator.exe"}
    monkeypatch.setattr(menu.tools, "creators", lambda: values)
    calls, removed = [], []
    def select(breadcrumb, title, choices):
        calls.append(title)
        if len(calls) == 1:
            return "2"
        assert choices == [{"key": "1", "label": "creator-3-8"}]
        return "1"
    def confirm(breadcrumb, title, **kwargs):
        assert title == i18n.t("plugin_native.tool_config.confirm_remove")
        assert values["creator-3-8"] in kwargs["info_lines"][0]
        return approved
    monkeypatch.setattr(menu, "select", select)
    monkeypatch.setattr(menu, "confirm", confirm)
    monkeypatch.setattr(menu, "text_input", lambda *args: pytest.fail("removal asked for a freeform name"))
    monkeypatch.setattr(menu, "print_info", lambda *args: None)
    monkeypatch.setattr(menu, "print_success", lambda *args: None)
    monkeypatch.setattr(menu, "pause", lambda: None)
    monkeypatch.setattr(menu.tools, "remove_creator", removed.append)
    menu.entry()
    assert removed == (["creator-3-8"] if approved else [])
