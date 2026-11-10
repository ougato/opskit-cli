"""Interrupted journal commits must be synchronized before transaction retries."""
import errno
import os
import stat
from contextlib import contextmanager
from pathlib import Path

import pytest

from core import plugin_install_transaction as publication
from core import plugin_workspace as workspace
import test_plugin_install_transaction as publication_tests

candidate = publication_tests.candidate
publish = publication_tests.publish

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync crash semantics")


def inject_publish_worker(connection, operation, arguments, request):
    from core import private_export
    phase = os.environ["OPSKIT_PROOF_PHASE"]
    original = private_export.publish_private_file
    def selected(target):
        name = Path(target).name
        return ((phase == "intent" and len(name) == 37 and name.endswith(".json"))
                or (phase == "outcome" and name.endswith(".published.json"))
                or (phase == "withdrawal" and name.endswith(".withdrawal.json"))
                or (phase == "withdrawn" and name.endswith(".withdrawn.json"))
                or (phase == "retired" and name.endswith(".retired.json"))
                or (phase == "abort" and name.endswith(".aborted.json")))
    def injected(target, payload):
        if not selected(target):
            return original(target, payload)
        original_sync = os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                os._exit(73)
            return original_sync(fd)
        os.fsync = fail_directory
        try:
            original(target, payload)
        finally:
            os.fsync = original_sync
    private_export.publish_private_file = injected
    publication._worker(connection, operation, arguments, request)


