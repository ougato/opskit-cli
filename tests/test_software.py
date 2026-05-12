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


def test_nginx_versions_match_distro_package_semantics() -> None:
    from software.recipes.nginx.constants import NGINX_SYSTEM_PACKAGE_VERSION

    cls = get_recipe("nginx")

    assert cls.has_upgrade is False
    assert cls().versions() == [NGINX_SYSTEM_PACKAGE_VERSION]


def test_nginx_driver_rejects_non_linux(monkeypatch) -> None:
    from software.recipes.nginx import driver as nginx_driver

    monkeypatch.setattr(nginx_driver.sys, "platform", "win32")

    with pytest.raises(RuntimeError, match="only supports Linux"):
        nginx_driver.get_driver()


def test_nginx_extra_stream_package_is_apt_only() -> None:
    from software.recipes.nginx.linux import LinuxDriver

    driver = LinuxDriver()

    assert "libnginx-mod-stream" in driver._packages_for_runner("apt")
    assert driver._packages_for_runner("dnf") == ["nginx"]


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


def test_python_package_manager_skips_build_only_versions(monkeypatch) -> None:
    from software.recipes.python.common import VersionEntry
    from software.recipes.python.recipe import PythonRecipe

    monkeypatch.setattr(
        PythonRecipe,
        "_version_entries",
        lambda self: [VersionEntry(display="3.12.13", need_build=True)],
    )

    assert PythonRecipe()._install_with_package_manager("3.12.13", "3.12") is None


def test_python_activate_install_updates_snapshot(monkeypatch) -> None:
    from types import SimpleNamespace
    from software.recipes.python import recipe as python_recipe
    from software.recipes.python.recipe import PythonRecipe

    saved: dict[str, object] = {}

    class FakeDriver:
        def apply_version_link(self, new_bin: str) -> None:
            saved["linked"] = new_bin

        def install_shim(self, fallback: str) -> None:
            saved["fallback"] = fallback

    monkeypatch.setattr(
        python_recipe.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="Python 3.12.9\n", stderr=""),
    )
    monkeypatch.setattr(python_recipe, "save_snapshot", lambda data: saved.update(data))

    PythonRecipe()._activate_install(
        "3.12.13",
        "/tmp/python3.12",
        {"installed_versions": [], "symlink_path": "/usr/bin/python3"},
        FakeDriver(),
    )

    assert saved["installed_versions"] == ["3.12.13"]
    assert saved["active_version"] == "3.12.13"
    assert saved["uv_python_path"] == "/tmp/python3.12"
    assert saved["linked"] == "/tmp/python3.12"
    assert saved["fallback"] == "/usr/bin/python3"


def test_python_activate_install_rejects_wrong_minor(monkeypatch) -> None:
    from types import SimpleNamespace
    from software.recipes.python import recipe as python_recipe
    from software.recipes.python.recipe import PythonRecipe

    saved: dict[str, object] = {}

    class FakeDriver:
        def apply_version_link(self, new_bin: str) -> None:
            pass

        def install_shim(self, fallback: str) -> None:
            pass

    monkeypatch.setattr(
        python_recipe.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="Python 3.11.9\n", stderr=""),
    )
    monkeypatch.setattr(python_recipe, "save_snapshot", lambda data: saved.update(data))

    with pytest.raises(InstallError):
        PythonRecipe()._activate_install(
            "3.12.13",
            "/tmp/python3.12",
            {"installed_versions": [], "symlink_path": "/usr/bin/python3"},
            FakeDriver(),
        )

    assert saved == {}


def test_python_uninstall_active_keeps_switched_snapshot(monkeypatch) -> None:
    from software.recipes.python import recipe as python_recipe
    from software.recipes.python.recipe import PythonRecipe

    state = {
        "installed_versions": ["3.12.13", "3.11.9"],
        "active_version": "3.12.13",
        "uv_python_path": "/tmp/python3.12",
    }
    saved: dict[str, object] = {}

    class FakeDriver:
        def restore_original(self, *args, **kwargs) -> None:
            pass

        def remove_shim(self) -> None:
            pass

    def fake_switch(self, version: str) -> None:
        state["active_version"] = version
        state["uv_python_path"] = f"/tmp/python{version}"

    monkeypatch.setattr(python_recipe, "load_snapshot", lambda: dict(state))
    monkeypatch.setattr(python_recipe, "save_snapshot", lambda data: saved.update(data))
    monkeypatch.setattr(python_recipe, "get_driver", lambda: FakeDriver())
    monkeypatch.setattr(PythonRecipe, "installed_versions", lambda self: ["3.12.13", "3.11.9"])
    monkeypatch.setattr(PythonRecipe, "switch", fake_switch)
    monkeypatch.setattr(python_recipe, "find_uv_python", lambda version: f"/tmp/python{version}")
    monkeypatch.setattr(python_recipe, "uv_python_dir", lambda: __import__("pathlib").Path("/missing"))

    PythonRecipe().uninstall("3.12.13")

    assert saved["installed_versions"] == ["3.11.9"]
    assert saved["active_version"] == "3.11.9"
    assert saved["uv_python_path"] == "/tmp/python3.11.9"


def test_windows_python_shim_fallback_skips_itself() -> None:
    from software.recipes.python.constants import SHIM_CMD_TEMPLATE

    assert 'if /I not "%%~fP"=="%~f0"' in SHIM_CMD_TEMPLATE
    assert "( python %* )" not in SHIM_CMD_TEMPLATE


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
