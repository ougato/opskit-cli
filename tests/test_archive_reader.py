"""Hostile metadata must be bounded before stdlib requests its payload."""
import bz2
import gzip
import io
import lzma
import struct
import tarfile
import zipfile

import pytest

from core import archive_reader as reader
from core.archive import extract_tar, extract_zip


class GuardedBytes(io.BytesIO):
    def __init__(self, data, max_read=1024 * 1024):
        super().__init__(data)
        self.max_read = max_read
        self.largest = 0

    def read(self, size=-1):
        self.largest = max(self.largest, size)
        assert 0 <= size <= self.max_read, "stdlib requested an unbounded metadata allocation"
        return super().read(size)


def tar_bytes(names=("root/bin/tool",), *, format=tarfile.PAX_FORMAT):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=format) as archive:
        for name in names:
            member = tarfile.TarInfo(name)
            member.size, member.mode = 5, 0o755
            archive.addfile(member, io.BytesIO(b"inert"))
    return buffer.getvalue()


@pytest.mark.parametrize("kind", [tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
                                  tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK])
@pytest.mark.parametrize("compressed", [False, True])
def test_giant_extension_fails_before_requested_read(kind, compressed):
    member = tarfile.TarInfo("metadata")
    member.type, member.size = kind, 64 * 1024**2
    source = GuardedBytes(gzip.compress(member.tobuf()) if compressed else member.tobuf())
    with pytest.raises(ValueError, match="extension header exceeds limits"):
        with reader.open_tar(fileobj=source):
            pytest.fail("oversized metadata yielded an archive")
    assert source.largest <= reader.CHUNK_BYTES
    assert not source.closed, "borrowed file must remain owned by the caller"


def test_extension_chain_is_bounded_before_recursion_overflow():
    member = tarfile.TarInfo("metadata")
    member.type, member.size = tarfile.XHDTYPE, 0
    source = GuardedBytes(member.tobuf() * 100 + tar_bytes())
    with pytest.raises(ValueError, match="extension nesting"):
        with reader.open_tar(fileobj=source):
            pytest.fail("recursive header chain accepted")
    assert source.tell() <= (reader.MAX_TAR_HEADER_DEPTH + 1) * 512


@pytest.mark.parametrize("format", [tarfile.PAX_FORMAT, tarfile.GNU_FORMAT])
@pytest.mark.parametrize("compression", ["raw", "gzip", "xz", "bzip2"])
def test_normal_long_unicode_paths_preserve_layout(tmp_path, format, compression):
    name = "root/" + "nested/" * 20 + "\u00de-tool"
    data = tar_bytes([name], format=format)
    compress = {"raw": lambda value: value, "gzip": gzip.compress,
                "xz": lzma.compress, "bzip2": bz2.compress}[compression]
    source = io.BytesIO(compress(data))
    with reader.open_tar(fileobj=source) as archive:
        extract_tar(archive, tmp_path / "dest", strip_components=1)
    assert (tmp_path / "dest" / name.removeprefix("root/")).read_bytes() == b"inert"
    assert not source.closed


def test_members_stop_at_the_quota_before_loading_the_whole_inventory(monkeypatch):
    source = GuardedBytes(tar_bytes([f"root/tool{i}" for i in range(20)]))
    monkeypatch.setattr(reader, "MAX_FILES", 2)
    with reader.open_tar(fileobj=source) as archive:
        with pytest.raises(ValueError, match="inventory"):
            archive.getmembers()
        assert len(archive.members) == 3
    assert source.tell() < 4096


def test_pax_overridden_size_is_checked_before_seeking_its_payload():
    member = tarfile.TarInfo("root/tool")
    member.size = 0
    member.pax_headers = {"size": str(reader.MAX_PAYLOAD_BYTES + 1)}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        archive.addfile(member)
    source = GuardedBytes(output.getvalue())
    with pytest.raises(ValueError, match="payload"):
        with reader.open_tar(fileobj=source):
            pytest.fail("PAX size bypassed the payload quota")
    assert source.largest <= 1024


@pytest.mark.parametrize("headers", [
    {"GNU.sparse.map": "0,1"},
    {"GNU.sparse.major": "1", "GNU.sparse.minor": "0"},
    {f"xattr{i}": "value" for i in range(129)},
])
def test_sparse_or_excessive_pax_fields_rejected_before_expansion(headers):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo("root/tool")
        member.size, member.pax_headers = 5, headers
        archive.addfile(member, io.BytesIO(b"inert"))
    with pytest.raises(ValueError):
        with reader.open_tar(fileobj=GuardedBytes(output.getvalue())):
            pytest.fail("unsafe extension accepted")


def test_global_pax_metadata_does_not_accumulate_unbounded_member_copies(monkeypatch):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", pax_headers={"comment": "global"}) as archive:
        for i in range(5):
            member = tarfile.TarInfo("root/tool" + str(i))
            archive.addfile(member)
    monkeypatch.setattr(reader, "MAX_METADATA_BYTES", 400)
    with reader.open_tar(fileobj=io.BytesIO(output.getvalue())) as archive:
        with pytest.raises(ValueError, match="inventory"):
            archive.getmembers()
        assert len(archive.members) <= 3


def test_compression_trailer_is_verified_before_context_completes(tmp_path):
    data = bytearray(gzip.compress(tar_bytes()))
    data[-8] ^= 1
    with pytest.raises((gzip.BadGzipFile, EOFError)):
        with reader.open_tar(fileobj=io.BytesIO(data)) as archive:
            extract_tar(archive, tmp_path / "dest")