def deny_journal_sync_worker(connection, operation, arguments, request):
    journal = Path(os.environ["OPSKIT_PLUGINS_DIR"]) / publication.NAMESPACE
    identity = journal.stat()
    original_sync = os.fsync
    def fail_journal(fd):
        current = os.fstat(fd)
        if stat.S_ISDIR(current.st_mode) and (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
            raise OSError(errno.EINVAL, "injected unsupported journal fsync")
        return original_sync(fd)
    os.fsync = fail_journal
    try:
        publication._worker(connection, operation, arguments, request)
    finally:
        os.fsync = original_sync


def replace_journal_after_entry_worker(connection, operation, arguments, request):
    import shutil

    original_locks = publication._locks

    @contextmanager
    def replace_after_lock(root, plugin):
        with original_locks(root, plugin):
            journal = root / publication.NAMESPACE
            moved = root / "original-journal"
            journal.rename(moved)
            shutil.copytree(moved, journal)
            yield

    publication._locks = replace_after_lock
    publication._worker(connection, operation, arguments, request)


def fail_worker(monkeypatch, phase):
    monkeypatch.setenv("OPSKIT_PROOF_PHASE", phase)
    monkeypatch.setattr(publication, "_worker", inject_publish_worker)


def test_intent_failure_preserves_prepared_transaction(candidate, monkeypatch):
    with monkeypatch.context() as patch:
        fail_worker(patch, "intent")
        with pytest.raises(publication.PublicationError) as caught:
            publish(candidate)
    token = caught.value.transaction
    journal = candidate[3] / publication.NAMESPACE
    assert (journal / (token + ".json")).is_file()
    assert publication.inspect_publication(token, timeout=30) == "prepared"
    with monkeypatch.context() as patch:
        patch.setattr(publication, "_worker", deny_journal_sync_worker)
        assert publication.inspect_publication(token, timeout=30) == "prepared"
        with pytest.raises(publication.PublicationError):
            publication.recover_publication(token, timeout=30)
    assert publication.recover_publication(token, timeout=30) == "prepared"
    assert candidate[2].candidate.root.exists() and not (candidate[3] / "demo").exists()


def test_outcome_retry_requires_journal_sync_before_reporting_published(candidate, monkeypatch):
    with monkeypatch.context() as patch:
        fail_worker(patch, "outcome")
        with pytest.raises(publication.PublicationError) as caught:
            publish(candidate)
    token = caught.value.transaction
    journal = candidate[3] / publication.NAMESPACE
    assert (journal / (token + ".published.json")).is_file()
    with monkeypatch.context() as patch:
        patch.setattr(publication, "_worker", deny_journal_sync_worker)
        with pytest.raises(publication.PublicationError):
            publication.recover_publication(token, timeout=30)
    assert (candidate[3] / "demo").is_dir()
    assert publication.recover_publication(token, timeout=30) == "published"


def test_withdrawal_retry_requires_journal_sync_before_moving_candidate(candidate, monkeypatch):
    result = publish(candidate)
    withdrawn = workspace.allocate(timeout=30)
    try:
        with monkeypatch.context() as patch:
            fail_worker(patch, "withdrawal")
            with pytest.raises(publication.PublicationError):
                publication.withdraw_publication(result.transaction, withdrawn, timeout=30)
        journal = candidate[3] / publication.NAMESPACE
        assert (journal / (result.transaction + ".withdrawal.json")).is_file()
        assert result.root.exists()
        with monkeypatch.context() as patch:
            patch.setattr(publication, "_worker", deny_journal_sync_worker)
            with pytest.raises(publication.PublicationError):
                publication.withdraw_publication(result.transaction, withdrawn, timeout=30)
        assert result.root.exists() and not (withdrawn.root / "candidate").exists()
        assert publication.withdraw_publication(result.transaction, withdrawn, timeout=30) == "withdrawn"
        assert (withdrawn.root / "candidate/demo.py").exists()
    finally:
        workspace.cleanup(withdrawn, timeout=30)


def test_retirement_retry_requires_journal_sync_before_removing_intent(candidate, monkeypatch):
    result = publish(candidate)
    withdrawn = workspace.allocate(timeout=30)
    try:
        publication.withdraw_publication(result.transaction, withdrawn, timeout=30)
    finally:
        workspace.cleanup(withdrawn, timeout=30)
    workspace.cleanup(candidate[1], timeout=30)
    journal = candidate[3] / publication.NAMESPACE
    intent = journal / (result.transaction + ".json")
    tombstone = journal / (result.transaction + ".retired.json")
    with monkeypatch.context() as patch:
        fail_worker(patch, "retired")
        with pytest.raises(publication.PublicationError):
            publication.retire_publication(result.transaction, timeout=30)
    assert intent.exists() and tombstone.exists()
    with monkeypatch.context() as patch:
        patch.setattr(publication, "_worker", deny_journal_sync_worker)
        with pytest.raises(publication.PublicationError):
            publication.retire_publication(result.transaction, timeout=30)
    assert intent.exists() and tombstone.exists()
    assert publication.retire_publication(result.transaction, timeout=30) == "absent"


def test_withdrawn_result_requires_journal_sync_before_confirmation(candidate, monkeypatch):
    result = publish(candidate)
    withdrawn = workspace.allocate(timeout=30)
    try:
        with monkeypatch.context() as patch:
            fail_worker(patch, "withdrawn")
            with pytest.raises(publication.PublicationError):
                publication.withdraw_publication(result.transaction, withdrawn, timeout=30)
        journal = candidate[3] / publication.NAMESPACE
        assert (journal / (result.transaction + ".withdrawn.json")).exists()
        assert not result.root.exists() and (withdrawn.root / "candidate/demo.py").exists()
        with monkeypatch.context() as patch:
            patch.setattr(publication, "_worker", deny_journal_sync_worker)
            with pytest.raises(publication.PublicationError):
                publication.recover_publication(result.transaction, timeout=30)
            with pytest.raises(publication.PublicationError):
                publication.withdraw_publication(result.transaction, withdrawn, timeout=30)
        assert publication.withdraw_publication(result.transaction, withdrawn, timeout=30) == "withdrawn"
    finally:
        workspace.cleanup(withdrawn, timeout=30)


def test_abort_retry_requires_journal_sync_before_confirming_decision(candidate, monkeypatch):
    with monkeypatch.context() as patch:
        fail_worker(patch, "intent")
        with pytest.raises(publication.PublicationError) as caught:
            publish(candidate)
    token = caught.value.transaction
    with monkeypatch.context() as patch:
        fail_worker(patch, "abort")
        with pytest.raises(publication.PublicationError):
            publication.abort_publication(token, timeout=30)
    journal = candidate[3] / publication.NAMESPACE
    assert (journal / (token + ".aborted.json")).exists()
    with monkeypatch.context() as patch:
        patch.setattr(publication, "_worker", deny_journal_sync_worker)
        with pytest.raises(publication.PublicationError):
            publication.abort_publication(token, timeout=30)
    assert candidate[2].candidate.root.exists()
    assert publication.abort_publication(token, timeout=30) == "aborted"


def test_replaced_journal_during_sync_is_rejected_without_deletion(tmp_path, monkeypatch):
    journal = tmp_path / "journal"
    journal.mkdir(mode=0o700)
    (journal / "evidence").write_bytes(b"retain original")
    original = tmp_path / "original"
    identity = publication._private_directory(journal)
    real_sync = os.fsync

    def replace_during_sync(descriptor):
        real_sync(descriptor)
        journal.rename(original)
        journal.mkdir(mode=0o700)
        (journal / "competitor").write_bytes(b"retain competitor")

    monkeypatch.setattr(os, "fsync", replace_during_sync)
    with pytest.raises(ValueError, match="during sync"):
        publication._sync_journal(journal, identity)
    assert (original / "evidence").read_bytes() == b"retain original"
    assert (journal / "competitor").read_bytes() == b"retain competitor"


@pytest.mark.parametrize("operation", ["recover", "abort", "withdraw", "retire"])
def test_replaced_journal_between_entry_and_lock_stops_transaction(candidate, monkeypatch, operation):
    withdrawn = None
    if operation in ("recover", "abort"):
        with monkeypatch.context() as patch:
            fail_worker(patch, "intent")
            with pytest.raises(publication.PublicationError) as caught:
                publish(candidate)
        token = caught.value.transaction
    else:
        result = publish(candidate)
        token = result.transaction
        withdrawn = workspace.allocate(timeout=30)
        if operation == "retire":
            publication.withdraw_publication(token, withdrawn, timeout=30)
            workspace.cleanup(withdrawn, timeout=30)
            withdrawn = None
            workspace.cleanup(candidate[1], timeout=30)

    def snapshot(path):
        return {entry.name: entry.read_bytes() for entry in path.iterdir()}

    journal = candidate[3] / publication.NAMESPACE
    before = snapshot(journal)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(publication, "_worker", replace_journal_after_entry_worker)
            with pytest.raises(publication.PublicationError):
                if operation == "recover":
                    publication.recover_publication(token, timeout=30)
                elif operation == "abort":
                    publication.abort_publication(token, timeout=30)
                elif operation == "withdraw":
                    publication.withdraw_publication(token, withdrawn, timeout=30)
                else:
                    publication.retire_publication(token, timeout=30)
        assert snapshot(journal) == before
        assert snapshot(candidate[3] / "original-journal") == before
        if operation == "withdraw":
            assert result.root.exists() and not (withdrawn.root / "candidate").exists()
    finally:
        if withdrawn is not None:
            workspace.cleanup(withdrawn, timeout=30)
