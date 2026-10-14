"""MySQL gzip/xz drivers retain internal libraries and reject unsafe publication."""
import importlib
import io
import tarfile

import pytest

from software.base import InstallError


@pytest.fixture(params=["linux", "darwin"])
def driver(request, tmp_path, monkeypatch):
    from software.recipes.mysql import common, linux
    module = importlib.import_module("software.recipes.mysql." + request.param)
    dest = tmp_path / "mysql1"
    monkeypatch.setattr(common, "mysql_version_dir", lambda version: dest)
    monkeypatch.setattr(common, "mysql_bin_dir", lambda version: dest / "bin")
    dependencies = []
    monkeypatch.setattr(linux, "_ensure_linux_deps", lambda root: dependencies.append(True))
    return module.LinuxDriver() if request.param == "linux" else module.DarwinDriver(), dest, dependencies


def package(path, extra=None, missing=False):
    with tarfile.open(path, "w:gz" if str(path).endswith(".gz") else "w:xz") as archive:
        for name in (["mysql/README"] if missing else ["mysql/bin/mysql", "mysql/lib/private/libx.so.1"]):
            member = tarfile.TarInfo(name)
            member.size, member.mode = 5, 0o755
            archive.addfile(member, io.BytesIO(b"inert"))
        if not missing:
            member = tarfile.TarInfo("mysql/lib/private/libx.so")
            member.type, member.linkname = tarfile.SYMTYPE, "libx.so.1"
            archive.addfile(member)
        if extra:
            member = tarfile.TarInfo(extra)
            member.size = 5
            archive.addfile(member, io.BytesIO(b"inert"))


@pytest.mark.parametrize("suffix", [".tar.gz", ".tar.xz"])
def test_normal_layout_with_bundled_library(driver, tmp_path, suffix):
    try:
        (tmp_path / "probe").symlink_to("missing")
        (tmp_path / "probe").unlink()
    except OSError:
        pytest.skip("native symlink creation unavailable")
    instance, dest, _ = driver
    source = tmp_path / ("package" + suffix)
    package(source)
    assert instance.install_tarball("1", source) == str(dest / "bin")
    assert (dest / "bin/mysql").read_bytes() == b"inert"
    assert (dest / "lib/private/libx.so").is_symlink()
    assert (dest / "lib/private/libx.so").read_bytes() == b"inert"
    assert not list(tmp_path.glob(".opskit-mysql-*"))


@pytest.mark.parametrize("extra", ["mysql/../../outside", "mysql/..\\outside", "C:/outside", "other/bin/mysql", "mysql/bin/mysql"])
def test_invalid_package_does_not_install_dependencies_or_publish(driver, tmp_path, extra):
    instance, dest, dependencies = driver
    source = tmp_path / "package.tar.gz"
    package(source, extra)
    with pytest.raises(InstallError):
        instance.install_tarball("1", source)
    assert not dest.exists() and not dependencies
    assert not list(tmp_path.glob(".opskit-mysql-*"))


def test_missing_binary_precedes_dependencies(driver, tmp_path):
    instance, dest, dependencies = driver
    source = tmp_path / "package.tar.gz"
    package(source, missing=True)
    with pytest.raises(InstallError):
        instance.install_tarball("1", source)
    assert not dest.exists() and not dependencies


def test_existing_database_preserved(driver, tmp_path):
    instance, dest, dependencies = driver
    dest.mkdir()
    (dest / "data").write_bytes(b"keep")
    with pytest.raises(InstallError, match="refusing to overwrite"):
        instance.install_tarball("1", tmp_path / "nonexistent.tar.gz")
    assert (dest / "data").read_bytes() == b"keep" and not dependencies


@pytest.mark.parametrize("failure", [PermissionError, KeyboardInterrupt])
def test_publish_failure_or_cancel_cleans_own_files(driver, tmp_path, monkeypatch, failure):
    instance, dest, _ = driver
    source = tmp_path / "package.tar.gz"
    package(source, missing=True)
    # Use a regular-file-only valid package so failure reaches publication without link privileges.
    with tarfile.open(source, "w:gz") as archive:
        member = tarfile.TarInfo("mysql/bin/mysql")
        member.size = 5
        archive.addfile(member, io.BytesIO(b"inert"))

    def reject(*args, **kwargs):
        raise failure("injected")

    monkeypatch.setattr("software._shared.archive_install.publish_new_directory", reject)
    with pytest.raises(KeyboardInterrupt if failure is KeyboardInterrupt else InstallError):
        instance.install_tarball("1", source)
    assert not dest.exists() and not list(tmp_path.glob(".opskit-mysql-*"))
