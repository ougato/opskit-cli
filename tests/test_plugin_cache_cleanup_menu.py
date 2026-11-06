import pytest

from core import i18n, theme
from core.theme import get_icon
from rich.cells import cell_len
from plugin import cache_cleanup_menu as menu


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("approved", [False, True])
@pytest.mark.parametrize("selection,kind", [("1", "runtime"), ("2", "fetch"), ("3", "prepare")])
def test_cache_menu_excludes_references_and_requires_confirmation(monkeypatch, language, approved, selection, kind):
    theme.init()
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
            assert kwargs["info_lines"] == i18n.t("plugin_native.cache_cleanup.hint").splitlines()
            for choice, name in zip(choices, ("runtime", "fetch", "prepare")):
                icon, label = choice["label"].split(" ", 1)
                assert icon == get_icon(name + "_cache") and cell_len(icon) == 2
                assert label == i18n.t("plugin_native.cache_cleanup." + name)
                if language == "zh":
                    assert len(label) == 4 and all("\u4e00" <= c <= "\u9fff" for c in label)
            return selection
        assert choices == [{"key": "1", "label": f"{get_icon('cache_cleanup')} {item['object']}"}]
        return "1"
    def confirm(breadcrumb, title, **kwargs):
        assert title == i18n.t("confirm_ui.cache")
        assert kwargs["info_lines"][0] == i18n.t("confirm_ui.cache_detail")
        assert item["object"] in kwargs["info_lines"][1]
        assert item["record_sha256"] in kwargs["info_lines"][1]
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
    theme.init()
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
