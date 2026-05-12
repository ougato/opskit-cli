"""software/ 模块单元测试"""
from __future__ import annotations

import pytest

from software.base import Recipe, InstallStep, InstallError, UninstallError
from software.registry import all_recipes, get as get_recipe


def test_all_recipes_returns_list(tmp_path) -> None:
    recipes = all_recipes()
    assert isinstance(recipes, list)
    assert len(recipes) >= 3


def test_recipe_keys_unique(tmp_path) -> None:
    recipes = all_recipes()
    keys = [r.key for r in recipes]
    assert len(keys) == len(set(keys))


def test_get_recipe_docker(tmp_path) -> None:
    cls = get_recipe("docker")
    assert cls is not None
    assert cls.key == "docker"


def test_get_recipe_nginx(tmp_path) -> None:
    cls = get_recipe("nginx")
    assert cls is not None
    assert cls.key == "nginx"


def test_nginx_detect_uses_driver(monkeypatch) -> None:
    from software.recipes.nginx import recipe as nginx_recipe

    class FakePlatform:
        os_type = "linux"

    class FakeDriver:
        def detect(self) -> str:
            return "1.27.0"

    monkeypatch.setattr("core.platform.get_platform", lambda: FakePlatform())
    monkeypatch.setattr(nginx_recipe, "get_driver", lambda: FakeDriver())

    assert nginx_recipe.NginxRecipe().detect() == "1.27.0"


def test_nginx_install_steps_match_runtime_flow(tmp_path) -> None:
    cls = get_recipe("nginx")
    steps = [s.description_key for s in cls().steps("install")]
    assert steps == [
        "software.step.install",
    ]


def test_python_install_steps_are_decoupled(tmp_path) -> None:
    cls = get_recipe("python")
    steps = [s.description_key for s in cls().steps("install")]
    assert steps == ["software.step.install"]


def test_python_install_delegates_to_do_install(monkeypatch) -> None:
    from software.recipes.python.recipe import PythonRecipe

    seen: dict[str, object] = {}

    def fake_do_install(self, version: str, on_progress=None) -> None:
        seen["version"] = version
        seen["has_progress"] = callable(on_progress)
        if on_progress:
            on_progress(100)

    monkeypatch.setattr(PythonRecipe, "_do_install", fake_do_install)

    PythonRecipe().install("3.12.13")

    assert seen == {"version": "3.12.13", "has_progress": True}


def test_nginx_enable_service_reports_failure(monkeypatch) -> None:
    from software.recipes.nginx.linux import LinuxDriver

    def fail_run_as_root(*args, **kwargs):
        raise RuntimeError("systemctl failed")

    monkeypatch.setattr("core.privilege.run_as_root", fail_run_as_root)

    with pytest.raises(InstallError):
        LinuxDriver().enable_service()


def test_get_recipe_python(tmp_path) -> None:
    cls = get_recipe("python")
    assert cls is not None
    assert cls.key == "python"


def test_get_recipe_missing(tmp_path) -> None:
    assert get_recipe("nonexistent_xyz") is None


def test_recipe_has_required_attrs(tmp_path) -> None:
    for cls in all_recipes():
        assert hasattr(cls, "key")
        assert hasattr(cls, "platforms")
        assert hasattr(cls, "dependencies")
        assert isinstance(cls.platforms, list)
        assert isinstance(cls.dependencies, list)


def test_recipe_detect_no_crash(tmp_path) -> None:
    for cls in all_recipes():
        instance = cls()
        result = instance.detect()
        assert result is None or isinstance(result, str)


def test_recipe_steps_install(tmp_path) -> None:
    for cls in all_recipes():
        instance = cls()
        steps = instance.steps("install")
        assert isinstance(steps, list)
        assert len(steps) > 0
        for s in steps:
            assert isinstance(s, InstallStep)


def test_recipe_steps_uninstall(tmp_path) -> None:
    for cls in all_recipes():
        instance = cls()
        steps = instance.steps("uninstall")
        assert isinstance(steps, list)
        assert len(steps) > 0


def test_register_returns_module_info(tmp_path) -> None:
    from software import register
    from core.module import ModuleInfo
    info = register()
    assert isinstance(info, ModuleInfo)
    assert info.key == "software"
    assert info.order > 0
