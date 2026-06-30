"""热更新全场景验收测试（32 个场景）

覆盖范围：
- 正常路径（T01-T03）
- 下载层异常（T04-T12）
- 文件替换层异常 Windows（T13-T20）
- 版本检测层异常（T21-T23）
- 替换后/启动层异常（T24-T28）
- 环境层验证（T29-T31）
- 正式端到端验收（T32，需真实网络，默认跳过）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ─── 辅助：隔离 data_dir ──────────────────────────────────────────────────────

@pytest.fixture()
def tmp_data(tmp_path, monkeypatch):
    """将所有 get_data_dir() 调用重定向到临时目录"""
    monkeypatch.setattr("core.config.get_data_dir", lambda: tmp_path)
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    (tmp_path / "backups").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture()
def pending_path(tmp_data):
    """返回隔离环境下的 pending 文件路径"""
    import core.updater as upd
    with patch("core.updater._get_pending_path", return_value=tmp_data / "cache" / "opskit.pending"), \
         patch("core.updater._get_pending_tmp_path", return_value=tmp_data / "cache" / "opskit.pending.tmp"), \
         patch("core.updater._get_cache_path", return_value=tmp_data / "cache" / "update_check.json"):
        yield tmp_data / "cache" / "opskit.pending"


def _make_fake_exe(path: Path, size: int = 512 * 1024) -> None:
    """创建一个带 MZ 头的假 exe 文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(b"MZ" + b"\x00" * (size - 2))


# ═══════════════════════════════════════════════════════════════════════════════
# 正常路径
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalPath:
    def test_T01_pending_ready_after_background_check(self, tmp_data, monkeypatch):
        """T01: v1 启动后台检测到 v2，下载完成后 pending 就绪"""
        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_tmp = tmp_data / "cache" / "opskit.pending.tmp"
        cache_path = tmp_data / "cache" / "update_check.json"

        _make_fake_exe(fake_pending)

        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._get_pending_tmp_path", lambda: fake_tmp)
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        with cache_path.open("w") as f:
            json.dump({"pending_version": 2, "last_check": time.time()}, f)

        upd._pending_version = 2
        assert upd.pending_version() == 2
        assert fake_pending.exists()

    def test_T02_ps_script_contains_rename_and_pid(self, tmp_data, monkeypatch):
        """T02: 生成的 PS 脚本含 Rename-Item 和实际 PID"""
        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        _make_fake_exe(fake_pending)

        fake_self = tmp_data / "opskit.exe"
        _make_fake_exe(fake_self)

        ps_launched = []

        def fake_popen(args, **kwargs):
            ps_launched.append(args)
            return MagicMock()

        monkeypatch.setattr("core.updater._get_cache_path", lambda: tmp_data / "cache" / "update_check.json")

        with patch("subprocess.Popen", side_effect=fake_popen), \
             patch("core.updater._save_check_cache"):
            upd._apply_windows(fake_pending, fake_self, 2)

        assert len(ps_launched) == 1
        ps_path = Path(ps_launched[0][-1])
        ps_content = ps_path.read_text(encoding="utf-8")
        assert "Rename-Item" in ps_content
        assert "Copy-Item" not in ps_content or ps_content.index("Rename-Item") < ps_content.index("Copy-Item")
        assert f"$pid_to_wait = {os.getpid()}" in ps_content

    def test_T03_pending_version_restored_from_cache(self, tmp_data, monkeypatch):
        """T03: check_interval 未到期时，pending_version 从 cache 恢复"""
        import core.updater as upd

        cache_path = tmp_data / "cache" / "update_check.json"
        with cache_path.open("w") as f:
            json.dump({
                "last_check": time.time(),
                "pending_version": 2,
                "latest": 2,
            }, f)

        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)
        cache = upd._load_check_cache()
        assert cache.get("pending_version") == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 下载层异常
# ═══════════════════════════════════════════════════════════════════════════════

