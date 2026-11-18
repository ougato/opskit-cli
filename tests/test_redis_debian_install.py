"""Redis Debian package groups resolve exact aliases before publishing an owned version."""
import io
import tarfile

import pytest

from software.base import InstallError
from test_debian_container import member


def deb(path, files=(), links=()):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        for name in files:
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = 5, 0o755
            archive.addfile(entry, io.BytesIO(b"inert"))
        for name, target, kind in links:
            entry = tarfile.TarInfo(name)
            entry.type, entry.linkname = kind, target
            archive.addfile(entry)
    path.write_bytes(b"!<arch>\n" + member("debian-binary/", b"2.0\n") + member("data.tar.gz/", data.getvalue()))
    return path


@pytest.fixture
def install(tmp_path, monkeypatch):
    from software.recipes.redis import common
    from software.recipes.redis.linux import LinuxDriver
    dest = tmp_path / "redis1"
    monkeypatch.setattr(common, "redis_version_dir", lambda version: dest)
    monkeypatch.setattr(common, "redis_bin_dir", lambda version: dest / "bin")
    return LinuxDriver(), dest


def test_regular_binaries(install, tmp_path):
    driver, dest = install
    source = deb(tmp_path / "package.deb", ["./usr/bin/redis-server", "usr/bin/redis-cli", "usr/share/doc/ignored"])
    assert driver.install_binary("1", source) == str(dest / "bin")
    assert (dest / "bin/redis-server").read_bytes() == b"inert"
    assert sorted(path.name for path in (dest / "bin").iterdir()) == ["redis-cli", "redis-server"]


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_alias_target_in_later_package(install, tmp_path, kind):
    driver, dest = install
    if kind == tarfile.SYMTYPE:
        try:
            (tmp_path / "probe").symlink_to("missing")
            (tmp_path / "probe").unlink()
        except OSError:
            pytest.skip("native symlink creation unavailable")
    target = "redis-check-rdb" if kind == tarfile.SYMTYPE else "./usr/bin/redis-check-rdb"
    first = deb(tmp_path / "server.deb", links=[("usr/bin/redis-server", target, kind)])
    second = deb(tmp_path / "tools.deb", ["usr/bin/redis-check-rdb", "usr/bin/redis-cli"])
    driver.install_binary("1", [first, second])
    assert (dest / "bin/redis-server").read_bytes() == b"inert"
    if kind == tarfile.SYMTYPE:
        assert str((dest / "bin/redis-server").readlink()) == "redis-check-rdb"
    else:
        assert (dest / "bin/redis-server").stat().st_ino == (dest / "bin/redis-check-rdb").stat().st_ino
    assert not list(tmp_path.glob(".opskit-redis-*"))


@pytest.mark.parametrize("target", ["../../../outside", "/usr/bin/redis-check-rdb", "C:/outside", "../../usr/share/redis-check-rdb", "redis-sentinel"])
def test_unsafe_or_missing_alias_is_not_faked(install, tmp_path, target):
    driver, dest = install
    source = deb(tmp_path / "package.deb", ["usr/bin/redis-check-rdb", "usr/bin/redis-cli"],
                 [("usr/bin/redis-server", target, tarfile.SYMTYPE)])
    with pytest.raises(InstallError):
        driver.install_binary("1", source)
    assert not dest.exists() and not list(tmp_path.glob(".opskit-redis-*"))


def test_alias_cycle_rejected(install, tmp_path):
    driver, dest = install
    source = deb(tmp_path / "package.deb", ["usr/bin/redis-cli"],
                 [("usr/bin/redis-server", "redis-check-rdb", tarfile.SYMTYPE),
                  ("usr/bin/redis-check-rdb", "redis-server", tarfile.SYMTYPE)])
    with pytest.raises(InstallError, match="cyclic"):
        driver.install_binary("1", source)
    assert not dest.exists()


@pytest.mark.parametrize("extra", ["../usr/bin/redis-cli", "usr/bin/../../outside", "usr/bin/redis-server"])
def test_original_path_or_duplicate_rejected(install, tmp_path, extra):
    driver, dest = install
    source = deb(tmp_path / "package.deb", ["usr/bin/redis-server", "usr/bin/redis-cli", extra])
    with pytest.raises(InstallError):
        driver.install_binary("1", source)
    assert not dest.exists()


def test_cross_package_collision_not_overwritten(install, tmp_path):
    driver, dest = install
    first = deb(tmp_path / "one.deb", ["usr/bin/redis-server", "usr/bin/redis-cli"])
    second = deb(tmp_path / "two.deb", ["usr/bin/redis-cli"])
    with pytest.raises(InstallError):
        driver.install_binary("1", [first, second])
    assert not dest.exists() and not list(tmp_path.glob(".opskit-redis-*"))


def test_missing_cli_not_published(install, tmp_path):
    driver, dest = install
    source = deb(tmp_path / "package.deb", ["usr/bin/redis-server"])
    with pytest.raises(InstallError):
        driver.install_binary("1", source)
    assert not dest.exists()


def test_existing_data_preserved(install, tmp_path):
    driver, dest = install
    dest.mkdir()
    (dest / "data.rdb").write_bytes(b"keep")
    with pytest.raises(InstallError, match="refusing to overwrite"):
        driver.install_binary("1", tmp_path / "not-read.deb")
    assert (dest / "data.rdb").read_bytes() == b"keep"


@pytest.mark.parametrize("failure", [PermissionError, KeyboardInterrupt])
def test_failed_publication_or_cancel_clean_owned_files(install, tmp_path, monkeypatch, failure):
    driver, dest = install
    source = deb(tmp_path / "package.deb", ["usr/bin/redis-server", "usr/bin/redis-cli"])

    def reject(*args, **kwargs):
        raise failure("injected")

    monkeypatch.setattr("software._shared.archive_install.publish_new_directory", reject)
    with pytest.raises(KeyboardInterrupt if failure is KeyboardInterrupt else InstallError):
        driver.install_binary("1", source)
    assert not dest.exists() and not list(tmp_path.glob(".opskit-redis-*"))
