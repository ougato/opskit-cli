"""Candidate integrity remains mandatory without release signing keys."""
import pytest

from core import updater
from core.file_digest import file_digest


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    identity = "a" * 32
    build = updater.APP_VERSION + 1
    updater._write_update_record(updater._get_cache_path(), {
        "candidate_id": identity, "pending_version": build})
    pending = updater._get_pending_path()
    pending.parent.mkdir(parents=True)
    pending.write_bytes(b"candidate bytes")
    receipt = {"schema_version": 1, "candidate_id": identity, "build": build,
               "sha256": file_digest(pending), "state": "verified"}
    updater._write_update_record(pending.parent / "candidate.json", receipt)
    return pending, build, receipt


def test_candidate_integrity_accepts_matching_receipt_and_rejects_changed_bytes(candidate):
    pending, build, _ = candidate
    assert updater._verify_pending_checksum(pending, build)
    pending.write_bytes(b"tampered candidate")
    assert not updater._verify_pending_checksum(pending, build)


@pytest.mark.parametrize("field,value", [
    ("build", True), ("build", 0), ("candidate_id", "b" * 32),
    ("state", "preparing"), ("sha256", "invalid"), ("sha256", "0" * 64),
    ("schema_version", True), ("schema_version", 2),
])
def test_candidate_integrity_refuses_invalid_receipt(candidate, field, value):
    pending, build, receipt = candidate
    updater._write_update_record(pending.parent / "candidate.json", {**receipt, field: value})
    assert not updater._verify_pending_checksum(pending, build)


def test_candidate_integrity_refuses_missing_receipt_and_wrong_requested_version(candidate):
    pending, build, _ = candidate
    assert not updater._verify_pending_checksum(pending, build + 1)
    assert not updater._verify_pending_checksum(pending, updater.APP_VERSION)
    (pending.parent / "candidate.json").unlink()
    assert not updater._verify_pending_checksum(pending, build)