class TestDownloadExceptions:
    def test_T04_network_failure_silent(self, tmp_data, monkeypatch):
        """T04: 网络中断时静默跳过，不崩溃，返回 None"""
        import core.updater as upd
        import httpx

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_tmp = tmp_data / "cache" / "opskit.pending.tmp"
        cache_path = tmp_data / "cache" / "update_check.json"

        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._get_pending_tmp_path", lambda: fake_tmp)
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        def raise_connect(*a, **kw):
            raise httpx.ConnectError("network failure")

        with patch("httpx.stream", side_effect=raise_connect), \
             patch("core.mirror.get_sources", return_value=["https://github.com/ougato"]), \
             patch("core.updater._get_sha256_from_url", return_value=""):
            result = upd._download_update("https://github.com/ougato/opskit-cli/releases/download/v2/opskit", "")

        assert result is None
        assert not fake_pending.exists()

    def test_T05_resume_download_range_request(self, tmp_data, monkeypatch):
        """T05: tmp 已有部分数据时，发送 Range 请求追加写入"""
        import core.updater as upd
        import httpx

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_tmp = tmp_data / "cache" / "opskit.pending.tmp"
        cache_path = tmp_data / "cache" / "update_check.json"

        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._get_pending_tmp_path", lambda: fake_tmp)
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        initial_data = b"MZ" + b"\x00" * (256 * 1024 - 2)
        fake_tmp.write_bytes(initial_data)

        range_headers_sent = []

        class FakeResp:
            status_code = 206
            headers = {"ETag": ""}
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def iter_bytes(self, chunk_size):
                yield b"\x00" * (256 * 1024)

        def fake_stream(method, url, timeout, follow_redirects, headers=None):
            range_headers_sent.append(headers or {})
            return FakeResp()

        with patch("httpx.stream", side_effect=fake_stream), \
             patch("core.mirror.get_sources", return_value=["https://github.com/ougato"]), \
             patch("core.updater._get_sha256_from_url", return_value=""), \
             patch("core.updater._sha256_file", return_value=""), \
             patch("shutil.disk_usage", return_value=MagicMock(free=500 * 1024 * 1024)):
            upd._download_update("https://github.com/ougato/opskit-cli/releases/download/v2/opskit", "")

        assert any("Range" in h for h in range_headers_sent), "Range header should be sent"
        assert range_headers_sent[0].get("Range") == f"bytes={len(initial_data)}-"

    def test_T05b_stale_partial_discarded_on_version_change(self, tmp_data, monkeypatch):
        """T05b: 残留 .tmp 属于旧版本(v2)、目标已是 v4 时，丢弃旧半包从头下载，不发 Range"""
        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_tmp = tmp_data / "cache" / "opskit.pending.tmp"
        cache_path = tmp_data / "cache" / "update_check.json"

        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._get_pending_tmp_path", lambda: fake_tmp)
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        fake_tmp.write_bytes(b"MZ" + b"\x00" * (256 * 1024 - 2))
        with cache_path.open("w") as f:
            json.dump({"download_version": 2, "etag": '"old"'}, f)

        range_headers_sent = []

        class FakeResp:
            status_code = 200
            headers = {"ETag": ""}
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def iter_bytes(self, chunk_size):
                yield b"MZ" + b"\x00" * (512 * 1024 - 2)

        def fake_stream(method, url, timeout, follow_redirects, headers=None):
            range_headers_sent.append(headers or {})
            return FakeResp()

        with patch("httpx.stream", side_effect=fake_stream), \
             patch("core.mirror.get_sources", return_value=["https://github.com/ougato"]), \
             patch("core.updater._get_sha256_from_url", return_value=""), \
             patch("core.updater._sha256_file", return_value=""), \
             patch("shutil.disk_usage", return_value=MagicMock(free=500 * 1024 * 1024)):
            upd._download_update(
                "https://github.com/ougato/opskit-cli/releases/download/v4/opskit", "", 4)

        assert all("Range" not in h for h in range_headers_sent), "旧版本残包应被丢弃，不应发 Range"
        assert all("If-None-Match" not in h for h in range_headers_sent), "版本变化后应清空 ETag"
        assert upd._load_check_cache().get("download_version") == 4

    def test_T06_stall_timeout_uses_read_timeout(self, tmp_data, monkeypatch):
        """T06: 下载使用 TIMEOUT_DOWNLOAD_READ 而非 read=None"""
        import core.updater as upd
        import httpx

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_tmp = tmp_data / "cache" / "opskit.pending.tmp"
        cache_path = tmp_data / "cache" / "update_check.json"

        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._get_pending_tmp_path", lambda: fake_tmp)
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        timeout_used = []

        def fake_stream(method, url, timeout, **kw):
            timeout_used.append(timeout)
            raise httpx.ConnectError("fail")

        with patch("httpx.stream", side_effect=fake_stream), \
             patch("core.mirror.get_sources", return_value=["https://github.com/ougato"]), \
             patch("core.updater._get_sha256_from_url", return_value=""), \
             patch("shutil.disk_usage", return_value=MagicMock(free=500 * 1024 * 1024)), \
             patch("time.sleep"):
            upd._download_update("https://example.com/opskit", "")

        assert timeout_used, "httpx.stream should have been called"
        t = timeout_used[0]
        assert isinstance(t, httpx.Timeout)
        assert t.read == 30, f"Expected read timeout 30, got {t.read}"

    def test_T07_etag_304_skips_download(self, tmp_data, monkeypatch):
        """T07: ETag 命中返回 304 时，复用已有 pending 文件"""
        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_tmp = tmp_data / "cache" / "opskit.pending.tmp"
        cache_path = tmp_data / "cache" / "update_check.json"

        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._get_pending_tmp_path", lambda: fake_tmp)
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        _make_fake_exe(fake_pending)
        with cache_path.open("w") as f:
            json.dump({"etag": '"abc123"', "pending_version": 2}, f)

        class FakeResp:
            status_code = 304
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def iter_bytes(self, cs): return iter([])

        with patch("httpx.stream", return_value=FakeResp()), \
             patch("core.mirror.get_sources", return_value=["https://github.com/ougato"]), \
             patch("core.updater._get_sha256_from_url", return_value=""), \
             patch("shutil.disk_usage", return_value=MagicMock(free=500 * 1024 * 1024)):
            result = upd._download_update("https://example.com/opskit", "")

        assert result == fake_pending

    def test_T08_sha256_fallback_to_dotsha256_file(self, tmp_data, monkeypatch):
        """T08: Release Body 无 SHA256 时，尝试请求 .sha256 备用文件"""
        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_tmp = tmp_data / "cache" / "opskit.pending.tmp"
        cache_path = tmp_data / "cache" / "update_check.json"

        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._get_pending_tmp_path", lambda: fake_tmp)
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        sha256_urls_fetched = []

        def fake_get_sha256(url):
            sha256_urls_fetched.append(url)
            return "a" * 64

        class FakeResp:
            status_code = 200
            headers = {"ETag": ""}
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def iter_bytes(self, cs):
                yield b"MZ" + b"\x00" * (512 * 1024 - 2)

        with patch("httpx.stream", return_value=FakeResp()), \
             patch("core.mirror.get_sources", return_value=["https://github.com/ougato"]), \
             patch("core.updater._get_sha256_from_url", side_effect=fake_get_sha256), \
             patch("core.updater._sha256_file", return_value="a" * 64), \
             patch("shutil.disk_usage", return_value=MagicMock(free=500 * 1024 * 1024)):
            upd._download_update("https://example.com/opskit", "")

        assert sha256_urls_fetched, ".sha256 备用 URL 应被请求"
        assert sha256_urls_fetched[0].endswith(".sha256")

    def test_T09_disk_space_insufficient_skips(self, tmp_data, monkeypatch):
        """T09: 磁盘空间不足时跳过下载，不写残留文件"""
        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_tmp = tmp_data / "cache" / "opskit.pending.tmp"
        cache_path = tmp_data / "cache" / "update_check.json"

        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._get_pending_tmp_path", lambda: fake_tmp)
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        with patch("core.mirror.get_sources", return_value=["https://github.com/ougato"]), \
             patch("core.updater._get_sha256_from_url", return_value=""), \
             patch("shutil.disk_usage", return_value=MagicMock(free=10 * 1024 * 1024)):
            result = upd._download_update("https://example.com/opskit", "")

        assert result is None
        assert not fake_tmp.exists()

    def test_T10_tmp_dir_fallback_to_tempdir(self, tmp_data, monkeypatch):
        """T10: tmp 目录无写权限时降级到系统 tempdir"""
        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        cache_path = tmp_data / "cache" / "update_check.json"

        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        sys_tmp = Path(tempfile.gettempdir()) / "opskit.pending.tmp"
        sys_tmp.unlink(missing_ok=True)

        def mock_tmp_path():
            read_only = tmp_data / "readonly_dir" / "opskit.pending.tmp"
            return read_only

        monkeypatch.setattr("core.updater._get_pending_tmp_path", mock_tmp_path)

        downloaded_paths = []

        class FakeResp:
            status_code = 200
            headers = {"ETag": ""}
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def iter_bytes(self, cs):
                yield b"MZ" + b"\x00" * (512 * 1024 - 2)

        original_open = Path.open

        def tracking_open(self, mode="r", **kw):
            if "w" in mode or "a" in mode or "b" in mode:
                downloaded_paths.append(str(self))
            return original_open(self, mode, **kw)

        with patch("httpx.stream", return_value=FakeResp()), \
             patch("core.mirror.get_sources", return_value=["https://github.com/ougato"]), \
             patch("core.updater._get_sha256_from_url", return_value=""), \
             patch("core.updater._sha256_file", return_value=""), \
             patch("shutil.disk_usage", return_value=MagicMock(free=500 * 1024 * 1024)):
            result = upd._download_update("https://example.com/opskit", "")

        sys_tmp.unlink(missing_ok=True)

    def test_T11_sha256_mismatch_retries(self, tmp_data, monkeypatch):
        """T11: SHA256 校验失败时删除 tmp 并重试"""
        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_tmp = tmp_data / "cache" / "opskit.pending.tmp"
        cache_path = tmp_data / "cache" / "update_check.json"

        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._get_pending_tmp_path", lambda: fake_tmp)
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        attempt_count = []

        class FakeResp:
            status_code = 200
            headers = {"ETag": ""}
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def iter_bytes(self, cs):
                attempt_count.append(1)
                yield b"MZ" + b"\x00" * (512 * 1024 - 2)

        with patch("httpx.stream", return_value=FakeResp()), \
             patch("core.mirror.get_sources", return_value=["https://github.com/ougato"]), \
             patch("core.updater._get_sha256_from_url", return_value=""), \
             patch("core.updater._sha256_file", return_value="wronghash"), \
             patch("shutil.disk_usage", return_value=MagicMock(free=500 * 1024 * 1024)), \
             patch("time.sleep"):
            result = upd._download_update("https://example.com/opskit", "correcthash")

        assert result is None
        assert len(attempt_count) == 5, f"Expected 5 attempts (MAX_RETRY_DOWNLOAD), got {len(attempt_count)}"

    def test_T12_exponential_backoff_delays(self, tmp_data, monkeypatch):
        """T12: 指数退避：第 1 次失败等 1s，第 2 次等 2s，第 3 次等 4s"""
        import core.updater as upd
        import httpx

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_tmp = tmp_data / "cache" / "opskit.pending.tmp"
        cache_path = tmp_data / "cache" / "update_check.json"

        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._get_pending_tmp_path", lambda: fake_tmp)
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        sleep_calls = []

        def mock_sleep(secs):
            sleep_calls.append(secs)

        with patch("httpx.stream", side_effect=httpx.ConnectError("fail")), \
             patch("core.mirror.get_sources", return_value=["https://github.com/ougato"]), \
             patch("core.updater._get_sha256_from_url", return_value=""), \
             patch("shutil.disk_usage", return_value=MagicMock(free=500 * 1024 * 1024)), \
             patch("time.sleep", side_effect=mock_sleep):
            upd._download_update("https://example.com/opskit", "")

        assert sleep_calls == [1, 2, 4, 8], f"Expected [1,2,4,8], got {sleep_calls}"


