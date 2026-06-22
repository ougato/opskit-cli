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


def test_docker_uses_system_package_install_flow() -> None:
    from software.recipes.docker.constants import DOCKER_SYSTEM_PACKAGE_VERSION

    cls = get_recipe("docker")
    assert cls is not None
    recipe = cls()

    assert cls.has_upgrade is False
    assert cls.has_install_version_selection is False
    assert cls.confirm_before_install is False
    assert recipe.versions() == [DOCKER_SYSTEM_PACKAGE_VERSION]


def test_docker_apt_package_uses_distro_package(monkeypatch) -> None:
    from software.recipes.docker.linux import LinuxDriver
    from software.recipes.docker.constants import DOCKER_APT_PACKAGE

    class FakePlatform:
        pkg_manager = "apt"

    monkeypatch.setattr("core.platform.get_platform", lambda: FakePlatform())

    assert LinuxDriver().pkg_name("latest") == DOCKER_APT_PACKAGE


def test_docker_detect_prefers_owned_apt_package(monkeypatch) -> None:
    from software.recipes.docker.recipe import DockerRecipe

    class FakePlatform:
        os_type = "linux"
        pkg_manager = "apt"

    class FakeDriver:
        def detect_package_version(self) -> str:
            return "24.0.7-0ubuntu1"

    monkeypatch.setattr("core.platform.get_platform", lambda: FakePlatform())
    monkeypatch.setattr("software.recipes.docker.recipe.get_driver", lambda: FakeDriver())

    assert DockerRecipe().detect() == "24.0.7-0ubuntu1"


def test_docker_detect_ignores_external_cli_after_uninstall(monkeypatch) -> None:
    from types import SimpleNamespace
    from software.recipes.docker.recipe import DockerRecipe

    class FakePlatform:
        os_type = "linux"
        pkg_manager = "apt"

    class FakeDriver:
        def detect_package_version(self) -> None:
            return None

    monkeypatch.setattr("core.platform.get_platform", lambda: FakePlatform())
    monkeypatch.setattr("software.recipes.docker.recipe.get_driver", lambda: FakeDriver())
    monkeypatch.setattr("core.runner.which", lambda command: "/usr/bin/docker")
    monkeypatch.setattr("core.runner.run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="Docker version 26.1.4\n"))

    assert DockerRecipe().detect() is None


def test_docker_install_uses_localized_progress_labels(monkeypatch) -> None:
    from software.recipes.docker import recipe as docker_recipe
    from software.recipes.docker.recipe import DockerRecipe

    class FakePlatform:
        os_type = "linux"

    class FakeDriver:
        def ensure_deps(self) -> None:
            pass

        def pkg_name(self, version: str) -> str:
            return "docker.io"

        def install_pkg(self, pkg: str) -> None:
            pass

        def enable_service(self) -> None:
            pass

    class FakeProgress:
        def __init__(self, descs):
            seen["descs"] = descs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def step(self, desc):
            seen.setdefault("steps", []).append(desc)

        def complete(self):
            seen["complete"] = True

    seen: dict[str, object] = {}

    monkeypatch.setattr("core.platform.get_platform", lambda: FakePlatform())
    monkeypatch.setattr(docker_recipe, "get_driver", lambda: FakeDriver())
    monkeypatch.setattr(docker_recipe, "t", lambda key, **kwargs: f"label:{key}")
    monkeypatch.setattr("core.progress.MultiStepProgress", FakeProgress)
    monkeypatch.setattr(DockerRecipe, "detect", lambda self: "24.0.7")

    DockerRecipe().install("latest")

    assert "software.step.check" not in seen["descs"]
    assert "label:software.step.check" in seen["descs"]
    assert seen["steps"][0] == seen["descs"][0]


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


def test_java_and_node_progress_labels_match_golang_style() -> None:
    import inspect
    from software.recipes.golang.recipe import GoRecipe
    from software.recipes.java.recipe import JavaRecipe
    from software.recipes.nodejs.recipe import NodeRecipe

    expected_install_keys = [
        't("software.step.check")',
        't("software.step.download")',
        't("software.step.install")',
        't("software.step.verify")',
    ]
    expected_uninstall_keys = [
        't("software.step.remove_files")',
        't("software.step.cleanup")',
    ]

    for recipe in (GoRecipe, JavaRecipe, NodeRecipe):
        install_source = inspect.getsource(recipe.install)
        uninstall_source = inspect.getsource(recipe.uninstall)
        for key in expected_install_keys:
            assert key in install_source
        for key in expected_uninstall_keys:
            assert key in uninstall_source
        assert 'sp.step("check")' not in install_source
        assert 'sp.step("download")' not in install_source
        assert 'sp.step("install")' not in install_source
        assert 'sp.step("verify")' not in install_source
        assert 'sp.step("remove")' not in uninstall_source
        assert 'sp.step("cleanup")' not in uninstall_source


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
    assert cls.has_install_version_selection is False
    assert cls.confirm_before_install is False
    assert cls().versions() == [NGINX_SYSTEM_PACKAGE_VERSION]


def test_nginx_install_skips_version_select_and_plain_confirm(monkeypatch) -> None:
    from software import menu

    installed: dict[str, object] = {"called": False}
    successes: list[str] = []

    class FakeNginxRecipe:
        key = "nginx"
        description = "Nginx"
        has_wizard = False
        has_version_picker = False
        has_install_version_selection = False
        confirm_before_install = False

    class FakeNginx:
        def detect(self) -> str | None:
            return "1.22.1" if installed["called"] else None

        def install(self, version: str) -> None:
            installed["called"] = version

    monkeypatch.setattr("software.resolver.resolve_deps", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.platform.check_disk_space", lambda *args, **kwargs: True)
    monkeypatch.setattr(menu, "select", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("select should not be called")))
    monkeypatch.setattr(menu, "confirm", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("confirm should not be called")))
    monkeypatch.setattr(menu, "clear_screen", lambda: None)
    monkeypatch.setattr(menu, "print_header", lambda *args, **kwargs: None)
    monkeypatch.setattr(menu, "print_success", lambda message: successes.append(message))
    monkeypatch.setattr(menu, "pause", lambda: None)
    monkeypatch.setattr(
        menu,
        "t",
        lambda key, **kwargs: (
            f"{kwargs.get('name')} {kwargs.get('version')}"
            if key == "install.success"
            else ("Nginx" if key == "software.nginx" else key)
        ),
    )

    menu._do_install(["OpsKit"], FakeNginxRecipe, FakeNginx())

    assert installed["called"] == "latest"
    assert successes
    assert "1.22.1" in successes[0]
    assert "distro-package" not in successes[0]


