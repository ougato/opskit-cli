"""Real signatures plus target state prevent stale-runtime/cross-cache downgrades."""
import base64
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import pytest

from core import config, updater, update_target_state as state
from core.release_signature import payload, file_digest


@pytest.fixture
def signed_installer(tmp_path, monkeypatch):
    target = tmp_path / "program.exe"
    target.write_bytes(b"old-program")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    monkeypatch.setattr(config, "load_config", lambda **kw: {"update": {"signing_public_key": public}})
    def commit(pending, destination, version, recovery=None):
        updater._atomic_copy(pending, destination)
        pending.unlink()
        if recovery:
            state.completed(destination, recovery["transaction"])
            updater.mark_update_applied(version, backup=config.get_data_dir() / "backups" / recovery["backup_id"])
    monkeypatch.setattr(updater, "_apply_windows", commit)
    monkeypatch.setattr(updater, "_apply_unix", commit)
    def install(data, build, body=b"N"):
        monkeypatch.setenv("OPSKIT_DATA_DIR", str(data))
        pending = updater._get_pending_path()
        pending.parent.mkdir(parents=True, exist_ok=True)
        magic = b"MZ" if sys.platform == "win32" else b"\xcf\xfa\xed\xfe" if sys.platform == "darwin" else b"\x7fELF"
        pending.write_bytes(magic + body * (100 * 1024))
        digest = file_digest(pending)
        filename = updater._asset_filename()
        envelope = {"build": build, "filename": filename, "sha256": digest,
                    "signature": base64.b64encode(key.sign(payload(build, filename, digest))).decode()}
        pending.with_name(pending.name + ".sig.json").write_text(json.dumps(envelope))
        updater._write_update_record(updater._get_cache_path(), {"pending_version": build})
        return updater.apply_verified_pending()
    return target, install


@pytest.mark.parametrize("offset", [1, 3], ids=["downgrade", "newer-but-stale-runtime"])
def test_stale_runtime_cannot_apply_another_cache_candidate(signed_installer, tmp_path, offset):
    target, install = signed_installer
    running_build = updater.APP_VERSION
    install(tmp_path / "first", running_build + 2, b"H")
    before = target.read_bytes(), state.path(target).read_bytes()
    with pytest.raises(RuntimeError, match="verification or application failed"):
        install(tmp_path / "second", running_build + offset, b"L")
    assert (target.read_bytes(), state.path(target).read_bytes()) == before
    assert not (tmp_path / "second" / "backups").exists()
    assert updater._get_pending_path().exists()


def test_fresh_runtime_can_upgrade_then_restore_authorized_backup(signed_installer, tmp_path, monkeypatch):
    target, install = signed_installer
    higher = updater.APP_VERSION + 2
    install(tmp_path / "first", higher, b"H")
    higher_bytes = target.read_bytes()
    monkeypatch.setattr(updater, "APP_VERSION", higher)
    install(tmp_path / "second", higher + 1, b"N")
    assert state.read(target)["to_build"] == higher + 1
    assert updater.rollback()
    assert target.read_bytes() == higher_bytes
    record = state.read(target)
    assert record["operation"] == "rollback" and record["phase"] == "applied"
    assert state.current_build(target, file_digest(target), higher + 1) == higher
    assert updater.rollback()


def test_prepared_record_reconciles_actual_bytes_without_guessing(tmp_path):
    target = tmp_path / "program.exe"
    target.write_bytes(b"old")
    old_digest = file_digest(target)
    import hashlib
    new_digest = hashlib.sha256(b"new").hexdigest()
    state.prepare(target, operation="upgrade", from_build=1, to_build=2,
                  from_digest=old_digest, to_digest=new_digest)
    assert state.current_build(target, old_digest, 99) == 1
    target.write_bytes(b"new")
    assert state.current_build(target, file_digest(target), 99) == 2
    target.write_bytes(b"unrecorded")
    with pytest.raises(ValueError, match="bytes do not match"):
        state.current_build(target, file_digest(target), 99)


