"""Update cache writes must never replace an unvalidated record or link."""
import os
import subprocess
import sys
import time

import pytest

from core import updater


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    path = updater._get_cache_path()
    path.parent.mkdir(parents=True)
    return path


@pytest.mark.parametrize("body", [b"{broken", b"[1, 2]", b"x" * 65537, b"\xff"],
                         ids=["malformed", "array", "oversized", "invalid-utf8"])
def test_invalid_existing_record_is_not_overwritten(cache, body):
    cache.write_bytes(body)
    assert updater._load_check_cache() == {}
    updater._save_check_cache({"latest": 123})
    assert cache.read_bytes() == body


@pytest.mark.parametrize("link_type", ["hardlink", "symlink"])
def test_linked_cache_neither_read_nor_overwritten(cache, tmp_path, link_type):
    outside = tmp_path / "user-data"
    outside.write_text('{"latest": 42}', encoding="utf-8")
    if link_type == "hardlink":
        os.link(outside, cache)
    else:
        try:
            cache.symlink_to(outside)
        except OSError:
            pytest.skip("symlink privilege unavailable")
    assert updater._load_check_cache() == {}
    updater._save_check_cache({"latest": 123})
    assert outside.read_text(encoding="utf-8") == '{"latest": 42}'
    assert cache.exists()


def test_fixed_temporary_name_is_never_overwritten(cache):
    temporary = cache.with_suffix(cache.suffix + ".tmp")
    temporary.write_bytes(b"user-owned file")
    updater._save_check_cache({"latest": 7, "apply_job": "still-pinned"})
    updater._save_check_cache({"etag": "tag"})
    assert updater._load_check_cache() == {"latest": 7, "apply_job": "still-pinned", "etag": "tag"}
    assert temporary.read_bytes() == b"user-owned file"


def test_busy_update_lock_does_not_replace_or_clear_cache(cache, monkeypatch):
    updater._write_update_record(cache, {"candidate_id": "a" * 32, "pending_version": 10})
    before = cache.read_bytes()
    class Busy:
        def __enter__(self):
            raise RuntimeError("busy")
        def __exit__(self, *_):
            return False
    monkeypatch.setattr(updater._update_resource, "hold", lambda: Busy())
    updater._save_check_cache({"latest": 11})
    assert cache.read_bytes() == before


def test_other_process_lock_defers_cache_write_without_losing_evidence(cache):
    updater._write_update_record(cache, {"pending_version": 10, "candidate_id": "a" * 32})
    before = cache.read_bytes()
    ready = cache.parent / "child-ready"
    source = """
import sys
from pathlib import Path
from core import updater
with updater._update_resource.hold():
    Path(sys.argv[1]).write_text('ready')
    sys.stdin.read()
"""
    child = subprocess.Popen([sys.executable, "-u", "-c", source, str(ready)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists(), "child failed to acquire update lock"
        updater._save_check_cache({"latest": 11})
        assert cache.read_bytes() == before
    finally:
        child.communicate(input=b"", timeout=5)
    updater._save_check_cache({"latest": 11})
    assert updater._load_check_cache() == {"pending_version": 10, "candidate_id": "a" * 32, "latest": 11}
