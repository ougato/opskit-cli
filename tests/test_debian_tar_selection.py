"""Mapped Debian paths remain safe before and after selection, with bounded expansion."""
import bz2
import gzip
import io
import lzma
import tarfile
import zstandard

import pytest

from core.archive import extract_tar_files
from core.debian import open_data_tar


def package(names):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        for name in names:
            member = tarfile.TarInfo(name)
            member.size, member.mode = 5, 0o755
            archive.addfile(member, io.BytesIO(b"inert"))
    return data.getvalue()


def select(name, member):
    return name.rsplit("/", 1)[-1] if member.isfile() and name.startswith("usr/bin/") else None


@pytest.mark.parametrize("suffix,compress", [(".tar", lambda data: data), (".tar.gz", gzip.compress), (".tar.xz", lzma.compress), (".tar.bz2", bz2.compress), (".tar.zst", zstandard.ZstdCompressor().compress)])
def test_stream_expansion_and_allowlist_mapping(tmp_path, suffix, compress):
    source = tmp_path / ("data" + suffix)
    source.write_bytes(compress(package(["./usr/bin/psql", "usr/share/doc/not-selected"])))
    dest = tmp_path / "bin"
    with open_data_tar(source) as archive:
        extract_tar_files(archive, dest, select)
    assert (dest / "psql").read_bytes() == b"inert"
    assert list(dest.iterdir()) == [dest / "psql"]
    assert not list(tmp_path.glob(".opskit-deb-tar-*"))


@pytest.mark.parametrize("extra", ["../usr/bin/evil", "usr/bin/../../escape", "/usr/bin/evil", "C:/evil", "usr/bin/psql", "./usr/bin/psql"])
def test_original_paths_and_duplicate_names_not_hidden_by_mapping(tmp_path, extra):
    source = tmp_path / "data.tar"
    source.write_bytes(package(["usr/bin/psql", extra]))
    dest = tmp_path / "bin"
    with open_data_tar(source) as archive, pytest.raises(ValueError):
        extract_tar_files(archive, dest, select)
    assert not dest.exists()


def test_distinct_sources_cannot_collide_after_mapping(tmp_path):
    source = tmp_path / "data.tar"
    source.write_bytes(package(["usr/bin/psql", "usr/bin/nested/psql"]))
    with open_data_tar(source) as archive, pytest.raises(ValueError):
        extract_tar_files(archive, tmp_path / "bin", select)
    assert not (tmp_path / "bin").exists()


def test_existing_output_preserved(tmp_path):
    source = tmp_path / "data.tar"
    source.write_bytes(package(["usr/bin/psql"]))
    dest = tmp_path / "bin"
    dest.mkdir()
    (dest / "psql").write_bytes(b"keep")
    with open_data_tar(source) as archive, pytest.raises(ValueError):
        extract_tar_files(archive, dest, select)
    assert (dest / "psql").read_bytes() == b"keep"


def test_expansion_and_payload_limits_are_separate(tmp_path, monkeypatch):
    from core import debian, archive as archive_module
    source = tmp_path / "data.tar.gz"
    source.write_bytes(gzip.compress(package(["usr/bin/psql"])))
    monkeypatch.setattr(debian, "MAX_EXPANDED_TAR_BYTES", 10)
    with pytest.raises(ValueError, match="expanded"):
        with open_data_tar(source):
            pytest.fail("should not yield oversized tar")
    assert not list(tmp_path.glob(".opskit-deb-tar-*"))
    monkeypatch.setattr(debian, "MAX_EXPANDED_TAR_BYTES", 100000)
    monkeypatch.setattr(archive_module, "MAX_EXTRACT_BYTES", 1)
    with open_data_tar(source) as archive, pytest.raises(ValueError):
        extract_tar_files(archive, tmp_path / "bin", select)
    assert not (tmp_path / "bin").exists()


def test_cancel_cleans_expanded_tar(tmp_path):
    source = tmp_path / "data.tar"
    source.write_bytes(package(["usr/bin/psql"]))
    with pytest.raises(KeyboardInterrupt):
        with open_data_tar(source):
            raise KeyboardInterrupt()
    assert not list(tmp_path.glob(".opskit-deb-tar-*"))


def test_xz_dictionary_limit_and_truncation(tmp_path, monkeypatch):
    from core import debian
    source = tmp_path / "data.tar.xz"
    compressed = lzma.compress(package(["usr/bin/psql"]))
    source.write_bytes(compressed)
    monkeypatch.setattr(debian, "MAX_LZMA_MEMORY_BYTES", 1024 * 1024)
    with pytest.raises(lzma.LZMAError):
        with open_data_tar(source):
            pytest.fail("oversized dictionary accepted")
    assert not list(tmp_path.glob(".opskit-deb-tar-*"))
    monkeypatch.setattr(debian, "MAX_LZMA_MEMORY_BYTES", 128 * 1024**2)
    source.write_bytes(compressed[:-5])
    with pytest.raises(ValueError, match="truncated"):
        with open_data_tar(source):
            pytest.fail("truncated stream accepted")
    assert not list(tmp_path.glob(".opskit-deb-tar-*"))


@pytest.mark.parametrize("trailer,valid", [(b"\0" * 4, True), (b"\0", False), (b"not padding", False)])
def test_xz_trailing_data_policy(tmp_path, trailer, valid):
    source = tmp_path / "data.tar.xz"
    source.write_bytes(lzma.compress(package(["usr/bin/psql"])) + trailer)
    if valid:
        with open_data_tar(source) as archive:
            extract_tar_files(archive, tmp_path / "bin", select)
        assert (tmp_path / "bin/psql").read_bytes() == b"inert"
    else:
        with pytest.raises(ValueError):
            with open_data_tar(source):
                pytest.fail("invalid trailing bytes accepted")
