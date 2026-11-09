"""Install preflight checks every known required filesystem."""
import sys

import pytest
import typer

from core import platform as host
from software import disk


@pytest.mark.parametrize(("values", "expected"), [
    ((True, True), True),
    ((True, False), False),
    ((False, True), False),
    ((True, None), None),
    ((None, False), False),
    ((None, True), None),
])
def test_all_required_paths_are_checked(monkeypatch, tmp_path, values, expected):
    paths = (tmp_path / "install", tmp_path / "temporary")
    class Recipe:
        def disk_check_paths(self):
            return paths
    observed = []
    results = dict(zip(paths, values))
    def check(required, path):
        observed.append((required, path))
        return results[path]
    monkeypatch.setattr(host, "check_disk_space", check)
    assert disk.check_recipe_disk_space(Recipe(), 123) is expected
    assert observed[0] == (123, paths[0])
    if values[0] is not False:
        assert observed[1] == (123, paths[1])


def test_unknown_route_cannot_be_reported_as_sufficient(monkeypatch, tmp_path):
    class Recipe:
        def disk_check_paths(self):
            return (tmp_path, None)
    monkeypatch.setattr(host, "check_disk_space", lambda required, path: True)
    assert disk.check_recipe_disk_space(Recipe(), 1) is None
    monkeypatch.setattr(host, "check_disk_space", lambda required, path: False)
    assert disk.check_recipe_disk_space(Recipe(), 1) is False


@pytest.mark.parametrize("recipe_module,recipe_class", [
    ("golang", "GoRecipe"), ("nodejs", "NodeRecipe"),
    ("java", "JavaRecipe"), ("mongodb", "MongoDBRecipe"),
    ("mysql", "MySQLRecipe"), ("clickhouse", "ClickHouseRecipe"),
])
def test_versioned_tarballs_check_temporary_and_install_locations(
    recipe_module, recipe_class, monkeypatch, tmp_path,
):
    from importlib import import_module
    from software._shared import versioned_recipe

    temporary = tmp_path / "temporary"
    monkeypatch.setattr(versioned_recipe.tempfile, "gettempdir", lambda: str(temporary))
    cls = getattr(import_module(f"software.recipes.{recipe_module}.recipe"), recipe_class)
    instance = cls()
    assert instance.disk_check_paths() == (instance._versions_dir(), temporary)


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_redis_checks_temporary_and_install_locations(platform, monkeypatch, tmp_path):
    from software.recipes.redis import common, recipe
    temporary, install = tmp_path / "temporary", tmp_path / "install"
    monkeypatch.setattr(recipe.tempfile, "gettempdir", lambda: str(temporary))
    monkeypatch.setattr(common, "redis_versions_dir", lambda: install)
    # The source code uses the same TemporaryDirectory and version root on all platforms.
    monkeypatch.setattr(sys, "platform", platform)
    assert recipe.RedisRecipe().disk_check_paths() == (install, temporary)


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_postgresql_checks_platform_specific_download_directory(platform, monkeypatch, tmp_path):
    from core import paths
    from software.recipes.postgresql import recipe
    temporary, install, cache = tmp_path / "temporary", tmp_path / "install", tmp_path / "cache"
    monkeypatch.setattr(recipe.sys, "platform", platform)
    monkeypatch.setattr(recipe.tempfile, "gettempdir", lambda: str(temporary))
    monkeypatch.setattr(recipe, "pgsql_versions_dir", lambda: install)
    monkeypatch.setattr(paths, "cache_dir", lambda: cache)
    download = temporary if platform == "linux" else cache / "work"
    assert recipe.PostgreSQLRecipe().disk_check_paths() == (install, download)


def test_python_fallback_target_is_unknown():
    from software.recipes.python.recipe import PythonRecipe
    assert PythonRecipe().disk_check_paths() == (None,)


def test_unavailable_temporary_directory_does_not_hide_low_install_space(monkeypatch, tmp_path):
    from software.recipes.redis import common, recipe
    install = tmp_path / "install"
    def fail_temp():
        raise OSError("temporary directory unavailable")
    monkeypatch.setattr(recipe.tempfile, "gettempdir", fail_temp)
    monkeypatch.setattr(common, "redis_versions_dir", lambda: install)
    monkeypatch.setattr(host, "check_disk_space", lambda required, path: False)
    assert recipe.RedisRecipe().disk_check_paths() == (install, None)
    assert disk.check_recipe_disk_space(recipe.RedisRecipe(), 1) is False


def test_low_temporary_space_stops_menu_and_cli(monkeypatch, tmp_path):
    from core import cli, i18n
    from software import lifecycle_cli, menu
    from software.recipes.redis import common, recipe
    temporary, install = tmp_path / "temporary", tmp_path / "install"
    monkeypatch.setattr(recipe.tempfile, "gettempdir", lambda: str(temporary))
    monkeypatch.setattr(common, "redis_versions_dir", lambda: install)
    monkeypatch.setattr(host, "check_disk_space", lambda required, path: path != temporary)
    monkeypatch.setattr(i18n, "_current", {})
    monkeypatch.setattr(i18n, "_lang", "en")
    monkeypatch.setattr(i18n, "_core_keys", set())
    i18n.init(language="en")
    errors, pauses = [], []
    monkeypatch.setattr(menu, "print_error", errors.append)
    monkeypatch.setattr(menu, "pause", lambda: pauses.append(True))
    instance = recipe.RedisRecipe()
    assert menu._install_disk_check(instance) is False
    assert errors and pauses == [True]
    cli.configure("text", False)
    with pytest.raises(typer.Exit):
        lifecycle_cli._check_disk_or_exit(instance)
