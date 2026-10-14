"""Uninstall must establish a retained interpreter before removing the active one."""
from types import SimpleNamespace
import shutil

import pytest

from software.base import InstallError
from software._shared.activation import ActivationRollbackError
from software.recipes.python import recipe


@pytest.fixture
def installation(tmp_path, monkeypatch):
    versions = ["3.12.13", "3.11.9", "3.10.20"]
    directories = {}
    for version in versions:
        directory = tmp_path / f"cpython-{version}-test"
        directory.mkdir()
        (directory / "data").write_text(version)
        directories[version] = directory
    state = {"active_version": versions[0], "installed_versions": versions[:], "uv_python_path": "old-python"}
    monkeypatch.setattr(recipe, "uv_python_dir", lambda: tmp_path)
    monkeypatch.setattr(recipe, "load_snapshot", lambda: dict(state))
    monkeypatch.setattr(recipe, "save_snapshot", lambda data: state.update(data))
    def forbidden(*args):
        pytest.fail("failed fallback removed activation state")
    monkeypatch.setattr(recipe, "delete_snapshot", forbidden)
    monkeypatch.setattr(recipe, "get_driver", lambda: SimpleNamespace(restore_original=forbidden, remove_shim=forbidden))
    monkeypatch.setattr(recipe, "find_uv_python", lambda version: "retained-python")
    return versions, directories, state


@pytest.mark.parametrize("failure", ["ordinary", "cancel", "rollback"])
def test_failed_switch_preserves_all_installations(installation, monkeypatch, failure):
    versions, directories, state = installation
    before = dict(state)
    calls = []
    def switch(self, version):
        calls.append(version)
        assert all(directory.exists() for directory in directories.values())
        if failure == "cancel":
            raise KeyboardInterrupt()
        if failure == "rollback":
            raise ActivationRollbackError("version activation failed; rollback incomplete")
        raise InstallError("candidate cannot activate")
    monkeypatch.setattr(recipe.PythonRecipe, "switch", switch)
    with pytest.raises(KeyboardInterrupt if failure == "cancel" else InstallError):
        recipe.PythonRecipe().uninstall(versions[0])
    assert state == before
    assert all((directory / "data").read_text() == version for version, directory in directories.items())
    assert calls == (versions[1:] if failure == "ordinary" else versions[1:2])


def test_successful_fallback_precedes_physical_removal(installation, monkeypatch):
    versions, directories, state = installation
    calls = []
    def switch(self, version):
        calls.append(version)
        assert (directories[versions[0]] / "data").read_text() == versions[0]
        if version == versions[1]:
            raise InstallError("first fallback failed")
        state.update(active_version=version, uv_python_path="retained-python")
    monkeypatch.setattr(recipe.PythonRecipe, "switch", switch)
    recipe.PythonRecipe().uninstall(versions[0])
    assert calls == versions[1:]
    assert not directories[versions[0]].exists()
    assert all(directories[version].exists() for version in versions[1:])
    assert state["active_version"] == versions[2]
    assert state["installed_versions"] == versions[1:]


@pytest.mark.parametrize("all_versions", [False, True])
@pytest.mark.parametrize("stage", ["restore", "shim"])
@pytest.mark.parametrize("cancel", [False, True])
def test_final_removal_route_failure_preserves_interpreters(installation, monkeypatch, all_versions, stage, cancel):
    versions, directories, state = installation
    if not all_versions:
        for version in versions[1:]:
            shutil.rmtree(directories[version])
        state["installed_versions"] = versions[:1]
        directories = {versions[0]: directories[versions[0]]}
    before = dict(state)
    calls = []

    def route_step(name):
        def operation(*args):
            calls.append(name)
            assert all(directory.exists() for directory in directories.values())
            if name == stage:
                raise KeyboardInterrupt() if cancel else InstallError("route restoration failure")
        return operation

    monkeypatch.setattr(recipe, "get_driver", lambda: SimpleNamespace(
        restore_original=route_step("restore"), remove_shim=route_step("shim")))
    with pytest.raises(KeyboardInterrupt if cancel else InstallError):
        recipe.PythonRecipe().uninstall(None if all_versions else versions[0])
    assert state == before
    assert all((directory / "data").read_text() == version for version, directory in directories.items())
    assert calls == (["restore"] if stage == "restore" else ["restore", "shim"])


@pytest.mark.parametrize("all_versions", [False, True])
def test_final_removal_restores_route_and_shim_before_deleting(installation, monkeypatch, all_versions):
    versions, directories, state = installation
    if not all_versions:
        for version in versions[1:]:
            shutil.rmtree(directories[version])
        state["installed_versions"] = versions[:1]
        directories = {versions[0]: directories[versions[0]]}
    calls = []

    def restore(*args):
        assert all(directory.exists() for directory in directories.values())
        calls.append("restore")

    def shim():
        assert calls == ["restore"]
        assert all(directory.exists() for directory in directories.values())
        calls.append("shim")

    def delete():
        assert calls == ["restore", "shim"]
        assert all(not directory.exists() for directory in directories.values())
        calls.append("snapshot")

    monkeypatch.setattr(recipe, "get_driver", lambda: SimpleNamespace(restore_original=restore, remove_shim=shim))
    monkeypatch.setattr(recipe, "delete_snapshot", delete)
    recipe.PythonRecipe().uninstall(None if all_versions else versions[0])
    assert calls == ["restore", "shim", "snapshot"]


def test_all_removal_failure_keeps_snapshot_and_stops_further_deletion(installation, monkeypatch):
    versions, directories, state = installation
    before = dict(state)
    monkeypatch.setattr(recipe, "get_driver", lambda: SimpleNamespace(
        restore_original=lambda *args: None, remove_shim=lambda: None))
    removed = []

    def fail_removal(path):
        removed.append(path)
        raise PermissionError("directory in use")

    monkeypatch.setattr(recipe.shutil, "rmtree", fail_removal)
    with pytest.raises(InstallError, match="directory removal failed"):
        recipe.PythonRecipe().uninstall()
    assert removed == [directories[versions[0]]]
    assert state == before
    assert all(directory.exists() for directory in directories.values())
