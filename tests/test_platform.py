"""core/platform.py 单元测试"""
from __future__ import annotations

import pytest

from core.platform import (
    PlatformInfo,
    get_platform,
    preflight_check,
    check_disk_space,
    _arch,
    _os_type,
)


def test_get_platform_returns_info(tmp_path) -> None:
    info = get_platform()
    assert isinstance(info, PlatformInfo)


def test_os_type_valid(tmp_path) -> None:
    ot = _os_type()
    assert ot in ("linux", "windows", "darwin") or len(ot) > 0


def test_arch_valid(tmp_path) -> None:
    arch = _arch()
    assert len(arch) > 0


def test_platform_fields(tmp_path) -> None:
    info = get_platform()
    assert info.os_type in ("linux", "windows", "darwin") or len(info.os_type) > 0
    assert len(info.arch) > 0
    assert len(info.python_version) > 0
    assert info.disk_free_bytes is None or info.disk_free_bytes >= 0


def test_preflight_check_returns_list(tmp_path) -> None:
    issues = preflight_check()
    assert isinstance(issues, list)


def test_check_disk_space_large(tmp_path) -> None:
    assert check_disk_space(0) is True


def test_check_disk_space_impossible(tmp_path) -> None:
    # 1 PB 必然不足
    assert check_disk_space(1024 ** 5) is False


def test_check_disk_space_is_fresh_and_uses_target_ancestor(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from core import platform as host
    target = tmp_path / "nested" / "future"
    seen = []
    values = iter([150, 30])
    def usage(path):
        seen.append(path)
        return SimpleNamespace(free=next(values))
    monkeypatch.setattr(host.shutil, "disk_usage", usage)
    assert host.check_disk_space(100, target) is True
    assert host.check_disk_space(100, target) is False
    assert seen == [tmp_path, tmp_path]
    assert not target.exists()


@pytest.mark.parametrize("lang,expected", [("zh", "无法读取"), ("en", "Could not read")])
def test_preflight_distinguishes_unknown_and_localizes(tmp_path, monkeypatch, lang, expected):
    from core import config, i18n, platform as host
    monkeypatch.setattr(config, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(host, "_disk_free", lambda path=None: None)
    monkeypatch.setattr(host, "get_platform", lambda: PlatformInfo(
        "linux", "linux", "", "x86_64", "apt", "systemd", True, "3.10", 0))
    monkeypatch.setattr(i18n, "_current", {})
    monkeypatch.setattr(i18n, "_lang", "en")
    i18n.init(language=lang)
    issues = host.preflight_check()
    assert [issue.code for issue in issues] == ["disk_unknown"]
    assert expected in issues[0].message
    assert host.check_disk_space(1, tmp_path) is None


def test_recipe_disk_check_uses_declared_target_or_returns_unknown(tmp_path, monkeypatch):
    from core import platform as host
    from software.disk import check_recipe_disk_space
    from software.base import Recipe
    class Target:
        def disk_check_path(self):
            return tmp_path / "target"
    seen = []
    monkeypatch.setattr(host, "check_disk_space", lambda size, path: seen.append((size, path)) or False)
    assert check_recipe_disk_space(Target(), 123) is False
    assert seen == [(123, tmp_path / "target")]
    assert check_recipe_disk_space(object(), 123) is None
    assert Recipe.disk_check_path(object()) is None


def test_python_install_checks_disk_before_creating_target(tmp_path, monkeypatch):
    from pathlib import Path
    from software.disk import check_recipe_disk_space
    from software.recipes.python.recipe import PythonRecipe

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    recipe = PythonRecipe()
    target = tmp_path / ".opskit" / "python"
    assert recipe.disk_check_paths() == (target,)
    assert check_recipe_disk_space(recipe, 1) is True
    assert not target.exists()


@pytest.mark.parametrize("descendant", [False, True])
def test_disk_space_for_dangling_link_is_unknown(tmp_path, symlink_capable, monkeypatch, descendant):
    from core import platform as host
    link = tmp_path / "missing-target-link"
    link.symlink_to(tmp_path / "absent", target_is_directory=True)
    target = link / "install" if descendant else link
    monkeypatch.setattr(host.shutil, "disk_usage", lambda path: pytest.fail("must not report parent space"))
    assert host.check_disk_space(1, target) is None


def test_unknown_install_disk_warns_in_menu_and_cli(monkeypatch, capsys):
    from core import i18n
    from software import disk, lifecycle_cli, menu
    monkeypatch.setattr(i18n, "_current", {})
    monkeypatch.setattr(i18n, "_lang", "en")
    i18n.init(language="en")
    monkeypatch.setattr(disk, "check_recipe_disk_space", lambda instance, required: None)
    warnings = []
    pauses = []
    monkeypatch.setattr(menu, "print_warning", warnings.append)
    monkeypatch.setattr(menu, "pause", lambda: pauses.append(True))
    instance = object()
    assert menu._install_disk_check(instance) is True
    lifecycle_cli._check_disk_or_exit(instance)
    assert warnings == [i18n.t("platform.install_disk_unknown")]
    assert pauses == [True]
    assert i18n.t("platform.install_disk_unknown") in capsys.readouterr().err


def test_unknown_disk_warning_is_visible_before_upgrade_clears_screen(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from core import i18n, progress
    from software import disk, menu, resolver
    monkeypatch.setattr(i18n, "_current", {})
    monkeypatch.setattr(i18n, "_lang", "en")
    i18n.init(language="en")
    events = []
    monkeypatch.setattr(disk, "check_recipe_disk_space", lambda *args: None)
    monkeypatch.setattr(resolver, "resolve_deps", lambda *args: None)
    monkeypatch.setattr(progress, "spinner", lambda *args: nullcontext())
    monkeypatch.setattr(menu, "paged_select", lambda **kwargs: "2.0")
    monkeypatch.setattr(menu, "print_warning", lambda message: events.append("warning"))
    monkeypatch.setattr(menu, "pause", lambda: events.append("pause"))
    monkeypatch.setattr(menu, "clear_screen", lambda: events.append("clear"))
    monkeypatch.setattr(menu, "print_header", lambda *args: None)
    monkeypatch.setattr(menu, "print_success", lambda *args: None)
    monkeypatch.setattr(menu, "execute_upgrade", lambda *args, **kwargs:
                        (events.append("upgrade") or SimpleNamespace(ok=True, switched=False, elapsed=0)))
    class Recipe:
        key = "test"
        description = "test"
        def detect(self): return "1.0"
        def versions(self): return ["2.0"]
    menu._do_upgrade(["OpsKit"], Recipe, Recipe())
    assert events == ["warning", "pause", "clear", "upgrade", "pause"]


def test_unknown_disk_warning_does_not_corrupt_noninteractive_json(monkeypatch):
    import json
    import typer
    from typer.testing import CliRunner
    from core import i18n
    from core.cli import configure, emit
    from software import disk, lifecycle_cli
    monkeypatch.setattr(i18n, "_current", {})
    monkeypatch.setattr(i18n, "_lang", "en")
    i18n.init(language="en")
    monkeypatch.setattr(disk, "check_recipe_disk_space", lambda *args: None)
    app = typer.Typer()
    @app.command()
    def command():
        configure("json", True)
        lifecycle_cli._check_disk_or_exit(object())
        emit({"done": True})
    try:
        result = CliRunner().invoke(app, [])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["data"] == {"done": True}
        assert i18n.t("platform.install_disk_unknown") in result.stderr
    finally:
        configure("text", False)
