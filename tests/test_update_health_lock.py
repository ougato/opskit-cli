"""Health read/modify/write uses the same native lock as update helpers."""
from contextlib import contextmanager
import multiprocessing
import os
from pathlib import Path
import threading

import pytest

from core import updater
from core.resource_lock import ResourceBusyError, ResourceLock


@pytest.fixture
def health(tmp_path):
    path = updater._get_health_path()
    updater._write_update_record(path, {"build": updater.APP_VERSION, "confirmed": False,
        "fails": 1, "job_id": "a" * 32, "backup_id": "owned-backup"})
    return path


def _paused_action(directory, ready, release, action):
    os.environ["OPSKIT_DATA_DIR"] = directory
    from core import updater
    original = updater._read_health_record
    def paused(path):
        data = original(path)
        ready.set()
        if not release.wait(15):
            raise RuntimeError("owned health test release timed out")
        return data
    updater._read_health_record = paused
    if action == "confirm":
        assert updater.confirm_health() is True
    else:
        assert updater._check_health() is False


def _hold_lock(directory, ready, release, replacement=None):
    os.environ["OPSKIT_DATA_DIR"] = directory
    from core import updater
    with updater._update_resource.hold():
        ready.set()
        if not release.wait(15):
            raise RuntimeError("owned health test release timed out")
        if replacement is not None:
            updater._write_update_record(updater._get_health_path(), replacement)


@contextmanager
def owned_process(target, directory, *extra):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=target, args=(str(directory), ready, release, *extra), daemon=True)
    process.start()
    try:
        assert ready.wait(10), "owned child must reach the actual locked health read"
        yield process, release
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
        assert not process.is_alive()
        code = process.exitcode
        process.close()
        assert code == 0


@pytest.mark.parametrize("action", ["confirm", "check"])
def test_actual_child_holds_lock_across_health_read_and_write(health, action):
    if action == "check":
        updater._write_update_record(health, {**updater._read_update_record(health), "fails": 0})
    before = health.read_bytes()
    with owned_process(_paused_action, health.parent.parent, action):
        with pytest.raises(ResourceBusyError):
            with ResourceLock(updater._update_lock_path, label="another health writer").hold():
                pytest.fail("paused health reader must retain the OS lock")
        assert updater.confirm_health() is False
        assert updater._check_health() is False
        updater.mark_update_applied(updater.APP_VERSION + 1)
        assert health.read_bytes() == before
    completed = updater._read_update_record(health)
    assert completed["job_id"] == "a" * 32
    assert completed["fails"] == 1
    assert completed["confirmed"] is (action == "confirm")
    updater.mark_update_applied(updater.APP_VERSION + 1)
    assert updater._read_update_record(health)["build"] == updater.APP_VERSION + 1
    newer = health.read_bytes()
    assert updater._check_health() is False and updater.confirm_health() is True
    assert health.read_bytes() == newer, "old runtime cannot confirm/delete newer health"


def test_background_confirmation_retries_native_contention_without_losing_count(health):
    results = []
    replacement = {"build": updater.APP_VERSION, "confirmed": False,
                   "fails": 2, "job_id": "a" * 32, "backup_id": "owned-backup"}
    with owned_process(_hold_lock, health.parent.parent, replacement) as (_, release):
        busy_seen = threading.Event()
        original_hold = updater._update_resource.hold
        @contextmanager
        def observed_hold():
            try:
                with original_hold():
                    yield
            except ResourceBusyError:
                busy_seen.set()
                raise
        # Observe the real failure, without replacing the OS lock protocol.
        from unittest.mock import patch
        with patch.object(updater._update_resource, "hold", observed_hold):
            thread = threading.Thread(target=lambda: results.append(updater.confirm_health_after(0, retry_timeout=5)))
            thread.start()
            try:
                assert busy_seen.wait(3)
                release.set()
                thread.join(8)
                assert not thread.is_alive()
            finally:
                release.set()
                thread.join(8)
    assert results == [True]
    result = updater._read_update_record(health)
    assert result["confirmed"] is True and result["fails"] == 2 and result["job_id"] == "a" * 32


def test_background_confirmation_contention_budget_is_bounded(health):
    before = health.read_bytes()
    with owned_process(_hold_lock, health.parent.parent):
        assert updater.confirm_health_after(0, retry_timeout=.1) is False
        assert health.read_bytes() == before


