"""Publication binds reviewed bytes and retains uncertainty without deleting."""
from dataclasses import replace
import json
import os

import pytest

from core import plugin_install_transaction as publication
from core import plugin_workspace as workspace
from core.plugin_fetch_prepare import prepare_candidate
from core.plugin_fetch_review import review_candidate
from core.private_file import temporary_private_file
from test_plugin_fetch_candidate import archive, response
from test_plugin_fetch_review import entries
from worker_test_markers import publish_process_marker, marked_process_exists


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    plugins.mkdir(mode=0o700)
    if os.name == "nt":
        from core.secrets import restrict_permissions
        restrict_permissions(plugins)
    monkeypatch.setenv("OPSKIT_PLUGINS_DIR", str(plugins))
    lease = workspace.allocate(timeout=30)
    try:
        with temporary_private_file() as source:
            files = entries()
            source.write(response(archive(files), entries=len(files), size=sum(len(f[1]) for f in files)))
            source.flush()
            prepared = prepare_candidate(source, lease, timeout=30)
            reviewed = review_candidate(source, lease, prepared, timeout=30)
            yield source, lease, reviewed, plugins
    finally:
        workspace.cleanup(lease, timeout=30)


def publish(candidate):
    source, lease, reviewed, _ = candidate
    return publication.publish_candidate(source, lease, reviewed, "demo", timeout=30)


def test_publication_binds_receipt_and_revokes_matching_old_trust(candidate):
    from core.plugin_trust import grant, is_trusted, trusted_record
    source, lease, reviewed, plugins = candidate
    grant("demo", reviewed.fingerprint, "1.0.0", "https://example.invalid/demo.git")
    assert is_trusted("demo", reviewed.fingerprint)
    result = publish(candidate)
    assert result.root == plugins / "demo" and result.review == reviewed
    assert not reviewed.candidate.root.exists() and not source.closed
    assert not is_trusted("demo", reviewed.fingerprint)
    assert trusted_record("demo")["source"] == "https://example.invalid/demo.git"
    assert (result.root / "demo.py").read_bytes() == entries()[1][1]
    assert publication.inspect_publication(result.transaction, timeout=30) == "published"
    assert publication.recover_publication(result.transaction, timeout=30) == "published"
    paths = list((plugins / publication.NAMESPACE).iterdir())
    assert {p.name for p in paths} == {result.transaction + ".json", result.transaction + ".published.json"}
    record = json.loads((plugins / publication.NAMESPACE / (result.transaction + ".json")).read_bytes())
    assert record["workspace"] == workspace.journal_binding(lease)
    assert record["receipt"] == reviewed.content_receipt


@pytest.mark.parametrize("change", ["bytes", "extra", "cache", "empty", "manifest"])
def test_candidate_changes_after_review_never_publish(candidate, change):
    root = candidate[2].candidate.root
    if change == "bytes":
        (root / "demo.py").write_bytes(b"unknown")
    elif change == "extra":
        (root / "extra").write_bytes(b"unknown")
    elif change == "cache":
        (root / "__pycache__").mkdir()
    elif change == "empty":
        (root / "empty").mkdir()
    else:
        value = json.loads((root / "plugin.yaml").read_bytes()); value["version"] = "2.0.0"
        (root / "plugin.yaml").write_text(json.dumps(value))
    with pytest.raises(publication.PublicationError):
        publish(candidate)
    assert not (candidate[3] / "demo").exists() and root.exists()


@pytest.mark.parametrize("field", ["manifest_sha256", "fingerprint", "content_receipt", "checksums"])
def test_caller_receipt_cannot_relabel_review(candidate, field):
    source, lease, reviewed, plugins = candidate
    forged = replace(reviewed, **{field: "ok" if field == "checksums" else "0" * 64})
    with pytest.raises(publication.PublicationError):
        publication.publish_candidate(source, lease, forged, "demo", timeout=30)
    assert not (plugins / "demo").exists() and reviewed.candidate.root.exists()


