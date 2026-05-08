"""模拟切换源测试 — 验证两阶段下载策略（HEAD 探针 + 赛马下载）"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ─── 辅助 ─────────────────────────────────────────────────────────────────────

def _make_chunk_iter(data: bytes, chunk_size: int = 65536, delay: float = 0.0):
    """模拟 httpx iter_bytes，可注入每 chunk 延迟"""
    offset = 0
    while offset < len(data):
        if delay:
            time.sleep(delay)
        yield data[offset:offset + chunk_size]
        offset += chunk_size


def _fake_resp(status: int, data: bytes = b"", chunk_delay: float = 0.0):
    resp = MagicMock()
    resp.status_code = status
    resp.iter_bytes = lambda chunk_size=65536: _make_chunk_iter(data, chunk_size, chunk_delay)
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ─── 测试 1：url_template 拼接格式正确 ────────────────────────────────────────

def test_url_template_no_double_https():
    """修复后的 url_template 不应出现双 https:// 路径"""
    ver = "25.3.6"
    zip_name = "Xray-linux-64.zip"
    github_rel_path = f"XTLS/Xray-core/releases/download/v{ver}/{zip_name}"
    url_template = f"{{mirror}}/{github_rel_path}"

    mirror = "https://mirror.ghproxy.com"
    result = url_template.format(mirror=mirror.rstrip("/"))
    assert "https://github.com" not in result
    assert result == f"https://mirror.ghproxy.com/XTLS/Xray-core/releases/download/v{ver}/{zip_name}"


# ─── 测试 2：HEAD 探针过滤 404 镜像 ───────────────────────────────────────────

def _make_probe_mock(status: int, headers: dict, final_url: str = ""):
    mock_client_cls = MagicMock()
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__ = lambda s: mock_client
    mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.headers = headers
    mock_resp.url = final_url or "https://example.com/file.zip"
    mock_client.head.return_value = mock_resp
    return mock_client_cls


def test_probe_reachable_filters_404():
    from core.mirror import _probe_reachable
    mock = _make_probe_mock(404, {})
    with patch("httpx.Client", mock):
        _, latency = _probe_reachable("https://bad-mirror.example.com/some/file.zip")
    assert latency == float("inf")


def test_probe_reachable_filters_banned_redirect():
    """gh.con.sh 等返回 200 但 Content-Type: text/plain 且 Content-Length 极小的封禁页面应被过滤"""
    from core.mirror import _probe_reachable
    mock = _make_probe_mock(200, {"content-type": "text/plain;charset=UTF-8", "content-length": "48"})
    with patch("httpx.Client", mock):
        _, latency = _probe_reachable("https://banned-mirror.example.com/some/file.zip")
    assert latency == float("inf")


def test_probe_reachable_accepts_large_content_length():
    """Content-Length >= 1MB 的响应应视为可达"""
    from core.mirror import _probe_reachable
    mock = _make_probe_mock(200, {"content-type": "application/zip", "content-length": "16168806"})
    with patch("httpx.Client", mock):
        _, latency = _probe_reachable("https://good-mirror.example.com/some/file.zip")
    assert latency < float("inf")


def test_probe_reachable_accepts_github_cdn():
    """重定向到 objects.githubusercontent.com 的响应应视为可达"""
    from core.mirror import _probe_reachable
    mock = _make_probe_mock(
        200, {"content-type": "application/octet-stream", "content-length": "0"},
        final_url="https://objects.githubusercontent.com/github-production-release-asset/file.zip"
    )
    with patch("httpx.Client", mock):
        _, latency = _probe_reachable("https://github.com/XTLS/Xray-core/releases/download/v1/file.zip")
    assert latency < float("inf")


# ─── 测试 3：赛马下载 — 第一个成功后其他线程停止 ──────────────────────────────

def test_race_first_wins_others_stop(tmp_path: Path):
    from core.mirror import _download_single

    fake_data = b"X" * (2 * 1024 * 1024)
    done_event = threading.Event()
    result_holder: list[Path] = []
    dest = tmp_path / "output.zip"

    fast_resp = _fake_resp(200, fake_data, chunk_delay=0.0)
    slow_resp = _fake_resp(200, fake_data, chunk_delay=0.5)

    with patch("httpx.stream") as mock_stream:
        call_count = [0]

        def side_effect(method, url, **kwargs):
            call_count[0] += 1
            if "fast" in url:
                return fast_resp
            return slow_resp

        mock_stream.side_effect = side_effect

        t1 = threading.Thread(
            target=_download_single,
            args=("https://fast-mirror/file.zip", dest, done_event, result_holder),
            daemon=True,
        )
        t2 = threading.Thread(
            target=_download_single,
            args=("https://slow-mirror/file.zip", dest, done_event, result_holder),
            daemon=True,
        )
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

    assert done_event.is_set(), "done_event 应在第一个成功后置位"
    assert len(result_holder) >= 1, "至少一个源应成功"


# ─── 测试 4：低速检测 — 慢源应被放弃 ─────────────────────────────────────────

def test_slow_source_abandoned(tmp_path: Path):
    """
    模拟：每个 chunk 1 字节，每次间隔 2s，窗口 3s 内速度远低于阈值（1000 KB/s）。
    窗口到期后字节数极少，应触发低速检测并退出，不写入 result_holder。
    patch core.mirror 模块里已绑定的名字（而非 constants 属性），确保函数内生效。
    """
    from core.mirror import _download_single

    done_event = threading.Event()
    result_holder: list[Path] = []
    dest = tmp_path / "slow.zip"

    def slow_iter(chunk_size=65536):
        for _ in range(100):
            time.sleep(2.0)
            yield b"Y"

    resp = MagicMock()
    resp.status_code = 200
    resp.iter_bytes = slow_iter
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)

    with patch("httpx.stream", return_value=resp), \
         patch("core.mirror.DOWNLOAD_SLOW_WINDOW", 3), \
         patch("core.mirror.DOWNLOAD_MIN_SPEED_KBPS", 1000):
        start = time.monotonic()
        _download_single("https://slow/file.zip", dest, done_event, result_holder)
        elapsed = time.monotonic() - start
    assert elapsed < 30, f"慢源应在低速检测内退出，实际耗时 {elapsed:.1f}s"
    assert len(result_holder) == 0, "慢源不应写入 result_holder"


# ─── 测试 5：fallback_url 在所有镜像失败时被使用 ──────────────────────────────

def test_fallback_used_when_all_mirrors_fail(tmp_path: Path):
    from core import mirror as mirror_mod

    fake_data = b"Z" * (2 * 1024 * 1024)
    dest = tmp_path / "fallback.zip"

    with patch.object(mirror_mod, "get_sources", return_value=[]):
        fallback_resp = _fake_resp(200, fake_data)
        with patch("httpx.stream", return_value=fallback_resp):
            result = mirror_mod.download(
                "{mirror}/some/file.zip",
                dest,
                category="github_releases",
                fallback_url="https://github.com/some/file.zip",
            )
    assert result == dest
    assert dest.exists()
    assert dest.stat().st_size > 0