@pytest.mark.parametrize("offset", [-1, 1])
def test_other_build_health_is_retained_for_recovery(health, monkeypatch, offset):
    record = updater._read_update_record(health)
    updater._write_update_record(health, {**record, "build": updater.APP_VERSION + offset})
    before = health.read_bytes()
    monkeypatch.setattr(updater, "rollback", lambda: pytest.fail("other build cannot roll back"))
    assert updater._check_health() is False and updater.confirm_health() is True
    assert health.read_bytes() == before


def test_replayed_publication_preserves_confirmation_failures_and_job(health):
    record = updater._read_update_record(health)
    updater._write_update_record(health, {**record, "confirmed": True, "fails": 2})
    before = health.read_bytes()
    updater.mark_update_applied(updater.APP_VERSION)
    assert health.read_bytes() == before
    updater.mark_update_applied(updater.APP_VERSION, backup=Path("different-backup"))
    assert health.read_bytes() == before


@pytest.mark.parametrize("field,value", [("build", True), ("build", "7"), ("build", -1),
    ("confirmed", "yes"), ("fails", True), ("fails", -1), ("job_id", "../other")])
def test_invalid_health_is_retained_and_not_confirmed(health, field, value, caplog):
    record = updater._read_update_record(health)
    updater._write_update_record(health, {**record, field: value, "secret": "do-not-log"})
    before = health.read_bytes()
    assert updater.confirm_health() is False and updater._check_health() is False
    updater.mark_update_applied(updater.APP_VERSION + 1)
    assert health.read_bytes() == before
    assert "do-not-log" not in caplog.text


def test_rollback_keeps_health_backup_evidence_and_holds_root_lock(health, monkeypatch):
    record = updater._read_update_record(health)
    updater._write_update_record(health, {**record, "fails": updater.MAX_HEALTH_FAILS - 1})
    before = health.read_bytes()
    def rollback():
        with pytest.raises(ResourceBusyError):
            with ResourceLock(updater._update_lock_path, label="concurrent health writer").hold():
                pytest.fail("rollback decision must remain under the root lock")
        return True
    monkeypatch.setattr(updater, "rollback", rollback)
    assert updater._check_health() is True
    assert health.read_bytes() == before


def test_cleanup_error_does_not_undo_successful_confirmation(health, monkeypatch):
    monkeypatch.setattr(updater, "_cleanup_old_backups", lambda: (_ for _ in ()).throw(OSError("private-path")))
    assert updater.confirm_health() is True
    assert updater._read_update_record(health)["confirmed"] is True


def test_absent_health_does_not_initialize_runtime(tmp_path, monkeypatch):
    directory = tmp_path / "absent"
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(directory))
    assert updater.confirm_health() is True and updater._check_health() is False
    assert updater.confirm_health_after(0) is True
    assert not directory.exists()


@pytest.mark.parametrize("delay,budget", [(-1, 1), (0, -1), (float("nan"), 1), (0, float("inf"))])
def test_confirmation_timing_validation(delay, budget):
    with pytest.raises(ValueError, match="finite and nonnegative"):
        updater.confirm_health_after(delay, retry_timeout=budget)


def test_invalid_record_is_not_retried_as_contention(health, monkeypatch):
    health.write_text("invalid private JSON")
    sleeps = []
    monkeypatch.setattr(updater.time, "sleep", sleeps.append)
    assert updater.confirm_health_after(0, retry_timeout=5) is False
    assert sleeps == [0]
    assert health.read_text() == "invalid private JSON"


@pytest.mark.skipif(os.name != "nt", reason="Windows byte-lock error classification")
def test_non_contention_byte_lock_error_is_not_retried(health, monkeypatch):
    import errno
    import msvcrt
    before = health.read_bytes()
    def invalid(*args):
        raise OSError(errno.EINVAL, "invalid native lock operation")
    monkeypatch.setattr(msvcrt, "locking", invalid)
    sleeps = []
    monkeypatch.setattr(updater.time, "sleep", sleeps.append)
    assert updater.confirm_health_after(0, retry_timeout=5) is False
    assert sleeps == [0] and health.read_bytes() == before
