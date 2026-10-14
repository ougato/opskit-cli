"""Inert Redis ZIPs verify layout, rejection and owned publication cleanup."""
import stat
import zipfile

import pytest

from software.base import InstallError
from software.recipes.redis.windows import WindowsDriver


@pytest.fixture
def install(tmp_path, monkeypatch):
    from software.recipes.redis import common
    dest = tmp_path / "redis1"
    monkeypatch.setattr(common, "redis_version_dir", lambda version: dest)
    monkeypatch.setattr(common, "redis_bin_dir", lambda version: dest / "bin")
    return WindowsDriver(), dest, tmp_path / "package.zip"


def package(path, prefix="", extra=None):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(prefix + "redis-server.exe", b"inert server")
        archive.writestr(prefix + "msys-runtime.dll", b"inert runtime")
        archive.writestr(prefix + "redis.conf", b"inert config")
        if extra is not None:
            archive.writestr(extra, b"extra")


@pytest.mark.parametrize("prefix", ["", "bin/", "redis-1/", "redis-1/bin/"])
def test_layout_and_runtime_files_without_duplicates(install, prefix):
    driver, dest, source = install
    package(source, prefix)
    assert driver.install_binary("1", source) == str(dest / "bin")
    assert (dest / "bin/redis-server.exe").read_bytes() == b"inert server"
    assert (dest / "bin/msys-runtime.dll").read_bytes() == b"inert runtime"
    assert (dest / "bin/redis.conf").read_bytes() == b"inert config"
    assert len(list(dest.rglob("redis-server.exe"))) == 1


@pytest.mark.parametrize("extra", ["../outside", "root/../../outside", "..\\outside", "C:/outside", "other/redis-server.exe", "bin/msys-runtime.dll"])
def test_unsafe_or_ambiguous_package_not_published(install, extra):
    driver, dest, source = install
    package(source, extra=extra)
    with pytest.raises(InstallError):
        driver.install_binary("1", source)
    assert not dest.exists()
    assert not (dest.parent / "outside").exists()
    assert not list(dest.parent.glob(".opskit-redis-*"))


def test_link_rejected(install):
    driver, dest, source = install
    package(source)
    with zipfile.ZipFile(source, "a") as archive:
        member = zipfile.ZipInfo("link")
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(member, "../outside")
    with pytest.raises(InstallError):
        driver.install_binary("1", source)
    assert not dest.exists()


def test_quota_rejected(install, monkeypatch):
    from core import archive
    driver, dest, source = install
    package(source)
    monkeypatch.setattr(archive, "MAX_EXTRACT_BYTES", 1)
    with pytest.raises(InstallError):
        driver.install_binary("1", source)
    assert not dest.exists()


def test_existing_data_preserved(install):
    driver, dest, source = install
    package(source)
    dest.mkdir()
    (dest / "data.rdb").write_bytes(b"keep")
    with pytest.raises(InstallError, match="refusing to overwrite"):
        driver.install_binary("1", source)
    assert (dest / "data.rdb").read_bytes() == b"keep"


@pytest.mark.parametrize("failure", [PermissionError, KeyboardInterrupt])
def test_publication_failure_or_cancel_cleans_owned_files(install, monkeypatch, failure):
    driver, dest, source = install
    package(source, "bin/")

    def reject(self, target, **kwargs):
        raise failure("injected publish failure")

    monkeypatch.setattr("software._shared.archive_install.publish_new_directory", reject)
    with pytest.raises(KeyboardInterrupt if failure is KeyboardInterrupt else InstallError):
        driver.install_binary("1", source)
    assert not dest.exists()
    assert not list(dest.parent.glob(".opskit-redis-*"))


def test_destination_race_preserves_competing_data(install, monkeypatch):
    from core import archive
    driver, dest, source = install
    package(source)
    original = archive.extract_zip

    def compete(*args, **kwargs):
        original(*args, **kwargs)
        dest.mkdir()
        (dest / "data.rdb").write_bytes(b"other operation")

    monkeypatch.setattr(archive, "extract_zip", compete)
    with pytest.raises(InstallError, match="refusing to overwrite"):
        driver.install_binary("1", source)
    assert (dest / "data.rdb").read_bytes() == b"other operation"
    assert not list(dest.parent.glob(".opskit-redis-*"))
