"""Framework menu boundary, localized four-character labels and theme icons."""
import ast
from pathlib import Path

import pytest
from rich.cells import cell_len

from core import i18n, theme
from plugin import menu


@pytest.mark.parametrize("language", ["zh", "en"])
def test_management_and_maintenance_structure(monkeypatch, language):
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    theme.init()
    captured = []
    keys = iter(["5", None, None])
    def select(**kwargs):
        captured.append(kwargs)
        return next(keys)
    monkeypatch.setattr(menu, "select", select)
    menu._manage()
    assert len(captured[0]["choices"]) == 5
    assert len(captured[1]["choices"]) == 5
    assert captured[1]["breadcrumb"][-1] == i18n.t("plugin_native.maintenance")
    for screen in captured:
        assert screen["back_label"]
        if language == "zh":
            assert screen["back_label"] == theme.get_icon("back") + " 返回"
        for item in screen["choices"]:
            icon, label = item["label"].split(" ", 1)
            assert icon != "•" and cell_len(icon) == 2
            assert "Creator" not in label and "Cocos" not in label
            if language == "zh":
                assert len(label) == 4


@pytest.mark.parametrize("number,module", [
    ("1", "plugin.update_recovery_menu"), ("2", "plugin.native_profile_menu"),
    ("3", "plugin.workspace_recovery_menu"), ("4", "plugin.cache_cleanup_menu"), ("5", "plugin.disk_menu"),
])
def test_maintenance_routes(number, module, monkeypatch):
    import importlib
    calls = []
    monkeypatch.setattr(importlib.import_module(module), "entry", lambda: calls.append(module))
    keys = iter([number, None])
    monkeypatch.setattr(menu, "select", lambda **kwargs: next(keys))
    menu._maintenance()
    assert calls == [module]


def test_old_sdk_versions_are_rejected_before_plugin_code(tmp_path):
    from core.plugin import load_manifest
    for version in (1, 2):
        (tmp_path / "plugin.yaml").write_text(
            f"name: old\nversion: 1.0.0\napi_version: {version}\nkind: python\nentry: old\n")
        assert load_manifest(tmp_path) is None


@pytest.mark.parametrize("action", ["trust", "private"])
def test_yes_does_not_approve_security_confirmation(monkeypatch, action):
    from core import plugin, prompt
    from core.cli import non_interactive
    monkeypatch.setattr(prompt, "_auto_yes", True)
    monkeypatch.setattr(prompt.os, "system", lambda *_: 0)
    monkeypatch.setattr(prompt, "_render_header", lambda *_: None)
    monkeypatch.setattr(prompt, "_render_options", lambda *_, **__: None)
    monkeypatch.setattr(prompt, "_read_key", lambda: "n")
    grants = []
    monkeypatch.setattr(menu.commands, "grant_trust", lambda *args, **kwargs: grants.append((args, kwargs)))
    monkeypatch.setattr(menu.commands, "compute_fingerprint", lambda root: "a" * 64)
    monkeypatch.setattr(menu.commands, "trusted_record", lambda name: None)
    token = non_interactive.set(False)
    try:
        if action == "trust":
            manifest = plugin.PluginManifest(name="demo", version="1.0.0", api_version=4,
                                             kind="python", entry="demo")
            assert not menu.confirm_trust(manifest)
        else:
            assert not menu._fetch_interaction("git@example.com:team/repo", "fetch_private_denied")
        assert not grants
    finally:
        non_interactive.reset(token)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_trust_shows_source_and_fingerprint_and_rejects_changed_content(monkeypatch, tmp_path, language):
    from core import plugin
    root = tmp_path / "plugin"
    root.mkdir()
    content = root / "plugin.py"
    content.write_text("original", encoding="utf-8")
    manifest = plugin.PluginManifest(name="demo", version="1.0.0", api_version=4,
                                     kind="python", entry="demo", root=root)
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    original = menu.commands.compute_fingerprint(root)
    def confirm(**kwargs):
        assert kwargs["require_explicit"] is True
        assert any("git@example.com:team/repo" in line for line in kwargs["info_lines"])
        assert any("develop" in line for line in kwargs["info_lines"])
        assert any(original in line for line in kwargs["info_lines"])
        content.write_text("changed", encoding="utf-8")
        return True
    monkeypatch.setattr(menu, "confirm", confirm)
    with pytest.raises(ValueError):
        menu.confirm_trust(manifest, "git@example.com:team/repo", "develop")
    assert menu.commands.trust_status(manifest) != menu.commands.TRUST_OK


