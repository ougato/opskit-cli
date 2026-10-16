"""Metadata checks cannot authorize altered candidate bytes or execute entries."""
from dataclasses import replace
import hashlib
import json
import os

import pytest

from core import plugin_fetch_prepare as preparation, plugin_fetch_review as review
from core import plugin_workspace as workspace
from core.private_file import temporary_private_file
from test_plugin_fetch_candidate import archive, response
from worker_test_markers import publish_process_marker, marked_process_exists


BASE = {"name": "demo", "version": "1.0.0", "api_version": 2, "kind": "python", "entry": "demo"}


def entries(manifest=None, *, checksums=False, executable=False):
    data = json.dumps(BASE if manifest is None else manifest).encode()
    result = [("plugin.yaml", data, {}), ("demo.py", b"raise RuntimeError('must never import')\n", {})]
    if executable:
        result.append(("bin/run", b"#!/bin/sh\nexit 99\n", {"mode": 0o755}))
    if checksums:
        hashes = {name: hashlib.sha256(body).hexdigest() for name, body, _ in result}
        result.append(("CHECKSUMS.yaml", json.dumps({"version": "1.0.0", "files": hashes}).encode(), {}))
    return result


@pytest.fixture
def staged():
    with workspace.owned_workspace(timeout=30, cleanup_timeout=30) as lease:
        with temporary_private_file() as source:
            def create(items=None):
                items = entries() if items is None else items
                source.seek(0); source.truncate()
                source.write(response(archive(items), entries=len(items), size=sum(len(e[1]) for e in items)))
                source.flush()
                prepared = preparation.prepare_candidate(source, lease, timeout=30)
                return source, lease, prepared
            yield create


def check(value):
    return review.review_candidate(*value, timeout=30)


@pytest.mark.parametrize("checksums", [False, True])
def test_valid_review_reuses_manifest_fingerprint_and_receipt_without_trust(staged, checksums):
    from core.plugin import load_manifest
    from core.plugin_trust import compute_fingerprint
    from core.plugin_install_receipt import content_receipt
    from core.paths import data_dir
    value = staged(entries(checksums=checksums))
    result = check(value)
    assert result.checksums == ("ok" if checksums else "missing")
    assert result.manifest_sha256 == review._manifest_digest(load_manifest(result.candidate.root))
    assert result.fingerprint == compute_fingerprint(result.candidate.root)
    assert result.content_receipt == content_receipt(result.candidate.root)
    assert not (data_dir() / "plugin_trust.yaml").exists()


@pytest.mark.parametrize("change", ["bytes", "extra", "cache", "empty-dir", "missing"])
def test_every_byte_and_node_is_bound_to_response_including_ignored_files(staged, change):
    value = staged();root = value[2].root
    if change == "bytes":
        (root / "demo.py").write_bytes(b"x" * (root / "demo.py").stat().st_size)
    elif change == "extra":
        (root / "extra").write_bytes(b"unknown")
    elif change == "cache":
        (root / "__pycache__").mkdir();(root / "__pycache__/demo.pyc").write_bytes(b"unknown")
    elif change == "empty-dir":
        (root / "empty").mkdir()
    else:
        (root / "demo.py").unlink()
    with pytest.raises(ValueError, match="review rejected"):
        check(value)


@pytest.mark.parametrize("manifest", [{**BASE, "name": "software"}, {**BASE, "entry": "core"},
                                     {**BASE, "entry": "missing"}, {"name": "demo"},
                                     {**BASE, "kind": "exec", "entry": "demo.py"},
                                     {**BASE, "permissions": ["administrator"]}])
def test_invalid_official_reserved_or_missing_entry_is_rejected(staged, manifest):
    value = staged(entries(manifest))
    with pytest.raises(ValueError, match="review rejected"):
        check(value)


def test_executable_entry_is_checked_without_running_it(staged):
    value = staged(entries({**BASE, "kind": "exec", "entry": {"linux": "bin/run"}}, executable=True))
    assert check(value).checksums == "missing"


def test_mismatched_checksums_rejected_even_when_archive_matches(staged):
    items = entries(checksums=True)
    items[-1] = ("CHECKSUMS.yaml", b'{"files":{}}', {})
    value = staged(items)
    with pytest.raises(ValueError, match="review rejected"):
        check(value)


@pytest.mark.parametrize("field", ["identity", "archive_sha256", "reported_commit", "files"])
def test_changed_preparation_binding_is_rejected(staged, field):
    source, lease, prepared = staged()
    values = {"identity": [prepared.identity[0], prepared.identity[1] + 1],
              "archive_sha256": "0" * 64, "reported_commit": "0" * 40, "files": prepared.files + 1}
    with pytest.raises(ValueError, match="review rejected"):
        check((source, lease, replace(prepared, **{field: values[field]})))


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable modes")
def test_execution_mode_change_is_rejected(staged):
    value = staged();(value[2].root / "demo.py").chmod(0o700)
    with pytest.raises(ValueError, match="review rejected"):
        check(value)


def blocked_review_worker(connection, *args):
    stage = os.environ["OPSKIT_TEST_FETCH_REVIEW_STAGE"]
    def block(*args, **kwargs):
        read, write = os.pipe()
        publish_process_marker(os.environ["OPSKIT_TEST_FETCH_REVIEW_MARKER"])
        try:
            os.read(read, 1)
        finally:
            os.close(read);os.close(write)
    if stage == "tree":
        review._raw_tree = block
    elif stage == "manifest":
        from core import plugin
        plugin.load_manifest = block
    else:
        from core import plugin_integrity
        plugin_integrity.verify_checksums = block
    review._review_worker(connection, *args)


@pytest.mark.parametrize("stage", ["tree", "manifest", "checksums"])
def test_all_review_io_stages_are_supervised(staged, tmp_path, monkeypatch, expire_worker_after_marker, stage):
    source, lease, prepared = staged();marker = tmp_path / "worker.json"
    monkeypatch.setenv("OPSKIT_TEST_FETCH_REVIEW_STAGE", stage)
    monkeypatch.setenv("OPSKIT_TEST_FETCH_REVIEW_MARKER", str(marker))
    monkeypatch.setattr(review, "_review_worker", blocked_review_worker)
    budget, observed = expire_worker_after_marker(marker)
    with pytest.raises(TimeoutError):
        review.review_candidate(source, lease, prepared, timeout=budget)
    assert observed and not marked_process_exists(marker) and not source.closed
    assert (prepared.root / "demo.py").read_bytes() == entries()[1][1]


def malformed_review_worker(connection, *args):
    from network import worker
    original = worker.send
    def send(connection, result):
        if result.get("ok"):
            result.update(json.loads(os.environ["OPSKIT_TEST_FETCH_REVIEW_RESULT"]))
        original(connection, result)
    worker.send = send
    review._review_worker(connection, *args)


@pytest.mark.parametrize("change", [{"request": "stale"}, {"manifest": True}, {"receipt": "bad"},
                                    {"checksums": "mismatch"}, {"ok": 1}, {"extra": 1}])
def test_malformed_review_result_cannot_authorize_content(staged, monkeypatch, change):
    value = staged()
    monkeypatch.setenv("OPSKIT_TEST_FETCH_REVIEW_RESULT", json.dumps(change))
    monkeypatch.setattr(review, "_review_worker", malformed_review_worker)
    with pytest.raises(RuntimeError, match="invalid candidate review result"):
        check(value)
