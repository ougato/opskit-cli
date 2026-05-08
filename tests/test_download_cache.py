"""下载缓存单元测试 — 缓存命中 / 损坏 / 缺失 / 回写场景"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ─── 辅助：创建合法 ZIP ───────────────────────────────────────────────────────

def _make_valid_zip(path: Path, content: bytes = b"X" * 2048) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xray", content)


def _make_corrupt_zip(path: Path) -> None:
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 100)


# ─── 测试 1：_is_zip_valid ────────────────────────────────────────────────────

def test_is_zip_valid_good(tmp_path):
    from core.mirror import _is_zip_valid
    p = tmp_path / "good.zip"
    _make_valid_zip(p)
    assert _is_zip_valid(p) is True


def test_is_zip_valid_corrupt(tmp_path):
    from core.mirror import _is_zip_valid
    p = tmp_path / "bad.zip"
    _make_corrupt_zip(p)
    assert _is_zip_valid(p) is False


def test_is_zip_valid_missing(tmp_path):
    from core.mirror import _is_zip_valid
    assert _is_zip_valid(tmp_path / "nonexistent.zip") is False


def test_is_zip_valid_empty(tmp_path):
    from core.mirror import _is_zip_valid
    p = tmp_path / "empty.zip"
    p.write_bytes(b"")
    assert _is_zip_valid(p) is False


# ─── 测试 2：_is_cached_valid ─────────────────────────────────────────────────

def test_is_cached_valid_zip_good(tmp_path):
    from core.mirror import _is_cached_valid
    p = tmp_path / "file.zip"
    _make_valid_zip(p)
    assert _is_cached_valid(p) is True


def test_is_cached_valid_zip_corrupt(tmp_path):
    from core.mirror import _is_cached_valid
    p = tmp_path / "file.zip"
    _make_corrupt_zip(p)
    assert _is_cached_valid(p) is False


def test_is_cached_valid_nonzip(tmp_path):
    from core.mirror import _is_cached_valid
    p = tmp_path / "file.bin"
    p.write_bytes(b"binary data")
    assert _is_cached_valid(p) is True


def test_is_cached_valid_missing(tmp_path):
    from core.mirror import _is_cached_valid
    assert _is_cached_valid(tmp_path / "no.zip") is False


# ─── 测试 3：get_download_cache_path ─────────────────────────────────────────

def test_get_download_cache_path_structure(tmp_path):
    from core.mirror import get_download_cache_path
    import tempfile
    with patch("tempfile.gettempdir", return_value=str(tmp_path)):
        p = get_download_cache_path("xray", "25.3.6", "Xray-linux-64.zip")
    assert p.name == "Xray-linux-64.zip"
    assert "xray" in str(p)
    assert "v25.3.6" in str(p)
    assert p.parent.exists()


# ─── 测试 4：download() 缓存命中 — 跳过下载 ──────────────────────────────────

def test_download_cache_hit_skips_download(tmp_path):
    """缓存有效时 download() 不应发起任何网络请求"""
    from core.mirror import download

    cache = tmp_path / "cache" / "Xray-linux-64.zip"
    cache.parent.mkdir(parents=True)
    _make_valid_zip(cache)

    dest = tmp_path / "dest" / "Xray-linux-64.zip"

    with patch("httpx.Client") as mock_client, \
         patch("httpx.stream") as mock_stream, \
         patch("core.mirror.get_sources", return_value=[]), \
         patch("core.mirror.init"):
        result = download(
            url_template="{mirror}/XTLS/Xray-core/releases/download/v25.3.6/Xray-linux-64.zip",
            dest=dest,
            category="github_releases",
            cache_path=cache,
        )
        mock_client.assert_not_called()
        mock_stream.assert_not_called()

    assert result == dest
    assert dest.exists()


# ─── 测试 5：download() 缓存损坏 — 重新下载并回写 ────────────────────────────

def test_download_cache_corrupt_triggers_redownload(tmp_path):
    """缓存损坏时应重新下载，并将新文件回写到 cache_path"""
    from core.mirror import download

    cache = tmp_path / "cache" / "Xray-linux-64.zip"
    cache.parent.mkdir(parents=True)
    _make_corrupt_zip(cache)

    dest = tmp_path / "dest" / "Xray-linux-64.zip"
    fake_data = b"PK\x03\x04" + b"X" * (2 * 1024 * 1024)

    def fake_download_single(url, d, done_event, result_holder, cb=None):
        import time
        tmp = d.parent / (d.name + ".part.fake")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        _make_valid_zip(tmp, b"X" * (2 * 1024 * 1024))
        if not done_event.is_set():
            done_event.set()
            result_holder.append(tmp)

    with patch("core.mirror._probe_reachable", return_value=("https://mirror.ghproxy.com/XTLS/Xray-core/releases/download/v25.3.6/Xray-linux-64.zip", 100.0)), \
         patch("core.mirror._download_single", side_effect=fake_download_single), \
         patch("core.mirror.get_sources", return_value=["https://mirror.ghproxy.com"]), \
         patch("core.mirror.init"):
        result = download(
            url_template="{mirror}/XTLS/Xray-core/releases/download/v25.3.6/Xray-linux-64.zip",
            dest=dest,
            category="github_releases",
            cache_path=cache,
        )

    assert result == dest
    assert dest.exists()
    assert cache.exists()
    assert _is_zip_valid_helper(cache)


def _is_zip_valid_helper(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            return zf.testzip() is None
    except Exception:
        return False


# ─── 测试 6：download() 无缓存 — 正常下载后回写 ──────────────────────────────

def test_download_no_cache_writes_after_download(tmp_path):
    """无缓存时正常下载，完成后自动回写 cache_path"""
    from core.mirror import download

    cache = tmp_path / "cache" / "Xray-linux-64.zip"
    dest = tmp_path / "dest" / "Xray-linux-64.zip"

    def fake_download_single(url, d, done_event, result_holder, cb=None):
        tmp = d.parent / (d.name + ".part.fake")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        _make_valid_zip(tmp, b"X" * (2 * 1024 * 1024))
        if not done_event.is_set():
            done_event.set()
            result_holder.append(tmp)

    with patch("core.mirror._probe_reachable", return_value=("https://mirror.ghproxy.com/XTLS/Xray-core/releases/download/v25.3.6/Xray-linux-64.zip", 100.0)), \
         patch("core.mirror._download_single", side_effect=fake_download_single), \
         patch("core.mirror.get_sources", return_value=["https://mirror.ghproxy.com"]), \
         patch("core.mirror.init"):
        result = download(
            url_template="{mirror}/XTLS/Xray-core/releases/download/v25.3.6/Xray-linux-64.zip",
            dest=dest,
            category="github_releases",
            cache_path=cache,
        )

    assert result == dest
    assert dest.exists()
    assert cache.exists()
    assert _is_zip_valid_helper(cache)
