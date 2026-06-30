"""会话级缓存回归测试。

覆盖两项体验优化：
1. 安装状态缓存（``core.installed_cache``）：进入软件管理探测一次，浏览复用，
   动作后失效重探。
2. 版本列表会话级新鲜（``core.version_cache`` + ``resolve_versions``）：每会话
   每软件只联网拉一次，install / upgrade 复用同一份，避免「升级说没有、安装却
   有新版本」的不一致。
"""
from __future__ import annotations

from unittest.mock import patch


# ─── 安装状态缓存 ────────────────────────────────────────────────────────────

class _FakeRecipe:
    key = "fake"
    _calls = 0
    _ver = "1.0.0"

    def detect(self):
        type(self)._calls += 1
        return self._ver


def test_installed_cache_detect_once_until_invalidated():
    from core import installed_cache as ic
    ic.clear()
    _FakeRecipe._calls = 0

    assert ic.get_detected(_FakeRecipe) == "1.0.0"
    assert ic.get_detected(_FakeRecipe) == "1.0.0"
    assert ic.get_detected(_FakeRecipe) == "1.0.0"
    # 命中缓存 → detect 只跑一次
    assert _FakeRecipe._calls == 1

    ic.invalidate("fake")
    _FakeRecipe._ver = "2.0.0"
    # 失效后重新探测，反映新状态
    assert ic.get_detected(_FakeRecipe) == "2.0.0"
    assert _FakeRecipe._calls == 2
    ic.clear()


def test_installed_cache_detect_exception_cached_as_empty():
    from core import installed_cache as ic
    ic.clear()

    class _Boom:
        key = "boom"
        calls = 0

        def detect(self):
            type(self).calls += 1
            raise RuntimeError("boom")

    assert ic.get_detected(_Boom) == ""
    assert ic.get_detected(_Boom) == ""
    assert _Boom.calls == 1  # 异常结果也缓存，不反复触发慢探测
    ic.clear()


# ─── 版本列表会话级新鲜 ──────────────────────────────────────────────────────

def test_resolve_versions_fetches_once_per_session(tmp_path):
    """install / upgrade 共用：首次联网拉一次，之后复用，不再二次拉。"""
    from core import version_cache as vc
    from software._shared.version_resolver import resolve_versions

    vc._cache = {}
    vc._loaded = False
    vc._session_refreshed = set()

    calls = {"n": 0}

    def _fetch():
        calls["n"] += 1
        return ["1.26.4", "1.26.2"]

    with patch.object(vc, "_get_cache_path", return_value=tmp_path / "vc.yaml"):
        # 第一次（模拟「升级」入口）→ 联网拉一次
        first = resolve_versions("golang", _fetch, ["1.0"])
        # 第二次（模拟「安装」入口）→ 复用，不再联网
        second = resolve_versions("golang", _fetch, ["1.0"])

    assert first == ["1.26.4", "1.26.2"]
    assert second == first          # 升级与安装看到同一份，消除不一致
    assert calls["n"] == 1          # 全会话仅拉一次

    vc._cache = {}
    vc._loaded = False
    vc._session_refreshed = set()


def test_resolve_versions_falls_back_to_stale_when_fetch_fails(tmp_path):
    """联网失败时回退过期缓存，且不标记 session_refreshed（下次仍可重试）。"""
    from core import version_cache as vc
    from software._shared.version_resolver import resolve_versions

    vc._cache = {"redis": {"versions": ["7.2"], "timestamp": 0}}
    vc._loaded = True
    vc._session_refreshed = set()

    def _fetch_fail():
        return []

    with patch.object(vc, "_get_cache_path", return_value=tmp_path / "vc.yaml"):
        result = resolve_versions("redis", _fetch_fail, ["6.0"])

    assert result == ["7.2"]                 # 过期缓存兜底
    assert "redis" not in vc._session_refreshed

    vc._cache = {}
    vc._loaded = False
    vc._session_refreshed = set()
