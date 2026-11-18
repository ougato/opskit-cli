"""Real signatures plus target state prevent stale-runtime/cross-cache downgrades."""
import base64
import json
import logging
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
        identity = "b" * 32
        updater._write_update_record(updater._get_cache_path(), {"candidate_id": identity, "pending_version": build})
        pending = updater._get_pending_path()
        pending.parent.mkdir(parents=True, exist_ok=True)
        magic = b"MZ" if sys.platform == "win32" else b"\xcf\xfa\xed\xfe" if sys.platform == "darwin" else b"\x7fELF"
        pending.write_bytes(magic + body * (100 * 1024))
        digest = file_digest(pending)
        updater._write_update_record(pending.parent / "candidate.json", {
            "schema_version": 1, "candidate_id": identity, "build": build,
            "sha256": digest, "state": "verified"})
        filename = updater._asset_filename()
        envelope = {"build": build, "filename": filename, "sha256": digest,
                    "signature": base64.b64encode(key.sign(payload(build, filename, digest))).decode()}
        pending.with_name(pending.name + ".sig.json").write_text(json.dumps(envelope))
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
    assert updater.confirm_health()
    install(tmp_path / "first", higher + 1, b"N")
    assert state.read(target)["to_build"] == higher + 1
    assert updater.rollback()
    assert target.read_bytes() == higher_bytes
    record = state.read(target)
    assert record["operation"] == "rollback" and record["phase"] == "applied"
    assert state.current_build(target, file_digest(target), higher + 1) == higher
    assert updater.rollback()


def test_new_upgrade_waits_for_previous_owner_health(signed_installer, tmp_path, monkeypatch):
    target, install = signed_installer
    first, second = tmp_path / "first", tmp_path / "second"
    build = updater.APP_VERSION + 1
    install(first, build, b"H")
    monkeypatch.setattr(updater, "APP_VERSION", build)
    health = first / "cache" / updater.FILE_UPDATE_HEALTH
    before = target.read_bytes(), state.path(target).read_bytes(), health.read_bytes()

    with pytest.raises(RuntimeError, match="verification or application failed"):
        install(first, build + 1, b"N")
    assert (target.read_bytes(), state.path(target).read_bytes(), health.read_bytes()) == before
    assert updater._get_pending_path().is_file()

    monkeypatch.setenv("OPSKIT_DATA_DIR", str(first))
    assert updater.confirm_health()
    with pytest.raises(RuntimeError, match="verification or application failed"):
        install(second, build + 1, b"N")
    assert (target.read_bytes(), state.path(target).read_bytes()) == before[:2]
    assert not (second / "backups").exists()
    install(first, build + 1, b"N")
    assert state.read(target)["to_build"] == build + 1


def test_forged_owner_declaration_never_causes_foreign_file_probe(signed_installer, tmp_path, monkeypatch):
    target, install = signed_installer
    first = tmp_path / "first"
    build = updater.APP_VERSION + 1
    install(first, build, b"H")
    monkeypatch.setattr(updater, "APP_VERSION", build)
    foreign = r"\\untrusted.invalid\share\data" if sys.platform == "win32" else "/foreign-untrusted-data"
    transaction = state.read(target)
    updater._write_update_record(state.path(target), {**transaction, "owner_data_dir": foreign})
    original_read = updater._read_update_record
    def checked_read(path):
        if str(path).startswith(foreign):
            pytest.fail("must not read an untrusted owner declaration")
        return original_read(path)
    monkeypatch.setattr(updater, "_read_update_record", checked_read)
    before = target.read_bytes(), state.path(target).read_bytes()
    with pytest.raises(RuntimeError, match="verification or application failed"):
        install(first, build + 1, b"N")
    assert (target.read_bytes(), state.path(target).read_bytes()) == before


@pytest.mark.parametrize("phase", ["upgrade_aborted", "rollback_aborted", "rollback_applied"])
def test_other_data_directory_cannot_supersede_owned_history(signed_installer, tmp_path, monkeypatch, caplog, phase):
    # Other startup tests initialize the application's logger with propagation
    # disabled. Enable it only here so caplog observes the real warning in any
    # suite order without changing the application logging policy.
    monkeypatch.setattr(logging.getLogger("opskit"), "propagate", True)
    target, install = signed_installer
    first, second = tmp_path / "first", tmp_path / "second"
    build = updater.APP_VERSION + 1
    install(first, build, b"H")
    monkeypatch.setattr(updater, "APP_VERSION", build)
    original = state.read(target)
    backup = first / "backups" / original["backup_id"]
    if phase == "upgrade_aborted":
        target.write_bytes(backup.read_bytes())
        updater._write_update_record(state.path(target), {**original, "phase": "aborted"})
        monkeypatch.setattr(updater, "APP_VERSION", build - 1)
    elif phase == "rollback_aborted":
        rollback = state.prepare(target, operation="rollback", from_build=build, to_build=build - 1,
            from_digest=original["to_sha256"], to_digest=original["from_sha256"],
            owner_data_dir=first, backup_id=original["backup_id"],
            source_transaction_id=original["transaction_id"])
        state.aborted(target, rollback)
    else:
        assert updater.rollback()
        monkeypatch.setattr(updater, "APP_VERSION", build - 1)
    before = target.read_bytes(), state.path(target).read_bytes(), backup.read_bytes()
    with pytest.raises(RuntimeError, match="verification or application failed"):
        install(second, build + 1, b"N")
    assert (target.read_bytes(), state.path(target).read_bytes(), backup.read_bytes()) == before
    assert "data directory changed; upgrade deferred" in caplog.text


