"""Untrusted Git framing is bounded before isolated object parsing."""
import io

import pytest

from core.native_git_protocol import advertisement, packet, receive_pack, read_advertisement


OID = b"a" * 40


def advertised(caps=b"shallow ofs-delta no-progress", refs=None):
    refs = refs or [(OID, b"HEAD"), (OID, b"refs/heads/main")]
    return (packet(b"# service=git-upload-pack\n") + b"0000"
            + b"".join(packet(oid + b" " + name + (b"\0" + caps if index == 0 else b"") + b"\n")
                       for index, (oid, name) in enumerate(refs)) + b"0000")


def test_exact_ref_selection_and_minimal_request():
    selection, request = advertisement(advertised(), "main")
    assert selection == {"commit": OID.decode(), "shallow": True}
    assert b"deepen 1\n" in request
    assert b"side-band" not in request and b"thin-pack" not in request
    with pytest.raises(ValueError, match="unavailable"):
        advertisement(advertised(), "missing")


def test_ssh_advertisement_stops_at_flush_without_consuming_pack():
    raw = packet(OID + b" HEAD\0shallow\n") + b"0000"
    stream = io.BytesIO(raw + b"PACK")
    assert read_advertisement(stream) == raw
    assert stream.read() == b"PACK"
    assert advertisement(raw, http=False)[0]["commit"] == OID.decode()


@pytest.mark.parametrize("raw", [b"zzzz", b"0001", b"0004", b"0008xx", advertised() + b"extra",
    advertised(refs=[(OID, b"HEAD"), (OID, b"HEAD")]),
    advertised(caps=b"object-format=sha256"),
    advertised(refs=[(b"0" * 40, b"HEAD")])])
def test_invalid_advertisements_rejected(raw):
    with pytest.raises(ValueError):
        advertisement(raw)


def test_raw_pack_transport_does_not_parse_objects():
    # Deliberately not a valid pack: only the isolated Git worker may parse it.
    pack = b"PACK" + b"untrusted-compressed-object-data" * 2
    stream = io.BytesIO(packet(b"shallow " + OID + b"\n") + b"0000" + packet(b"NAK\n") + pack)
    output = io.BytesIO()
    result = receive_pack(stream, output, {"commit": OID.decode(), "shallow": True})
    assert output.getvalue() == pack
    assert result["bytes"] == len(pack)
    assert result["shallow"] == [OID.decode()]


@pytest.mark.parametrize("prefix", [b"", b"0000" * 5, packet(b"ACK " + OID + b"\n"),
    packet(b"shallow " + b"b" * 64 + b"\n"), packet(b"NAK\n") + b"0000"])
def test_invalid_pack_negotiation_rejected(prefix):
    with pytest.raises(ValueError):
        receive_pack(io.BytesIO(prefix + b"PACK" + b"x" * 40), io.BytesIO(),
                     {"commit": OID.decode(), "shallow": True})


def test_pack_budget_enforced(monkeypatch):
    monkeypatch.setattr("core.native_git_protocol.MAX_PACK", 40)
    with pytest.raises(ValueError, match="size limit"):
        receive_pack(io.BytesIO(packet(b"NAK\n") + b"PACK" + b"x" * 40), io.BytesIO(),
                     {"commit": OID.decode(), "shallow": False})
