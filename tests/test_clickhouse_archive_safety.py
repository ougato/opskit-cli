"""ClickHouse extraction validates the whole package and publishes working aliases."""
import io
import tarfile

import pytest

from software.base import InstallError


@pytest.fixture
def driver(tmp_path, monkeypatch):
    from software.recipes.clickhouse.linux import LinuxDriver
    dest = tmp_path / "clickhouse1"
    monkeypatch.setattr("software.recipes.clickhouse.linux.clickhouse_version_dir", lambda version: dest)
    return LinuxDriver(), dest, tmp_path / "package.tgz"


def package(path, names, special=None):
    with tarfile.open(path, "w:gz") as archive:
        for name in names:
            member = tarfile.TarInfo(name)
            member.size, member.mode = 5, 0o755
            archive.addfile(member, io.BytesIO(b"inert"))
        if special:
            member = tarfile.TarInfo("root/link")
            member.type, member.linkname = tarfile.SYMTYPE, special
            archive.addfile(member)


@pytest.mark.parametrize("name", ["clickhouse-common-static/usr/bin/clickhouse", "root/clickhouse"])
def test_layout_and_alias_after_publication(driver, name):
    instance, dest, source = driver
    package(source, [name])
    assert instance.install_tarball("1", source) == str(dest / "bin")
    alias = dest / "bin/clickhouse-server"
    assert alias.exists()
    if alias.is_symlink():
        assert alias.read_bytes() == b"inert"
        assert str(alias.readlink()) == "clickhouse"
    else:
        assert str(dest / "bin/clickhouse") in alias.read_text()
    assert not list(dest.parent.glob(".opskit-clickhouse-*"))


@pytest.mark.parametrize("extra", ["../escape", "root/../../escape", "C:/escape", "root/usr/bin/clickhouse", "other/usr/bin/clickhouse"])
def test_bad_or_ambiguous_archive_not_published(driver, extra):
    instance, dest, source = driver
    package(source, ["root/usr/bin/clickhouse", extra])
    with pytest.raises(InstallError):
        instance.install_tarball("1", source)
    assert not dest.exists() and not list(dest.parent.glob(".opskit-clickhouse-*"))


def test_unused_escaping_link_still_rejected(driver):
    instance, dest, source = driver
    package(source, ["root/usr/bin/clickhouse"], special="../../escape")
    with pytest.raises(InstallError):
        instance.install_tarball("1", source)
    assert not dest.exists()


def test_quota_and_missing_binary_not_published(driver, monkeypatch):
    from core import archive
    instance, dest, source = driver
    package(source, ["root/README"])
    with pytest.raises(InstallError):
        instance.install_tarball("1", source)
    assert not dest.exists()
    package(source, ["root/usr/bin/clickhouse"])
    monkeypatch.setattr(archive, "MAX_EXTRACT_BYTES", 1)
    with pytest.raises(InstallError):
        instance.install_tarball("1", source)
    assert not dest.exists()


def test_existing_database_preserved(driver):
    instance, dest, source = driver
    dest.mkdir()
    (dest / "database").write_bytes(b"keep")
    with pytest.raises(InstallError, match="refusing to overwrite"):
        instance.install_tarball("1", source)
    assert (dest / "database").read_bytes() == b"keep"


@pytest.mark.parametrize("failure", [PermissionError, KeyboardInterrupt])
def test_publication_failure_or_cancel_cleans_owned_data(driver, monkeypatch, failure):
    instance, dest, source = driver
    package(source, ["root/usr/bin/clickhouse"])

    def reject(*args, **kwargs):
        raise failure("injected")

    monkeypatch.setattr("software._shared.archive_install.publish_new_directory", reject)
    with pytest.raises(KeyboardInterrupt if failure is KeyboardInterrupt else InstallError):
        instance.install_tarball("1", source)
    assert not dest.exists() and not list(dest.parent.glob(".opskit-clickhouse-*"))


def test_alias_fallback_uses_final_path(driver, monkeypatch):
    from pathlib import Path
    instance, dest, source = driver
    package(source, ["root/usr/bin/clickhouse"])

    def unavailable(*args, **kwargs):
        raise OSError("no symlink privilege")

    monkeypatch.setattr(Path, "symlink_to", unavailable)
    instance.install_tarball("1", source)
    assert str(dest / "bin/clickhouse") in (dest / "bin/clickhouse-server").read_text()
    assert ".opskit-" not in (dest / "bin/clickhouse-server").read_text()
