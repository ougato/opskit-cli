"""Both macOS archive formats exercise the actual driver without running binaries."""
import io
import os
import stat
import tarfile
import zipfile

import pytest

from software.base import InstallError


@pytest.fixture(params=[".zip", ".tar.gz"])
def install(request, tmp_path, monkeypatch):
    from software.recipes.postgresql import common
    from software.recipes.postgresql.darwin import DarwinDriver
    dest = tmp_path / "postgresql1"
    monkeypatch.setattr(common, "pgsql_version_dir", lambda version: dest)
    monkeypatch.setattr(common, "pgsql_bin_dir", lambda version: dest / "bin")
    return DarwinDriver(), dest, tmp_path / ("package" + request.param)


def package(path, extra=None, missing=False):
    names = ["pgsql/README" if missing else "pgsql/bin/psql", "pgsql/doc/readme"]
    if extra:
        names.append(extra)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path, "w") as archive:
            for name in names:
                member = zipfile.ZipInfo(name)
                member.create_system = 3
                member.external_attr = (stat.S_IFREG | (0o6755 if name.endswith("/psql") else 0o644)) << 16
                archive.writestr(member, b"inert")
    else:
        with tarfile.open(path, "w:gz") as archive:
            for name in names:
                member = tarfile.TarInfo(name)
                member.size, member.mode = 5, 0o6755 if name.endswith("/psql") else 0o644
                archive.addfile(member, io.BytesIO(b"inert"))


def test_valid_format_publishes_complete_layout(install):
    driver, dest, source = install
    package(source)
    assert driver.install_tarball("1", source) == str(dest / "bin")
    assert (dest / "bin/psql").read_bytes() == b"inert"
    assert (dest / "doc/readme").read_bytes() == b"inert"
    if os.name == "posix":
        assert stat.S_IMODE((dest / "bin/psql").stat().st_mode) == 0o755
        assert stat.S_IMODE((dest / "doc/readme").stat().st_mode) == 0o644


def test_archive_content_selects_parser_with_misleading_download_suffix(install):
    driver, dest, source = install
    package(source)
    renamed = source.with_name("download.tar.gz" if source.suffix == ".zip" else "download.zip")
    source.rename(renamed)
    assert driver.install_tarball("1", renamed) == str(dest / "bin")
    assert (dest / "bin/psql").read_bytes() == b"inert"


@pytest.mark.parametrize("extra", ["pgsql/../../outside", "pgsql/..\\outside", "C:/outside", "other/bin/psql"])
def test_bad_path_prevents_publication(install, extra):
    driver, dest, source = install
    package(source, extra)
    with pytest.raises(InstallError):
        driver.install_tarball("1", source)
    assert not dest.exists() and not list(dest.parent.glob(".opskit-pgsql-*"))
    assert not (dest.parent / "outside").exists()


def test_missing_psql_not_published(install):
    driver, dest, source = install
    package(source, missing=True)
    with pytest.raises(InstallError):
        driver.install_tarball("1", source)
    assert not dest.exists() and not list(dest.parent.glob(".opskit-pgsql-*"))


def test_existing_database_retained(install):
    driver, dest, source = install
    package(source)
    dest.mkdir()
    (dest / "database").write_bytes(b"keep")
    with pytest.raises(InstallError, match="refusing to overwrite"):
        driver.install_tarball("1", source)
    assert (dest / "database").read_bytes() == b"keep"


@pytest.mark.parametrize("failure", [PermissionError, KeyboardInterrupt])
def test_failure_or_cancel_cleans_owned_staging(install, monkeypatch, failure):
    driver, dest, source = install
    package(source)

    def reject(*args, **kwargs):
        raise failure("injected")

    monkeypatch.setattr("software._shared.archive_install.publish_new_directory", reject)
    with pytest.raises(KeyboardInterrupt if failure is KeyboardInterrupt else InstallError):
        driver.install_tarball("1", source)
    assert not dest.exists() and not list(dest.parent.glob(".opskit-pgsql-*"))