@pytest.mark.parametrize("fault", ["missing_health", "wrong_backup", "bad_receipt", "changed_backup", "wrong_job"])
def test_new_upgrade_retains_previous_evidence_when_confirmation_is_unproven(
    signed_installer, tmp_path, monkeypatch, fault,
):
    target, install = signed_installer
    first, second = tmp_path / "first", tmp_path / "second"
    build = updater.APP_VERSION + 1
    install(first, build, b"H")
    monkeypatch.setattr(updater, "APP_VERSION", build)
    assert updater.confirm_health()
    transaction = state.read(target)
    health = first / "cache" / updater.FILE_UPDATE_HEALTH
    health_record = updater._read_health_record(health)
    backup = first / "backups" / transaction["backup_id"]
    receipt = backup.with_name(backup.name + ".json")
    if fault == "missing_health":
        health.unlink()
    elif fault == "wrong_backup":
        updater._write_update_record(health, {**health_record, "backup_id": "opskit.v1.1." + "f" * 32 + ".bak"})
    elif fault == "bad_receipt":
        updater._write_update_record(receipt, {**updater._read_update_record(receipt), "to_build": build + 1})
    elif fault == "changed_backup":
        backup.write_bytes(b"different backup")
    else:
        job_id = "a" * 32
        updater._write_update_record(health, {**health_record, "job_id": job_id})
        updater._write_update_record(first / "cache" / "update-jobs" / job_id / "job.json", {
            "schema_version": 2, "job_id": job_id, "state": "scheduled",
            "target": str(target.resolve()), "version": build, "transaction_id": "f" * 32,
            "backup_id": transaction["backup_id"], "old_sha256": transaction["from_sha256"],
            "new_sha256": transaction["to_sha256"],
        })
    before = target.read_bytes(), state.path(target).read_bytes(), backup.read_bytes(), receipt.read_bytes()
    with pytest.raises(RuntimeError, match="verification or application failed"):
        install(first, build + 1, b"N")
    assert (target.read_bytes(), state.path(target).read_bytes(), backup.read_bytes(), receipt.read_bytes()) == before
    assert not (second / "backups").exists()
    assert updater._get_pending_path().is_file()


@pytest.mark.parametrize("job_state", ["scheduled", "retired"])
def test_previous_confirmed_job_can_be_superseded(signed_installer, tmp_path, monkeypatch, job_state):
    target, install = signed_installer
    first, second = tmp_path / "first", tmp_path / "second"
    build = updater.APP_VERSION + 1
    install(first, build, b"H")
    monkeypatch.setattr(updater, "APP_VERSION", build)
    assert updater.confirm_health()
    transaction = state.read(target)
    job_id = "a" * 32
    health = first / "cache" / updater.FILE_UPDATE_HEALTH
    updater._write_update_record(health, {**updater._read_health_record(health), "job_id": job_id})
    metadata = {"schema_version": 2, "job_id": job_id, "state": job_state,
                "target": str(target.resolve()), "version": build,
                "transaction_id": transaction["transaction_id"], "backup_id": transaction["backup_id"],
                "old_sha256": transaction["from_sha256"], "new_sha256": transaction["to_sha256"]}
    if job_state == "retired":
        metadata["retirement"] = {"schema_version": 2, "outcome": "applied",
            "job_transaction_id": transaction["transaction_id"], "from_build": transaction["from_build"],
            "terminal_build": build, "target_sha256": transaction["to_sha256"],
            "helper_proof": "exited", "helper_created_ticks": 1, "health_confirmed": True}
    updater._write_update_record(first / "cache" / "update-jobs" / job_id / "job.json", metadata)
    install(first, build + 1, b"N")
    assert state.read(target)["to_build"] == build + 1


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


def test_owner_location_accepts_link_alias_and_rejects_moved_directory(tmp_path):
    target = tmp_path / "program.exe"
    target.write_bytes(b"old")
    owner = tmp_path / "owner"
    owner.mkdir()
    marker = owner / "recovery-evidence"
    marker.write_bytes(b"keep")
    record = state.prepare(target, operation="upgrade", from_build=1, to_build=2,
                           from_digest=file_digest(target), to_digest="a" * 64,
                           owner_data_dir=owner)
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(owner, target_is_directory=True)
    except OSError:
        alias = None  # Creating symlinks may require Windows developer privileges.
    if alias is not None:
        state.require_owner(record, alias)
    moved = tmp_path / "renamed-owner"
    owner.rename(moved)
    with pytest.raises(state.UpdateOwnerMismatch, match="recovery evidence retained"):
        state.require_owner(record, moved)
    assert (moved / marker.name).read_bytes() == b"keep"
    assert state.read(target) == record


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths are case insensitive")
def test_owner_location_is_case_insensitive_on_windows(tmp_path):
    directory = tmp_path / "OwnerCase"
    directory.mkdir()
    record = {"owner_data_dir": state.data_directory_identity(directory)}
    state.require_owner(record, directory.parent / directory.name.upper())


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
    with pytest.raises(RuntimeError, match="verification or application failed"):
        install(tmp_path / "third", build + 2, b"good")
    assert (target.read_bytes(), state.path(target).read_bytes()) == before
    install(tmp_path / "first", build + 2, b"good")
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
