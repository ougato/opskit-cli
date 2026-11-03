import pytest

from core.native_sandbox import CapabilityRequest, GrantChoice
from core.plugin_capabilities import CapabilityAuthority


@pytest.fixture(autouse=True)
def language(monkeypatch):
    from core import i18n
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang("zh")))


@pytest.mark.parametrize("confirmed", [True, False])
def test_permission_menu_inspects_and_revokes_exact_grant(tmp_path, monkeypatch, confirmed):
    from plugin import capability_menu as menu
    authority = CapabilityAuthority(path=tmp_path / "grants.yaml")
    request = CapabilityRequest("demo", "a" * 64, "directory.read", str(tmp_path))
    authority.require(request, check_identity=lambda *a: True, prompt=lambda _: GrantChoice.ALWAYS)
    monkeypatch.setattr(menu, "CapabilityAuthority", lambda: authority)
    selections = iter(["1", None])
    monkeypatch.setattr(menu, "select", lambda *a, **k: next(selections))
    details = []
    def confirm(*args, **kwargs):
        details.extend(kwargs["info_lines"])
        return confirmed
    monkeypatch.setattr(menu, "confirm", confirm)
    monkeypatch.setattr(menu, "pause", lambda: None)
    menu.entry()
    assert request.fingerprint in details[0]
    assert request.resource in details[0]
    assert authority.closed
    assert CapabilityAuthority(path=authority.path).list_grants() == ([] if confirmed else [request])


def test_permission_menu_corrupt_store_does_not_modify_evidence(tmp_path, monkeypatch):
    from plugin import capability_menu as menu
    path = tmp_path / "grants.yaml"
    path.write_text("invalid: store")
    original = path.read_bytes()
    authority = CapabilityAuthority(path=path)
    monkeypatch.setattr(menu, "CapabilityAuthority", lambda: authority)
    messages = []
    monkeypatch.setattr(menu, "print_error", messages.append)
    monkeypatch.setattr(menu, "pause", lambda: None)
    menu.entry()
    assert messages and path.read_bytes() == original
    assert authority.closed
