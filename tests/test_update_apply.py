"""Real signature gates, atomic replacement failure and explicit apply states."""
import base64
import errno
import json
from pathlib import Path
import sys
import os
import subprocess
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import pytest
from typer.testing import CliRunner

import main
from core import config, updater
from core.release_signature import file_digest, payload


@pytest.fixture
def signed_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    current = tmp_path / "current.exe"
    current.write_bytes(b"old-program")
    monkeypatch.setattr(updater, "_self_path", lambda: current)
    pending = updater._get_pending_path()
    pending.parent.mkdir()
    magic = b"MZ" if sys.platform == "win32" else b"\xcf\xfa\xed\xfe" if sys.platform == "darwin" else b"\x7fELF"
    pending.write_bytes(magic + b"\0" * (1024 * 100))
    build = updater.APP_VERSION + 1
    updater._get_cache_path().write_text(json.dumps({"pending_version": build}))
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    monkeypatch.setattr(config, "load_config", lambda **kwargs: {"update": {"signing_public_key": public}})
    digest = file_digest(pending)
    filename = updater._asset_filename()
    envelope = {"build": build, "filename": filename, "sha256": digest,
                "signature": base64.b64encode(key.sign(payload(build, filename, digest))).decode()}
    pending.with_name(pending.name + ".sig.json").write_text(json.dumps(envelope))
    return current, pending, build


def test_source_mode_apply_never_creates_data(tmp_path, monkeypatch):
    root = tmp_path / "absent"
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(root))
    monkeypatch.setattr(updater, "_is_frozen", lambda: False)
    result = CliRunner().invoke(main.app, ["--output", "json", "--yes", "update", "apply"])
    assert result.exit_code == 2
    assert not root.exists()


def test_apply_requires_explicit_confirmation_before_mutation(monkeypatch):
    monkeypatch.setattr(updater, "_is_frozen", lambda: True)
    monkeypatch.setattr(updater, "apply_verified_pending", lambda: pytest.fail("unconfirmed mutation"))
    result = CliRunner().invoke(main.app, ["--output", "json", "update", "apply"])
    assert result.exit_code == 2


def test_changed_signed_artifact_is_not_applied(signed_pending, monkeypatch):
    current, pending, _ = signed_pending
    pending.write_bytes(pending.read_bytes() + b"tampered")
    monkeypatch.setattr(updater, "_apply_windows", lambda *a: pytest.fail("invalid signature applied"))
    monkeypatch.setattr(updater, "_apply_unix", lambda *a: pytest.fail("invalid signature applied"))
    with pytest.raises(RuntimeError, match="verification or application failed"):
        updater.apply_verified_pending()
    assert current.read_bytes() == b"old-program"
    assert not (current.parent / "backups").exists()


def test_backup_failure_reaches_real_backup_then_preserves_current(signed_pending, monkeypatch):
    current, pending, _ = signed_pending
    calls = []
    def fail(source, destination):
        calls.append((source, destination))
        raise OSError("synthetic backup failure")
    monkeypatch.setattr(updater.shutil, "copy2", fail)
    with pytest.raises(RuntimeError):
        updater.apply_verified_pending()
    assert len(calls) == 1 and calls[0][0] == current
    assert current.read_bytes() == b"old-program" and pending.exists()