@pytest.mark.parametrize("field,value", [("from_build", True), ("to_build", 1),
                                         ("target", "other-program"), ("phase", "unknown"),
                                         ("transaction_id", "../escape"), ("from_sha256", "bad")])
def test_invalid_target_record_is_not_a_version_authority(tmp_path, field, value):
    target = tmp_path / "program.exe"
    target.write_bytes(b"old")
    record = state.prepare(target, operation="upgrade", from_build=1, to_build=2,
                           from_digest=file_digest(target), to_digest="a" * 64)
    updater._write_update_record(state.path(target), {**record, field: value})
    with pytest.raises(ValueError):
        state.current_build(target, file_digest(target), 99)


def test_equal_digests_cannot_claim_different_installed_versions(tmp_path):
    target = tmp_path / "program.exe"
    target.write_bytes(b"old")
    record = state.prepare(target, operation="upgrade", from_build=1, to_build=2,
                           from_digest=file_digest(target), to_digest="a" * 64)
    updater._write_update_record(state.path(target), {**record, "to_sha256": record["from_sha256"]})
    with pytest.raises(ValueError, match="transition"):
        state.current_build(target, file_digest(target), 99)


def test_rollback_version_guard_spans_data_dirs_and_allows_later_release(signed_installer, tmp_path):
    target, install = signed_installer
    build = updater.APP_VERSION
    install(tmp_path / "first", build + 1, b"B")
    assert updater.rollback()
    before = target.read_bytes(), state.path(target).read_bytes()
    with pytest.raises(RuntimeError, match="verification or application failed"):
        install(tmp_path / "second", build + 1, b"different-signed-bytes-same-build")
    assert (target.read_bytes(), state.path(target).read_bytes()) == before
    assert not (tmp_path / "second" / "backups").exists()
    install(tmp_path / "third", build + 2, b"good")
    assert state.read(target)["to_build"] == build + 2


def test_changed_transition_cannot_be_overwritten_using_same_id(tmp_path):
    target = tmp_path / "program.exe"
    target.write_bytes(b"old")
    original = state.prepare(target, operation="upgrade", from_build=1, to_build=2,
                             from_digest=file_digest(target), to_digest="a" * 64)
    changed = {**original, "to_build": 3}
    updater._write_update_record(state.path(target), changed)
    with pytest.raises(ValueError, match="transaction changed"):
        state.aborted(target, original)
    assert state.read(target) == changed


def test_completed_replay_verifies_bytes_without_rewriting_authority(tmp_path, monkeypatch):
    import hashlib
    target = tmp_path / "program.exe"
    target.write_bytes(b"old")
    original = state.prepare(target, operation="upgrade", from_build=1, to_build=2,
        from_digest=file_digest(target), to_digest=hashlib.sha256(b"new").hexdigest())
    target.write_bytes(b"new")
    state.completed(target, original)
    before = state.path(target).read_bytes(), state.path(target).stat().st_mtime_ns
    monkeypatch.setattr(updater, "_write_update_record", lambda *args: pytest.fail("no durable rewrite for completed replay"))
    state.completed(target, original)
    assert (state.path(target).read_bytes(), state.path(target).stat().st_mtime_ns) == before
    target.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="expected bytes"):
        state.completed(target, original)


def test_failed_version_floor_survives_new_uncommitted_transaction(tmp_path):
    import hashlib
    target = tmp_path / "program.exe"
    target.write_bytes(b"stable")
    stable = file_digest(target)
    bad = hashlib.sha256(b"bad").hexdigest()
    rollback = state.prepare(target, operation="rollback", from_build=8, to_build=7,
                             from_digest=bad, to_digest=stable)
    state.completed(target, rollback)
    later = state.prepare(target, operation="upgrade", from_build=7, to_build=9,
                          from_digest=stable, to_digest=hashlib.sha256(b"later").hexdigest())
    state.aborted(target, later)
    assert state.read(target)["upgrade_floor"] == 8
    for build in (7, 8):
        with pytest.raises(ValueError, match="rolled back"):
            state.validate_upgrade(target, build)
    state.validate_upgrade(target, 9)
