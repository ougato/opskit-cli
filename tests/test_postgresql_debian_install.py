"""Actual PostgreSQL Debian orchestration uses synthetic packages and no network/service."""
import io
import tarfile
from types import SimpleNamespace

import pytest

from software.base import InstallError
from test_debian_container import member


def deb(names):
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w:gz") as archive:
        for name in names:
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = 5, 0o755
            archive.addfile(entry, io.BytesIO(b"inert"))
    return (b"!<arch>\n" + member("debian-binary/", b"2.0\n")
            + member("control.tar.gz/", b"inert metadata" * 1200)
            + member("data.tar.gz/", content.getvalue()))


@pytest.fixture
def install(tmp_path, monkeypatch):
    from software.recipes.postgresql import common
    responses, calls = {}, []
    monkeypatch.setattr(common, "_pgdg_query_deb_paths", lambda *args: {"server": "server.deb"})

    def get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status_code=200, content=responses[url.rsplit("/", 1)[-1]])

    monkeypatch.setattr("httpx.get", get)
    return common, tmp_path / "postgresql17.1", responses, calls


def test_selected_binary_and_library_publish_together(install):
    common, dest, responses, _ = install
    responses["server.deb"] = deb(["./usr/lib/postgresql/17/bin/psql", "usr/lib/x86_64-linux-gnu/libpq.so.5.17", "usr/share/doc/ignore"])
    assert common.download_pgsql_deb_linux("17.1", dest) == dest / "bin"
    assert (dest / "bin/psql").read_bytes() == b"inert"
    assert (dest / "lib/libpq.so.5.17").read_bytes() == b"inert"
    assert not (dest / "usr").exists()
    assert not list(dest.parent.glob(".opskit-pgsql-deb-*"))


@pytest.mark.parametrize("extra", ["../usr/lib/postgresql/17/bin/evil", "usr/lib/postgresql/17/bin/../../escape", "/usr/lib/postgresql/17/bin/evil"])
def test_original_path_attack_leaves_no_version(install, extra):
    common, dest, responses, _ = install
    responses["server.deb"] = deb(["usr/lib/postgresql/17/bin/psql", extra])
    with pytest.raises(InstallError):
        common.download_pgsql_deb_linux("17.1", dest)
    assert not dest.exists() and not list(dest.parent.glob(".opskit-pgsql-deb-*"))


def test_existing_database_rejected_before_network(install):
    common, dest, _, calls = install
    dest.mkdir()
    (dest / "database").write_bytes(b"keep")
    with pytest.raises(InstallError, match="refusing to overwrite"):
        common.download_pgsql_deb_linux("17.1", dest)
    assert not calls and (dest / "database").read_bytes() == b"keep"


def test_later_package_collision_rolls_back_owned_preparation(install, monkeypatch):
    common, dest, responses, _ = install
    monkeypatch.setattr(common, "_pgdg_query_deb_paths", lambda *args: {"server": "server.deb", "client": "client.deb"})
    responses["server.deb"] = responses["client.deb"] = deb(["usr/lib/postgresql/17/bin/psql"])
    with pytest.raises(InstallError):
        common.download_pgsql_deb_linux("17.1", dest)
    assert not dest.exists() and not list(dest.parent.glob(".opskit-pgsql-deb-*"))


def test_cancel_before_publish_cleans_owned_files(install, monkeypatch):
    common, dest, responses, _ = install
    responses["server.deb"] = deb(["usr/lib/postgresql/17/bin/psql"])

    def reject(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr("software._shared.archive_install.publish_new_directory", reject)
    with pytest.raises(KeyboardInterrupt):
        common.download_pgsql_deb_linux("17.1", dest)
    assert not dest.exists() and not list(dest.parent.glob(".opskit-pgsql-deb-*"))
