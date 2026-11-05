"""Publication integrity tests; real isolated preparation lives in native_fetch tests."""
from contextlib import contextmanager
import hashlib
import json
from types import SimpleNamespace

import pytest

from core.native_fetch_catalog import FetchCatalog
from core.plugin_fetch_candidate import CandidateFile, CandidateInventory


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    from core import native_fetch_catalog as catalog, native_fetch
    monkeypatch.setattr(catalog, "get_backend", lambda: SimpleNamespace(probe=lambda: None))
    python = tmp_path / "python"
    python.mkdir()
    (python / "python.exe").write_bytes(b"test-only-executable-not-run")
    @contextmanager
    def probe(*args, **kwargs):
        root = args[2]
        commit = (root / "refs/heads/main").read_text().strip()
        file = CandidateFile("probe.txt", 0o644, 19, hashlib.sha256(b"native-fetch-probe\n").hexdigest())
        yield CandidateInventory(commit, "a" * 64, (file,), 19), None
    monkeypatch.setattr(native_fetch, "local_candidate", probe)
    value = FetchCatalog(tmp_path / "catalog")
    identity = value.prepare(python)
    return value, identity, python


def test_published_tools_remain_independent_of_originals(prepared):
    catalog, identity, python = prepared
    (python / "python.exe").write_bytes(b"changed-original")
    runtime, record = catalog.load()
    assert runtime.parent.name == identity
    assert (runtime / "python/python.exe").read_bytes() == b"test-only-executable-not-run"
    assert set(record["files"]) == {"python/python.exe"}


@pytest.mark.parametrize("mutation", ["bytes", "extra", "manifest", "policy", "pointer", "bool"])
def test_tampered_or_stale_catalog_is_rejected(prepared, monkeypatch, mutation):
    catalog, identity, _ = prepared
    obj = catalog.root / "objects" / identity
    if mutation == "bytes":
        (obj / "runtime/python/python.exe").write_bytes(b"changed")
    elif mutation == "extra":
        (obj / "runtime/python/injected.dll").write_bytes(b"changed")
    elif mutation == "manifest":
        record = json.loads((obj / "manifest.json").read_text())
        record["files"]["python/python.exe"] = "0" * 64
        (obj / "manifest.json").write_text(json.dumps(record))
    elif mutation == "policy":
        monkeypatch.setattr("core.native_fetch_catalog.native_fetch_snapshot", lambda: {"changed": b"policy"})
    else:
        path = catalog.root / "current.json"
        pointer = json.loads(path.read_text())
        pointer["object" if mutation == "pointer" else "schema_version"] = "../outside" if mutation == "pointer" else True
        path.write_text(json.dumps(pointer))
    with pytest.raises(ValueError):
        catalog.load()


def test_failed_preparation_preserves_previous_pointer(prepared, monkeypatch):
    catalog, _, python = prepared
    before = (catalog.root / "current.json").read_bytes()
    from core import native_fetch
    @contextmanager
    def failed(*args, **kwargs):
        raise RuntimeError("probe failed")
        yield
    monkeypatch.setattr(native_fetch, "local_candidate", failed)
    with pytest.raises(RuntimeError, match="probe failed"):
        catalog.prepare(python)
    assert (catalog.root / "current.json").read_bytes() == before
    from core import native_catalog_cleanup
    assert native_catalog_cleanup.inspect("prepare") == []
    catalog.load()


def test_loaded_inventories_are_passed_to_execution_snapshot(prepared, monkeypatch, tmp_path):
    catalog, _, _ = prepared
    from core import native_fetch
    @contextmanager
    def snapshot(python, git, source, **kwargs):
        assert kwargs["tool_files"] == {
            "python": {"python.exe": hashlib.sha256(b"test-only-executable-not-run").hexdigest()},
        }
        yield "inventory", "response"
    monkeypatch.setattr(native_fetch, "local_candidate", snapshot)
    with catalog.local_candidate(tmp_path, staging_parent=tmp_path, deadline=100,
                                 checkpoint=lambda: None) as result:
        assert result == ("inventory", "response")
