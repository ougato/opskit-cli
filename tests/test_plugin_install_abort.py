"""Only an explicit decision can abandon a prepared publication; no tree deletion."""
import json
import os

import pytest

from core import plugin_install_transaction as publication
from core import plugin_workspace as workspace
import test_plugin_install_transaction as publication_tests

candidate = publication_tests.candidate


def prepare_intent(candidate, monkeypatch):
    monkeypatch.setenv("OPSKIT_TEST_PUBLICATION_FAULT", "trust")
    with monkeypatch.context() as patch:
        patch.setattr(publication, "_worker", publication_tests.fault_worker)
        with pytest.raises(publication.PublicationError) as caught:
            publication_tests.publish(candidate)
    return caught.value.transaction


def test_abort_never_deletes_and_retirement_requires_explicit_workspace_cleanup(candidate, monkeypatch):
    from core.plugin_trust import grant, is_trusted
    token = prepare_intent(candidate, monkeypatch)
    grant("demo", candidate[2].fingerprint, "1.0.0", "https://example.invalid/demo.git")
    assert is_trusted("demo", candidate[2].fingerprint)
    root = candidate[2].candidate.root
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert publication.recover_publication(token, timeout=30) == "prepared"
    assert publication.abort_publication(token, timeout=30) == "aborted"
    assert not is_trusted("demo", candidate[2].fingerprint)
    assert publication.inspect_publication(token, timeout=30) == "aborted"
    assert publication.recover_publication(token, timeout=30) == "aborted"
    assert publication.abort_publication(token, timeout=30) == "aborted"
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    with pytest.raises(publication.PublicationError):
        publication.retire_publication(token, timeout=30)
    assert root.exists()
    workspace.cleanup(candidate[1], timeout=30)
    assert publication.inspect_publication(token, timeout=30) == "aborted"
    assert publication.retire_publication(token, timeout=30) == "absent"
    assert publication.retire_publication(token, timeout=30) == "absent"
    assert not list((candidate[3] / publication.NAMESPACE).iterdir())
    # A completed abort must not permanently consume the plugin name/alias.
    from core.plugin_fetch_prepare import prepare_candidate
    from core.plugin_fetch_review import review_candidate
    next_workspace = workspace.allocate(timeout=30)
    try:
        prepared = prepare_candidate(candidate[0], next_workspace, timeout=30)
        reviewed = review_candidate(candidate[0], next_workspace, prepared, timeout=30)
        installed = publication.publish_candidate(candidate[0], next_workspace, reviewed, "demo", timeout=30)
        assert installed.root == candidate[3] / "demo"
        assert not is_trusted("demo", reviewed.fingerprint)
    finally:
        workspace.cleanup(next_workspace, timeout=30)


@pytest.mark.parametrize("change", ["content", "extra", "hardlink", "missing", "replacement", "destination"])
def test_abort_refuses_changed_or_uncertain_objects(candidate, monkeypatch, change):
    token = prepare_intent(candidate, monkeypatch)
    root = candidate[2].candidate.root
    if change == "content":
        (root / "demo.py").write_bytes(b"developer change")
    elif change == "extra":
        (root / "developer.txt").write_bytes(b"keep")
    elif change == "hardlink":
        os.link(root / "demo.py", candidate[3] / "developer-link")
    elif change == "missing":
        root.rename(root.with_name("keep"))
    elif change == "replacement":
        root.rename(root.with_name("keep"))
        root.mkdir(mode=0o700)
    else:
        (candidate[3] / "demo").mkdir()
        (candidate[3] / "demo/keep").write_bytes(b"other writer")
    journal = candidate[3] / publication.NAMESPACE
    before = {p.name: p.read_bytes() for p in journal.iterdir()}
    with pytest.raises(publication.PublicationError):
        publication.abort_publication(token, timeout=30)
    assert {p.name: p.read_bytes() for p in journal.iterdir()} == before
    assert candidate[1].root.exists()
    if change == "hardlink":
        # The external fixture link is test-owned. Remove that exact link so
        # ordinary workspace cleanup need not weaken its hard-link rejection.
        (candidate[3] / "developer-link").unlink()


def test_published_candidate_requires_withdrawal_not_abort(candidate):
    result = publication_tests.publish(candidate)
    with pytest.raises(publication.PublicationError):
        publication.abort_publication(result.transaction, timeout=30)
    assert result.root.exists()
    assert publication.inspect_publication(result.transaction, timeout=30) == "published"


@pytest.mark.parametrize("change", ["marker", "extra", "target", "published-marker"])
def test_aborted_evidence_never_hides_later_unknown_objects(candidate, monkeypatch, change):
    token = prepare_intent(candidate, monkeypatch)
    publication.abort_publication(token, timeout=30)
    journal = candidate[3] / publication.NAMESPACE
    if change == "marker":
        (journal / (token + ".aborted.json")).write_text(json.dumps({"schema": 1, "outcome": "aborted"}))
    elif change == "extra":
        (candidate[1].root / "unknown").write_bytes(b"preserve")
    elif change == "target":
        (candidate[3] / "demo").mkdir()
    else:
        (journal / (token + ".published.json")).write_text("{}")
    before = {p.name: p.read_bytes() for p in journal.iterdir()}
    with pytest.raises(publication.PublicationError):
        publication.recover_publication(token, timeout=30)
    with pytest.raises(publication.PublicationError):
        publication.retire_publication(token, timeout=30)
    assert {p.name: p.read_bytes() for p in journal.iterdir()} == before
