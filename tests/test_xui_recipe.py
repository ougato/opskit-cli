from __future__ import annotations

from software.registry import all_recipes, get
from xui.utils import redact_state, write_secret_json


def test_xui_recipes_are_registered() -> None:
    keys = {cls.key for cls in all_recipes()}
    assert "xui" in keys
    assert "xui_server" in keys
    parent = get("xui")
    child = get("xui_server")
    assert parent is not None
    assert child is not None
    assert parent.has_submenu is True
    assert child.hidden is True
    assert child.has_diagnose is True
    assert child.has_manage is True


def test_xui_parent_exposes_server_submenu() -> None:
    recipe_cls = get("xui")
    assert recipe_cls is not None
    items = recipe_cls().submenu_items()
    assert items == [{"key": "xui_server", "label_key": "software.xui_server"}]


def test_xui_state_redacts_sensitive_fields_and_uses_0600(tmp_path) -> None:
    target = tmp_path / "server.json"
    state: dict[str, object] = {
        "panel_password": "panel-secret",
        "vless": {"private_key": "reality-private", "public_key": "pub"},
        "trojan": {"password": "trojan-secret"},
    }
    write_secret_json(target, state)
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    redacted = redact_state(state)
    assert isinstance(redacted, dict)
    assert redacted["panel_password"] == "<redacted>"
    vless = redacted["vless"]
    assert isinstance(vless, dict)
    assert vless["private_key"] == "<redacted>"
    assert vless["public_key"] == "pub"
