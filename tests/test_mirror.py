"""core/mirror.py 单元测试（不发起真实网络请求）"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from core import mirror as mir


def test_download_switches_mirror_before_a_trickle_fills_a_buffer(tmp_path, monkeypatch):
    clock = 0
    slow_chunks = []
    content = b"a" * 2048
    urls = ["https://slow.example/file", "https://fast.example/file"]

    class SlowBody(httpx.SyncByteStream):
        def __iter__(self):
            nonlocal clock
            for _ in range(5):
                clock += 1
                slow_chunks.append(clock)
                yield b"x"

    def response(request):
        if request.url.host == "slow.example":
            return httpx.Response(200, headers={"Content-Length": "2048"}, stream=SlowBody())
        return httpx.Response(200, content=content)

    monkeypatch.setattr(mir, "time", SimpleNamespace(monotonic=lambda: clock))
    monkeypatch.setattr(mir, "_probe_concurrent", lambda *args, **kwargs: list(zip(urls, range(2))))
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr(mir.httpx, "stream", client.stream)
        result = mir.download_file(urls, tmp_path / "file", slow_window=2)

    assert slow_chunks == [1, 2]
    assert result.read_bytes() == content


def test_load_sources_returns_dict(tmp_path) -> None:
    sources = mir._load_sources()
    assert isinstance(sources, dict)
    assert "pip" in sources
    assert "github_releases" in sources


def test_get_sources_before_init(tmp_path) -> None:
    mir._initialized = False
    mir._cache = {}
    mir._sources = {}
    with patch.object(mir, "detect_region", return_value="global"), \
         patch.object(mir, "rank_sources", return_value=["https://example.com"]):
        sources = mir.get_sources("pip")
    assert isinstance(sources, list)


def test_detect_region_returns_str(tmp_path) -> None:
    with patch("httpx.Client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"country": "CN"}
        mock_client.return_value.__enter__.return_value.get.return_value = mock_resp
        result = mir.detect_region()
    assert result in ("cn", "global")


def test_detect_region_fallback(tmp_path) -> None:
    with patch("httpx.Client", side_effect=Exception("no network")):
        result = mir.detect_region()
    assert result == "global"


def test_cache_save_load(tmp_path) -> None:
    mir._initialized = False
    data = {"region": "cn", "timestamp": 12345.0, "ranked": {"pip": ["https://a.com"]}}
    mir._save_cache(data)
    loaded = mir._load_cache()
    assert loaded["region"] == "cn"
    assert loaded["ranked"]["pip"] == ["https://a.com"]
