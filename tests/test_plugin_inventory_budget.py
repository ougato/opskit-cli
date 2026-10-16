"""Inventory limits fail before parsing and cannot return partial identities."""
import json

import pytest
from typer.testing import CliRunner

from core import plugin


def _limited_inventory_session(connection, buffer, length, requested):
    from core.plugin_inspection import _inspection_session
    plugin.MAX_INVENTORY_ENTRIES = 1
    _inspection_session(connection, buffer, length, requested)


def test_entry_limit_counts_ignored_and_non_directory_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(tmp_path))
    monkeypatch.setattr(plugin, "MAX_INVENTORY_ENTRIES", 4)
    for index in range(5):
        (tmp_path / (".hidden" + str(index))).touch()
    monkeypatch.setattr(plugin, "load_manifest", lambda *args: pytest.fail("parsed incomplete inventory"))
    with pytest.raises(plugin.PluginInventoryError, match="entry limit"):
        plugin._list_manifests(tmp_path)


def test_manifest_limit_applies_before_any_manifest_is_parsed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(tmp_path))
    monkeypatch.setattr(plugin, "MAX_MANIFESTS", 2)
    for name in ("a", "b", "c"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "plugin.yaml").write_text("not valid")
    monkeypatch.setattr(plugin, "load_manifest", lambda *args: pytest.fail("parsed partial inventory"))
    with pytest.raises(plugin.PluginInventoryError, match="manifest limit"):
        plugin._list_manifests(tmp_path)


def test_inventory_preserves_sorted_directory_order(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(tmp_path))
    for name in ("z", "a", "b"):
        root = tmp_path / name
        root.mkdir()
        (root / "plugin.yaml").write_text(f"name: {name}\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: demo\n")
    assert [manifest.name for manifest in plugin.list_manifests()] == ["a", "b", "z"]


def test_scan_failure_is_explicit_and_does_not_echo_os_error(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(tmp_path))
    def denied(path):
        raise PermissionError("synthetic-sensitive-path")
    monkeypatch.setattr(plugin.os, "scandir", denied)
    with pytest.raises(plugin.PluginInventoryError, match="^plugin inventory scan failed$"):
        plugin._list_manifests(tmp_path)


@pytest.mark.parametrize("arguments", [["plugin", "list"], ["plugin", "run", "demo", "--", "--help"]])
def test_cli_inventory_overflow_is_not_successful_empty_list(tmp_path, monkeypatch, arguments):
    import main
    from core.cli import configure
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "plugins"
    root.mkdir()
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(root))
    from core import plugin_inspection
    monkeypatch.setattr(plugin_inspection, "_inspection_session", _limited_inventory_session)
    (root / "a").touch()
    (root / "b").touch()
    try:
        result = CliRunner().invoke(main.app, ["--output", "json", *arguments])
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["error"]["message"] == "plugin inventory entry limit exceeded"
    finally:
        configure("text", False)


def test_install_rolls_back_its_clone_on_incomplete_inventory(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    from plugin import commands
    root = tmp_path / "plugins"
    root.mkdir()
    existing = root / "existing"
    existing.write_text("preserve")
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(root))
    from core import plugin_inspection
    monkeypatch.setattr(plugin_inspection, "_inspection_session", _limited_inventory_session)
    def clone(command, **kwargs):
        dest = Path(command[-1])
        dest.mkdir()
        (dest / "plugin.yaml").write_text("name: demo\nversion: 1.0.0\napi_version: 2\nkind: python\nentry: demo\n")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(commands, "run", clone)
    monkeypatch.setattr(commands, "grant", lambda *args: pytest.fail("incomplete inventory granted trust"))
    manifest, error = commands.install("https://example.invalid/repo.git", alias="demo")
    assert manifest is None
    assert error == "plugin inventory scan failed"
    assert not (root / "demo").exists()
    assert existing.read_text() == "preserve"


def test_menu_inventory_failure_returns_to_platform(tmp_path, monkeypatch):
    from plugin import menu
    seen = []
    def fail():
        raise plugin.PluginInventoryError("plugin inventory entry limit exceeded")
    monkeypatch.setattr(menu.commands, "loaded_plugins", fail)
    monkeypatch.setattr(menu, "print_error", seen.append)
    monkeypatch.setattr(menu, "pause", lambda: None)
    menu.entry()
    assert seen == ["plugin inventory entry limit exceeded"]
