"""main.py 非交互 CLI 契约测试"""
from __future__ import annotations

import sys
import types

import pytest
import typer
from typer.testing import CliRunner


runner = CliRunner()


def _patch_boot(monkeypatch, main) -> None:
    monkeypatch.setattr(main, "_boot", lambda: {})


def test_software_search_query_uses_direct_table(monkeypatch) -> None:
    import main

    _patch_boot(monkeypatch, main)
    seen: dict[str, object] = {}
    monkeypatch.setattr(main, "_print_software_table", lambda **kwargs: seen.update(kwargs))

    result = runner.invoke(main.app, ["software", "search", "python"])

    assert result.exit_code == 0
    assert seen == {"query": "python"}


def test_software_versions_system_package_hides_internal_marker(monkeypatch) -> None:
    import main

    _patch_boot(monkeypatch, main)
    printed: list[str] = []

    class FakeNginxRecipe:
        key = "nginx"
        description = "Nginx"
        has_submenu = False
        has_install_version_selection = False

    class FakeNginx:
        def versions(self) -> list[str]:
            return ["distro-package"]

    monkeypatch.setattr(main, "_get_recipe_for_direct", lambda name: (FakeNginxRecipe, FakeNginx()))
    monkeypatch.setattr(main.console, "print", lambda message: printed.append(str(message)))

    result = runner.invoke(main.app, ["software", "versions", "nginx"])

    assert result.exit_code == 0
    assert printed
    assert "distro-package" not in printed[0]


def test_install_requires_version_for_versioned_recipe(monkeypatch) -> None:
    import main

    _patch_boot(monkeypatch, main)

    class FakeRecipe:
        key = "python"
        description = "Python"
        has_submenu = False
        has_wizard = False
        has_install_version_selection = True

    class FakeInstance:
        pass

    monkeypatch.setattr(main, "_get_recipe_for_direct", lambda name: (FakeRecipe, FakeInstance()))

    result = runner.invoke(main.app, ["software", "install", "python"])

    assert result.exit_code == 2


def test_install_rejects_version_for_system_package_recipe(monkeypatch) -> None:
    import main

    _patch_boot(monkeypatch, main)

    class FakeRecipe:
        key = "nginx"
        description = "Nginx"
        has_submenu = False
        has_wizard = False
        has_install_version_selection = False

    class FakeInstance:
        pass

    monkeypatch.setattr(main, "_get_recipe_for_direct", lambda name: (FakeRecipe, FakeInstance()))

    result = runner.invoke(main.app, ["software", "install", "nginx", "--version", "1.27.0"])

    assert result.exit_code == 2


def test_install_parent_recipe_does_not_enter_submenu(monkeypatch) -> None:
    import main

    _patch_boot(monkeypatch, main)

    class FakeRecipe:
        key = "wireguard"
        description = "WireGuard"
        has_submenu = True

    class FakeInstance:
        pass

    monkeypatch.setattr(main, "_get_recipe_for_direct", lambda name: (FakeRecipe, FakeInstance()))

    result = runner.invoke(main.app, ["software", "install", "wireguard"])

    assert result.exit_code == 2


def test_wg_client_token_install_resolves_deps_first(monkeypatch) -> None:
    import main

    events: list[str] = []

    class FakeRecipe:
        key = "wg_client"
        description = "WG Client"
        has_submenu = False
        has_wizard = True

    class FakeInstance:
        def detect(self) -> str:
            return "installed"

    fake_client = types.ModuleType("wireguard.client")
    fake_client.install_client = lambda token="": events.append(f"install:{token}")

    monkeypatch.setitem(sys.modules, "wireguard.client", fake_client)
    monkeypatch.setattr(main, "_resolve_deps_or_exit", lambda *args, **kwargs: events.append("deps"))
    monkeypatch.setattr("core.theme.print_success", lambda *args, **kwargs: None)

    main._install_direct(["OpsKit"], FakeRecipe, FakeInstance(), token="TOKEN")

    assert events == ["deps", "install:TOKEN"]


def test_multi_version_uninstall_requires_version_or_all() -> None:
    import main

    class FakeRecipe:
        key = "python"
        description = "Python"
        has_switch = True
        has_submenu = False

    class FakeInstance:
        def installed_versions(self) -> list[str]:
            return ["3.12.3"]

        def uninstall(self, version=None) -> None:
            raise AssertionError("uninstall should not run without --version or --all")

    with pytest.raises(typer.Exit) as exc:
        main._uninstall_direct(FakeRecipe, FakeInstance())

    assert exc.value.exit_code == 2