def fault_worker(connection, operation, arguments, request):
    from core import directory_publish, plugin_trust
    stage = os.environ["OPSKIT_TEST_PUBLICATION_FAULT"]
    original_outcome, original_publish = publication._outcome, directory_publish.publish_new_directory

    def outcome(journal, record, *, create=False):
        if create and stage == "outcome":
            raise OSError("fixture-secret must not escape")
        return original_outcome(journal, record, create=create)

    def moved(source, destination):
        if stage == "competitor":
            destination.mkdir()
            (destination / "keep").write_bytes(b"other writer")
        original_publish(source, destination)
        if stage == "post-move-change":
            (destination / "demo.py").write_bytes(b"other writer")

    def invalidate(*args):
        raise OSError("fixture-secret trust-store failure")

    publication._outcome = outcome
    directory_publish.publish_new_directory = moved
    if stage == "trust":
        plugin_trust.invalidate = invalidate
    publication._worker(connection, operation, arguments, request)


@pytest.mark.parametrize("stage", ["outcome", "trust", "competitor", "post-move-change"])
def test_failure_retains_bound_evidence_and_never_removes_other_writers(candidate, monkeypatch, stage):
    monkeypatch.setenv("OPSKIT_TEST_PUBLICATION_FAULT", stage)
    with monkeypatch.context() as patch:
        patch.setattr(publication, "_worker", fault_worker)
        with pytest.raises(publication.PublicationError) as caught:
            publish(candidate)
    assert "fixture-secret" not in str(caught.value)
    token = caught.value.transaction
    plugins, root = candidate[3], candidate[2].candidate.root
    assert (plugins / publication.NAMESPACE / (token + ".json")).is_file()
    if stage == "outcome":
        assert not root.exists()
        assert publication.inspect_publication(token, timeout=30) == "published-unconfirmed"
        assert publication.recover_publication(token, timeout=30) == "published"
        assert publication.inspect_publication(token, timeout=30) == "published"
    elif stage == "trust":
        assert root.exists() and not (plugins / "demo").exists()
        assert publication.inspect_publication(token, timeout=30) == "prepared"
        assert publication.recover_publication(token, timeout=30) == "prepared"
        assert root.exists() and not (plugins / "demo").exists()
    else:
        with pytest.raises(publication.PublicationError):
            publication.recover_publication(token, timeout=30)
        if stage == "competitor":
            assert root.exists() and (plugins / "demo/keep").read_bytes() == b"other writer"
        else:
            assert not root.exists() and (plugins / "demo/demo.py").read_bytes() == b"other writer"


def test_inspection_does_not_recreate_missing_locks_or_change_modes(candidate):
    result = publish(candidate)
    from core.paths import data_dir
    locks = [candidate[3] / ".installation.lock", data_dir() / "plugin-runtimes/demo.lock"]
    for lock in locks:
        lock.unlink()
    before = {p: p.stat().st_mode for p in [candidate[3], result.root, candidate[1].root]}
    assert publication.inspect_publication(result.transaction, timeout=30) == "published"
    assert all(not p.exists() for p in locks)
    assert before == {p: p.stat().st_mode for p in before}


@pytest.mark.parametrize("alteration", ["record", "marker", "identity", "content", "hardlink"])
def test_recovery_refuses_corrupt_or_changed_evidence(candidate, alteration):
    result = publish(candidate)
    journal = candidate[3] / publication.NAMESPACE
    if alteration == "hardlink":
        os.link(result.root / "demo.py", candidate[3] / "keep-link")
    elif alteration == "content":
        (result.root / "extra").write_bytes(b"preserve unknown bytes")
    elif alteration == "record":
        (journal / (result.transaction + ".json")).write_bytes(b'{"schema":1,"schema":1}')
    elif alteration == "marker":
        (journal / (result.transaction + ".published.json")).write_bytes(b'{"outcome":"published"}')
    else:
        result.root.rename(result.root.with_name("original"))
        result.root.mkdir(mode=0o700)
    with pytest.raises(publication.PublicationError):
        publication.recover_publication(result.transaction, timeout=30)
    assert result.root.exists()


