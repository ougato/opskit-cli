"""core/mirror.py 单元测试（不发起真实网络请求）"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core import mirror as mir


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