def test_expanded_stream_is_bounded_even_for_unparsed_tar_padding(monkeypatch):
    monkeypatch.setattr(reader, "MAX_STREAM_BYTES", 10240)
    source = io.BytesIO(gzip.compress(tar_bytes() + b"\0" * 2048))
    with pytest.raises(ValueError, match="expanded archive"):
        with reader.open_tar(fileobj=source) as archive:
            archive.getmembers()


def test_xz_dictionary_is_limited_before_the_first_tar_header(monkeypatch):
    source = io.BytesIO(lzma.compress(tar_bytes()))
    monkeypatch.setattr(reader, "MAX_XZ_MEMORY_BYTES", 1024 * 1024)
    with pytest.raises(lzma.LZMAError):
        with reader.open_tar(fileobj=source):
            pytest.fail("unbounded XZ dictionary accepted")


def zip_bytes(names=("root/bin/tool",), *, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name in names:
            archive.writestr(name, b"inert")
    return output.getvalue()


def test_zip_giant_directory_fails_before_single_allocation():
    end = struct.pack(zipfile.structEndArchive, zipfile.stringEndArchive,
                      0, 0, 1, 1, 64 * 1024**2, 0, 0)
    source = GuardedBytes(end)
    with pytest.raises(ValueError, match="directory exceeds limits"):
        with reader.open_zip(fileobj=source):
            pytest.fail("giant central directory accepted")
    assert source.largest <= 65558


def test_declared_zip_count_cannot_hide_more_actual_entries(monkeypatch):
    data = bytearray(zip_bytes([f"root/tool{i}" for i in range(3)]))
    # Lie in both EOCD count fields, so only the streamed actual records catch it.
    struct.pack_into("<HH", data, len(data) - 22 + 8, 1, 1)
    monkeypatch.setattr(reader, "MAX_FILES", 2)
    with pytest.raises(ValueError, match="member count"):
        with reader.open_zip(fileobj=io.BytesIO(data)):
            pytest.fail("central directory count lie accepted")


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
@pytest.mark.parametrize("prefix", [b"", b"self-extracting-prefix"])
def test_normal_zip_and_prefixed_zip_preserve_payload(tmp_path, compression, prefix):
    source = io.BytesIO(prefix + zip_bytes(compression=compression))
    with reader.open_zip(fileobj=source) as archive:
        extract_zip(archive, tmp_path / "dest", strip_components=1)
    assert (tmp_path / "dest/bin/tool").read_bytes() == b"inert"
    assert not source.closed


@pytest.mark.parametrize("compression", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_unsupported_zip_decoder_is_rejected_before_payload_read(compression):
    with pytest.raises(ValueError, match="compression method"):
        with reader.open_zip(fileobj=io.BytesIO(zip_bytes(compression=compression))):
            pytest.fail("unbounded ZIP decoder accepted")


@pytest.mark.parametrize("prefix", [b"", b"self-extracting-prefix"])
def test_zip64_end_record_and_size_extensions_remain_supported(tmp_path, monkeypatch, prefix):
    with monkeypatch.context() as patch:
        patch.setattr(zipfile, "ZIP64_LIMIT", 1)
        data = prefix + zip_bytes()
    assert zipfile.stringEndArchive64 in data
    with reader.open_zip(fileobj=io.BytesIO(data)) as archive:
        extract_zip(archive, tmp_path / "dest", strip_components=1)
    assert (tmp_path / "dest/bin/tool").read_bytes() == b"inert"


def test_zip64_unknown_end_record_location_is_rejected(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(zipfile, "ZIP64_LIMIT", 1)
        data = zip_bytes()
    read_end = zipfile._EndRecData
    def corrupt_location(source):
        end = read_end(source)
        end[zipfile._ECD_LOCATION] = 0  # The local header is not either EOCD format.
        return end
    monkeypatch.setattr(zipfile, "_EndRecData", corrupt_location)
    with pytest.raises(zipfile.BadZipFile, match="end record location"):
        with reader.open_zip(fileobj=io.BytesIO(data)):
            pytest.fail("unknown ZIP64 end location accepted")


@pytest.mark.parametrize("opener", [reader.open_tar, reader.open_zip])
def test_openers_never_allow_write_or_append_modes(opener):
    with pytest.raises(ValueError, match="read-only"):
        with opener(fileobj=io.BytesIO(), mode="w"):
            pytest.fail("write mode was accepted")


@pytest.mark.parametrize("limit", [0, -1, True, 1.0, reader.MAX_FILES + 1])
def test_custom_parser_quota_cannot_expand_the_platform_policy(limit):
    with pytest.raises(ValueError, match="parser limit"):
        with reader.open_tar(fileobj=None, name="must-not-be-opened", max_files=limit):
            pytest.fail("invalid quota accepted")


def test_caller_can_apply_smaller_file_and_payload_quotas():
    with reader.open_tar(fileobj=io.BytesIO(tar_bytes(["root/one", "root/two"])),
                         max_files=1) as archive:
        with pytest.raises(ValueError, match="inventory"):
            archive.getmembers()
    with pytest.raises(ValueError, match="payload"):
        with reader.open_tar(fileobj=io.BytesIO(tar_bytes()), max_payload_bytes=4):
            pytest.fail("caller payload quota was ignored")
