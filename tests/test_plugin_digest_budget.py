"""Streaming fingerprints retain legacy normalization and bound actual reads."""
import hashlib
import os
from pathlib import Path

import pytest

from core import plugin_integrity as integrity


@pytest.mark.parametrize("payload", [
    b"", b"a\r\nb\rc\n", b"a" * 65535 + b"\r\nb\r",
    b"a" * 65535 + b"\r\r\nb", b"a\r\n" * 30000 + b"\x00late",
    bytes(range(256)) * 1024,
], ids=["empty", "mixed-lines", "boundary-crlf", "boundary-cr-crlf", "late-null", "binary"])
def test_streaming_digest_matches_legacy_across_chunks(tmp_path, monkeypatch, payload):
    path = tmp_path / "content"
    path.write_bytes(payload)
    expected = payload if b"\x00" in payload else payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    monkeypatch.setattr(Path, "read_bytes", lambda *a: pytest.fail("digest read entire file"))
    assert integrity.content_digest(path) == hashlib.sha256(expected).digest()


def test_digest_limits_actual_bytes_not_a_prior_stat(tmp_path):
    path = tmp_path / "changed"
    path.write_bytes(b"x" * 65)
    with pytest.raises(ValueError, match="digest exceeds limit"):
        integrity.content_digest(path, max_bytes=64)
    assert integrity.content_digest(path, max_bytes=65) == hashlib.sha256(b"x" * 65).digest()


def test_file_tree_budget_uses_actual_read_count(tmp_path, monkeypatch):
    for name in ("a", "b"):
        (tmp_path / name).write_bytes(b"x" * 64)
    original = integrity._content_digest
    original_stat = Path.stat
    def stale_stat(path, **kwargs):
        result = original_stat(path, **kwargs)
        if path.name in {"a", "b"}:
            fields = list(result)
            fields[6] = 0
            return os.stat_result(fields)
        return result
    monkeypatch.setattr(Path, "stat", stale_stat)
    budgets = []
    def observed(path, budget):
        budgets.append(budget)
        return original(path, budget)
    monkeypatch.setattr(integrity, "_content_digest", observed)
    integrity._file_hashes(tmp_path)
    assert budgets == [128 * 1024**2, 128 * 1024**2 - 64]


def test_tree_rejects_empty_directory_flood_before_hashing(tmp_path, monkeypatch):
    for index in range(5):
        (tmp_path / str(index)).mkdir()
    with pytest.raises(ValueError, match="tree limits"):
        list(integrity._bounded_paths(tmp_path, max_entries=4))


def test_tree_prunes_excluded_directory_before_opening(tmp_path, monkeypatch):
    ignored = tmp_path / ".git"
    ignored.mkdir()
    (tmp_path / "code.py").write_text("pass")
    from core import plugin_tree
    original = plugin_tree.os.scandir
    def guarded(path):
        assert Path(path) != ignored
        return original(path)
    monkeypatch.setattr(plugin_tree.os, "scandir", guarded)
    assert list(integrity._bounded_paths(tmp_path)) == [tmp_path / "code.py"]


def test_tree_depth_is_bounded(tmp_path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    (nested / "code").write_text("x")
    with pytest.raises(ValueError, match="tree limits"):
        list(integrity._bounded_paths(tmp_path, max_depth=2))


def test_tree_preserves_legacy_sorted_hash_order(tmp_path):
    for name in ("a/code", "a.py", "Z", "b"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    expected = [p.relative_to(tmp_path).as_posix() for p in sorted(tmp_path.rglob("*")) if p.is_file()]
    assert list(integrity._file_hashes(tmp_path)) == expected


@pytest.mark.parametrize("content", [b"x" * (1024 * 1024 + 1), b"[" * 65 + b"0" + b"]" * 65,
                                    b"a: &a [0]\nb: *a\n"], ids=["bytes", "depth", "alias"])
def test_checksum_metadata_rejected_before_document_construction(tmp_path, monkeypatch, content):
    (tmp_path / "CHECKSUMS.yaml").write_bytes(content)
    monkeypatch.setattr(integrity.yaml, "safe_load", lambda *args: pytest.fail("constructed overbudget YAML"))
    assert integrity.verify_checksums(tmp_path) == integrity.CHECK_MISMATCH