def test_apply_result_distinguishes_scheduling_from_committed_copy(signed_pending, monkeypatch):
    current, pending, build = signed_pending
    calls = []
    monkeypatch.setattr(updater, "_apply_windows", lambda *args: calls.append(args))
    def apply_unix(source, target, version):
        calls.append((source, target, version))
        updater._atomic_copy(source, target)
    monkeypatch.setattr(updater, "_apply_unix", apply_unix)
    result = CliRunner().invoke(main.app, ["--output", "json", "--yes", "update", "apply"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data == {"version": build, "state": "scheduled" if sys.platform == "win32" else "applied", "health_confirmed": False}
    assert len(calls) == 1


def test_atomic_copy_failure_never_truncates_current(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "current"
    source.write_bytes(b"new-program")
    target.write_bytes(b"old-program")
    def fail_copy(input_file, output, length):
        output.write(b"partial")
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(updater.shutil, "copyfileobj", fail_copy)
    with pytest.raises(OSError):
        updater._atomic_copy(source, target)
    assert target.read_bytes() == b"old-program"
    assert not list(tmp_path.glob(".opskit-replace-*"))


def test_atomic_copy_commit_failure_preserves_current(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "current"
    source.write_bytes(b"new-program")
    target.write_bytes(b"old-program")
    monkeypatch.setattr(updater.os, "replace", lambda *args: (_ for _ in ()).throw(PermissionError("commit denied")))
    with pytest.raises(PermissionError):
        updater._atomic_copy(source, target)
    assert target.read_bytes() == b"old-program"
    assert not list(tmp_path.glob(".opskit-replace-*"))


def test_marker_commit_failure_preserves_current_and_pending(signed_pending, monkeypatch):
    current, pending, build = signed_pending
    marker = pending.parent / "update_pending_path.json"
    marker.write_text(json.dumps({"pending": str(pending), "target": str(current), "version": build}))
    real_replace = updater.os.replace
    def fail_replace(source, target):
        if Path(target) == current:
            raise PermissionError("commit denied")
        return real_replace(source, target)
    monkeypatch.setattr(updater.os, "replace", fail_replace)
    assert not updater._apply_update_pending_path()
    assert current.read_bytes() == b"old-program"
    assert pending.exists() and marker.exists()


def test_source_mode_marker_does_not_replace_or_clear_data(signed_pending, monkeypatch):
    current, pending, build = signed_pending
    marker = pending.parent / "update_pending_path.json"
    marker.write_text(json.dumps({"pending": str(pending), "target": str(current), "version": build}))
    monkeypatch.setattr(updater, "_is_frozen", lambda: False)
    assert not updater._apply_update_pending_path()
    assert current.read_bytes() == b"old-program" and pending.exists() and marker.exists()


def test_cross_device_update_uses_atomic_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    pending, current = tmp_path / "pending", tmp_path / "current"
    pending.write_bytes(b"new-program")
    current.write_bytes(b"old-program")
    def cross_device(self, target):
        raise OSError(errno.EXDEV, "different filesystems")
    monkeypatch.setattr(Path, "replace", cross_device)
    # Windows tests exercise the real atomic file primitive; ownership calls
    # only exist on Unix, so native Unix CI exercises those separately.
    if not hasattr(updater.os, "chown"):
        monkeypatch.setattr(updater.os, "chown", lambda *a: None, raising=False)
    updater._apply_unix(pending, current, updater.APP_VERSION + 1)
    assert current.read_bytes() == b"new-program" and not pending.exists()
    assert not list(tmp_path.glob(".opskit-replace-*"))


def test_cross_device_copy_failure_preserves_both_files(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "data"))
    pending, current = tmp_path / "pending", tmp_path / "current"
    pending.write_bytes(b"new-program")
    current.write_bytes(b"old-program")
    monkeypatch.setattr(Path, "replace", lambda *a: (_ for _ in ()).throw(OSError(errno.EXDEV, "different filesystems")))
    def fail_copy(input_file, output, length):
        output.write(b"partial")
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(updater.shutil, "copyfileobj", fail_copy)
    with pytest.raises(OSError):
        updater._apply_unix(pending, current, updater.APP_VERSION + 1)
    assert current.read_bytes() == b"old-program" and pending.read_bytes() == b"new-program"
    assert not list(tmp_path.glob(".opskit-replace-*"))


@pytest.mark.parametrize("failure", [OSError("permission finalization failed"), KeyboardInterrupt()],
                         ids=["finalization-error", "cancel"])
def test_unix_post_commit_failure_restores_verified_backup(signed_pending, monkeypatch, failure):
    current, pending, build = signed_pending
    new_bytes = pending.read_bytes()
    # Exercise actual replacement/compensation on temporary files, never the
    # workspace executable. Inject after commit, as chmod/chown/cache can fail.
    monkeypatch.setattr(updater.sys, "platform", "linux")
    def commit_then_fail(source, target, version):
        updater._atomic_copy(source, target)
        source.unlink()
        raise failure
    monkeypatch.setattr(updater, "_apply_unix", commit_then_fail)
    if isinstance(failure, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            updater._do_apply(pending, build)
    else:
        assert not updater._do_apply(pending, build)
    assert current.read_bytes() == b"old-program"
    assert pending.read_bytes() == new_bytes
    assert updater._verify_pending_signature(pending, build)
    assert not list(current.parent.glob(".opskit-replace-*"))


def test_cleanup_failure_does_not_misreport_committed_update(signed_pending, monkeypatch):
    current, pending, build = signed_pending
    new_bytes = pending.read_bytes()
    monkeypatch.setattr(updater.sys, "platform", "linux")
    def commit(source, target, version):
        updater._atomic_copy(source, target)
        source.unlink()
    monkeypatch.setattr(updater, "_apply_unix", commit)
    monkeypatch.setattr(updater, "_cleanup_old_backups",
                        lambda: (_ for _ in ()).throw(PermissionError("cleanup denied")))
    assert updater._do_apply(pending, build)
    assert current.read_bytes() == new_bytes
    assert not pending.exists()


def test_backup_names_do_not_collide_in_same_second(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(updater.time, "time", lambda: 42)
    assert updater._get_backup_path() != updater._get_backup_path()


@pytest.fixture
def committed_update(signed_pending, monkeypatch):
    current, pending, build = signed_pending
    new_bytes = pending.read_bytes()
    def commit(source, target, version, recovery=None):
        updater._atomic_copy(source, target)
        source.unlink()
        if recovery:
            updater.mark_update_applied(version, backup=target.parent / "backups" / recovery["backup_id"])
    monkeypatch.setattr(updater, "_apply_windows", commit)
    monkeypatch.setattr(updater, "_apply_unix", commit)
    assert updater._do_apply(pending, build)
    health = updater._read_update_record(updater._get_health_path())
    backup = current.parent / "backups" / health["backup_id"]
    return current, backup, build, new_bytes


def test_recorded_rollback_restores_complete_old_binary(committed_update):
    current, backup, build, new_bytes = committed_update
    record = updater._read_update_record(backup.with_name(backup.name + ".json"))
    assert record["to_build"] == build
    assert record["replacement_sha256"] == file_digest(current)
    assert current.read_bytes() == new_bytes
    assert updater.rollback()
    assert current.read_bytes() == b"old-program"
    assert backup.read_bytes() == b"old-program"
    assert updater.rollback()  # Idempotent after a committed restore.


def test_signed_rolled_back_version_cannot_be_reapplied(committed_update, monkeypatch):
    from core import update_target_state
    current, backup, build, new_bytes = committed_update
    assert updater.rollback()
    pending = updater._get_pending_path()
    pending.write_bytes(new_bytes)  # Original real signature sidecar remains valid.
    before = update_target_state.path(current).read_bytes()
    backups = set(backup.parent.iterdir())
    monkeypatch.setattr(updater, "_apply_windows", lambda *a: pytest.fail("rolled back version scheduled"))
    monkeypatch.setattr(updater, "_apply_unix", lambda *a: pytest.fail("rolled back version applied"))
    with pytest.raises(RuntimeError, match="verification or application failed"):
        updater.apply_verified_pending()
    assert current.read_bytes() == b"old-program" and set(backup.parent.iterdir()) == backups
    assert update_target_state.path(current).read_bytes() == before
    marker = pending.parent / "update_pending_path.json"
    updater._write_update_record(marker, {"pending": str(pending), "target": str(current), "version": build})
    assert updater._apply_update_pending_path() is False
    assert current.read_bytes() == b"old-program" and marker.exists()


def test_rollback_retry_repairs_lost_completion_without_copying_again(committed_update, monkeypatch):
    from core import update_target_state
    current, backup, build, new_bytes = committed_update
    complete = update_target_state.completed
    with monkeypatch.context() as patch:
        def lost(target, transaction):
            if transaction["operation"] == "rollback":
                raise OSError("lost completion")
            return complete(target, transaction)
        patch.setattr(update_target_state, "completed", lost)
        assert updater.rollback(), "committed restore still needs restart"
    record = update_target_state.read(current)
    assert record["operation"] == "rollback" and record["phase"] == "prepared"
    assert current.read_bytes() == b"old-program"
    monkeypatch.setattr(updater, "_atomic_copy", lambda *a, **kw: pytest.fail("retry must not copy again"))
    assert updater.rollback()
    assert update_target_state.read(current) == {**record, "phase": "applied"}


@pytest.mark.parametrize("change", ["backup", "target", "metadata-target", "metadata-build", "missing-record"],
                         ids=["backup", "target", "wrong-target", "wrong-build", "missing-record"])
def test_rollback_refuses_changed_or_unrecorded_backup(committed_update, change):
    current, backup, build, new_bytes = committed_update
    sidecar = backup.with_name(backup.name + ".json")
    if change == "backup":
        backup.write_bytes(b"changed-backup")
    elif change == "target":
        current.write_bytes(b"different-installed-program")
    elif change == "missing-record":
        sidecar.unlink()
    else:
        record = updater._read_update_record(sidecar)
        record["target" if change == "metadata-target" else "to_build"] = "wrong" if change == "metadata-target" else build + 1
        updater._write_update_record(sidecar, record)
    before = current.read_bytes()
    assert not updater.rollback()
    assert current.read_bytes() == before
    assert updater._get_health_path().exists()


def test_rollback_commit_failure_keeps_new_binary_and_recovery_record(committed_update, monkeypatch):
    current, backup, build, new_bytes = committed_update
    real_replace = updater.os.replace
    def fail_replace(source, target):
        if Path(target) == current:
            raise PermissionError("restore denied")
        return real_replace(source, target)
    monkeypatch.setattr(updater.os, "replace", fail_replace)
    assert not updater.rollback()
    assert current.read_bytes() == new_bytes
    assert backup.exists() and updater._get_health_path().exists()
    assert not list(current.parent.glob(".opskit-replace-*"))


def test_failed_health_rollback_retains_record_for_next_boot(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    path = updater._get_health_path()
    updater._write_update_record(path, {"build": updater.APP_VERSION, "confirmed": False,
                                       "fails": updater.MAX_HEALTH_FAILS - 1})
    before = path.read_bytes()
    calls = []
    monkeypatch.setattr(updater, "rollback", lambda: calls.append(1) or False)
    assert not updater._check_health()
    assert not updater._check_health()
    assert calls == [1, 1] and path.read_bytes() == before


def test_health_record_atomic_write_failure_preserves_previous(tmp_path, monkeypatch):
    path = tmp_path / "health.json"
    updater._write_update_record(path, {"confirmed": False})
    monkeypatch.setattr(updater.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(OSError):
        updater._write_update_record(path, {"confirmed": True})
    assert updater._read_update_record(path) == {"confirmed": False}
    assert not list(tmp_path.glob(".opskit-*"))


@pytest.mark.parametrize("body", [b"[]", b" " * 65537], ids=["array", "oversize"])
def test_update_record_rejects_invalid_shape_or_size(tmp_path, body):
    path = tmp_path / "health.json"
    path.write_bytes(body)
    with pytest.raises(ValueError):
        updater._read_update_record(path)


def test_backup_record_failure_aborts_before_replacement(signed_pending, monkeypatch):
    current, pending, build = signed_pending
    monkeypatch.setattr(updater, "_write_update_record", lambda *a: (_ for _ in ()).throw(OSError("metadata disk full")))
    monkeypatch.setattr(updater, "_apply_windows", lambda *a: pytest.fail("unrecorded backup applied"))
    monkeypatch.setattr(updater, "_apply_unix", lambda *a: pytest.fail("unrecorded backup applied"))
    assert not updater._do_apply(pending, build)
    assert current.read_bytes() == b"old-program" and pending.exists()


def test_marker_cleanup_failure_still_reports_committed_replacement(signed_pending, monkeypatch):
    current, pending, build = signed_pending
    new_bytes = pending.read_bytes()
    from update_marker_fixtures import bind_marker
    marker, backup, transaction = bind_marker(current, pending, build)
    real_unlink = Path.unlink
    def fail_cleanup(self, *args, **kwargs):
        if self in (pending, marker):
            raise PermissionError("cleanup denied")
        return real_unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    assert updater._apply_update_pending_path()
    assert current.read_bytes() == new_bytes
    assert pending.exists() and marker.exists()


@pytest.mark.parametrize("version", [None, True, "123", updater.APP_VERSION], ids=["missing", "bool", "string", "old"])
def test_marker_requires_explicit_new_integer_build(signed_pending, version):
    current, pending, build = signed_pending
    marker = pending.parent / "update_pending_path.json"
    updater._write_update_record(marker, {"pending": str(pending), "target": str(current), "version": version})
    assert not updater._apply_update_pending_path()
    assert current.read_bytes() == b"old-program" and pending.exists() and marker.exists()


def test_update_record_rejects_hard_link(tmp_path):
    original, linked = tmp_path / "original", tmp_path / "linked"
    original.write_text("{}")
    linked.hardlink_to(original)
    with pytest.raises(ValueError, match="regular file"):
        updater._read_update_record(linked)
    assert original.read_text() == "{}"


def test_atomic_copy_binds_expected_digest(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"changed after validation")
    target.write_bytes(b"old-program")
    with pytest.raises(RuntimeError, match="integrity"):
        updater._atomic_copy(source, target, expected_digest="0" * 64)
    assert target.read_bytes() == b"old-program"


def test_backup_cleanup_retains_pinned_and_unrelated_files(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    current = tmp_path / "current.exe"
    current.write_bytes(b"program")
    monkeypatch.setattr(updater, "_self_path", lambda: current)
    backups = []
    for index in range(5):
        backup = updater._get_backup_path()
        backup.parent.mkdir(exist_ok=True)
        backup.write_bytes(b"program")
        updater._write_update_record(backup.with_name(backup.name + ".json"),
                                     {"backup_id": backup.name, "target": str(current.resolve())})
        updater.os.utime(backup, (index + 1, index + 1))
        backups.append(backup)
    unrelated = backups[0].parent / "opskit.v1.legacy.bak"
    unrelated.write_bytes(b"unrecorded")
    updater._write_update_record(updater._get_health_path(), {"backup_id": backups[0].name})
    updater._cleanup_old_backups()
    assert backups[0].exists() and unrelated.exists()
    assert not backups[1].exists()
    assert not backups[1].with_name(backups[1].name + ".json").exists()
    assert all(backup.exists() for backup in backups[2:])


def test_source_rollback_has_no_data_side_effect(tmp_path, monkeypatch):
    data = tmp_path / "absent"
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(data))
    monkeypatch.setattr(updater, "_is_frozen", lambda: False)
    assert not updater.rollback()
    assert not data.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows delayed scheduling semantics")
def test_startup_scheduling_does_not_request_restart_into_old_executable(signed_pending, monkeypatch):
    current, pending, build = signed_pending
    monkeypatch.setattr(updater, "_pending_version", None)
    calls = []
    monkeypatch.setattr(updater, "_do_apply", lambda *args: calls.append(args) or True)
    monkeypatch.setattr(updater, "_check_health", lambda: False)
    assert not updater.check_and_apply_pending()
    assert len(calls) == 1 and calls[0] == (pending, build)
    assert current.read_bytes() == b"old-program"


def test_windows_scheduling_does_not_mark_uncommitted_version_healthy(signed_pending, monkeypatch):
    current, pending, build = signed_pending
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "_apply_windows", lambda *args: None)
    assert updater._do_apply(pending, build)
    assert not updater._get_health_path().exists()


def test_backup_directory_link_is_rejected_before_any_copy(signed_pending, monkeypatch, tmp_path):
    current, pending, build = signed_pending
    external = tmp_path / "outside-backups"
    external.mkdir()
    try:
        (current.parent / "backups").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation requires OS permission")
    monkeypatch.setattr(updater.shutil, "copy2", lambda *a: pytest.fail("copy through linked backup directory"))
    assert not updater._do_apply(pending, build)
    assert current.read_bytes() == b"old-program" and pending.exists()
    assert not list(external.iterdir())


def test_same_executable_competes_across_data_directories(signed_pending, monkeypatch, tmp_path):
    current, original_pending, build = signed_pending
    first_data, second_data = tmp_path / "first-data", tmp_path / "second-data"
    marker = first_data / "ready"
    source = """
import sys
from pathlib import Path
from core import updater
updater._self_path = lambda: Path(sys.argv[1])
with updater._update_resource.hold(), updater._update_target_resource.hold():
    Path(sys.argv[2]).write_text('ready')
    sys.stdin.read()
"""
    child = subprocess.Popen([sys.executable, "-u", "-c", source, str(current), str(marker)],
                             env={**os.environ, "OPSKIT_DATA_DIR": str(first_data)},
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert marker.exists(), "target lock holder did not start"
        monkeypatch.setenv("OPSKIT_DATA_DIR", str(second_data))
        pending = updater._get_pending_path()
        pending.parent.mkdir(parents=True)
        pending.write_bytes(original_pending.read_bytes())
        pending.with_name(pending.name + ".sig.json").write_bytes(
            original_pending.with_name(original_pending.name + ".sig.json").read_bytes())
        updater._write_update_record(updater._get_cache_path(), {"pending_version": build})
        assert updater._update_lock_path() != first_data / "cache" / "locks" / "update.lock"
        with pytest.raises(RuntimeError, match="update executable is busy"):
            updater.apply_verified_pending()
        assert current.read_bytes() == b"old-program" and pending.exists()
        assert not (second_data / "backups").exists()
        # A different program in the same directory is an independent target.
        other = current.with_name("other-program.exe")
        other.write_bytes(b"other")
        with monkeypatch.context() as changed:
            changed.setattr(updater, "_self_path", lambda: other)
            with updater._update_target_resource.hold():
                assert updater._update_target_lock_path(other).exists()
        child.terminate()
        child.communicate(timeout=5)
        with updater._update_target_resource.hold():
            assert updater._update_target_lock_path().exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_target_lock_identity_change_inside_operation_is_rejected(tmp_path, monkeypatch):
    target = tmp_path / "program.exe"
    target.write_bytes(b"program")
    monkeypatch.setattr(updater, "_self_path", lambda: target)
    with updater._update_target_resource.hold():
        monkeypatch.setattr(updater, "_self_path", lambda: target.with_name("another.exe"))
        with pytest.raises(RuntimeError, match="resource changed"):
            with updater._update_target_resource.hold():
                pytest.fail("target identity changed inside operation")