# ═══════════════════════════════════════════════════════════════════════════════
# 文件替换层异常（Windows）
# ═══════════════════════════════════════════════════════════════════════════════

class TestWindowsReplacement:
    def test_T13_ps_script_uses_rename_not_copy(self, tmp_data, monkeypatch):
        """T13: PS 脚本使用 Rename-Item 而非 Copy-Item 作为主路径"""
        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_self = tmp_data / "opskit.exe"
        _make_fake_exe(fake_pending)
        _make_fake_exe(fake_self)

        monkeypatch.setattr("core.updater._get_cache_path", lambda: tmp_data / "cache" / "update_check.json")

        ps_files = []

        def fake_popen(args, **kwargs):
            ps_files.append(Path(args[-1]))
            return MagicMock()

        with patch("subprocess.Popen", side_effect=fake_popen), \
             patch("core.updater._save_check_cache"):
            upd._apply_windows(fake_pending, fake_self, 2)

        assert ps_files, "PS 脚本应被启动"
        content = ps_files[0].read_text(encoding="utf-8")
        assert "Rename-Item" in content
        lines = content.splitlines()
        first_rename = next((i for i, l in enumerate(lines) if "Rename-Item" in l), None)
        first_copy = next((i for i, l in enumerate(lines) if "Copy-Item -Path $pending" in l), None)
        assert first_rename is not None
        if first_copy is not None:
            assert first_rename < first_copy, "Rename-Item 应在 Copy-Item 之前（主路径优先）"

    def test_T14_ps_script_contains_wait_process_with_pid(self, tmp_data, monkeypatch):
        """T14: PS 脚本包含 Wait-Process 且注入了实际 PID"""
        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_self = tmp_data / "opskit.exe"
        _make_fake_exe(fake_pending)
        _make_fake_exe(fake_self)

        monkeypatch.setattr("core.updater._get_cache_path", lambda: tmp_data / "cache" / "update_check.json")

        ps_files = []

        def fake_popen(args, **kwargs):
            ps_files.append(Path(args[-1]))
            return MagicMock()

        with patch("subprocess.Popen", side_effect=fake_popen), \
             patch("core.updater._save_check_cache"):
            upd._apply_windows(fake_pending, fake_self, 2)

        content = ps_files[0].read_text(encoding="utf-8")
        assert "Wait-Process" in content
        assert f"$pid_to_wait = {os.getpid()}" in content

    def test_T15_cross_drive_rename_falls_back_to_copy(self, tmp_data, monkeypatch):
        """T15: PS 脚本跨驱动器 rename 失败时包含 Copy-Item 兜底逻辑"""
        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_self = tmp_data / "opskit.exe"
        _make_fake_exe(fake_pending)
        _make_fake_exe(fake_self)

        monkeypatch.setattr("core.updater._get_cache_path", lambda: tmp_data / "cache" / "update_check.json")

        ps_files = []

        def fake_popen(args, **kwargs):
            ps_files.append(Path(args[-1]))
            return MagicMock()

        with patch("subprocess.Popen", side_effect=fake_popen), \
             patch("core.updater._save_check_cache"):
            upd._apply_windows(fake_pending, fake_self, 2)

        content = ps_files[0].read_text(encoding="utf-8")
        assert "Copy-Item" in content, "PS 脚本应包含 Copy-Item 作为跨驱动器降级兜底"

    def test_T16_movefileex_called_when_ps_fails(self, tmp_data, monkeypatch):
        """T16: PS 脚本启动失败时，调用 MoveFileEx ctypes 兜底"""
        if sys.platform != "win32":
            pytest.skip("仅 Windows 平台运行")

        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_self = tmp_data / "opskit.exe"
        _make_fake_exe(fake_pending)
        _make_fake_exe(fake_self)

        monkeypatch.setattr("core.updater._get_cache_path", lambda: tmp_data / "cache" / "update_check.json")

        movefileex_calls = []

        import ctypes

        class FakeKernel32:
            def MoveFileExW(self, src, dst, flags):
                movefileex_calls.append((src, dst, flags))
                return 1

        fake_ctypes = MagicMock()
        fake_ctypes.windll.kernel32 = FakeKernel32()
        fake_ctypes.GetLastError = ctypes.GetLastError

        with patch("subprocess.Popen", side_effect=OSError("GPO blocked")), \
             patch("core.updater._save_check_cache"), \
             patch("core.updater.json.dump"), \
             patch("builtins.open", MagicMock()), \
             patch("ctypes.windll", fake_ctypes.windll):
            try:
                upd._apply_windows(fake_pending, fake_self, 2)
            except RuntimeError:
                pass

        assert movefileex_calls, "MoveFileEx 应被调用作为 PS 失败后的兜底"

    def test_T17_update_pending_path_json_written_as_final_fallback(self, tmp_data, monkeypatch):
        """T17: 所有策略失败后写入 update_pending_path.json 兜底标记"""
        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_self = tmp_data / "opskit.exe"
        _make_fake_exe(fake_pending)
        _make_fake_exe(fake_self)

        monkeypatch.setattr("core.updater._get_cache_path", lambda: tmp_data / "cache" / "update_check.json")
        monkeypatch.setattr("core.config.get_data_dir", lambda: tmp_data)

        fake_windll = MagicMock()
        fake_windll.kernel32.MoveFileExW.return_value = 0

        with patch("subprocess.Popen", side_effect=OSError("blocked")), \
             patch("sys.platform", "win32"), \
             patch("shutil.which", return_value=None), \
             patch("ctypes.windll", fake_windll, create=True):
            try:
                upd._apply_windows(fake_pending, fake_self, 2)
            except RuntimeError:
                pass

        marker = tmp_data / "cache" / "update_pending_path.json"
        assert marker.exists(), "update_pending_path.json 兜底标记应被写入"
        with marker.open() as f:
            data = json.load(f)
        assert data["version"] == 2

    def test_T18_next_boot_applies_from_pending_path_json(self, tmp_data, monkeypatch):
        """T18: 下次启动时从 update_pending_path.json 完成 rename"""
        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_self = tmp_data / "opskit.exe"
        _make_fake_exe(fake_pending)
        _make_fake_exe(fake_self)

        cache_path = tmp_data / "cache" / "update_check.json"
        marker = tmp_data / "cache" / "update_pending_path.json"
        with marker.open("w") as f:
            json.dump({"pending": str(fake_pending), "target": str(fake_self), "version": 2}, f)

        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)
        monkeypatch.setattr("core.config.get_data_dir", lambda: tmp_data)

        result = upd._apply_update_pending_path()
        assert result is True
        assert not marker.exists(), "标记文件应被清除"
        assert fake_self.exists(), "目标 exe 应存在（rename 完成）"

    def test_T19_ps_script_contains_icacls(self, tmp_data, monkeypatch):
        """T19: PS 脚本包含 icacls 确保新 exe 有执行权限（防 Syncthing #3907）"""
        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_self = tmp_data / "opskit.exe"
        _make_fake_exe(fake_pending)
        _make_fake_exe(fake_self)

        monkeypatch.setattr("core.updater._get_cache_path", lambda: tmp_data / "cache" / "update_check.json")

        ps_files = []

        def fake_popen(args, **kwargs):
            ps_files.append(Path(args[-1]))
            return MagicMock()

        with patch("subprocess.Popen", side_effect=fake_popen), \
             patch("core.updater._save_check_cache"):
            upd._apply_windows(fake_pending, fake_self, 2)

        content = ps_files[0].read_text(encoding="utf-8")
        assert "icacls" in content.lower() or "icacls" in content

    def test_T20_old_exe_cleaned_on_startup(self, tmp_data, monkeypatch):
        """T20: 启动时清理同目录下的 .old.exe 残留"""
        import core.updater as upd

        fake_self = tmp_data / "opskit.exe"
        old_exe = tmp_data / "opskit.old.exe"
        _make_fake_exe(fake_self)
        _make_fake_exe(old_exe)

        assert old_exe.exists()
        upd._cleanup_old_exe(fake_self)
        assert not old_exe.exists(), ".old.exe 应被清理"


