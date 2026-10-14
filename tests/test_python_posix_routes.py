"""POSIX Python links preserve commands and compensate profile failures."""
from pathlib import Path

import pytest

from software.base import InstallError
from software._shared.activation import ActivationRollbackError
from software.recipes.python.linux import LinuxDriver


@pytest.fixture
def routing(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    driver = LinuxDriver()
    link = tmp_path / "local-bin/python3"
    monkeypatch.setattr(driver, "_symlink_target", lambda: (str(link), False))
    targets = [tmp_path / name for name in ("old", "new")]
    for target in targets:
        target.write_bytes(b"inert interpreter")
    link.parent.mkdir()
    return driver, link, targets


def test_regular_existing_command_never_deleted(routing):
    driver, link, (_, target) = routing
    link.write_bytes(b"administrator command")
    with pytest.raises(FileExistsError):
        driver.apply_version_link(str(target))
    assert link.read_bytes() == b"administrator command"
    with pytest.raises(FileExistsError):
        driver.snapshot_pre_install()


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
def test_profile_failure_restores_previous_link(routing, monkeypatch, restore, cancel):
    driver, link, (old, target) = routing
    link.symlink_to(old)
    previous = link.readlink()
    def fail(*args):
        raise KeyboardInterrupt() if cancel else PermissionError("profile failure")
    monkeypatch.setattr(driver, "_ensure_local_bin_in_path", fail)
    monkeypatch.setattr(driver, "_remove_path_block", fail)
    with pytest.raises(KeyboardInterrupt if cancel else PermissionError):
        if restore:
            driver.restore_original(str(link), str(target), True)
        else:
            driver.apply_version_link(str(target))
    assert link.readlink() == previous
    assert not list(link.parent.glob(".opskit-link-*"))


def test_relative_dangling_original_link_restored_exactly(routing):
    driver, link, (old, _) = routing
    link.symlink_to(old)
    driver.restore_original(str(link), "../missing-original", False)
    assert link.readlink() == Path("../missing-original")


def test_restore_arbitrary_snapshot_path_rejected(routing, tmp_path):
    driver, _, _ = routing
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"preserve")
    with pytest.raises(InstallError, match="outside"):
        driver.restore_original(str(foreign), None, False)
    assert foreign.read_bytes() == b"preserve"


def test_external_link_change_preserved_and_rollback_reported(routing, tmp_path, monkeypatch):
    driver, link, (old, target) = routing
    link.symlink_to(old)
    external = tmp_path / "administrator-interpreter"
    external.write_bytes(b"preserve")
    def interfere(*args):
        link.unlink()
        link.symlink_to(external)
        raise PermissionError("later profile failure")
    monkeypatch.setattr(driver, "_ensure_local_bin_in_path", interfere)
    with pytest.raises(ActivationRollbackError, match="rollback incomplete"):
        driver.apply_version_link(str(target))
    assert link.resolve() == external
    assert external.read_bytes() == b"preserve"
