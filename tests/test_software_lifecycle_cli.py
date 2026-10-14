import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from core.cli import configure


@pytest.fixture
def software_environment(monkeypatch):
    import main
    from software import registry, lifecycle_cli, resolver
    state = {"installed": ["1.0", "2.0"], "active": "1.0", "events": []}
    class Recipe:
        key = "synthetic"
        description = "Synthetic software"
        platforms = ["windows", "linux", "darwin"]
        has_submenu = False
        has_wizard = False
        has_install_version_selection = True
        has_switch = True
        has_upgrade = True
        requires_root = False
        dependencies = []
        def detect(self):
            return state["active"]
        def installed_versions(self):
            return list(state["installed"])
        def install(self, version):
            print("installer progress")
            state["events"].append(("install", version))
            state["installed"].append(version)
            state["active"] = version
        def switch(self, version):
            print("switch progress")
            state["events"].append(("switch", version))
            state["active"] = version
        def upgrade(self, version):
            print("upgrade progress")
            state["events"].append(("upgrade", version))
            state["installed"].append(version)
            state["active"] = version
        def uninstall(self, version):
            print("uninstall progress")
            state["events"].append(("uninstall", version))
            if version is None:
                state["installed"] = []
                state["active"] = None
            else:
                state["installed"].remove(version)
    monkeypatch.setattr(registry, "get", lambda key: Recipe if key == Recipe.key else None)
    monkeypatch.setattr(main, "_boot", lambda: pytest.fail("explicit lifecycle CLI must not boot menus or updates"))
    def deps(*args):
        print("dependency progress")
        state["events"].append(("deps", None))
    monkeypatch.setattr(resolver, "resolve_deps", deps)
    monkeypatch.setattr(lifecycle_cli, "_check_disk_or_exit", lambda: None)
    yield SimpleNamespace(state=state, recipe=Recipe)
    configure("text", False)


def invoke(*args):
    import main
    return CliRunner().invoke(main.app, ["--output", "json", *args])


@pytest.mark.parametrize("command,options,action", [
    ("install", ["--version", "3.0"], "install"),
    ("upgrade", ["--version", "3.0"], "upgrade"),
    ("switch", ["--version", "2.0"], "switch"),
    ("uninstall", ["--version", "2.0"], "uninstall"),
    ("uninstall", ["--all"], "uninstall"),
])
def test_lifecycle_single_json_and_shared_actions(software_environment, command, options, action):
    result = invoke("--yes", "software", command, "synthetic", *options)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] and payload["data"]["action"] == action
    assert payload["data"]["software"] == "synthetic"
    assert "progress" not in result.stdout and "progress" in result.stderr
    assert any(event[0] == action for event in software_environment.state["events"])


def test_upgrade_switches_installed_version(software_environment):
    result = invoke("software", "upgrade", "synthetic", "--version", "2.0")
    assert result.exit_code == 0
    assert json.loads(result.stdout)["data"]["switched"]
    assert ("switch", "2.0") in software_environment.state["events"]
    assert not any(event[0] == "upgrade" for event in software_environment.state["events"])


@pytest.mark.parametrize("command,options", [
    ("install", []), ("upgrade", []), ("uninstall", []),
    ("install", ["--version", "../outside"]),
    ("uninstall", ["--version", "1.0", "--all"]),
    ("install", ["--version", "3.0", "--token", "synthetic-secret"]),
])
def test_invalid_request_has_no_business_side_effect(software_environment, command, options):
    result = invoke("software", command, "synthetic", *options)
    assert result.exit_code == 2, result.output
    assert not json.loads(result.stdout)["ok"]
    assert not software_environment.state["events"]
    assert "synthetic-secret" not in result.output


def test_uninstall_requires_explicit_confirmation(software_environment):
    result = invoke("software", "uninstall", "synthetic", "--all")
    assert result.exit_code == 2
    assert not json.loads(result.stdout)["ok"]
    assert not software_environment.state["events"]


def test_install_failure_is_single_error_without_exception_secret(software_environment, monkeypatch):
    def fail_install(self, version):
        print("installer progress")
        raise RuntimeError("token=synthetic-secret")
    monkeypatch.setattr(software_environment.recipe, "install", fail_install)
    result = invoke("software", "install", "synthetic", "--version", "3.0")
    assert result.exit_code == 1
    assert not json.loads(result.stdout)["ok"]
    assert "synthetic-secret" not in result.output


def test_hidden_installer_prompt_is_one_usage_error(software_environment, monkeypatch):
    def install(self, version):
        from core.prompt import text_input
        text_input([], "unexpected input")
    monkeypatch.setattr(software_environment.recipe, "install", install)
    result = invoke("software", "install", "synthetic", "--version", "3.0")
    assert result.exit_code == 2, result.output
    assert not json.loads(result.stdout)["ok"]


@pytest.mark.parametrize("command", ["install", "uninstall", "upgrade"])
def test_missing_target_cannot_boot_menu(software_environment, command):
    result = invoke("software", command)
    assert result.exit_code == 2
    assert not json.loads(result.stdout)["ok"]


def test_manage_is_interactive_only(software_environment):
    result = invoke("software", "manage", "synthetic")
    assert result.exit_code == 2
    assert not json.loads(result.stdout)["ok"]


def test_install_without_detected_result_cannot_report_success(software_environment, monkeypatch):
    monkeypatch.setattr(software_environment.recipe, "detect", lambda _: None)
    result = invoke("software", "install", "synthetic", "--version", "3.0")
    assert result.exit_code == 1
    assert not json.loads(result.stdout)["ok"]


def test_official_recipe_parameter_matrix_rejects_invalid_operations(monkeypatch):
    import main
    from software.registry import all_recipes
    from software import lifecycle_cli
    def forbidden(*args, **kwargs):
        pytest.fail("invalid official request caused a business side effect")
    monkeypatch.setattr(main, "_boot", forbidden)
    monkeypatch.setattr(lifecycle_cli, "_check_disk_or_exit", forbidden)
    monkeypatch.setattr(lifecycle_cli, "_resolve_deps_or_exit", forbidden)
    instance = SimpleNamespace(detect=forbidden, installed_versions=forbidden, switch=forbidden)
    for cls in all_recipes():
        monkeypatch.setattr(main, "_get_recipe_for_direct", lambda name, cls=cls: (cls, instance))
        options = [] if cls.has_wizard or cls.has_submenu or cls.has_install_version_selection else ["--version", "1.0"]
        result = invoke("software", "install", cls.key, *options)
        assert result.exit_code == 2, (cls.key, result.output)
        assert not json.loads(result.stdout)["ok"]
        if not cls.has_upgrade:
            result = invoke("software", "upgrade", cls.key, "--version", "1.0")
            assert result.exit_code == 2, (cls.key, result.output)
        if not cls.has_switch:
            result = invoke("software", "switch", cls.key, "--version", "1.0")
            assert result.exit_code == 2, (cls.key, result.output)
    configure("text", False)