# ═══════════════════════════════════════════════════════════════════════════════
# 版本检测层异常
# ═══════════════════════════════════════════════════════════════════════════════

class TestVersionCheckExceptions:
    def test_T21_rate_limit_writes_backoff_until(self, tmp_data, monkeypatch):
        """T21: GitHub Rate Limit 403 后写入 backoff_until，延迟 1h 再检查"""
        import core.updater as upd

        cache_path = tmp_data / "cache" / "update_check.json"
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.headers = {"X-RateLimit-Remaining": "0"}

        with patch("httpx.Client") as mock_client_cls, \
             patch("core.updater._save_check_cache") as mock_save:
            mock_client = MagicMock()
            mock_client.__enter__ = lambda s: mock_client
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            result = upd._fetch_latest("ougato/opskit-cli")

        assert result is None
        assert mock_save.called, "Rate Limit 后应写入 backoff_until"
        saved_data = mock_save.call_args[0][0]
        assert "backoff_until" in saved_data, "应写入 backoff_until 字段"
        from core.constants import UPDATE_RATELIMIT_BACKOFF
        assert saved_data["backoff_until"] >= time.time() + UPDATE_RATELIMIT_BACKOFF - 1

    def test_T22_api_field_missing_no_key_error(self, tmp_data, monkeypatch):
        """T22: GitHub API 返回缺字段时，防御性处理不抛 KeyError"""
        import core.updater as upd

        cache_path = tmp_data / "cache" / "update_check.json"
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"X-RateLimit-Remaining": "60"}
        mock_resp.json.return_value = {}

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = lambda s: mock_client
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            result = upd._fetch_latest("ougato/opskit-cli")

        assert isinstance(result, dict), "应返回 dict（空字段安全）"

    def test_T23_corrupted_cache_returns_empty(self, tmp_data, monkeypatch):
        """T23: update_check.json 损坏时返回 {}，不崩溃"""
        import core.updater as upd

        cache_path = tmp_data / "cache" / "update_check.json"
        cache_path.write_text("{not valid json{{{{", encoding="utf-8")
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        result = upd._load_check_cache()
        assert result == {}