def test_different_state_root_cannot_replay_publication(candidate, tmp_path, monkeypatch):
    result = publish(candidate)
    other = tmp_path / "other-state"; other.mkdir(mode=0o700)
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(other))
    with pytest.raises(publication.PublicationError):
        publication.recover_publication(result.transaction, timeout=30)
    assert result.root.is_dir() and not list(other.iterdir())


def crashed_worker(connection, operation, arguments, request):
    original = publication._outcome
    def outcome(journal, record, *, create=False):
        if create:
            os._exit(73)
        return original(journal, record, create=create)
    publication._outcome = outcome
    publication._worker(connection, operation, arguments, request)


def test_abrupt_worker_exit_after_rename_keeps_recoverable_transaction(candidate, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(publication, "_worker", crashed_worker)
        with pytest.raises(publication.PublicationError) as caught:
            publish(candidate)
    token = caught.value.transaction
    assert not candidate[2].candidate.root.exists()
    assert publication.inspect_publication(token, timeout=30) == "published-unconfirmed"
    assert publication.recover_publication(token, timeout=30) == "published"


def blocked_worker(connection, operation, arguments, request):
    from core import directory_publish
    stage = os.environ["OPSKIT_TEST_PUBLICATION_BLOCK"]
    def block(*args, **kwargs):
        read, write = os.pipe()
        publish_process_marker(os.environ["OPSKIT_TEST_PUBLICATION_MARKER"])
        try:
            os.read(read, 1)
        finally:
            os.close(read); os.close(write)
    if stage == "receipt":
        publication._receipt = block
    elif stage == "rename":
        directory_publish.publish_new_directory = block
    else:
        original = publication._outcome
        def outcome(journal, record, *, create=False):
            if create:
                block()
            return original(journal, record, create=create)
        publication._outcome = outcome
    publication._worker(connection, operation, arguments, request)


@pytest.mark.parametrize("stage", ["receipt", "rename", "outcome"])
def test_blocked_publication_io_expires_and_worker_is_gone(candidate, tmp_path, monkeypatch, expire_worker_after_marker, stage):
    marker = tmp_path / "worker.json"
    monkeypatch.setenv("OPSKIT_TEST_PUBLICATION_BLOCK", stage)
    monkeypatch.setenv("OPSKIT_TEST_PUBLICATION_MARKER", str(marker))
    original = publication._worker
    monkeypatch.setattr(publication, "_worker", blocked_worker)
    budget, observed = expire_worker_after_marker(marker)
    with pytest.raises(TimeoutError, match="retain workspace"):
        publication.publish_candidate(*candidate[:3], "demo", timeout=budget)
    assert observed and not marked_process_exists(marker)
    assert (candidate[3] / publication.NAMESPACE).is_dir()
    assert (candidate[3] / "demo").is_dir() == (stage == "outcome")
    monkeypatch.setattr(publication, "_worker", original)
    token, = [p.stem for p in (candidate[3] / publication.NAMESPACE).glob("*.json")]
    assert publication.recover_publication(token, timeout=30) == ("published" if stage == "outcome" else "prepared")


@pytest.mark.parametrize("token", ["../unknown", "0" * 31, "g" * 32, 1])
def test_invalid_transaction_is_rejected_before_io(token, tmp_path):
    with pytest.raises(ValueError):
        publication.inspect_publication(token, timeout=1)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("alias", ["../demo", "Demo", "con", "aux", "a" * 97])
def test_invalid_alias_is_rejected_before_publication(candidate, alias):
    with pytest.raises(ValueError):
        publication.publish_candidate(*candidate[:3], alias, timeout=30)
    assert not list(candidate[3].iterdir())
