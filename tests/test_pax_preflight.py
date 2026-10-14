"""Reject excessive raw PAX records before stdlib creates/decodes tuples."""
import gzip
import io
import tarfile

import pytest

from core import archive_reader as reader


def record(key=b"comment", value=b"value"):
    content = b" " + key + b"=" + value + b"\n"
    length = len(content) + 1
    while True:
        result = str(length).encode() + content
        if len(result) == length:
            return result
        length = len(result)


def packet(payload, kind=tarfile.XHDTYPE):
    header = tarfile.TarInfo("metadata")
    header.type, header.size = kind, len(payload)
    member = tarfile.TarInfo("root/tool")
    return (header.tobuf() + payload + b"\0" * (-len(payload) % 512)
            + member.tobuf() + b"\0" * 1024)


@pytest.mark.parametrize("kind", [tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE])
@pytest.mark.parametrize("compressed", [False, True])
def test_duplicate_records_rejected_before_stdlib_parser(monkeypatch, kind, compressed):
    payload = record() * 10000
    assert len(payload) < reader.MAX_TAR_HEADER_BYTES
    called = []
    original = tarfile.TarInfo._proc_pax

    def observe(self, archive):
        called.append(True)
        return original(self, archive)

    monkeypatch.setattr(tarfile.TarInfo, "_proc_pax", observe)
    data = packet(payload, kind)
    with pytest.raises(ValueError, match="PAX record"):
        with reader.open_tar(fileobj=io.BytesIO(gzip.compress(data) if compressed else data)):
            pytest.fail("duplicate PAX records bypassed unique-key quota")
    assert not called, "stdlib parsed raw fields before rejecting their count"


@pytest.mark.parametrize("payload", [
    b"9" * 10000 + b" x=v\n",
    b"5 x=\ntrailing",
    b"99 x=v\n",
    b"7 x=v!",
    b"6 =v\n",
    b"0 x=v\n",
    b"\0hidden",
], ids=["long-length", "trailing", "oversized", "newline", "empty-key", "zero-length", "hidden-padding"])
def test_malformed_record_fails_before_stdlib_parser(monkeypatch, payload):
    def forbidden(*args):
        pytest.fail("malformed framing reached stdlib PAX parser")

    monkeypatch.setattr(tarfile.TarInfo, "_proc_pax", forbidden)
    with pytest.raises(ValueError, match="PAX record"):
        with reader.open_tar(fileobj=io.BytesIO(packet(payload))):
            pytest.fail("malformed PAX accepted")


def test_exact_field_limit_preserves_duplicate_last_value_and_unicode():
    payload = record(b"comment", b"first") * (reader.MAX_PAX_FIELDS - 1)
    payload += record(b"comment", "final-中文".encode())
    with reader.open_tar(fileobj=io.BytesIO(packet(payload))) as archive:
        member = archive.getmembers()[0]
    assert member.pax_headers == {"comment": "final-中文"}


def test_binary_charset_paths_remain_compatible():
    payload = record(b"hdrcharset", b"BINARY") + record(b"path", b"root/\xff-tool")
    with reader.open_tar(fileobj=io.BytesIO(packet(payload))) as archive:
        assert archive.getmembers()[0].name == "root/\udcff-tool"


def test_short_pax_payload_is_rejected():
    header = tarfile.TarInfo("metadata")
    header.type, header.size = tarfile.XHDTYPE, 512
    with pytest.raises(ValueError, match="PAX record"):
        with reader.open_tar(fileobj=io.BytesIO(header.tobuf() + b"7 x=v\n")):
            pytest.fail("truncated PAX accepted")


def test_plain_member_names_consume_metadata_budget_during_enumeration():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as package:
        for index in range(10):
            package.addfile(tarfile.TarInfo("root/" + str(index) + "a" * 80))
    with reader.open_tar(fileobj=io.BytesIO(output.getvalue()), max_metadata_bytes=500) as package:
        with pytest.raises(ValueError, match="inventory"):
            package.getmembers()
        assert len(package.members) == 3