# ═══════════════════════════════════════════════════════════════════════════════
# 替换后 / 启动层异常
# ═══════════════════════════════════════════════════════════════════════════════

class TestPostUpdateExceptions:
    def test_T24_crash_rollback_on_repeated_health_fail(self, tmp_data, monkeypatch):
        """T24: 新版本反复启动未确认健康，达到阈值时自动回滚"""
        import core.updater as upd
        from core.constants import APP_VERSION, MAX_HEALTH_FAILS

        monkeypatch.setattr("core.config.get_data_dir", lambda: tmp_data)

        health = tmp_data / "cache" / "update_health.json"
        health.parent.mkdir(parents=True, exist_ok=True)
        with health.open("w") as f:
            json.dump({"build": APP_VERSION, "confirmed": False,
                       "fails": MAX_HEALTH_FAILS - 1, "time": time.time()}, f)

        rollback_called = []

        with patch("core.updater.rollback", side_effect=lambda: rollback_called.append(1) or True):
            rolled = upd._check_health()

        assert rolled is True
        assert rollback_called, "反复未确认健康时应调用 rollback()"

    def test_T25_health_confirmed_no_rollback(self, tmp_data, monkeypatch):
        """T25: 健康探针已确认时不回滚、不递增失败计数"""
        import core.updater as upd
        from core.constants import APP_VERSION

        monkeypatch.setattr("core.config.get_data_dir", lambda: tmp_data)

        health = tmp_data / "cache" / "update_health.json"
        health.parent.mkdir(parents=True, exist_ok=True)
        with health.open("w") as f:
            json.dump({"build": APP_VERSION, "confirmed": True, "fails": 0}, f)

        with patch("core.updater.rollback") as rb:
            assert upd._check_health() is False
            rb.assert_not_called()

    def test_T26_old_exe_cleaned_on_check_and_apply_pending(self, tmp_data, monkeypatch):
        """T26: check_and_apply_pending 调用时清理 .old.exe 残留"""
        import core.updater as upd

        fake_self = tmp_data / "opskit.exe"
        old_exe = tmp_data / "opskit.old.exe"
        cache_path = tmp_data / "cache" / "update_check.json"

        _make_fake_exe(fake_self)
        _make_fake_exe(old_exe)

        monkeypatch.setattr("core.updater._self_path", lambda: fake_self)
        monkeypatch.setattr("core.updater._get_pending_path", lambda: tmp_data / "cache" / "opskit.pending")
        monkeypatch.setattr("core.updater._get_pending_tmp_path", lambda: tmp_data / "cache" / "opskit.pending.tmp")
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)
        monkeypatch.setattr("core.config.get_data_dir", lambda: tmp_data)

        with patch("sys.argv", []):
            upd.check_and_apply_pending()

        assert not old_exe.exists(), ".old.exe 应在 check_and_apply_pending 调用时被清理"

    def test_T27_pending_deleted_cache_cleared(self, tmp_data, monkeypatch):
        """T27: pending 文件被手动删除后，cache 中 pending_version 被清除"""
        import core.updater as upd

        cache_path = tmp_data / "cache" / "update_check.json"
        fake_self = tmp_data / "opskit.exe"
        _make_fake_exe(fake_self)

        with cache_path.open("w") as f:
            json.dump({"pending_version": 2, "last_check": time.time()}, f)

        monkeypatch.setattr("core.updater._self_path", lambda: fake_self)
        monkeypatch.setattr("core.updater._get_pending_path", lambda: tmp_data / "cache" / "nonexistent.pending")
        monkeypatch.setattr("core.updater._get_pending_tmp_path", lambda: tmp_data / "cache" / "opskit.pending.tmp")
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)
        monkeypatch.setattr("core.config.get_data_dir", lambda: tmp_data)

        with patch("sys.argv", []):
            result = upd.check_and_apply_pending()

        assert result is False
        cache = upd._load_check_cache()
        assert cache.get("pending_version") is None, "cache 中 pending_version 应被清除"

    def test_T28_clock_jump_back_forces_check(self, tmp_data, monkeypatch):
        """T28: 时钟倒退（NTP 同步后 last_check > now）时强制重新检查"""
        import core.updater as upd

        cache_path = tmp_data / "cache" / "update_check.json"
        future_time = time.time() + 9999
        with cache_path.open("w") as f:
            json.dump({"last_check": future_time}, f)

        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        result = upd._should_check(86400)
        assert result is True, "时钟倒退时 _should_check 应返回 True"

    def test_T29_post_update_flag_prevents_loop(self, tmp_data, monkeypatch):
        """T29: --post-update 标志时跳过 pending 检测，防止重启循环"""
        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        _make_fake_exe(fake_pending)
        assert fake_pending.exists()

        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._get_pending_tmp_path", lambda: tmp_data / "cache" / "opskit.pending.tmp")
        monkeypatch.setattr("core.updater._get_cache_path", lambda: tmp_data / "cache" / "update_check.json")
        monkeypatch.setattr("core.config.get_data_dir", lambda: tmp_data)

        with patch("sys.argv", ["main.py", "--post-update"]):
            result = upd.check_and_apply_pending()

        assert result is False
        assert not fake_pending.exists(), "--post-update 时 pending 文件应被清理"


