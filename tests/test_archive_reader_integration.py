"""Oversized metadata reaches real consumers without any system installation."""
import base64
import gzip
import importlib
import io
import struct
import tarfile
import zipfile

import pytest

from software.base import InstallError


def giant_header():
    member = tarfile.TarInfo("metadata")
    member.type, member.size = tarfile.XHDTYPE, 64 * 1024**2
    return member.tobuf()


@pytest.mark.parametrize("name,platform,path_name,bin_name,method", [
    ("nodejs", "linux", "node_version_dir", "node_bin_dir", "install_tarball"),
    ("java", "linux", "java_version_dir", "java_bin_dir", "install_tarball"),
    ("golang", "linux", "go_version_dir", "go_bin_dir", "install_tarball"),
    ("mongodb", "linux", "mongo_version_dir", "mongo_bin_dir", "install_tarball"),
    ("mysql", "linux", "mysql_version_dir", "mysql_bin_dir", "install_tarball"),
    ("mysql", "darwin", "mysql_version_dir", "mysql_bin_dir", "install_tarball"),
    ("postgresql", "darwin", "pgsql_version_dir", "pgsql_bin_dir", "install_tarball"),
    ("redis", "linux", "redis_version_dir", "redis_bin_dir", "install_binary"),
    ("redis", "darwin", "redis_version_dir", "redis_bin_dir", "install_binary"),
])
def test_driver_rejects_metadata_before_publication_or_build(tmp_path, monkeypatch, name,
                                                            platform, path_name, bin_name, method):
    common = importlib.import_module(f"software.recipes.{name}.common")
    module = importlib.import_module(f"software.recipes.{name}.{platform}")
    dest = tmp_path / "version"
    monkeypatch.setattr(common, path_name, lambda version: dest)
    monkeypatch.setattr(common, bin_name, lambda version: dest / "bin")
    monkeypatch.setattr("software.recipes.mysql.linux._ensure_linux_deps",
                        lambda root: pytest.fail("invalid metadata must not install system dependencies"))
    monkeypatch.setattr("software.recipes.redis.linux.shutil.which", lambda name: name)
    monkeypatch.setattr("core.process.capture",
                        lambda *args, **kwargs: pytest.fail("invalid metadata must not start a build"))
    source = tmp_path / "source.tar.gz"
    source.write_bytes(gzip.compress(giant_header()))
    driver = module.LinuxDriver() if platform == "linux" else module.DarwinDriver()
    with pytest.raises(InstallError):
        getattr(driver, method)("1", source)
    assert not dest.exists() and not list(tmp_path.glob(".opskit-*"))


def test_clickhouse_rejects_metadata_before_moving_binary(tmp_path):
    from software.recipes.clickhouse.common import extract_clickhouse_tarball
    source = tmp_path / "source.tar.gz"
    source.write_bytes(gzip.compress(giant_header()))
    dest = tmp_path / "version"
    with pytest.raises(InstallError):
        extract_clickhouse_tarball(source, dest, "1")
    assert not dest.exists() and not list(tmp_path.glob(".opskit-*"))


def test_plugin_state_metadata_cannot_replace_existing_state(tmp_path):
    from core.plugin_sandbox import _save_state
    root = tmp_path / "state"
    root.mkdir()
    (root / "old").write_bytes(b"previous state")
    with pytest.raises(ValueError):
        _save_state(base64.b64encode(giant_header()).decode(), root, plugin="owner", caller="platform")
    assert (root / "old").read_bytes() == b"previous state"
    assert not root.with_name("state.previous").exists()
    assert not list(tmp_path.glob(".state-*"))


def test_plugin_file_quota_is_applied_during_inventory_parse(tmp_path):
    from core.plugin_sandbox import _save_state, MAX_FILES
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for index in range(MAX_FILES + 1):
            archive.addfile(tarfile.TarInfo("file" + str(index)))
    root = tmp_path / "state"
    root.mkdir()
    (root / "old").write_bytes(b"previous state")
    with pytest.raises(ValueError, match="inventory"):
        _save_state(base64.b64encode(output.getvalue()).decode(), root, plugin="owner", caller="platform")
    assert (root / "old").read_bytes() == b"previous state"
    assert not list(tmp_path.glob(".state-*"))


def test_cache_validation_and_rustdesk_refuse_giant_zip_directory(tmp_path, monkeypatch):
    from core.mirror import _is_zip_valid
    from software.recipes.rustdesk import server
    source = tmp_path / "source.zip"
    source.write_bytes(struct.pack(zipfile.structEndArchive, zipfile.stringEndArchive,
                                  0, 0, 1, 1, 64 * 1024**2, 0, 0))
    assert not _is_zip_valid(source)
    monkeypatch.setattr(server, "run_root",
                        lambda *args, **kwargs: pytest.fail("invalid ZIP must not mutate system paths"))
    with pytest.raises(InstallError):
        server.install_archive(source)
