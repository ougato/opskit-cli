"""Patch version prefixes must never select or delete another installation."""
from types import SimpleNamespace

import pytest

from software.base import InstallError
from software.recipes.python import common, recipe
import os
import shutil


def test_lookup_requires_exact_directory_version_and_regular_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "uv_python_dir", lambda: tmp_path)
    other = tmp_path / "cpython-3.12.13-windows-x86_64-none"
    other.mkdir()
    (other / "python.exe").write_bytes(b"other version")
    assert common.find_uv_python("3.12.1") is None
    exact = tmp_path / "cpython-3.12.1-windows-x86_64-none"
    exact.mkdir()
    (exact / "python.exe").mkdir()
    assert common.find_uv_python("3.12.1") is None
    (exact / "python.exe").rmdir()
    (exact / "python.exe").write_bytes(b"exact version")
    assert common.find_uv_python("3.12.1") == str(exact / "python.exe")


def test_uninstall_preserves_longer_patch_version_directory(tmp_path, monkeypatch):
    exact = tmp_path / "cpython-3.12.1-windows-x86_64-none"
    other = tmp_path / "cpython-3.12.13-windows-x86_64-none"
    for directory in (exact, other):
        directory.mkdir()
        (directory / "data").write_bytes(directory.name.encode())
    monkeypatch.setattr(recipe, "uv_python_dir", lambda: tmp_path)
    monkeypatch.setattr(recipe, "load_snapshot", lambda: {})
    monkeypatch.setattr(recipe, "get_driver", lambda: SimpleNamespace())
    monkeypatch.setattr(recipe.PythonRecipe, "installed_versions", lambda self: ["3.12.1", "3.12.13"])
    recipe.PythonRecipe().uninstall("3.12.1")
    assert not exact.exists()
    assert (other / "data").read_bytes() == other.name.encode()


@pytest.mark.parametrize("version", ["3", "3.12", "3.12.1rc1", "3.12.1/../other"])
def test_non_exact_uninstall_rejected_before_reading_state(monkeypatch, version):
    monkeypatch.setattr(recipe, "load_snapshot", lambda: pytest.fail("invalid version read installation state"))
    with pytest.raises(InstallError, match="exact stable version"):
        recipe.PythonRecipe().uninstall(version)


@pytest.mark.parametrize("location", ["root", "entry", "ancestor"])
def test_linked_installations_rejected_without_touching_external_data(tmp_path, location):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data").write_bytes(b"preserve")
    base = tmp_path / "managed"
    if location == "root":
        link = base
    elif location == "ancestor":
        link = tmp_path / "ancestor"
        base = link / "managed"
    else:
        base.mkdir()
        link = base / "cpython-3.12.1-windows-x86_64-none"
    if os.name == "nt":
        from software._shared import windows_junction
        link.mkdir()
        with windows_junction._open(link) as handle:
            windows_junction._control(handle, windows_junction._SET, windows_junction._body(outside))
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(InstallError, match="must not be a link"):
            common.uv_installation_directories(base)
        assert (outside / "data").read_bytes() == b"preserve"
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()


def test_deletion_failure_does_not_publish_success_state(tmp_path, monkeypatch):
    target = tmp_path / "cpython-3.12.1-windows-x86_64-none"
    target.mkdir()
    (target / "data").write_bytes(b"preserve")
    monkeypatch.setattr(recipe, "uv_python_dir", lambda: tmp_path)
    monkeypatch.setattr(recipe, "load_snapshot", lambda: {})
    def forbidden(*args):
        pytest.fail("failed removal published cleanup state")
    monkeypatch.setattr(recipe, "save_snapshot", forbidden)
    monkeypatch.setattr(recipe, "delete_snapshot", forbidden)
    monkeypatch.setattr(recipe, "get_driver", lambda: SimpleNamespace(restore_original=forbidden, remove_shim=forbidden))
    def fail_delete(*args, **kwargs):
        assert not kwargs.get("ignore_errors", False)
        raise PermissionError("injected deletion denial")
    monkeypatch.setattr(shutil, "rmtree", fail_delete)
    with pytest.raises(InstallError, match="directory removal failed"):
        recipe.PythonRecipe().uninstall("3.12.1")
    assert (target / "data").read_bytes() == b"preserve"