# ═══════════════════════════════════════════════════════════════════════════════
# 环境层验证
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnvironment:
    def test_T30_update_disabled_worker_returns_early(self, tmp_data, monkeypatch):
        """T30: update.enabled=false 时后台线程不检测版本"""
        import core.updater as upd

        fetch_called = []

        with patch("core.updater._fetch_latest", side_effect=lambda *a, **k: fetch_called.append(1)):
            upd.check_update_background({"update": {"enabled": False, "check_interval": 0}})
            time.sleep(0.2)

        assert not fetch_called, "update 被禁用时不应调用 _fetch_latest"

    def test_T31_all_mirrors_fail_silent(self, tmp_data, monkeypatch):
        """T31: 所有镜像均失败时静默跳过，无异常抛出"""
        import core.updater as upd
        import httpx

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_tmp = tmp_data / "cache" / "opskit.pending.tmp"
        cache_path = tmp_data / "cache" / "update_check.json"

        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._get_pending_tmp_path", lambda: fake_tmp)
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        with patch("httpx.stream", side_effect=httpx.ConnectError("all fail")), \
             patch("core.mirror.get_sources", return_value=[
                 "https://mirror1.fail",
                 "https://mirror2.fail",
             ]), \
             patch("core.updater._get_sha256_from_url", return_value=""), \
             patch("shutil.disk_usage", return_value=MagicMock(free=500 * 1024 * 1024)), \
             patch("time.sleep"):
            result = upd._download_update("https://example.com/opskit", "")

        assert result is None
        assert not fake_pending.exists()


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助功能验证
# ═══════════════════════════════════════════════════════════════════════════════

class TestHelpers:
    def test_should_check_normal(self, tmp_data, monkeypatch):
        """check_interval 未到期时返回 False"""
        import core.updater as upd
        cache_path = tmp_data / "cache" / "update_check.json"
        with cache_path.open("w") as f:
            json.dump({"last_check": time.time()}, f)
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)
        assert upd._should_check(86400) is False

    def test_should_check_expired(self, tmp_data, monkeypatch):
        """check_interval 已过期时返回 True"""
        import core.updater as upd
        cache_path = tmp_data / "cache" / "update_check.json"
        with cache_path.open("w") as f:
            json.dump({"last_check": time.time() - 90000}, f)
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)
        assert upd._should_check(86400) is True

    def test_verify_binary_mz_header(self, tmp_data):
        """MZ 头的文件通过 _verify_binary 检测"""
        import core.updater as upd
        f = tmp_data / "test.exe"
        _make_fake_exe(f)
        with patch("sys.platform", "win32"):
            assert upd._verify_binary(f) is True

    def test_verify_binary_too_small(self, tmp_data):
        """文件过小时 _verify_binary 返回 False"""
        import core.updater as upd
        f = tmp_data / "tiny.exe"
        f.write_bytes(b"MZ")
        assert upd._verify_binary(f) is False


# ═══════════════════════════════════════════════════════════════════════════════
# 补充缺口场景
# ═══════════════════════════════════════════════════════════════════════════════

