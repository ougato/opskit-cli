"""Known inert packages exercise each driver's archive-to-install boundary."""
import importlib
import stat
import zipfile

import pytest

DRIVERS = [
    ("nodejs", "node_version_dir", None, "node.exe", "node-v1-win-x64"),
    ("golang", "go_version_dir", "go_bin_dir", "bin/go.exe", "go"),
    ("java", "java_version_dir", "java_bin_dir", "bin/java.exe", "jdk-21"),
    ("mongodb", "mongo_version_dir", "mongo_bin_dir", "bin/mongod.exe", "mongodb-windows"),
    ("postgresql", "pgsql_version_dir", "pgsql_bin_dir", "bin/psql.exe", "pgsql"),
    ("mysql", "mysql_version_dir", "mysql_bin_dir", "bin/mysql.exe", "mysql-8"),
]


@pytest.fixture(params=DRIVERS)
def driver(request, tmp_path, monkeypatch):
    name, directory, bin_directory, executable, prefix = request.param
    common = importlib.import_module("software.recipes." + name + ".common")
    module = importlib.import_module("software.recipes." + name + ".windows")
    dest = tmp_path / "install"
    monkeypatch.setattr(common, directory, lambda version: dest)
    if bin_directory:
        monkeypatch.setattr(common, bin_directory, lambda version: dest / "bin")
    return module.WindowsDriver(), dest, executable, prefix


def package(path, executable, prefix, extra=None):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(prefix + "/" + executable, b"inert fixture")
        if extra:
            archive.writestr(extra, b"must not escape")


def test_official_layout_is_preserved(driver, tmp_path):
    instance, dest, executable, prefix = driver
    source = tmp_path / "package.zip"
    package(source, executable, prefix)
    result = instance.install_tarball("1.0.0", source)
    assert (dest / executable).read_bytes() == b"inert fixture"
    assert result == str((dest / executable).parent)


@pytest.mark.parametrize("escape", ["../outside", "../../outside", "..\\outside", "C:/outside"])
def test_escape_rejected_before_any_payload_write(driver, tmp_path, escape):
    from software.base import InstallError
    instance, dest, executable, prefix = driver
    source = tmp_path / "package.zip"
    package(source, executable, prefix, prefix + "/" + escape)
    with pytest.raises(InstallError):
        instance.install_tarball("1.0.0", source)
    assert not dest.exists() and not (tmp_path / "outside").exists()


def test_links_rejected_before_payload_write(driver, tmp_path):
    from software.base import InstallError
    instance, dest, executable, prefix = driver
    source = tmp_path / "package.zip"
    package(source, executable, prefix)
    with zipfile.ZipFile(source, "a") as archive:
        link = zipfile.ZipInfo(prefix + "/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, b"../outside")
    with pytest.raises(InstallError):
        instance.install_tarball("1.0.0", source)
    assert not dest.exists()


def test_size_limit_remains_effective_in_driver(driver, tmp_path, monkeypatch):
    from core import archive
    from software.base import InstallError
    instance, dest, executable, prefix = driver
    source = tmp_path / "package.zip"
    package(source, executable, prefix)
    monkeypatch.setattr(archive, "MAX_EXTRACT_BYTES", 1)
    with pytest.raises(InstallError):
        instance.install_tarball("1.0.0", source)
    assert not dest.exists()


def test_existing_version_data_is_not_overwritten(driver, tmp_path):
    from software.base import InstallError
    instance, dest, executable, prefix = driver
    dest.mkdir()
    data = dest / "keep-data"
    data.write_bytes(b"keep")
    source = tmp_path / "package.zip"
    package(source, executable, prefix)
    with pytest.raises(InstallError, match="refusing to overwrite"):
        instance.install_tarball("1.0.0", source)
    assert data.read_bytes() == b"keep"
    assert not (dest / executable).exists()


def test_missing_required_binary_is_not_published(driver, tmp_path, monkeypatch):
    from software.base import InstallError
    instance, dest, executable, prefix = driver
    module = importlib.import_module(instance.__class__.__module__)
    monkeypatch.setattr(module, "t", lambda key, **kwargs: key)
    source = tmp_path / "package.zip"
    package(source, "README", prefix)
    with pytest.raises(InstallError, match="bad_structure"):
        instance.install_tarball("1.0.0", source)
    assert not dest.exists()
    assert not list(tmp_path.glob(".opskit-*"))


@pytest.mark.parametrize("failure", [PermissionError, KeyboardInterrupt])
def test_publication_failure_or_cancel_cleans_owned_payload(driver, tmp_path, monkeypatch, failure):
    from software.base import InstallError
    instance, dest, executable, prefix = driver
    source = tmp_path / "package.zip"
    package(source, executable, prefix)

    def reject(self, target, **kwargs):
        assert self.name == "payload" and target == dest
        raise failure("injected publication failure")

    monkeypatch.setattr("software._shared.archive_install.publish_new_directory", reject)
    with pytest.raises(KeyboardInterrupt if failure is KeyboardInterrupt else InstallError):
        instance.install_tarball("1.0.0", source)
    assert not dest.exists()
    assert not list(tmp_path.glob(".opskit-*"))


def test_late_competing_directory_is_preserved(driver, tmp_path, monkeypatch):
    from software.base import InstallError
    instance, dest, executable, prefix = driver
    source = tmp_path / "package.zip"
    package(source, executable, prefix)
    from software._shared.archive_install import publish_new_directory
    original = publish_new_directory

    def compete(self, target, **kwargs):
        assert self.name == "payload" and target == dest
        dest.mkdir()
        (dest / "keep-data").write_bytes(b"other owner")
        return original(self, target, **kwargs)

    monkeypatch.setattr("software._shared.archive_install.publish_new_directory", compete)
    with pytest.raises(InstallError, match="refusing to overwrite"):
        instance.install_tarball("1.0.0", source)
    assert (dest / "keep-data").read_bytes() == b"other owner"
    assert not (dest / executable).exists()
    assert not list(tmp_path.glob(".opskit-*"))


def test_node_flat_package_remains_supported(tmp_path, monkeypatch):
    from software.recipes.nodejs import common
    from software.recipes.nodejs.windows import WindowsDriver
    dest = tmp_path / "install"
    monkeypatch.setattr(common, "node_version_dir", lambda version: dest)
    source = tmp_path / "package.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("node.exe", b"inert fixture")
    assert WindowsDriver().install_tarball("1.0.0", source) == str(dest)
    assert (dest / "node.exe").read_bytes() == b"inert fixture"
