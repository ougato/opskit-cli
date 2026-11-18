import pytest

from core import i18n
from plugin import update_recovery_menu as menu


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("confirmed", [False, True])
def test_menu_displays_exact_record_before_optional_rollback(monkeypatch, language, confirmed):
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    item = {"transaction": "a" * 32, "plugin": "demo", "directory": "demo",
            "record_sha256": "b" * 64, "valid": True}
    monkeypatch.setattr(menu.recovery, "inspect", lambda: [item])
    titles, details, recovered, success = [], [], [], []
    def select(breadcrumb, title, choices, **kwargs):
        assert kwargs["back_label"]
        titles.append(title)
        assert breadcrumb[-1] == i18n.t("plugin_native.update_recovery.menu")
        assert item["transaction"] in choices[0]["label"]
        return "1"
    def confirm(breadcrumb, title, **kwargs):
        titles.append(title)
        details.extend(kwargs["info_lines"])
        return confirmed
    monkeypatch.setattr(menu, "select", select)
    monkeypatch.setattr(menu, "confirm", confirm)
    monkeypatch.setattr(menu, "pause", lambda: None)
    monkeypatch.setattr(menu, "print_success", success.append)
    monkeypatch.setattr(menu.recovery, "recover", lambda *args: recovered.append(args))
    menu.entry()
    assert recovered == ([(item["transaction"], item["record_sha256"])] if confirmed else [])
    assert item["record_sha256"] in details[0] and "demo" in details[0]
    assert all("plugin_native." not in title for title in titles)
    assert success == ([i18n.t("plugin_native.update_recovery.completed")] if confirmed else [])


@pytest.mark.parametrize("language", ["zh", "en"])
def test_invalid_records_are_retained_and_not_offered_for_rollback(monkeypatch, language):
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    monkeypatch.setattr(menu.recovery, "inspect", lambda: [{"transaction": "unknown", "valid": False}])
    monkeypatch.setattr(menu, "select", lambda *args: pytest.fail("invalid record offered for recovery"))
    monkeypatch.setattr(menu.recovery, "recover", lambda *args: pytest.fail("invalid record modified"))
    monkeypatch.setattr(menu, "pause", lambda: None)
    messages = []
    monkeypatch.setattr(menu, "print_info", messages.append)
    menu.entry()
    assert messages == [i18n.t("plugin_native.update_recovery.invalid"),
                        i18n.t("plugin_native.update_recovery.empty")]
