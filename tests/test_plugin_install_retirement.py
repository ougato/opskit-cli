"""Withdrawal preserves changed trees; retirement removes only bound metadata."""
import json
import os

import pytest

from core import plugin_install_transaction as publication
from core import plugin_workspace as workspace
import test_plugin_install_transaction as publication_tests

candidate = publication_tests.candidate
publish = publication_tests.publish


def test_withdraw_cleanup_retire_and_idempotent_absence(candidate):
    from core.plugin_trust import grant, is_trusted
    result = publish(candidate)
    withdrawn = workspace.allocate(timeout=30)
    try:
        grant("demo", candidate[2].fingerprint, "1.0.0", "https://example.invalid/demo.git")
        assert publication.withdraw_publication(result.transaction, withdrawn, timeout=30) == "withdrawn"
        assert not result.root.exists()
        assert (withdrawn.root / "candidate/demo.py").read_bytes()
        assert not is_trusted("demo", candidate[2].fingerprint)
        assert publication.inspect_publication(result.transaction, timeout=30) == "withdrawn"
        assert publication.recover_publication(result.transaction, timeout=30) == "withdrawn"
        assert publication.withdraw_publication(result.transaction, withdrawn, timeout=30) == "withdrawn"
        with pytest.raises(publication.PublicationError):
            publication.retire_publication(result.transaction, timeout=30)
    finally:
        workspace.cleanup(withdrawn, timeout=30)
    workspace.cleanup(candidate[1], timeout=30)
    assert publication.retire_publication(result.transaction, timeout=30) == "absent"
    assert publication.retire_publication(result.transaction, timeout=30) == "absent"
    assert not list((candidate[3] / publication.NAMESPACE).iterdir())


@pytest.mark.parametrize("change", ["content", "extra", "empty", "hardlink"])
def test_withdraw_refuses_developer_changes(candidate, change):
    result = publish(candidate)
    if change == "content":
        (result.root / "demo.py").write_bytes(b"developer modification")
    elif change == "extra":
        (result.root / "local.txt").write_bytes(b"keep")
    elif change == "empty":
        (result.root / "local").mkdir()
    else:
        os.link(result.root / "demo.py", candidate[3] / "developer-link")
    withdrawn = workspace.allocate(timeout=30)
    try:
        with pytest.raises(publication.PublicationError):
            publication.withdraw_publication(result.transaction, withdrawn, timeout=30)
        assert result.root.exists() and not list(withdrawn.root.iterdir())
        with pytest.raises(publication.PublicationError):
            publication.retire_publication(result.transaction, timeout=30)
        assert result.root.exists()
    finally:
        workspace.cleanup(withdrawn, timeout=30)


def retirement_fault_worker(connection, operation, arguments, request):
    from core import plugin_install_retirement as retirement
    from core import directory_publish
    stage = os.environ["OPSKIT_TEST_RETIREMENT_FAULT"]
    original_move = directory_publish.publish_new_directory
    original_confirm = retirement._withdrawn
    original_sync = retirement.sync_directory

    def moved(source, target):
        if stage == "prepared":
            raise OSError("injected before move")
        return original_move(source, target)

    def confirmed(journal, record, value, *, create=False):
        if create and stage == "unconfirmed":
            raise OSError("injected before marker")
        return original_confirm(journal, record, value, create=create)

    def synced(path, identity):
        if stage == "partial" and list(path.glob("*.retired.json")):
            raise OSError("injected after metadata unlink")
        return original_sync(path, identity)

    directory_publish.publish_new_directory = moved
    retirement._withdrawn = confirmed
    retirement.sync_directory = synced
    publication._worker(connection, operation, arguments, request)


@pytest.mark.parametrize("stage", ["prepared", "unconfirmed"])
def test_recovery_confirms_only_already_moved_withdrawal(candidate, monkeypatch, stage):
    result = publish(candidate)
    withdrawn = workspace.allocate(timeout=30)
    try:
        monkeypatch.setenv("OPSKIT_TEST_RETIREMENT_FAULT", stage)
        with monkeypatch.context() as patch:
            patch.setattr(publication, "_worker", retirement_fault_worker)
            with pytest.raises(publication.PublicationError):
                publication.withdraw_publication(result.transaction, withdrawn, timeout=30)
        expected = "withdrawal-prepared" if stage == "prepared" else "withdrawn-unconfirmed"
        assert publication.inspect_publication(result.transaction, timeout=30) == expected
        assert publication.recover_publication(result.transaction, timeout=30) == (
            "withdrawal-prepared" if stage == "prepared" else "withdrawn")
        assert result.root.exists() == (stage == "prepared")
        assert publication.withdraw_publication(result.transaction, withdrawn, timeout=30) == "withdrawn"
    finally:
        workspace.cleanup(withdrawn, timeout=30)


@pytest.mark.parametrize("value", [[], None, 1, "published", {"schema": True}])
def test_malformed_marker_is_controlled_failure(candidate, value):
    result = publish(candidate)
    marker = candidate[3] / publication.NAMESPACE / (result.transaction + ".published.json")
    marker.write_text(json.dumps(value))
    with pytest.raises(publication.PublicationError):
        publication.inspect_publication(result.transaction, timeout=30)
    assert result.root.exists()


@pytest.mark.parametrize("change", ["none", "changed", "unexpected"])
def test_partial_retirement_resumes_only_exact_surviving_records(candidate, monkeypatch, change):
    result = publish(candidate)
    withdrawn = workspace.allocate(timeout=30)
    try:
        publication.withdraw_publication(result.transaction, withdrawn, timeout=30)
    finally:
        workspace.cleanup(withdrawn, timeout=30)
    workspace.cleanup(candidate[1], timeout=30)
    journal = candidate[3] / publication.NAMESPACE
    # Exercise a legitimately absent publication marker: withdrawal can follow
    # an unconfirmed publication too, so it is not mandatory in a tombstone.
    if change == "unexpected":
        (journal / (result.transaction + ".published.json")).unlink()
    monkeypatch.setenv("OPSKIT_TEST_RETIREMENT_FAULT", "partial")
    with monkeypatch.context() as patch:
        patch.setattr(publication, "_worker", retirement_fault_worker)
        with pytest.raises(publication.PublicationError):
            publication.retire_publication(result.transaction, timeout=30)
    assert not (journal / (result.transaction + ".json")).exists()
    assert (journal / (result.transaction + ".retired.json")).exists()
    if change == "changed":
        (journal / (result.transaction + ".withdrawn.json")).write_bytes(b"changed")
    elif change == "unexpected":
        (journal / (result.transaction + ".published.json")).write_bytes(b"new record")
    before = {p.name: p.read_bytes() for p in journal.iterdir()}
    if change != "none":
        with pytest.raises(publication.PublicationError):
            publication.retire_publication(result.transaction, timeout=30)
        assert {p.name: p.read_bytes() for p in journal.iterdir()} == before
    else:
        assert publication.inspect_publication(result.transaction, timeout=30) == "retiring"
        assert publication.recover_publication(result.transaction, timeout=30) == "retiring"
        assert {p.name: p.read_bytes() for p in journal.iterdir()} == before
        assert publication.retire_publication(result.transaction, timeout=30) == "absent"
        assert not list(journal.iterdir())
