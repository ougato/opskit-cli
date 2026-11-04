import pytest

from core import i18n
from core.theme import get_icon
from plugin import native_profile_menu as menu


@pytest.fixture(params=["zh", "en"])
def language(request, monkeypatch):
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(request.param)))
    monkeypatch.setattr(menu, "pause", lambda: None)


@pytest.mark.parametrize("approved", [False, True])
@pytest.mark.parametrize("phase", ["created", "removed"])
def test_confirmed_profile_requires_explicit_confirmation(language, monkeypatch, approved, phase):
    item = {"transaction": "a" * 32, "profile": "OpsKit.Plugin." + "a" * 32,
            "record_sha256": "b" * 64, "valid": True, "phase": phase}
    monkeypatch.setattr(menu.recovery, "inspect", lambda: [item])
    recovered, messages = [], []

    def select(breadcrumb, title, choices, **kwargs):
        assert kwargs["back_label"]
        assert breadcrumb[-1] == i18n.t("plugin_native.profile_recovery.menu")
        assert title == i18n.t("plugin_native.profile_recovery.select")
        assert choices == [{"key": "1", "label": f"{get_icon('sandbox_cleanup')} {item['profile']}"}]
        return "1"

    def confirm(breadcrumb, title, **kwargs):
        key = "confirm_removed" if phase == "removed" else "confirm"
        assert title == i18n.t("plugin_native.profile_recovery." + key)
        assert all(item[key] in kwargs["info_lines"][0]
                   for key in ("transaction", "profile", "record_sha256"))
        return approved

    monkeypatch.setattr(menu, "select", select)
    monkeypatch.setattr(menu, "confirm", confirm)
    monkeypatch.setattr(menu.recovery, "recover", lambda *args: recovered.append(args))
    monkeypatch.setattr(menu, "print_success", messages.append)
    menu.entry()
    assert recovered == ([(item["transaction"], item["record_sha256"])] if approved else [])
    result_key = "completed_removed" if phase == "removed" else "completed"
    assert messages == ([i18n.t("plugin_native.profile_recovery." + result_key)] if approved else [])


def test_uncertain_profiles_are_never_offered(language, monkeypatch):
    monkeypatch.setattr(menu.recovery, "inspect", lambda: [
        {"valid": False}, {"valid": True, "phase": "creating"}])
    monkeypatch.setattr(menu, "select", lambda *args: pytest.fail("uncertain profile offered"))
    monkeypatch.setattr(menu.recovery, "recover", lambda *args: pytest.fail("uncertain profile removed"))
    messages = []
    monkeypatch.setattr(menu, "print_info", messages.append)
    menu.entry()
    assert messages == [i18n.t("plugin_native.profile_recovery.uncertain"),
                        i18n.t("plugin_native.profile_recovery.empty")]


def test_busy_or_changed_profile_reports_localized_failure(language, monkeypatch):
    def fail():
        raise RuntimeError("internal details should not leak")
    monkeypatch.setattr(menu.recovery, "inspect", fail)
    messages = []
    monkeypatch.setattr(menu, "print_error", messages.append)
    menu.entry()
    assert messages == [i18n.t("plugin_native.lifecycle.profile_recovery_failed")]