def test_content_changed_during_trust_write_is_not_left_trusted(monkeypatch, tmp_path):
    from core import plugin
    root = tmp_path / "plugin"
    root.mkdir()
    content = root / "plugin.py"
    content.write_text("original", encoding="utf-8")
    manifest = plugin.PluginManifest(name="demo", version="1.0.0", api_version=4,
                                     kind="python", entry="demo", root=root)
    fingerprint = menu.commands.compute_fingerprint(root)
    real_grant = menu.commands.grant
    def grant_then_change(*args, **kwargs):
        real_grant(*args, **kwargs)
        content.write_text("changed", encoding="utf-8")
    monkeypatch.setattr(menu.commands, "grant", grant_then_change)
    with pytest.raises(ValueError):
        menu.commands.grant_trust(manifest, expected_fingerprint=fingerprint)
    assert menu.commands.trust_status(manifest) != menu.commands.TRUST_OK


def test_downgrade_uses_same_explicit_trust_flow(monkeypatch):
    from core import plugin
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang("zh")))
    old = plugin.PluginManifest(name="demo", version="2.0.0", api_version=4,
                                kind="python", entry="demo")
    new = plugin.PluginManifest(name="demo", version="1.0.0", api_version=4,
                                kind="python", entry="demo")
    monkeypatch.setattr(menu, "_pick", lambda *args, **kwargs: old)
    monkeypatch.setattr(menu.commands, "trust_status", lambda manifest: menu.commands.TRUST_OK)
    monkeypatch.setattr(menu.commands, "update", lambda *args, **kwargs: (True, "downgrade"))
    monkeypatch.setattr(menu.commands, "manifests", lambda: [new])
    monkeypatch.setattr(menu, "_update_fetch_options", lambda manifest: {})
    monkeypatch.setattr(menu, "clear_screen", lambda: None)
    monkeypatch.setattr(menu, "print_header", lambda *args: None)
    monkeypatch.setattr(menu, "print_error", lambda *args: None)
    monkeypatch.setattr(menu, "pause", lambda: None)
    monkeypatch.setattr(menu.commands, "grant_trust", lambda *args, **kwargs: pytest.fail("unexpected grant"))
    prompts = []
    monkeypatch.setattr(menu, "confirm_trust", lambda manifest, **kwargs: prompts.append(kwargs) or False)
    menu._update()
    assert len(prompts) == 1 and "2.0.0" in prompts[0]["prompt_override"]
    assert "1.0.0" in prompts[0]["prompt_override"]


def test_untrusted_plugin_menu_labels_are_plain_text():
    from core import plugin
    from rich.text import Text
    manifest = plugin.PluginManifest(name="demo", version="1.0.0", api_version=4,
                                     kind="python", entry="demo", icon="[red]X",
                                     label={"en": "[bold]Plugin"})
    assert Text.from_markup(menu._manifest_item(manifest)).plain == "[red]X [bold]Plugin"


def test_active_framework_has_no_business_implementation():
    root = Path(__file__).resolve().parents[1]
    for folder in ("core", "plugin"):
        for path in (root / folder).glob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "creator" not in text.lower() and "cocos" not in text.lower(), path
    for path in (root / "plugin").glob("*menu.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "select":
                assert any(kw.arg == "back_label" for kw in node.keywords), (path, node.lineno)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_permission_prompt_uses_icons_and_back_cancels(monkeypatch, language):
    from core import plugin_capabilities as capabilities, prompt
    from core.cli import non_interactive
    from core.native_sandbox import CapabilityRequest
    monkeypatch.setattr(i18n, "_current", i18n._flatten(i18n._load_lang(language)))
    theme.init()
    monkeypatch.setattr(capabilities, "_interactive_input", lambda: True)
    captured = []
    monkeypatch.setattr(prompt, "select", lambda *args, **kwargs: captured.append((args, kwargs)))
    token = non_interactive.set(False)
    try:
        with pytest.raises(prompt.UserCancel):
            capabilities.prompt_grant(CapabilityRequest("demo", "a" * 64, "directory.read", "E:/project"))
    finally:
        non_interactive.reset(token)
    args, kwargs = captured[0]
    assert [item["key"] for item in args[2]] == ["1", "2"]
    labels = [item["label"] for item in args[2]]
    assert kwargs["back_label"] == theme.get_icon("back") + " " + ("返回" if language == "zh" else "Back")
    for label in labels:
        icon, text = label.split(" ", 1)
        assert cell_len(icon) == 2 and icon != "•"
        if language == "zh":
            assert len(text) == 4
    assert i18n.t("plugin_native.description", operation=i18n.t("plugin_native.operations.directory.read"),
                  resource="E:/project", fingerprint="a" * 64) == kwargs["description"]
