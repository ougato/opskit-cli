"""Artifact limits preserve old pending data; real staging locks reject races."""
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from core import updater


@pytest.fixture
def download_env(tmp_path, monkeypatch):
    from core import mirror
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(mirror, "get_sources", lambda *a: ["https://github.com"])
    monkeypatch.setattr(updater.shutil, "disk_usage", lambda *a: SimpleNamespace(free=2 * 1024 ** 3))
    monkeypatch.setattr(updater, "MAX_UPDATE_BYTES", 8)
    cache = updater._get_pending_path().parent
    cache.mkdir()
    old = updater._get_pending_path()
    old.write_bytes(b"previous verified pending")
    return old


@pytest.mark.parametrize("status,headers,chunks,initial", [
    (200, {}, [b"123456", b"789"], b""),
    (206, {}, [b"789"], b"123456"),
    (200, {"Content-Length": "9"}, [b"not read"], b""),
    (200, {"Content-Encoding": "gzip"}, [b"not read"], b""),
])
def test_artifact_limit_never_overwrites_old_pending(download_env, monkeypatch, status, headers, chunks, initial):
    before = download_env.read_bytes()
    partial = updater._get_pending_tmp_path()
    if initial:
        partial.write_bytes(initial)
    seen = []
    class Response:
        status_code = status
        def __init__(self):
            self.headers = headers
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def iter_bytes(self, size):
            for chunk in chunks:
                seen.append(chunk)
                yield chunk
    calls = []
    def stream(*args, **kwargs):
        calls.append(kwargs)
        return Response()
    monkeypatch.setattr(updater.httpx, "stream", stream)
    assert updater._download_update("https://github.com/owner/repo/artifact", "a" * 64) is None
    assert len(calls) == 1
    assert calls[0]["headers"]["Accept-Encoding"] == "identity"
    assert download_env.read_bytes() == before
    assert not partial.exists()
    if headers:
        assert not seen


def test_oversized_existing_partial_rejected_before_request(download_env, monkeypatch):
    updater._get_pending_tmp_path().write_bytes(b"x" * 9)
    monkeypatch.setattr(updater.httpx, "stream", lambda *a, **k: pytest.fail("oversized partial reached network"))
    assert updater._download_update("https://github.com/owner/repo/artifact", "a" * 64) is None
    assert not updater._get_pending_tmp_path().exists()
    assert download_env.read_bytes() == b"previous verified pending"


@pytest.mark.parametrize("magic", [b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
                                  b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
                                  b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"])
def test_macos_artifact_headers(magic, tmp_path, monkeypatch):
    path = tmp_path / "artifact"
    path.write_bytes(magic + b"\0" * (1024 * 100))
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    assert updater._verify_binary(path)
    path.write_bytes(b"\x7fELF" + b"\0" * (1024 * 100))
    assert not updater._verify_binary(path)


def test_real_competing_update_process_rejected_before_download(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path))
    source = """
import sys
from core import updater
with updater._update_resource.hold():
    print('ready', flush=True)
    sys.stdin.read()
"""
    child = subprocess.Popen([sys.executable, "-u", "-c", source], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(RuntimeError, match="busy"):
            updater._download_update("https://github.com/owner/repo/artifact", "a" * 64)
        with pytest.raises(RuntimeError, match="busy"):
            updater._do_apply(tmp_path / "unused", updater.APP_VERSION + 1)
        child.terminate()
        child.communicate(timeout=5)
        with updater._update_resource.hold(), updater._update_resource.hold():
            assert updater._update_lock_path().exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_changing_resource_inside_lock_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "first"))
    with updater._update_resource.hold():
        monkeypatch.setenv("OPSKIT_DATA_DIR", str(tmp_path / "second"))
        with pytest.raises(RuntimeError, match="resource changed"):
            with updater._update_resource.hold():
                pytest.fail("changed resource bypassed lock")


def test_partial_hard_link_preserves_unrelated_target(download_env, tmp_path):
    target = tmp_path / "unrelated-file"
    target.write_bytes(b"preserved")
    os.link(target, updater._get_pending_tmp_path())
    with pytest.raises(RuntimeError, match="hard links"):
        updater._download_update("https://github.com/owner/repo/artifact", "a" * 64)
    assert target.read_bytes() == b"preserved"
