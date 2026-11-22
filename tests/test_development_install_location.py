from contextlib import nullcontext
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.mark.parametrize("name", ["golang", "java", "nodejs", "python"])
def test_cli_preflights_selected_development_tool_location(name, tmp_path, monkeypatch):
    from core import platform
    from main import app
    from software import actions, registry
    from software._shared import mutation
    from software.install_location import record_location

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(mutation, "versioned_mutation_scope", nullcontext)
    selected = tmp_path / "custom tool directory"
    checked = []
    installed = []

    def check_space(required, path):
        checked.append(path)
        return True

    def execute(instance, version):
        assert instance.disk_check_path() == selected
        snapshot = record_location(name, {"active_version": version})
        if name == "python":
            from software.recipes.python.common import save_snapshot
            save_snapshot(snapshot)
        else:
            instance._save_snapshot(snapshot)
        installed.append(version)
        return actions.ActionResult(ok=True, version=version)

    monkeypatch.setattr(platform, "check_disk_space", check_space)
    monkeypatch.setattr(actions, "execute_install", execute)
    result = CliRunner().invoke(
        app, ["software", "install", name, "--version", "1.2.3",
              "--install-location", str(selected)],
    )
    assert result.exit_code == 0, result.output
    assert checked[0] == selected
    assert installed == ["1.2.3"]
    assert registry.get(name)().disk_check_path() == selected

    result = CliRunner().invoke(app, ["--output", "json", "software", "capabilities", name])
    assert result.exit_code == 0, result.output
    capabilities = json.loads(result.stdout)["data"]
    assert capabilities["custom_install_location"] is True
    assert capabilities["install_location"] == str(selected)


def test_menu_prefills_editable_default_and_preflights_input(tmp_path, monkeypatch):
    from core import progress
    from software import menu
    from software._shared import mutation
    from software.actions import ActionResult
    from software.recipes.golang.recipe import GoRecipe

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(mutation, "versioned_mutation_scope", nullcontext)
    monkeypatch.setattr(GoRecipe, "versions", lambda self: ["1.25.8"])
    monkeypatch.setattr(progress, "spinner", lambda *args: nullcontext())
    monkeypatch.setattr(menu, "paged_select", lambda **kwargs: "1.25.8")
    selected = tmp_path / "edited go folder"
    recipe = GoRecipe()
    default = str(recipe.disk_check_path())

    def edit_path(**kwargs):
        assert kwargs["initial"] == kwargs["default"] == default
        return str(selected)

    def preflight(instance):
        assert instance.disk_check_path() == selected
        return True

    def execute(instance, version):
        assert instance.disk_check_path() == selected
        return ActionResult(ok=True, version=version)

    monkeypatch.setattr(menu, "text_input", edit_path)
    monkeypatch.setattr(menu, "_install_disk_check", preflight)
    monkeypatch.setattr(menu, "execute_install", execute)
    monkeypatch.setattr(menu, "clear_screen", lambda: None)
    monkeypatch.setattr(menu, "pause", lambda: None)
    menu._do_install_version_picker(["OpsKit"], GoRecipe, recipe)
