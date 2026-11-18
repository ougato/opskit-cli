"""Background failures stay in logs and reuse cached version metadata."""
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
import logging
import threading
from types import SimpleNamespace

import httpx
import pytest

from core import http, logger, version_cache as cache


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    monkeypatch.setattr(http, "_github_retry_at", 0.0)
    monkeypatch.setattr(http, "_inflight", {})
    monkeypatch.setattr(cache, "_loaded", True)
    monkeypatch.setattr(cache, "_cache", {})
    monkeypatch.setattr(cache, "_session_refreshed", set())


def mock_responses(monkeypatch, response):
    calls = []
    transport = httpx.MockTransport(lambda request: (calls.append(request), response)[1])
    client = httpx.Client
    monkeypatch.setattr(http.httpx, "Client", lambda **kw: client(transport=transport, **kw))
    return calls


@pytest.mark.parametrize("status,headers,message", [
    (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1200"}, "API rate limit exceeded"),
    (429, {"retry-after": "180"}, "Too many requests"),
    (403, {}, "You have exceeded a secondary rate limit"),
])
def test_github_cooldown_shared_across_repositories(monkeypatch, status, headers, message):
    monkeypatch.setattr(http.time, "time", lambda: 1000)
    monkeypatch.setattr(http.time, "monotonic", lambda: 50)
    calls = mock_responses(monkeypatch, httpx.Response(status, headers=headers, json={"message": message}))
    assert http.get_json("https://api.github.com/repos/a/a/releases") is None
    assert http.get_json("https://api.github.com/repos/b/b/releases") is None
    assert len(calls) == 1
    assert http._github_retry_at >= 110
    monkeypatch.setattr(http.time, "monotonic", lambda: 10000)
    http.get_json("https://api.github.com/repos/b/b/releases")
    assert len(calls) == 2


def test_plain_forbidden_does_not_claim_rate_limit(monkeypatch):
    calls = mock_responses(monkeypatch, httpx.Response(403, json={"message": "Forbidden"}))
    http.get_json("https://api.github.com/repos/a/a/releases")
    http.get_json("https://api.github.com/repos/b/b/releases")
    assert len(calls) == 2
    assert http._github_retry_at == 0


def test_background_warning_goes_to_file_not_menu(monkeypatch, tmp_path):
    parent = logging.getLogger("opskit")
    previous = parent.handlers, parent.level, parent.propagate
    monkeypatch.setattr(logger, "_initialized", False)
    monkeypatch.setattr(logger, "_logger", None)
    parent.handlers = []
    stream = StringIO()
    monkeypatch.setattr("sys.stderr", stream)
    try:
        logger.init()
        mock_responses(monkeypatch, httpx.Response(403, json={"message": "Forbidden"}))
        http.get_json("https://api.github.com/repos/a/a/releases")
        assert stream.getvalue() == ""
        contents = logger._get_log_path().read_text(encoding="utf-8")
        assert "HTTP 403 Forbidden" in contents
        assert "rate limited" not in contents
    finally:
        for handler in parent.handlers:
            handler.close()
        parent.handlers, parent.level, parent.propagate = previous


def test_concurrent_requests_share_result(monkeypatch):
    entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
    class ObservedFuture(http.Future):
        def result(self, timeout=None):
            waiting.set()
            return super().result(timeout)
    monkeypatch.setattr(http, "Future", ObservedFuture)
    calls = []
    def fetch(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return ["v1"]
    monkeypatch.setattr(http, "_get_json", fetch)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(http.get_json, "https://example.test/releases")
        assert entered.wait(3)
        second = pool.submit(http.get_json, "https://example.test/releases")
        try:
            assert waiting.wait(3)
        finally:
            release.set()
        assert first.result() == second.result() == ["v1"]
    assert len(calls) == 1


def test_fresh_disk_cache_and_failed_refresh_preserve_old_data(monkeypatch):
    monkeypatch.setattr(cache.time, "time", lambda: 10000)
    cache._cache.update({
        "fresh": {"timestamp": 9990, "versions": ["2"]},
        "stale": {"timestamp": 1, "versions": ["1"]},
    })
    assert cache.get_cached_versions("fresh") == ["2"]
    assert cache.get_cached_versions("stale") is None
    assert cache.get_cached_versions_stale("stale") == ["1"]
    classes = [type(key, (), {"key": key, "platforms": platforms}) for key, platforms in (
        ("fresh", ["windows"]), ("stale", ["windows"]), ("linux_only", ["linux"]),
    )]
    monkeypatch.setattr("software.registry.all_recipes", lambda: classes)
    monkeypatch.setattr("core.platform.get_platform", lambda: SimpleNamespace(os_type="windows"))
    monkeypatch.setattr(cache, "_refresh_event", SimpleNamespace(wait=lambda **kw: True))
    monkeypatch.setattr(cache.time, "sleep", lambda _: None)
    calls = []
    monkeypatch.setattr(cache, "fetch_versions_online", lambda recipe: calls.append(recipe.key))
    cache.refresh_all_background()
    assert calls == ["stale"]
    assert cache.get_cached_versions_stale("stale") == ["1"]


def test_network_timeout_returns_none(monkeypatch):
    def fail(request):
        raise httpx.ReadTimeout("offline")
    client = httpx.Client
    monkeypatch.setattr(http.httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(fail), **kw))
    assert http.get_json("https://api.github.com/repos/a/a/releases") is None
    assert not http._inflight


def test_clickhouse_foreground_uses_shared_cooldown_and_stale_cache(monkeypatch):
    from software.recipes.clickhouse.recipe import ClickHouseRecipe
    mock_responses(monkeypatch, httpx.Response(429, headers={"retry-after": "180"}))
    http.get_json("https://api.github.com/repos/other/other/releases")
    cache._cache["clickhouse"] = {"timestamp": 0, "versions": ["26.3.17.110"]}
    def unexpected_request(**kwargs):
        pytest.fail("foreground request ignored GitHub cooldown")
    monkeypatch.setattr(http.httpx, "Client", unexpected_request)
    assert ClickHouseRecipe().versions() == ["26.3.17.110"]
