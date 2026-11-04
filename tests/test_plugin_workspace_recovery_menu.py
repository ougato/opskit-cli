import pytest

from core import i18n
from plugin import workspace_recovery_menu as menu


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("approved", [False, True])
def test_menu_requires_review_and_confirmation(monkeypatch, language, approved):
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    item = {"transaction": "a" * 32, "directory": "owned-temporary-directory",
            "record_sha256": "b" * 64, "valid": True}
    monkeypatch.setattr(menu.recovery, "inspect", lambda: [item])
    recovered, messages = [], []
    def select(breadcrumb, title, choices, **kwargs):
        assert kwargs["back_label"]
        assert breadcrumb[-1] == i18n.t("plugin_native.workspace_recovery.menu")
        assert title == i18n.t("plugin_native.workspace_recovery.select")
        assert choices[0]["label"] == item["directory"]
        return "1"
    def confirm(breadcrumb, title, **kwargs):
        assert title == i18n.t("plugin_native.workspace_recovery.confirm")
        assert all(item[key] in kwargs["info_lines"][0]
                   for key in ("transaction", "directory", "record_sha256"))
        return approved
    monkeypatch.setattr(menu, "select", select)
    monkeypatch.setattr(menu, "confirm", confirm)
    monkeypatch.setattr(menu, "pause", lambda: None)
    monkeypatch.setattr(menu, "print_success", messages.append)
    monkeypatch.setattr(menu.recovery, "recover", lambda *args: recovered.append(args))
    menu.entry()
    assert recovered == ([(item["transaction"], item["record_sha256"])] if approved else [])
    assert messages == ([i18n.t("plugin_native.workspace_recovery.completed")] if approved else [])


@pytest.mark.parametrize("language", ["zh", "en"])
def test_invalid_records_are_retained(monkeypatch, language):
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    monkeypatch.setattr(menu.recovery, "inspect", lambda: [{"valid": False}])
    monkeypatch.setattr(menu, "select", lambda *args: pytest.fail("invalid workspace offered"))
    monkeypatch.setattr(menu, "pause", lambda: None)
    messages = []
    monkeypatch.setattr(menu, "print_info", messages.append)
    menu.entry()
    assert messages == [i18n.t("plugin_native.workspace_recovery.uncertain"),
                        i18n.t("plugin_native.workspace_recovery.empty")]