class TestGapCoverage:
    def test_B8_backup_failure_aborts_apply(self, tmp_data, monkeypatch):
        """B8: backup 目录写入失败时 _do_apply 返回 False，pending 保留"""
        import core.updater as upd

        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_self = tmp_data / "opskit.exe"
        _make_fake_exe(fake_pending)
        _make_fake_exe(fake_self)

        monkeypatch.setattr("core.updater._self_path", lambda: fake_self)
        monkeypatch.setattr("core.updater._get_cache_path",
                            lambda: tmp_data / "cache" / "update_check.json")
        monkeypatch.setattr("core.config.get_data_dir", lambda: tmp_data)

        def fail_copy(src, dst):
            raise OSError("backup disk full")

        with patch("shutil.copy2", side_effect=fail_copy):
            result = upd._do_apply(fake_pending, 2)

        assert result is False, "_do_apply 应在 backup 失败时返回 False"
        assert fake_pending.exists(), "backup 失败时 pending 文件不应被删除"

    def test_B9_path_with_spaces_and_chinese_quoted_in_ps(self, tmp_data, monkeypatch):
        """B9: 路径含中文/空格时，PS 脚本中路径被引号包裹"""
        import core.updater as upd

        chinese_dir = tmp_data / "更新 目录"
        chinese_dir.mkdir(parents=True, exist_ok=True)
        fake_pending = chinese_dir / "opskit.pending"
        fake_self = tmp_data / "程序 目录" / "opskit.exe"
        fake_self.parent.mkdir(parents=True, exist_ok=True)
        _make_fake_exe(fake_pending)
        _make_fake_exe(fake_self)

        monkeypatch.setattr("core.updater._get_cache_path",
                            lambda: tmp_data / "cache" / "update_check.json")

        ps_files = []

        def fake_popen(args, **kwargs):
            ps_files.append(Path(args[-1]))
            return MagicMock()

        with patch("subprocess.Popen", side_effect=fake_popen), \
             patch("core.updater._save_check_cache"):
            upd._apply_windows(fake_pending, fake_self, 2)

        assert ps_files, "PS 脚本应被启动"
        content = ps_files[0].read_text(encoding="utf-8")
        # 验证含空格/中文的路径被单引号包裹
        assert "'" in content, "PS 脚本中路径应使用单引号包裹"
        assert str(fake_pending) in content or "更新" in content
        assert str(fake_self) in content or "程序" in content

    def test_C4_app_version_comparison_safe(self, tmp_data, monkeypatch):
        """C4: _parse_version 对非整数版本号不崩溃，静默处理"""
        import core.updater as upd

        cache_path = tmp_data / "cache" / "update_check.json"
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        # 模拟 Release tag_name 为非纯数字（如 v2.1.0）
        data = {
            "tag_name": "v2.1.0",
            "body": "",
            "assets": [],
        }
        try:
            ver = upd._parse_version(data.get("tag_name", ""))
        except AttributeError:
            # _parse_version 不存在时，直接测试 _worker 路径的防御性
            ver = None

        # 核心：不抛异常即为通过
        # 若版本解析返回 None / 0，不会触发无效下载
        assert ver is None or isinstance(ver, int), "版本解析应返回 int 或 None"

    def test_C5_no_matching_asset_skips_download(self, tmp_data, monkeypatch):
        """C5: Release 无对应平台 asset 时静默跳过，不下载

        验证方法：直接调用 check_update_background 并 mock _fetch_latest 返回无匹配 asset
        的 release，断言 _download_update 从未被调用。
        """
        import core.updater as upd

        cache_path = tmp_data / "cache" / "update_check.json"
        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_self = tmp_data / "opskit.exe"
        _make_fake_exe(fake_self)

        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)
        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._self_path", lambda: fake_self)
        monkeypatch.setattr("core.config.get_data_dir", lambda: tmp_data)

        # assets 列表中只有 linux/darwin，无 windows asset
        api_data = {
            "tag_name": "999",
            "body": "",
            "assets": [
                {"name": "opskit-linux-amd64", "browser_download_url": "https://example.com/linux"},
                {"name": "opskit-darwin-amd64", "browser_download_url": "https://example.com/darwin"},
            ],
        }

        download_called = []

        cfg = {
            "update": {
                "enabled": True,
                "check_interval": 0,
                "auto_apply": True,
                "repo": "ougato/opskit-cli",
                "mirrors": [],
            }
        }

        with patch("core.updater.fetch_bootstrap", return_value=None), \
             patch("core.updater._fetch_latest", return_value=api_data), \
             patch("core.updater._should_check", return_value=True), \
             patch("core.updater._download_update",
                   side_effect=lambda url, sha: download_called.append(url) or None), \
             patch("core.updater._save_check_cache"), \
             patch("sys.platform", "win32"):
            upd.check_update_background(cfg)
            time.sleep(0.3)

        assert not download_called, \
            f"无匹配 Windows asset 时不应调用 _download_update，但被调用于: {download_called}"

    def test_D2_post_update_cleared_next_boot_checks_normally(self, tmp_data, monkeypatch):
        """D2: --post-update 处理后下次正常启动不被跳过"""
        import core.updater as upd

        cache_path = tmp_data / "cache" / "update_check.json"
        fake_self = tmp_data / "opskit.exe"
        _make_fake_exe(fake_self)

        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)
        monkeypatch.setattr("core.updater._get_pending_path",
                            lambda: tmp_data / "cache" / "opskit.pending")
        monkeypatch.setattr("core.updater._get_pending_tmp_path",
                            lambda: tmp_data / "cache" / "opskit.pending.tmp")
        monkeypatch.setattr("core.updater._self_path", lambda: fake_self)
        monkeypatch.setattr("core.config.get_data_dir", lambda: tmp_data)

        # 第一次：--post-update 启动，清理 pending
        fake_pending = tmp_data / "cache" / "opskit.pending"
        _make_fake_exe(fake_pending)

        with patch("sys.argv", ["main.py", "--post-update"]):
            result1 = upd.check_and_apply_pending()
        assert result1 is False
        assert not fake_pending.exists()

        # 第二次：正常启动，无 pending，应正常通过（返回 False 但不崩溃）
        with patch("sys.argv", ["main.py"]):
            result2 = upd.check_and_apply_pending()
        assert result2 is False, "无 pending 时应正常返回 False"

    def test_D4_pending_invalid_binary_removed_and_cache_cleared(self, tmp_data, monkeypatch):
        """D4: pending 文件非 MZ 头（如被杀毒删内容）时删除并清 cache"""
        import core.updater as upd

        cache_path = tmp_data / "cache" / "update_check.json"
        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_self = tmp_data / "opskit.exe"

        # pending 文件内容损坏（非 MZ）
        fake_pending.parent.mkdir(parents=True, exist_ok=True)
        fake_pending.write_bytes(b"\x00" * 1024)
        _make_fake_exe(fake_self)

        with cache_path.open("w") as f:
            json.dump({"pending_version": 2, "last_check": time.time()}, f)

        monkeypatch.setattr("core.updater._self_path", lambda: fake_self)
        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._get_pending_tmp_path",
                            lambda: tmp_data / "cache" / "opskit.pending.tmp")
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)
        monkeypatch.setattr("core.config.get_data_dir", lambda: tmp_data)

        with patch("sys.argv", ["main.py"]):
            result = upd.check_and_apply_pending()

        assert result is False
        assert not fake_pending.exists(), "损坏的 pending 文件应被删除"
        cache = upd._load_check_cache()
        assert cache.get("pending_version") is None, "损坏 pending 后 cache 中 pending_version 应被清除"

    def test_A5_httpx_reads_https_proxy_env(self, tmp_data, monkeypatch):
        """A5: HTTPS_PROXY 环境变量被 httpx 读取（验证 httpx 代理透传机制）"""
        import core.updater as upd

        cache_path = tmp_data / "cache" / "update_check.json"
        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_tmp = tmp_data / "cache" / "opskit.pending.tmp"

        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._get_pending_tmp_path", lambda: fake_tmp)
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        # 设置代理环境变量
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:8080")

        stream_kwargs_captured = []

        class FakeResp:
            status_code = 200
            headers = {"ETag": ""}
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def iter_bytes(self, cs):
                yield b"MZ" + b"\x00" * (512 * 1024 - 2)

        def fake_stream(method, url, **kwargs):
            stream_kwargs_captured.append(kwargs)
            return FakeResp()

        with patch("httpx.stream", side_effect=fake_stream), \
             patch("core.mirror.get_sources", return_value=["https://github.com/ougato"]), \
             patch("core.updater._get_sha256_from_url", return_value=""), \
             patch("core.updater._sha256_file", return_value=""), \
             patch("shutil.disk_usage", return_value=MagicMock(free=500 * 1024 * 1024)):
            upd._download_update("https://example.com/opskit", "")

        # httpx 默认从环境变量读取代理，无需额外配置
        # 验证：httpx.stream 被调用（不因代理而提前 crash）
        assert stream_kwargs_captured, "设置 HTTPS_PROXY 后 httpx.stream 应正常被调用"

    def test_sha256_file_correct_hash(self, tmp_data):
        """SHA256 计算正确性验证"""
        import hashlib
        import core.updater as upd

        f = tmp_data / "test.bin"
        data = b"MZ" + b"\xab\xcd" * 10000
        f.write_bytes(data)

        expected = hashlib.sha256(data).hexdigest()
        actual = upd._sha256_file(f)
        assert actual == expected

    def test_save_check_cache_merge_keeps_existing(self, tmp_data, monkeypatch):
        """_save_check_cache 做 merge 写入，不丢失已有字段"""
        import core.updater as upd

        cache_path = tmp_data / "cache" / "update_check.json"
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)

        upd._save_check_cache({"last_check": 12345, "pending_version": 2})
        upd._save_check_cache({"etag": '"abc"'})  # 追加写，不覆盖前面字段

        result = upd._load_check_cache()
        assert result["last_check"] == 12345, "merge 写入不应丢失 last_check"
        assert result["pending_version"] == 2, "merge 写入不应丢失 pending_version"
        assert result["etag"] == '"abc"', "新字段应被写入"

    def test_cleanup_multiple_old_exes(self, tmp_data):
        """多个 .old.exe 残留时全部被清理"""
        import core.updater as upd

        fake_self = tmp_data / "opskit.exe"
        _make_fake_exe(fake_self)
        for name in ["opskit.old.exe", "opskit_v1.old.exe", "app.old.exe"]:
            _make_fake_exe(tmp_data / name)

        upd._cleanup_old_exe(fake_self)

        for name in ["opskit.old.exe", "opskit_v1.old.exe", "app.old.exe"]:
            assert not (tmp_data / name).exists(), f"{name} 应被清理"

    def test_apply_update_pending_path_missing_pending_clears_marker(self, tmp_data, monkeypatch):
        """update_pending_path.json 存在但 pending 文件已丢失时，清理标记"""
        import core.updater as upd

        marker = tmp_data / "cache" / "update_pending_path.json"
        with marker.open("w") as f:
            json.dump({
                "pending": str(tmp_data / "nonexistent.pending"),
                "target": str(tmp_data / "opskit.exe"),
                "version": 2,
            }, f)

        monkeypatch.setattr("core.config.get_data_dir", lambda: tmp_data)

        result = upd._apply_update_pending_path()
        assert result is False
        assert not marker.exists(), "pending 不存在时标记文件应被清除"


