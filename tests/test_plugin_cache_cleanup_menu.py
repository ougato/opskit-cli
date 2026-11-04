import pytest

from core import i18n
from plugin import cache_cleanup_menu as menu


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("approved", [False, True])
@pytest.mark.parametrize("selection,kind", [("1", "runtime"), ("3", "prepare")])
def test_cache_menu_excludes_references_and_requires_confirmation(monkeypatch, language, approved, selection, kind):
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    item = {"object": "a" * 32, "record_sha256": "b" * 64, "valid": True, "referenced": False}
    monkeypatch.setattr(menu.cleanup, "inspect", lambda kind: [
        {"valid": False}, {"valid": True, "referenced": True}, item])
    calls, recovered, messages, notices = [], [], [], []
    def select(breadcrumb, title, choices, **kwargs):
        assert kwargs["back_label"]
        calls.append(title)
        if len(calls) == 1:
            assert title == i18n.t("plugin_native.cache_cleanup.catalog")
            assert choices[-1]["label"] == i18n.t("plugin_native.cache_cleanup.prepare")
            return selection
        assert choices == [{"key": "1", "label": item["object"]}]
        return "1"
    def confirm(breadcrumb, title, **kwargs):
        assert title == i18n.t("plugin_native.cache_cleanup.confirm")
        assert item["object"] in kwargs["info_lines"][0]
        assert item["record_sha256"] in kwargs["info_lines"][0]
        return approved
    monkeypatch.setattr(menu, "select", select)
    monkeypatch.setattr(menu, "confirm", confirm)
    monkeypatch.setattr(menu, "pause", lambda: None)
    monkeypatch.setattr(menu, "print_success", messages.append)
    monkeypatch.setattr(menu, "print_info", notices.append)
    monkeypatch.setattr(menu.cleanup, "collect", lambda *args: recovered.append(args))
    menu.entry()
    assert recovered == ([(kind, item["object"], item["record_sha256"])] if approved else [])
    assert messages == ([i18n.t("plugin_native.cache_cleanup.completed")] if approved else [])
    assert notices == [i18n.t("plugin_native.cache_cleanup.uncertain")]


@pytest.mark.parametrize("language", ["zh", "en"])
def test_cache_failure_uses_localized_error(monkeypatch, language):
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    monkeypatch.setattr(menu, "select", lambda *args, **kwargs: "2")
    def fail(kind):
        assert kind == "fetch"
        raise RuntimeError("internal path and secret")
    monkeypatch.setattr(menu.cleanup, "inspect", fail)
    monkeypatch.setattr(menu, "pause", lambda: None)
    messages = []
    monkeypatch.setattr(menu, "print_error", messages.append)
    menu.entry()
    assert messages == [i18n.t("plugin_native.lifecycle.cache_cleanup_failed")]