def test_switch_requires_installed_version() -> None:
    import main

    class FakeRecipe:
        key = "python"
        description = "Python"
        has_switch = True

    class FakeInstance:
        def installed_versions(self) -> list[str]:
            return ["3.11.9"]

        def switch(self, version: str) -> None:
            raise AssertionError("switch should not run for missing version")

    with pytest.raises(typer.Exit) as exc:
        main._switch_direct(FakeRecipe, FakeInstance(), "3.12.3")

    assert exc.value.exit_code == 1


def test_network_direct_host_disables_pause(monkeypatch) -> None:
    import main

    _patch_boot(monkeypatch, main)
    seen: dict[str, object] = {}
    fake_network_menu = types.ModuleType("network.menu")
    fake_network_menu.show_ping = lambda **kwargs: seen.update(kwargs)
    monkeypatch.setitem(sys.modules, "network.menu", fake_network_menu)

    result = runner.invoke(main.app, ["network", "ping", "example.com"])

    assert result.exit_code == 0
    assert seen == {"host": "example.com", "pause_after": False}


def test_network_missing_host_keeps_interactive_pause(monkeypatch) -> None:
    import main

    _patch_boot(monkeypatch, main)
    seen: dict[str, object] = {}
    fake_network_menu = types.ModuleType("network.menu")
    fake_network_menu.show_ping = lambda **kwargs: seen.update(kwargs)
    monkeypatch.setitem(sys.modules, "network.menu", fake_network_menu)

    result = runner.invoke(main.app, ["network", "ping"])

    assert result.exit_code == 0
    assert seen == {"host": None, "pause_after": True}


def test_monitor_disk_cli_disables_pause(monkeypatch) -> None:
    import main

    _patch_boot(monkeypatch, main)
    seen: dict[str, object] = {}
    fake_monitor_menu = types.ModuleType("monitor.menu")
    fake_monitor_menu.show_disk_detail = lambda **kwargs: seen.update(kwargs)
    monkeypatch.setitem(sys.modules, "monitor.menu", fake_monitor_menu)

    result = runner.invoke(main.app, ["monitor", "disk"])

    assert result.exit_code == 0
    assert seen == {"pause_after": False}


# ─── CLI 参数大小写兼容 + 单字符别名 ──────────────────────────────────────────

@pytest.mark.parametrize("flag", ["-V", "-v", "--version", "--VERSION"])
def test_version_flag_case_insensitive(flag) -> None:
    import main

    result = runner.invoke(main.app, [flag])

    assert result.exit_code == 0
    assert "v" in result.stdout


@pytest.mark.parametrize("flag", ["-h", "-H", "--help", "--HELP"])
def test_help_flag_case_insensitive(flag) -> None:
    import main

    result = runner.invoke(main.app, [flag])

    assert result.exit_code == 0
    assert "Usage" in result.stdout


@pytest.mark.parametrize("cmd", ["software", "SOFTWARE", "Software"])
def test_command_name_case_insensitive(monkeypatch, cmd) -> None:
    import main

    _patch_boot(monkeypatch, main)
    seen: dict[str, object] = {}
    monkeypatch.setattr(main, "_print_software_table", lambda **kwargs: seen.update(kwargs))

    result = runner.invoke(main.app, [cmd, "search", "python"])

    assert result.exit_code == 0
    assert seen == {"query": "python"}


def test_install_version_short_flag(monkeypatch) -> None:
    import main

    _patch_boot(monkeypatch, main)
    captured: dict[str, object] = {}
    monkeypatch.setattr(main, "_sw_action_by_name", lambda *a, **kw: captured.update(name=a[0], action=a[1], **kw))

    result = runner.invoke(main.app, ["software", "install", "python", "-v", "3.12.3"])

    assert result.exit_code == 0
    assert captured["version"] == "3.12.3"


def test_uninstall_all_short_flag(monkeypatch) -> None:
    import main

    _patch_boot(monkeypatch, main)
    captured: dict[str, object] = {}
    monkeypatch.setattr(main, "_sw_action_by_name", lambda *a, **kw: captured.update(name=a[0], action=a[1], **kw))

    result = runner.invoke(main.app, ["software", "uninstall", "python", "-a"])

    assert result.exit_code == 0
    assert captured["all_versions"] is True