def test_nginx_reinstall_still_requires_confirmation(monkeypatch) -> None:
    from software import menu

    confirm_calls: list[str] = []

    class FakeNginxRecipe:
        key = "nginx"
        description = "Nginx"
        has_wizard = False
        has_version_picker = False
        has_install_version_selection = False
        confirm_before_install = False

    class FakeNginx:
        def detect(self) -> str | None:
            return "1.22.1"

        def install(self, version: str) -> None:
            raise AssertionError("install should not run when reinstall is declined")

    monkeypatch.setattr("software.resolver.resolve_deps", lambda *args, **kwargs: None)
    monkeypatch.setattr(menu, "print_warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(menu, "pause", lambda: None)
    monkeypatch.setattr(menu, "confirm", lambda *args, **kwargs: confirm_calls.append("called") or False)

    menu._do_install(["OpsKit"], FakeNginxRecipe, FakeNginx())

    assert confirm_calls == ["called"]


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


def test_nginx_enable_service_uses_service_compat_layer(monkeypatch) -> None:
    from software.recipes.nginx.linux import LinuxDriver
    from software.recipes.nginx.constants import NGINX_SERVICE

    calls: list[str] = []
    monkeypatch.setattr("core.service.enable_now", lambda service_name: calls.append(service_name))

    LinuxDriver().enable_service()

    assert calls == [NGINX_SERVICE]


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


def test_python_linux_uv_installer_output_is_captured() -> None:
    import inspect
    from software.recipes.python.linux import LinuxDriver

    source = inspect.getsource(LinuxDriver.ensure_uv)

    assert '["sh", script]' in source
    assert "capture_output=True" in source
    assert "text=True" in source


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
