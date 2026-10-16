"""Guest hashes never authorize unsafe tar members or inconsistent inventories."""
import hashlib
from contextlib import contextmanager
import io
import json
import tarfile

import pytest

from core import plugin_fetch_candidate as candidate


def archive(entries):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, body, changes in entries:
            item = tarfile.TarInfo(name)
            item.mode, item.size = 0o644, len(body)
            for key, value in changes.items():
                setattr(item, key, value)
            tar.addfile(item, io.BytesIO(body) if item.isfile() else None)
    return out.getvalue()


def response(raw, entries=1, size=3, **changes):
    header = {"schema_version": 1, "commit": "a" * 40, "entries": entries, "bytes": size,
              "archive_bytes": len(raw), "archive_sha256": hashlib.sha256(raw).hexdigest()}
    header.update(changes)
    return json.dumps(header).encode() + b"\n" + raw


def test_valid_raw_unicode_and_executable_inventory():
    raw = archive([("src/猫.py", b"raw", {"mode": 0o755}), (".gitattributes", b"filter=demo", {})])
    result = candidate.validate_output(io.BytesIO(response(raw, entries=2, size=14)))
    assert result.files == (candidate.CandidateFile("src/猫.py", 0o755, 3, hashlib.sha256(b"raw").hexdigest()),
                            candidate.CandidateFile(".gitattributes", 0o644, 11, hashlib.sha256(b"filter=demo").hexdigest()))
    assert result.reported_commit == "a" * 40 and result.archive_sha256 == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a\\b", "a:stream", ".git/config", "NUL.txt",
                                  "a.", "a ", "a//b", "git~1/config", "CONIN$", "a\u200c", "a\nlog"])
def test_unsafe_paths_rejected_even_with_matching_guest_hash(name):
    with pytest.raises(ValueError):
        candidate.validate_output(io.BytesIO(response(archive([(name, b"raw", {})]))))


@pytest.mark.parametrize("changes", [{"type": tarfile.SYMTYPE, "linkname": "../outside", "size": 0},
                                    {"type": tarfile.LNKTYPE, "linkname": "elsewhere", "size": 0},
                                    {"type": tarfile.DIRTYPE, "size": 0}, {"type": tarfile.FIFOTYPE, "size": 0},
                                    {"mode": 0o4644}, {"uid": 1}, {"gname": "user"}, {"mtime": 1},
                                    {"pax_headers": {"comment": "untrusted"}}])
def test_unexpected_member_properties_refused(changes):
    with pytest.raises(ValueError):
        candidate.validate_output(io.BytesIO(response(archive([("file", b"raw", changes)]))))


@pytest.mark.parametrize("names", [("a", "A"), ("a", "a/b"), ("a/b", "a"), ("A/x", "a/y"), ("é", "e\u0301")])
def test_duplicate_or_cross_host_collisions_refused(names):
    raw = archive([(name, b"raw", {}) for name in names])
    with pytest.raises(ValueError):
        candidate.validate_output(io.BytesIO(response(raw, entries=2, size=6)))


@pytest.mark.parametrize("changes", [{"schema_version": True}, {"entries": True}, {"bytes": -1}, {"entries": 2},
                                    {"bytes": 2}, {"archive_sha256": "b" * 64}, {"commit": "bad"},
                                    {"archive_bytes": 1}, {"extra": "field"}])
def test_inconsistent_metadata_refused(changes):
    with pytest.raises(ValueError):
        candidate.validate_output(io.BytesIO(response(archive([("file", b"raw", {})]), **changes)))


@pytest.mark.parametrize("change", ["truncated", "extra", "hidden_tar", "header", "duplicate"])
def test_framing_and_hidden_archive_content_refused(change):
    raw = archive([("file", b"raw", {})]);wire = response(raw)
    if change == "truncated":
        wire = wire[:-1]
    elif change == "extra":
        wire += b"x"
    elif change == "hidden_tar":
        wire = response(raw + archive([("hidden", b"raw", {})]))
    elif change == "header":
        wire = b"x" * 1025 + b"\n" + raw
    else:
        wire = wire.replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1')
    with pytest.raises(ValueError):
        candidate.validate_output(io.BytesIO(wire))


@pytest.mark.parametrize("limit", ["MAX_ENTRIES", "MAX_FILE_BYTES", "MAX_TREE_BYTES"])
def test_host_budgets_independent_of_guest(monkeypatch, limit):
    monkeypatch.setattr(candidate, limit, 1)
    raw = archive([("a/b/file", b"raw", {})])
    with pytest.raises(ValueError):
        candidate.validate_output(io.BytesIO(response(raw)))


def test_verified_archive_does_not_reread_changed_source():
    raw = archive([("file", b"raw", {})])
    source = io.BytesIO(response(raw))
    with candidate.validated_archive(source) as (inventory, protected):
        source.seek(0)
        source.write(response(archive([("file", b"bad", {})])))
        source.close()
        assert inventory.files[0].sha256 == hashlib.sha256(b"raw").hexdigest()
        assert protected.tell() == 0
        assert protected.read() == raw
        protected.seek(0)
        with tarfile.open(fileobj=protected, mode="r:") as contents:
            assert contents.extractfile("file").read() == b"raw"
    assert protected.closed


def test_verified_archive_closes_when_consumer_fails():
    raw = archive([("file", b"raw", {})])
    with pytest.raises(RuntimeError, match="writer failed"):
        with candidate.validated_archive(io.BytesIO(response(raw))) as (_, protected):
            raise RuntimeError("writer failed")
    assert protected.closed


def test_verified_archive_never_yields_invalid_input_and_closes_snapshot(monkeypatch):
    snapshots = []
    original = candidate.temporary_private_file

    @contextmanager
    def track():
        with original() as stream:
            snapshots.append(stream)
            yield stream

    monkeypatch.setattr(candidate, "temporary_private_file", track)
    raw = archive([("../outside", b"raw", {})])
    with pytest.raises(ValueError):
        with candidate.validated_archive(io.BytesIO(response(raw))):
            pytest.fail("invalid archive escaped validation")
    assert snapshots and all(stream.closed for stream in snapshots)
