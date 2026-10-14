"""MongoDB tar installation rejects unsafe payloads and preserves existing data."""
import io
import tarfile

import pytest

from software.base import InstallError


@pytest.fixture
def driver(tmp_path, monkeypatch):
    from software.recipes.mongodb import common
    from software.recipes.mongodb.linux import LinuxDriver
    dest = tmp_path / "mongodb1"
    monkeypatch.setattr(common, "mongo_version_dir", lambda version: dest)
    monkeypatch.setattr(common, "mongo_bin_dir", lambda version: dest / "bin")
    return LinuxDriver(), dest, tmp_path / "package.tar.gz"


def package(path, extra=None, kind=tarfile.REGTYPE, binary=True):
    with tarfile.open(path, "w:gz") as archive:
        names = ["mongodb/bin/mongod" if binary else "mongodb/README"]
        if extra:
            names.append(extra)
        for i, name in enumerate(names):
            item = tarfile.TarInfo(name)
            item.mode, item.type = 0o755, kind if i else tarfile.REGTYPE
            item.linkname = "../outside"
            item.size = 5 if item.isfile() else 0
            archive.addfile(item, io.BytesIO(b"inert") if item.size else None)


def test_normal_layout(driver):
    instance, dest, source = driver
    package(source)
    assert instance.install_tarball("1", source) == str(dest / "bin")
    assert (dest / "bin/mongod").read_bytes() == b"inert"


@pytest.mark.parametrize("extra", ["mongodb/../../outside", "../outside", "mongodb/..\\outside", "C:/outside", "other/bin/mongod", "mongodb/bin/mongod"])
def test_unsafe_package_not_published(driver, extra):
    instance, dest, source = driver
    package(source, extra)
    with pytest.raises(InstallError):
        instance.install_tarball("1", source)
    assert not dest.exists() and not list(dest.parent.glob(".opskit-mongo-*"))
    assert not (dest.parent / "outside").exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_link_or_special_file_rejected(driver, kind):
    instance, dest, source = driver
    package(source, "mongodb/special", kind)
    with pytest.raises(InstallError):
        instance.install_tarball("1", source)
    assert not dest.exists()


def test_missing_binary_and_size_limit(driver, monkeypatch):
    from core import archive
    instance, dest, source = driver
    package(source, binary=False)
    with pytest.raises(InstallError):
        instance.install_tarball("1", source)
    assert not dest.exists()
    package(source)
    monkeypatch.setattr(archive, "MAX_EXTRACT_BYTES", 1)
    with pytest.raises(InstallError):
        instance.install_tarball("1", source)
    assert not dest.exists() and not list(dest.parent.glob(".opskit-mongo-*"))


def test_existing_database_preserved(driver):
    instance, dest, source = driver
    dest.mkdir()
    (dest / "database").write_bytes(b"keep")
    package(source)
    with pytest.raises(InstallError, match="refusing to overwrite"):
        instance.install_tarball("1", source)
    assert (dest / "database").read_bytes() == b"keep"


@pytest.mark.parametrize("failure", [PermissionError, KeyboardInterrupt])
def test_failed_publication_and_cancel_clean_staging(driver, monkeypatch, failure):
    instance, dest, source = driver
    package(source)

    def reject(self, target, **kwargs):
        raise failure("injected")

    monkeypatch.setattr("software._shared.archive_install.publish_new_directory", reject)
    with pytest.raises(KeyboardInterrupt if failure is KeyboardInterrupt else InstallError):
        instance.install_tarball("1", source)
    assert not dest.exists() and not list(dest.parent.glob(".opskit-mongo-*"))
