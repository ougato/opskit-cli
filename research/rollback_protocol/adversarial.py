"""Adversarial tests for the isolated recovery design, not production integration.

Run explicitly: python -m pytest research/rollback_protocol/adversarial.py
This simulation assumes independently authenticated helper status, and does not
exercise a packaged executable, production startup, or real recovery scheduling.
"""
import multiprocessing
import os

import pytest

from core import update_target_state, updater
from model import Model


@pytest.fixture
def recovery_model(tmp_path):
    model = Model(tmp_path)
    model.target.write_bytes(b"currently running build")
    model.backup.write_bytes(b"previous build")
    backup_id = "opskit.v1.123." + "b" * 32 + ".bak"
    update_target_state.prepare(
        model.target, operation="rollback", from_build=2, to_build=1,
        from_digest=updater._sha256_file(model.target), to_digest=updater._sha256_file(model.backup),
        owner_data_dir=tmp_path / "data", backup_id=backup_id, source_transaction_id="a" * 32,
    )
    updater._write_update_record(model.health, {"build": 2, "confirmed": False, "fails": 3, "backup_id": backup_id})
    (tmp_path / "unrelated-user-file").write_bytes(b"user data")
    return model


def _schedule_child(root, start, output):
    start.wait(15)
    output.put(Model(root).schedule())


def _observe_child(root, loaded_digest, job_id, output):
    output.put(Model(root).observe("alive", loaded_digest, job_id))


def _crash_after_intent(root):
    if Model(root).schedule() == "handoff":
        os._exit(7)
    os._exit(8)


def _crash_after_commit(root, job_id):
    Model(root).helper_commit(job_id, lose_result=True)
    os._exit(9)


def _expect_crash(context, target, arguments, exitcode):
    child = context.Process(target=target, args=arguments)
    child.start()
    try:
        child.join(20)
        assert child.exitcode == exitcode
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)


def test_competing_processes_publish_only_one_intent(recovery_model):
    model = recovery_model
    context = multiprocessing.get_context("spawn")
    start, output = context.Event(), context.Queue()
    children = [context.Process(target=_schedule_child, args=(str(model.root), start, output)) for _ in range(2)]
    for child in children:
        child.start()
    try:
        start.set()
        decisions = [output.get(timeout=20), output.get(timeout=20)]
        assert decisions.count("handoff") == 1
        assert set(decisions) <= {"handoff", "pending", "busy", "blocked"}
        assert model.schedule() == "pending"
    finally:
        for child in children:
            child.join(10)
            if child.is_alive():
                child.terminate()
                child.join(5)
    assert all(child.exitcode == 0 for child in children)


def test_scheduler_crash_requires_verified_helper_exit(recovery_model):
    model = recovery_model
    before = model.target.read_bytes(), model.backup.read_bytes(), model.health.read_bytes()
    loaded = updater._sha256_file(model.target)
    _expect_crash(multiprocessing.get_context("spawn"), _crash_after_intent, (str(model.root),), 7)
    job_id = updater._read_update_record(model.intent)["job_id"]
    # Simulate loss after publishing intent and before durable launch acknowledgement.
    assert Model(model.root).observe("unknown", loaded, job_id) == "wait"
    assert Model(model.root).schedule() == "pending"
    assert Model(model.root).retry("unknown", job_id) == "wait"
    assert Model(model.root).observe("exited", loaded, job_id) == "retry_available"
    assert (model.target.read_bytes(), model.backup.read_bytes(), model.health.read_bytes()) == before
    assert (model.root / "unrelated-user-file").read_bytes() == b"user data"


def test_recovered_target_without_result_is_reconciled_after_helper_exit(recovery_model):
    model = recovery_model
    before_health, before_backup = model.health.read_bytes(), model.backup.read_bytes()
    loaded = updater._sha256_file(model.target)
    assert model.schedule() == "handoff"
    job_id = updater._read_update_record(model.intent)["job_id"]
    _expect_crash(multiprocessing.get_context("spawn"), _crash_after_commit, (str(model.root), job_id), 9)
    assert not (model.root / "result.json").exists()
    assert update_target_state.read(model.target)["phase"] == "prepared"
    assert model.observe("alive", loaded, job_id) == "wait"
    assert model.observe("unknown", loaded, job_id) == "wait"
    assert model.observe("exited", loaded, job_id) == "restart"
    assert update_target_state.read(model.target)["phase"] == "applied"
    assert model.observe("exited", updater._sha256_file(model.target), job_id) == "proceed"
    assert model.health.read_bytes() == before_health and model.backup.read_bytes() == before_backup
    assert (model.root / "unrelated-user-file").read_bytes() == b"user data"


def test_other_process_waits_while_recovery_helper_is_active(recovery_model):
    model = recovery_model
    assert model.schedule() == "handoff"
    job_id = updater._read_update_record(model.intent)["job_id"]
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    child = context.Process(target=_observe_child, args=(str(model.root), updater._sha256_file(model.target), job_id, output))
    child.start()
    try:
        assert output.get(timeout=20) == "wait"
    finally:
        child.join(10)
        if child.is_alive():
            child.terminate()
            child.join(5)
    assert child.exitcode == 0


def test_changed_target_or_evidence_blocks_reconciliation(recovery_model):
    model = recovery_model
    loaded = updater._sha256_file(model.target)
    assert model.schedule() == "handoff"
    job_id = updater._read_update_record(model.intent)["job_id"]
    model.target.write_bytes(b"unrelated replacement")
    health, backup = model.health.read_bytes(), model.backup.read_bytes()
    assert model.observe("exited", loaded, job_id) == "blocked"
    assert model.target.read_bytes() == b"unrelated replacement"
    assert model.health.read_bytes() == health and model.backup.read_bytes() == backup


def test_other_data_directory_cannot_reconcile_original_owner(recovery_model):
    model = recovery_model
    loaded = updater._sha256_file(model.target)
    assert model.schedule() == "handoff"
    job_id = updater._read_update_record(model.intent)["job_id"]
    before = model.target.read_bytes(), model.backup.read_bytes(), model.health.read_bytes(), model.intent.read_bytes()
    other = Model(model.root, owner=model.root / "different-data")
    assert other.observe("exited", loaded, job_id) == "blocked"
    assert (model.target.read_bytes(), model.backup.read_bytes(), model.health.read_bytes(), model.intent.read_bytes()) == before


def test_retry_gets_new_identity_and_rejects_old_helper_observation(recovery_model):
    model = recovery_model
    loaded = updater._sha256_file(model.target)
    assert model.schedule() == "handoff"
    old = updater._read_update_record(model.intent)["job_id"]
    assert model.retry("alive", old) == "wait"
    assert model.retry("exited", old) == "handoff"
    current = updater._read_update_record(model.intent)
    assert current["job_id"] != old and current["attempt"] == 2
    assert model.retry("exited", old) == "blocked"
    assert model.observe("exited", loaded, old) == "blocked"
    with pytest.raises(ValueError, match="intent changed"):
        model.helper_commit(old)
    assert updater._sha256_file(model.target) == loaded
    assert model.observe("alive", loaded, current["job_id"]) == "wait"
