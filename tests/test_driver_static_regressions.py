"""Driver paths that import-time and ordinary CLI tests did not execute."""
import os
from pathlib import Path
import typing
import zipfile

import pytest


@pytest.mark.parametrize("name", ["golang", "java", "nodejs"])
def test_public_driver_annotations_resolve(name):
    import importlib
    driver = importlib.import_module("software.recipes." + name + ".driver")
    assert typing.get_type_hints(driver.PlatformDriver.install_tarball)["tarball"] is Path


def test_network_public_annotations_resolve():
    from network import commands
    import threading
    assert threading.Event in typing.get_args(typing.get_type_hints(commands.get_public_ip)["cancel_event"])


@pytest.mark.parametrize("unsafe", ["mysql/../escape.txt", "mysql/../../escape.txt",
                                   "mysql/..\\escape.txt", "mysql/C:/escape.txt", "/mysql/escape.txt"])
def test_mysql_zip_rejects_escape_before_writing_payload(tmp_path, monkeypatch, unsafe):
    from software.base import InstallError
    from software.recipes.mysql import common
    from software.recipes.mysql.windows import WindowsDriver
    dest = tmp_path / "installed"
    monkeypatch.setattr(common, "mysql_version_dir", lambda version: dest)
    monkeypatch.setattr(common, "mysql_bin_dir", lambda version: dest / "bin")
    package = tmp_path / "mysql.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("mysql/bin/mysql.exe", b"inert binary fixture")
        archive.writestr(unsafe, b"must not escape")
    with pytest.raises(InstallError):
        WindowsDriver().install_tarball("8.0.0", package)
    assert not dest.exists() and not (tmp_path / "escape.txt").exists()


def test_mysql_zip_preserves_official_prefix_layout(tmp_path, monkeypatch):
    from software.recipes.mysql import common
    from software.recipes.mysql.windows import WindowsDriver
    dest = tmp_path / "installed"
    monkeypatch.setattr(common, "mysql_version_dir", lambda version: dest)
    monkeypatch.setattr(common, "mysql_bin_dir", lambda version: dest / "bin")
    package = tmp_path / "mysql.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("mysql-8.0.0-winx64/bin/mysql.exe", b"inert binary fixture")
    assert WindowsDriver().install_tarball("8.0.0", package) == str(dest / "bin")
    assert (dest / "bin/mysql.exe").read_bytes() == b"inert binary fixture"


@pytest.mark.skipif(os.name != "nt", reason="native Windows MySQL junction")
def test_mysql_junction_switch_uses_real_cmd_and_keeps_target_data(tmp_path, monkeypatch):
    from software.recipes.mysql.windows import WindowsDriver
    driver = WindowsDriver()
    junction = tmp_path / "active"
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "keep").write_text("first")
    (second / "keep").write_text("second")
    monkeypatch.setattr(driver, "_active_junction", lambda: junction)
    try:
        assert driver._set_junction(str(first))
        assert (junction / "keep").read_text() == "first"
        assert driver._set_junction(str(second))
        assert (junction / "keep").read_text() == "second"
        assert (first / "keep").read_text() == "first"
        assert (second / "keep").read_text() == "second"
    finally:
        # Nonrecursive rmdir removes only the task-owned junction on Windows.
        if os.path.lexists(junction):
            junction.rmdir()
