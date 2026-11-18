from __future__ import annotations

import zipfile
from pathlib import Path

import pytest


def test_postgresql_installed_versions_ignore_partial_dirs(monkeypatch, tmp_path):
    from software.recipes.postgresql.recipe import PostgreSQLRecipe

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".opskit" / "postgresql"
    (root / "postgresql17.10").mkdir(parents=True)
    complete = root / "postgresql16.13" / "bin"
    complete.mkdir(parents=True)
    (complete / "psql.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr("sys.platform", "win32")

    assert PostgreSQLRecipe().installed_versions() == ["16.13"]


def test_postgresql_windows_extract_is_atomic(monkeypatch, tmp_path):
    from software.base import InstallError
    from software.recipes.postgresql.windows import WindowsDriver

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tarball = tmp_path / "broken.zip"
    with zipfile.ZipFile(tarball, "w") as zf:
        zf.writestr("pgsql/README.txt", "missing psql")

    with pytest.raises(InstallError):
        WindowsDriver().install_tarball("17.10", tarball)

    root = tmp_path / ".opskit" / "postgresql"
    assert not (root / "postgresql17.10").exists()
    assert not (root / "postgresql17.10.installing").exists()
    assert not list(root.glob(".opskit-pgsql-*"))


@pytest.fixture
def windows_install(monkeypatch, tmp_path):
    from software.recipes.postgresql import common
    from software.recipes.postgresql.windows import WindowsDriver
    dest = tmp_path / "postgresql17.10"
    monkeypatch.setattr(common, "pgsql_version_dir", lambda version: dest)
    monkeypatch.setattr(common, "pgsql_bin_dir", lambda version: dest / "bin")
    source = tmp_path / "package.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pgsql/bin/psql.exe", b"inert binary")
    return WindowsDriver(), dest, source


def test_windows_install_preserves_existing_version(windows_install):
    from software.base import InstallError
    driver, dest, source = windows_install
    dest.mkdir()
    data = dest / "user-data"
    data.write_bytes(b"keep")
    with pytest.raises(InstallError, match="refusing to overwrite"):
        driver.install_tarball("17.10", source)
    assert data.read_bytes() == b"keep"
    assert not (dest / "bin").exists()


def test_windows_install_does_not_claim_old_staging(windows_install):
    driver, dest, source = windows_install
    old = dest.with_name(dest.name + ".installing")
    old.mkdir()
    (old / "user-data").write_bytes(b"keep")
    assert driver.install_tarball("17.10", source) == str(dest / "bin")
    assert (old / "user-data").read_bytes() == b"keep"
    assert not list(dest.parent.glob(".opskit-pgsql-*"))


@pytest.mark.parametrize("failure", [PermissionError, KeyboardInterrupt])
def test_windows_publish_failure_cleans_only_owned_staging(windows_install, monkeypatch, failure):
    from software.base import InstallError
    driver, dest, source = windows_install

    def reject_publish(self, target, **kwargs):
        assert self.name == "payload" and target == dest
        raise failure("injected publication failure")

    monkeypatch.setattr("software._shared.archive_install.publish_new_directory", reject_publish)
    with pytest.raises(KeyboardInterrupt if failure is KeyboardInterrupt else InstallError):
        driver.install_tarball("17.10", source)
    assert not dest.exists()
    assert not list(dest.parent.glob(".opskit-pgsql-*"))


def test_windows_destination_created_during_extract_is_preserved(windows_install, monkeypatch):
    from core import archive
    from software.base import InstallError
    driver, dest, source = windows_install
    original = archive.extract_zip

    def extract_then_compete(*args, **kwargs):
        original(*args, **kwargs)
        dest.mkdir()
        (dest / "user-data").write_bytes(b"competing writer")

    monkeypatch.setattr(archive, "extract_zip", extract_then_compete)
    with pytest.raises(InstallError, match="refusing to overwrite"):
        driver.install_tarball("17.10", source)
    assert (dest / "user-data").read_bytes() == b"competing writer"
    assert not list(dest.parent.glob(".opskit-pgsql-*"))