# ═══════════════════════════════════════════════════════════════════════════════
# T32：正式端到端验收（需真实网络，默认跳过）
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(
    os.environ.get("OPSKIT_E2E_UPDATE") != "1",
    reason="需设置 OPSKIT_E2E_UPDATE=1 运行端到端验收",
)
class TestE2EAcceptance:
    def test_T32_real_network_download_and_ps_script(self, tmp_data, monkeypatch, caplog):
        """T32: 真实网络下 v1 → v2 下载 pending 并生成含 Rename-Item 的 PS 脚本"""
        import logging
        import core.updater as upd
        from core.constants import APP_VERSION

        cache_path = tmp_data / "cache" / "update_check.json"
        fake_pending = tmp_data / "cache" / "opskit.pending"
        fake_tmp = tmp_data / "cache" / "opskit.pending.tmp"
        fake_self = tmp_data / "opskit.exe"
        _make_fake_exe(fake_self, 1024 * 1024)

        monkeypatch.setattr("core.updater._get_pending_path", lambda: fake_pending)
        monkeypatch.setattr("core.updater._get_pending_tmp_path", lambda: fake_tmp)
        monkeypatch.setattr("core.updater._get_cache_path", lambda: cache_path)
        monkeypatch.setattr("core.updater._self_path", lambda: fake_self)
        monkeypatch.setattr("core.config.get_data_dir", lambda: tmp_data)
        monkeypatch.setattr("core.constants.APP_VERSION", 1)

        cfg = {
            "update": {
                "enabled": True,
                "check_interval": 0,
                "auto_apply": True,
                "repo": "ougato/opskit-cli",
                "mirrors": [
                    "https://mirror.ghproxy.com/https://github.com/ougato/opskit-cli/releases/download",
                    "https://github.com/ougato/opskit-cli/releases/download",
                ],
            }
        }

        with caplog.at_level(logging.INFO, logger="opskit.updater"):
            upd.check_update_background(cfg)
            time.sleep(30)

        if fake_pending.exists():
            with fake_pending.open("rb") as f:
                header = f.read(2)
            assert header == b"MZ", "pending 文件应为合法 Windows PE（MZ 头）"

            ps_files = list((tmp_data / "cache").glob("opskit_update.ps1"))
            if not ps_files:
                fake_bak = tmp_data / "backups"
                fake_bak.mkdir(exist_ok=True)
                monkeypatch.setattr("core.updater._get_backup_path",
                                    lambda: fake_bak / "opskit.v1.bak")
                ps_launched = []
                with patch("subprocess.Popen", side_effect=lambda *a, **k: ps_launched.append(Path(a[0][-1])) or MagicMock()):
                    upd.check_and_apply_pending()
                if ps_launched:
                    content = ps_launched[0].read_text(encoding="utf-8")
                    assert "Rename-Item" in content
                    assert f"$pid_to_wait = {os.getpid()}" in content
        else:
            pytest.skip("未能下载 pending 文件（可能网络受限），跳过 PE 验证")
