"""Cached marker declarations must not authorize arbitrary filesystem probes."""
from pathlib import Path

import pytest

from core import updater


@pytest.fixture
def marker_context(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    pending = tmp_path / "cache" / "opskit.pending"
    pending.parent.mkdir()
    pending.write_bytes(b"known new program")
    target = tmp_path / "program.exe"
    target.write_bytes(b"known old program")
    monkeypatch.setattr(updater, "_get_pending_path", lambda: pending)
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    marker = pending.parent / "update_pending_path.json"
    return pending, target, marker


@pytest.mark.parametrize("declaration", ["//untrusted.invalid/share/artifact.exe", "../outside.exe", "C:/unrelated/artifact.exe"])
@pytest.mark.parametrize("field", ["pending", "target"])
def test_marker_refuses_unowned_path_before_exists_hash_or_resolve(marker_context, monkeypatch, declaration, field):
    pending, target, marker = marker_context
    foreign = Path(declaration)
    record = {"schema_version": 2, "pending": str(pending), "target": str(target),
              "version": updater.APP_VERSION + 1, "transaction_id": "a" * 32,
              "backup_id": "opskit.v7.1." + "d" * 32 + ".bak",
              "old_sha256": "b" * 64, "new_sha256": "c" * 64}
    record[field] = declaration
    updater._write_update_record(marker, record)
    probes = []
    original_exists = Path.exists
    original_record_path = updater._update_record_path
    def record_path(path):
        if path == foreign:
            probes.append("path metadata")
            raise ValueError("blocked test probe")
        return original_record_path(path)
    def exists(path):
        if path == foreign:
            probes.append("exists")
            return True  # Never access the declared UNC or unrelated file.
        return original_exists(path)
    def digest(path):
        if path == foreign:
            probes.append("hash")
            raise ValueError("blocked test probe")
        return "a" * 64
    monkeypatch.setattr(Path, "exists", exists)
    monkeypatch.setattr(updater, "_update_record_path", record_path)
    monkeypatch.setattr(updater, "_sha256_file", digest)
    assert updater._apply_update_pending_path() is False
    assert probes == []
    assert target.read_bytes() == b"known old program" and marker.exists()


def test_marker_without_bound_backup_and_transaction_does_not_replace_target(marker_context, monkeypatch):
    pending, target, marker = marker_context
    updater._write_update_record(marker, {"pending": str(pending), "target": str(target),
                                         "version": updater.APP_VERSION + 1})
    # A signed candidate alone does not prove recovery/backup ownership.
    monkeypatch.setattr(updater, "_verify_pending_signature", lambda *args: True)
    assert updater._apply_update_pending_path() is False
    assert target.read_bytes() == b"known old program"
    assert pending.exists() and marker.exists()


@pytest.fixture
def bound_marker(marker_context, monkeypatch):
    from update_marker_fixtures import bind_marker
    pending, target, marker = marker_context
    marker, backup, transaction = bind_marker(target, pending, updater.APP_VERSION + 1)
    monkeypatch.setattr(updater, "_verify_pending_signature", lambda *args: True)
    return pending, target, marker, backup, transaction


@pytest.mark.parametrize("authority", ["backup", "transaction", "target", "signature", "health"])
def test_bound_marker_refuses_changed_authority_without_replacement(bound_marker, monkeypatch, authority):
    from core import update_target_state
    pending, target, marker, backup, transaction = bound_marker
    if authority == "backup":
        backup.write_bytes(b"tampered backup")
    elif authority == "transaction":
        path = update_target_state.path(target)
        updater._write_update_record(path, {**transaction, "transaction_id": "a" * 32})
    elif authority == "target":
        target.write_bytes(b"unrelated target")
    elif authority == "signature":
        monkeypatch.setattr(updater, "_verify_pending_signature", lambda *args: False)
    else:
        updater._write_update_record(updater._get_health_path(), {"build": transaction["to_build"],
            "confirmed": False, "fails": 0, "backup_id": "other-backup"})
    before = target.read_bytes(), marker.read_bytes()
    assert updater._apply_update_pending_path() is False
    assert (target.read_bytes(), marker.read_bytes()) == before
    assert pending.exists() and backup.exists()


def test_committed_marker_retains_evidence_when_health_write_fails_and_retries(bound_marker, monkeypatch):
    from core import update_target_state
    pending, target, marker, backup, transaction = bound_marker
    expected = pending.read_bytes()
    write = updater._write_update_record
    with monkeypatch.context() as patch:
        def failed_health(path, body):
            if path == updater._get_health_path():
                raise OSError("owned health write failure")
            return write(path, body)
        patch.setattr(updater, "_write_update_record", failed_health)
        assert updater._apply_update_pending_path() is True
    assert target.read_bytes() == expected and pending.exists() and marker.exists()
    assert update_target_state.read(target)["transaction_id"] == transaction["transaction_id"]
    assert updater._apply_update_pending_path() is True
    assert not marker.exists() and not pending.exists()
    health = updater._read_health_record(updater._get_health_path())
    assert health["backup_id"] == backup.name and health["build"] == transaction["to_build"]
    assert health["confirmed"] is False


def test_new_runtime_repairs_committed_marker_without_restarting_or_resetting_health(bound_marker, monkeypatch):
    pending, target, marker, backup, transaction = bound_marker
    target.write_bytes(pending.read_bytes())
    pending.unlink()  # Recovery uses transaction/target proof, not file existence.
    record = {"build": transaction["to_build"], "confirmed": True, "fails": 2, "backup_id": backup.name}
    updater._write_update_record(updater._get_health_path(), record)
    monkeypatch.setattr(updater, "APP_VERSION", transaction["to_build"])
    assert updater._apply_update_pending_path() is False
    assert not marker.exists() and updater._read_health_record(updater._get_health_path()) == record


def test_post_update_retains_committed_evidence_until_health_publication(bound_marker, monkeypatch):
    import sys
    from core import update_target_state
    pending, target, marker, backup, transaction = bound_marker
    expected = pending.read_bytes()
    write = updater._write_update_record
    monkeypatch.setattr(updater, "_reconcile_indexed_job_on_boot", lambda: (False, False))
    with monkeypatch.context() as patch:
        def failed_health(path, body):
            if path == updater._get_health_path():
                raise OSError("owned health write failure")
            return write(path, body)
        patch.setattr(updater, "_write_update_record", failed_health)
        assert updater._apply_update_pending_path() is True
        monkeypatch.setattr(updater, "APP_VERSION", transaction["to_build"])
        monkeypatch.setattr(sys, "argv", ["opskit", "--post-update"])
        assert updater.check_and_apply_pending() is False
        assert target.read_bytes() == expected
        assert marker.exists() and pending.exists() and backup.exists()
        assert update_target_state.read(target)["transaction_id"] == transaction["transaction_id"]
        assert not updater._get_health_path().exists()
    assert updater.check_and_apply_pending() is False
    assert not marker.exists() and not pending.exists()
    health = updater._read_health_record(updater._get_health_path())
    assert health["build"] == transaction["to_build"]
    assert health["confirmed"] is False and health["fails"] == 0
    assert health["backup_id"] == backup.name and health["time"] > 0
    assert backup.exists() and target.read_bytes() == expected


def test_missing_job_marker_enters_bound_handler_but_unknown_helper_does_not(bound_marker, monkeypatch):
    from core import update_cli
    pending, target, marker, backup, transaction = bound_marker
    for reason, expected in (("missing_job_evidence", (False, False)), ("helper_unknown", (True, False))):
        monkeypatch.setattr(update_cli, "reconcile_on_boot", lambda **kwargs: {"state": "deferred", "reason": reason})
        assert updater._reconcile_indexed_job_on_boot() == expected
