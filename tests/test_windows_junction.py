"""Native junction changes must avoid shells and preserve other directories."""
import os
import importlib
from pathlib import Path

import pytest

from software._shared import windows_junction as junction
from software.base import InstallError

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native reparse APIs")


def test_native_create_switch_and_remove_with_literal_paths(tmp_path):
    root = tmp_path / "managed"
    first = root / "中文 & first"
    second = root / "quote' second"
    first.mkdir(parents=True)
    second.mkdir()
    (first / "marker").write_text("first")
    (second / "marker").write_text("second")
    active = root / "active"
    junction.set_junction(active, first, root)
    assert (active / "marker").read_text() == "first"
    junction.set_junction(active, second, root)
    assert (active / "marker").read_text() == "second"
    junction.remove_junction(active, root)
    assert not os.path.lexists(active)
    assert (first / "marker").read_text() == "first"
    assert (second / "marker").read_text() == "second"


def test_ordinary_directory_never_replaced_or_removed(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    active = tmp_path / "active"
    active.mkdir()
    (active / "administrator").write_text("preserve")
    with pytest.raises(InstallError, match="unmanaged directory"):
        junction.set_junction(active, target, tmp_path)
    with pytest.raises(InstallError, match="unmanaged junction"):
        junction.remove_junction(active, tmp_path)
    assert (active / "administrator").read_text() == "preserve"


def test_external_existing_junction_never_replaced_or_removed(tmp_path):
    root = tmp_path / "managed"
    target = root / "target"
    target.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "marker").write_text("preserve")
    active = root / "active"
    active.mkdir()
    with junction._open(active) as handle:
        junction._control(handle, junction._SET, junction._body(outside))
    with pytest.raises(InstallError, match="outside its managed root"):
        junction.set_junction(active, target, root)
    with pytest.raises(InstallError, match="outside its managed root"):
        junction.remove_junction(active, root)
    assert active.resolve() == outside
    assert (outside / "marker").read_text() == "preserve"
    active.rmdir()


def test_failed_native_switch_keeps_old_route(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    active = tmp_path / "active"
    junction.set_junction(active, first, tmp_path)
    original = junction._control
    def denied(handle, code, body=None):
        if code == junction._SET:
            raise PermissionError("injected native publication failure")
        return original(handle, code, body)
    monkeypatch.setattr(junction, "_control", denied)
    with pytest.raises(PermissionError):
        junction.set_junction(active, second, tmp_path)
    assert active.resolve() == first


def test_failed_native_creation_leaves_no_temporary_directory(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    def denied(*args):
        raise PermissionError("injected native publication failure")
    monkeypatch.setattr(junction, "_control", denied)
    with pytest.raises(PermissionError):
        junction.set_junction(tmp_path / "active", target, tmp_path)
    assert list(tmp_path.glob(".opskit-junction-*")) == []
    assert not os.path.lexists(tmp_path / "active")


def test_partial_switch_failure_restores_old_native_target(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    active = tmp_path / "active"
    junction.set_junction(active, first, tmp_path)
    original = junction._control
    def partial(handle, code, body=None):
        result = original(handle, code, body)
        if code == junction._SET and junction._target(body) == second:
            raise PermissionError("failure after native publication")
        return result
    monkeypatch.setattr(junction, "_control", partial)
    with pytest.raises(PermissionError):
        junction.set_junction(active, second, tmp_path)
    assert active.resolve() == first


@pytest.mark.parametrize("recipe", ["nodejs", "golang", "java"])
def test_driver_creation_and_restoration_never_call_shell(tmp_path, monkeypatch, recipe):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    module = importlib.import_module(f"software.recipes.{recipe}.windows")
    driver = module.WindowsDriver()
    active = driver._active_junction()
    target = active.parent / "version & 中文"
    target.mkdir(parents=True)
    (target / "marker").write_text("preserve")
    def forbidden(*args, **kwargs):
        pytest.fail("junction driver invoked a subprocess shell")
    monkeypatch.setattr(module.subprocess, "run", forbidden)
    # Keep the native junction path while isolating all registry/profile changes.
    monkeypatch.setattr(driver, "_remove_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(driver, "_remove_paths", lambda *args, **kwargs: None)
    assert driver._set_junction(str(target)) is True
    assert active.resolve() == target
    driver.restore_original()
    assert not os.path.lexists(active)
    assert (target / "marker").read_text() == "preserve"
