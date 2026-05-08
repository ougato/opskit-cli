"""自动更新机制 — 后台检测、下载、热替换、回滚"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from core.constants import (
    APP_VERSION,
    BOOTSTRAP_TIMEOUT,
    BOOTSTRAP_URLS,
    DOWNLOAD_RETRY_BASE_DELAY,
    FILE_BOOTSTRAP_CACHE,
    FILE_UPDATE_CACHE,
    GITHUB_API_RELEASES,
    GITHUB_RATELIMIT_SAFE,
    TIMEOUT_DOWNLOAD_READ,
    TIMEOUT_UPDATE_CHECK,
    UPDATE_RATELIMIT_BACKOFF,
)

_log = logging.getLogger("opskit.updater")


# ─── 路径辅助 ─────────────────────────────────────────────────────────────────

def _get_cache_path() -> Path:
    from core.config import get_data_dir
    return get_data_dir() / "cache" / FILE_UPDATE_CACHE


def _get_pending_path() -> Path:
    """待应用的新版本二进制路径"""
    from core.config import get_data_dir
    return get_data_dir() / "cache" / "opskit.pending"


def _get_backup_path() -> Path:
    """备份路径用时间戳命名，避免同版本多次更新互相覆盖"""
    from core.config import get_data_dir
    ts = int(time.time())
    return get_data_dir() / "backups" / f"opskit.v{APP_VERSION}.{ts}.bak"


def _get_pending_tmp_path() -> Path:
    """下载临时文件路径，下载完成后才 rename 为 pending，防止部分下载污染"""
    from core.config import get_data_dir
    return get_data_dir() / "cache" / "opskit.pending.tmp"


def _self_path() -> Path:
    """当前可执行文件路径（打包 or python 解释器）"""
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.executable)
    return Path(__file__).resolve().parent.parent / "main.py"


# ─── Bootstrap（动态更新源）─────────────────────────────────────────────────

def _get_bootstrap_cache_path() -> Path:
    from core.config import get_data_dir
    return get_data_dir() / "cache" / FILE_BOOTSTRAP_CACHE


def fetch_bootstrap() -> dict[str, Any] | None:
    """并发拉取 bootstrap.json，取最快返回的结果。

    成功后写入本地缓存，失败时用本地缓存。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch_one(url: str) -> dict[str, Any] | None:
        try:
            with httpx.Client(timeout=BOOTSTRAP_TIMEOUT) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    return resp.json()
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=len(BOOTSTRAP_URLS)) as pool:
        futures = {pool.submit(_fetch_one, url): url for url in BOOTSTRAP_URLS}
        for f in as_completed(futures, timeout=BOOTSTRAP_TIMEOUT + 2):
            result = f.result()
            if result and isinstance(result, dict):
                # 写入本地缓存
                try:
                    cache_path = _get_bootstrap_cache_path()
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    with cache_path.open("w", encoding="utf-8") as fp:
                        json.dump(result, fp)
                except Exception:
                    pass
                return result

    # 全部失败，读本地缓存
    try:
        cache_path = _get_bootstrap_cache_path()
        if cache_path.exists():
            with cache_path.open("r", encoding="utf-8") as fp:
                return json.load(fp)
    except Exception:
        pass
    return None


# ─── 启动时 pending 检测 ──────────────────────────────────────────────────────

def _verify_binary(path: Path) -> bool:
    """验证下载的二进制文件基本可用"""
    try:
        if path.stat().st_size < 1024 * 100:  # < 100KB 肯定不对
            return False
        with path.open("rb") as f:
            header = f.read(4)
        if sys.platform == "win32":
            return header[:2] == b"MZ"  # PE 格式
        else:
            return header == b"\x7fELF"  # ELF 格式
    except Exception:
        return False


def _cleanup_old_exe(self_path: Path) -> None:
    """清理同目录下的 *.old.exe 残留（多次更新后的垃圾）"""
    try:
        for old in self_path.parent.glob("*.old.exe"):
            old.unlink(missing_ok=True)
            _log.info("startup: cleaned up residual %s", old.name)
    except Exception as e:
        _log.warning("startup: failed to clean .old.exe: %s", e)


def _check_startup_ok() -> None:
    """崩溃回滚：检测上次更新后新版本是否成功启动过。

    机制：
    - 每次正常启动写入 startup_ok.json（含当前版本号）
    - 若启动时发现 startup_ok.json 中版本 < APP_VERSION，
      说明新版本从未成功启动（可能持续崩溃），自动回滚
    """
    from core.config import get_data_dir
    ok_file = get_data_dir() / "cache" / "startup_ok.json"
    try:
        if ok_file.exists():
            with ok_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            last_ok_ver = data.get("version", APP_VERSION)
            if last_ok_ver > APP_VERSION:
                _log.warning(
                    "startup_ok check: last successful version was %d, current is %d — rolling back",
                    last_ok_ver, APP_VERSION,
                )
                rollback()
                return
        ok_file.parent.mkdir(parents=True, exist_ok=True)
        with ok_file.open("w", encoding="utf-8") as f:
            json.dump({"version": APP_VERSION, "time": time.time()}, f)
    except Exception as e:
        _log.warning("_check_startup_ok: %s", e)


def _apply_update_pending_path() -> bool:
    """处理 update_pending_path.json 兜底标记：下次启动再尝试 rename。"""
    from core.config import get_data_dir
    marker = get_data_dir() / "cache" / "update_pending_path.json"
    if not marker.exists():
        return False
    try:
        with marker.open("r", encoding="utf-8") as f:
            data = json.load(f)
        pending = Path(data["pending"])
        target = Path(data["target"])
        version = int(data.get("version", APP_VERSION + 1))
        if not pending.exists():
            _log.warning("update_pending_path: pending file missing, clearing marker")
            marker.unlink(missing_ok=True)
            return False
        _log.info("update_pending_path: retrying rename %s -> %s", pending, target)
        old_path = target.parent / (target.stem + ".old.exe")
        if old_path.exists():
            old_path.unlink(missing_ok=True)
        target.rename(old_path)
        pending.rename(target)
        old_path.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        _save_check_cache({"last_check": time.time(), "latest": version})
        _log.info("update_pending_path: rename succeeded for v%d", version)
        return True
    except Exception as e:
        _log.warning("update_pending_path: retry failed: %s", e)
        return False


def check_and_apply_pending() -> bool:
    """启动时检测 pending 文件，如果存在则替换并返回 True（调用方应 exec 重启）。

    完整流程：
    1. --post-update 防循环检测
    2. .old.exe 残留清理
    3. startup_ok.json 崩溃回滚检测
    4. update_pending_path.json 兜底标记处理
    5. pending/cache 同步（pending 被手动删除时清理 cache）
    6. 常规 pending 检测与应用
    """
    if "--post-update" in sys.argv:
        _get_pending_path().unlink(missing_ok=True)
        _get_pending_tmp_path().unlink(missing_ok=True)
        _log.info("post-update boot, cleared pending files")
        return False

    self_path = _self_path()

    # 清理 .old.exe 残留
    _cleanup_old_exe(self_path)

    # 崩溃回滚检测
    _check_startup_ok()

    # 兜底标记处理（上次 _apply_windows 所有策略失败后写入的标记）
    if _apply_update_pending_path():
        global _pending_version
        cache = _load_check_cache()
        _pending_version = cache.get("latest")
        return True

    pending = _get_pending_path()
    cache = _load_check_cache()

    # pending/cache 同步：pending 被手动删除时清理 cache 中的 pending_version
    if not pending.exists() and cache.get("pending_version"):
        _log.info("startup: pending file missing but cache has pending_version, clearing")
        _save_check_cache({"pending_version": None})
        return False

    if not pending.exists():
        return False

    if not _verify_binary(pending):
        _log.warning("startup: pending binary failed verification, removing")
        _report("startup_pending_verify_failed", level="error",
                pending=str(pending),
                file_size=str(pending.stat().st_size) if pending.exists() else "0")
        pending.unlink(missing_ok=True)
        _save_check_cache({"pending_version": None})
        return False

    remote_ver = cache.get("pending_version") or (APP_VERSION + 1)
    _log.info("startup: found pending v%s, applying", remote_ver)
    ok = _do_apply(pending, remote_ver)
    if ok:
        _pending_version = remote_ver
    return ok


# ─── 版本检测 ─────────────────────────────────────────────────────────────────

def _load_check_cache() -> dict[str, Any]:
    path = _get_cache_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_check_cache(data: dict[str, Any]) -> None:
    """Merge 写入 cache，永不丢失已有字段（关键：保留 pending_version）"""
    path = _get_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_check_cache()
    existing.update(data)
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(existing, f)
    except Exception as e:
        _log.warning("save_check_cache failed: %s", e)


def _should_check(check_interval: int) -> bool:
    """判断是否需要检查更新（基于上次检查时间戳）

    额外处理时钟跳变（NTP 同步后倒退）：若 last_check 比当前时间还大，
    说明时钟已倒退，强制重新检查。
    """
    cache = _load_check_cache()
    last = cache.get("last_check", 0)
    now = time.time()
    if last > now:
        _log.info("_should_check: clock jumped back (last=%s now=%s), forcing check", last, now)
        return True
    return (now - last) >= check_interval


def _fetch_latest(repo: str, token: str = "") -> dict[str, Any] | None:
    """
    调用 GitHub Releases API 获取最新版本信息。

    处理速率限制：
    - 收到 403 / 429 → 返回 None（静默跳过）
    - X-RateLimit-Remaining < GITHUB_RATELIMIT_SAFE → 记录，下次延迟检查
    """
    url = GITHUB_API_RELEASES.format(repo=repo)
    headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    try:
        with httpx.Client(timeout=TIMEOUT_UPDATE_CHECK) as client:
            resp = client.get(url, headers=headers)

        remaining = int(resp.headers.get("X-RateLimit-Remaining", 99))
        if remaining < GITHUB_RATELIMIT_SAFE:
            _save_check_cache({"rate_limit_hit": True})

        if resp.status_code in (403, 429):
            _save_check_cache({"last_check": time.time() + UPDATE_RATELIMIT_BACKOFF - 86400})
            _log.warning("_fetch_latest: rate limited (%s), backing off %ss", resp.status_code, UPDATE_RATELIMIT_BACKOFF)
            _report("fetch_latest_rate_limited", level="warning",
                    http_status=str(resp.status_code), repo=repo,
                    rate_limit_remaining=str(remaining))
            return None
        if resp.status_code != 200:
            _report("fetch_latest_http_error", level="warning",
                    http_status=str(resp.status_code), repo=repo, api_url=url)
            return None

        data = resp.json()
        if not isinstance(data, dict):
            _log.warning("_fetch_latest: unexpected API response type: %s", type(data))
            _report("fetch_latest_bad_response", level="error",
                    response_type=str(type(data)), repo=repo)
            return None
        return data
    except Exception as e:
        _report("fetch_latest_exception", exc=e, level="error",
                repo=repo, api_url=url)
        return None


def _parse_sha256(body: str, filename: str) -> str:
    """从 Release Body 中解析指定文件的 SHA256 校验值"""
    for line in (body or "").splitlines():
        if filename in line:
            parts = line.split()
            for p in parts:
                if len(p) == 64:
                    return p.lower()
    return ""


def _asset_filename() -> str:
    """根据当前平台和架构生成资产文件名"""
    from core.platform import get_platform
    info = get_platform()
    os_map = {"linux": "linux", "windows": "windows", "darwin": "darwin"}
    os_name = os_map.get(info.os_type, info.os_type)
    arch_map = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64", "armv7": "armv7"}
    arch = arch_map.get(info.arch, info.arch)
    ext = ".exe" if info.os_type == "windows" else ""
    return f"opskit-{os_name}-{arch}{ext}"


# ─── 下载与校验 ───────────────────────────────────────────────────────────────

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _get_sha256_from_url(sha256_url: str) -> str:
    """尝试从独立 .sha256 文件获取校验值（备用来源）"""
    try:
        from core.constants import TIMEOUT_HTTP
        with httpx.Client(timeout=TIMEOUT_HTTP) as client:
            resp = client.get(sha256_url, follow_redirects=True)
            if resp.status_code == 200:
                text = resp.text.strip().split()[0]
                if len(text) == 64:
                    return text.lower()
    except Exception:
        pass
    return ""


def _report(msg: str, exc: Exception | None = None, level: str = "warning", **ctx) -> None:
    """统一上报入口：附加更新通用上下文后转发给 telemetry"""
    try:
        import core.telemetry as _tel
        base: dict = {
            "component": "updater",
            "app_version": str(APP_VERSION),
            "platform": sys.platform,
        }
        base.update(ctx)
        if exc is not None:
            _tel.capture_error(exc, **base)
        else:
            _tel.capture_message(msg, level=level, **base)
    except Exception:
        pass


def _download_update(asset_url: str, sha256_expected: str) -> Path | None:
    """
    下载新版本到临时文件，校验 SHA256 后原子 rename 为 pending。

    优化项：
    - 断点续传：检测 .tmp 已有字节，发送 Range: bytes={offset}-
    - stall 超时：每块读取使用 TIMEOUT_DOWNLOAD_READ，防止连接挂死
    - 指数退避：第 n 次失败后等待 2^(n-1) * DOWNLOAD_RETRY_BASE_DELAY 秒
    - ETag 缓存：成功下载后缓存 ETag，下次发送 If-None-Match 协商
    - 磁盘检查：下载前检测剩余空间是否充足
    - tmp 备用目录：tmp 写入失败时尝试 tempfile.gettempdir()
    - SHA256 双源：Release Body 解析失败时尝试 {asset_url}.sha256 文件
    """
    import tempfile
    from core.mirror import get_sources
    from core.constants import MAX_RETRY_DOWNLOAD, DOWNLOAD_CHUNK_SIZE, TIMEOUT_HTTP

    tmp_path = _get_pending_tmp_path()
    pending_path = _get_pending_path()

    def _ensure_tmp_dir() -> Path:
        """确保 tmp 目录可写，失败时降级到系统 temp 目录"""
        try:
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            test_f = tmp_path.parent / ".write_test"
            test_f.write_bytes(b"")
            test_f.unlink(missing_ok=True)
            return tmp_path
        except OSError as _e:
            _log.warning("download: primary tmp dir not writable, falling back to tempdir")
            _report("download_tmp_dir_fallback", exc=_e, level="warning",
                    tmp_dir=str(tmp_path.parent))
            return Path(tempfile.gettempdir()) / "opskit.pending.tmp"

    actual_tmp = _ensure_tmp_dir()

    mirrors = get_sources("github_releases")
    urls: list[str] = []
    for m in mirrors:
        if "github.com" in m:
            urls.append(asset_url)
        else:
            proxied = f"{m.rstrip('/')}/{asset_url.lstrip('/')}"
            urls.append(proxied)
    if not urls:
        urls = [asset_url]

    # SHA256 双源：若 Release Body 无校验值，尝试下载独立 .sha256 文件
    if not sha256_expected:
        _log.info("download: no SHA256 in release body, trying %s.sha256", asset_url)
        sha256_expected = _get_sha256_from_url(asset_url + ".sha256")
        if sha256_expected:
            _log.info("download: got SHA256 from .sha256 file: %s", sha256_expected)

    # ETag 协商：发送 If-None-Match 头，服务器 304 则跳过下载
    cache = _load_check_cache()
    cached_etag: str = cache.get("etag", "")

    # 磁盘空间检查：至少需要 100MB 可用
    try:
        free = shutil.disk_usage(actual_tmp.parent).free
        if free < 100 * 1024 * 1024:
            _log.warning("download: insufficient disk space (%d bytes free), skipping", free)
            _report("download_disk_full", level="error",
                    free_bytes=str(free), tmp_dir=str(actual_tmp.parent),
                    asset_url=asset_url)
            return None
    except Exception:
        pass

    # stall 超时：连接/首字节超时 TIMEOUT_HTTP，单块读取超时 TIMEOUT_DOWNLOAD_READ
    stream_timeout = httpx.Timeout(
        connect=TIMEOUT_HTTP,
        read=TIMEOUT_DOWNLOAD_READ,
        write=None,
        pool=TIMEOUT_HTTP,
    )

    for attempt in range(MAX_RETRY_DOWNLOAD):
        url = urls[attempt % len(urls)]
        _log.info("download attempt %d/%d: %s", attempt + 1, MAX_RETRY_DOWNLOAD, url)

        # 指数退避（第 0 次不等待）
        if attempt > 0:
            delay = DOWNLOAD_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            _log.info("download: backoff %ds before attempt %d", delay, attempt + 1)
            time.sleep(delay)

        try:
            # 断点续传：检测已下载字节
            offset = actual_tmp.stat().st_size if actual_tmp.exists() else 0
            headers: dict[str, str] = {}
            if offset > 0:
                headers["Range"] = f"bytes={offset}-"
                _log.info("download: resuming from byte %d", offset)
            if cached_etag and attempt == 0 and offset == 0:
                headers["If-None-Match"] = cached_etag

            with httpx.stream("GET", url, timeout=stream_timeout,
                              follow_redirects=True, headers=headers) as resp:
                if resp.status_code == 304:
                    _log.info("download: ETag matched (304), pending still valid")
                    if pending_path.exists():
                        return pending_path
                    # pending 被删除但 ETag 命中，清除缓存重下
                    _save_check_cache({"etag": ""})
                    cached_etag = ""
                    continue
                if resp.status_code == 206:
                    _log.info("download: server supports range, appending from %d", offset)
                    mode = "ab"
                elif resp.status_code == 200:
                    if offset > 0:
                        _log.info("download: server returned 200 (no range support), restarting")
                    actual_tmp.unlink(missing_ok=True)
                    offset = 0
                    mode = "wb"
                else:
                    _log.warning("download HTTP %s from %s", resp.status_code, url)
                    _report("download_http_error", level="warning",
                            http_status=str(resp.status_code), mirror_url=url,
                            asset_url=asset_url, attempt=str(attempt + 1))
                    actual_tmp.unlink(missing_ok=True)
                    continue

                new_etag = resp.headers.get("ETag", "")
                with actual_tmp.open(mode) as f:
                    for chunk in resp.iter_bytes(DOWNLOAD_CHUNK_SIZE):
                        f.write(chunk)

            if sha256_expected:
                actual_hash = _sha256_file(actual_tmp)
                if actual_hash != sha256_expected:
                    _log.warning("SHA256 mismatch: expected %s got %s", sha256_expected, actual_hash)
                    _report("download_sha256_mismatch", level="error",
                            expected_sha256=sha256_expected, actual_sha256=actual_hash,
                            mirror_url=url, asset_url=asset_url, attempt=str(attempt + 1),
                            file_size=str(actual_tmp.stat().st_size) if actual_tmp.exists() else "0")
                    actual_tmp.unlink(missing_ok=True)
                    continue
            else:
                _log.warning("download: no SHA256 available, skipping integrity check")
                _report("download_no_sha256", level="warning",
                        asset_url=asset_url, mirror_url=url)

            actual_tmp.replace(pending_path)
            if new_etag:
                _save_check_cache({"etag": new_etag})
            _log.info("download complete, pending ready at %s", pending_path)
            return pending_path

        except Exception as e:
            _log.warning("download attempt %d failed: %s", attempt + 1, e)
            _report("download_attempt_exception", exc=e, level="warning",
                    attempt=str(attempt + 1), mirror_url=url, asset_url=asset_url)

    _log.error("all %d download attempts failed for %s", MAX_RETRY_DOWNLOAD, asset_url)
    _report("download_all_attempts_failed", level="error",
            max_retries=str(MAX_RETRY_DOWNLOAD), asset_url=asset_url)
    return None


# ─── 后台检测 ─────────────────────────────────────────────────────────────────

_pending_version: int | None = None


def check_update_background(cfg: dict) -> None:
    """
    在后台线程中检测更新（不阻塞启动）。

    发现新版本 → 下载到 pending 路径 → 设置全局标志
    用户退出时由 apply_pending_update() 完成热替换
    """
    def _worker():
        global _pending_version
        update_cfg = cfg.get("update", {})
        if not update_cfg.get("enabled", True):
            return

        check_interval: int = update_cfg.get("check_interval", 86400)
        if not _should_check(check_interval):
            # 检查是否有已下载但未应用的版本
            pending = _get_pending_path()
            if pending.exists():
                cache = _load_check_cache()
                _pending_version = cache.get("pending_version")
            return

        repo: str = update_cfg.get("repo", "ougato/opskit-cli")
        token: str = update_cfg.get("github_token", "")

        data = _fetch_latest(repo, token)
        if data is None:
            return

        from core.version import parse_version, is_newer
        tag = data.get("tag_name", "")
        remote_ver = parse_version(tag)
        if not is_newer(remote_ver):
            _save_check_cache({"last_check": time.time(), "latest": remote_ver})
            return

        # 发现新版本 → 下载
        filename = _asset_filename()
        body = data.get("body", "")
        sha256 = _parse_sha256(body, filename)

        assets = data.get("assets", [])
        asset_url = ""
        for a in assets:
            if a.get("name") == filename:
                asset_url = a.get("browser_download_url", "")
                break

        if not asset_url:
            _report("update_no_matching_asset", level="warning",
                    filename=filename, tag=tag,
                    available_assets=",".join(a.get("name", "") for a in assets))
            return

        pending = _download_update(asset_url, sha256)
        if pending:
            _pending_version = remote_ver
            _save_check_cache({
                "last_check": time.time(),
                "latest": remote_ver,
                "pending_version": remote_ver,
            })
        else:
            _report("update_download_failed", level="error",
                    tag=tag, remote_ver=str(remote_ver), asset_url=asset_url)

    t = threading.Thread(target=_worker, daemon=True, name="opskit-updater")
    t.start()


# ─── 热替换（退出时应用）─────────────────────────────────────────────────────

def apply_pending_update() -> None:
    """退出时应用已下载的更新（与 check_and_apply_pending 共享 _do_apply 逻辑）"""
    pending = _get_pending_path()
    if not pending.exists():
        return

    if not _verify_binary(pending):
        _log.warning("exit: pending binary failed verification, removing")
        _report("exit_pending_verify_failed", level="error",
                pending=str(pending),
                file_size=str(pending.stat().st_size) if pending.exists() else "0")
        pending.unlink(missing_ok=True)
        return

    cache = _load_check_cache()
    remote_ver = cache.get("pending_version") or (APP_VERSION + 1)
    _log.info("exit: applying pending v%s", remote_ver)
    _do_apply(pending, remote_ver)


def _do_apply(pending: Path, remote_ver: int) -> bool:
    """
    统一替换逻辑（被 check_and_apply_pending 和 apply_pending_update 共用）。

    流程：
    1. 备份当前版本到时间戳路径，并校验备份完整性
    2. Unix: pending.replace(self_path) 原子替换；跨 fs 降级为 copy2
       Windows: PowerShell 脚本延迟替换（进程退出后执行）
    3. 清理旧备份（保留最近 3 个）
    4. 更新 cache（merge 写，保留 pending_version 等字段）
    返回 True 表示替换已成功（Unix 同步）或已调度（Windows 异步）
    """
    self_path = _self_path()
    backup = _get_backup_path()
    backup.parent.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copy2(self_path, backup)
        if _sha256_file(backup) != _sha256_file(self_path):
            backup.unlink(missing_ok=True)
            _log.error("_do_apply: backup integrity check failed, aborting")
            _report("apply_backup_integrity_fail", level="error",
                    self_path=str(self_path), backup=str(backup),
                    remote_ver=str(remote_ver))
            return False
    except Exception as e:
        _log.error("_do_apply: backup failed: %s", e)
        _report("apply_backup_failed", exc=e, level="error",
                self_path=str(self_path), backup=str(backup),
                remote_ver=str(remote_ver))
        return False

    try:
        if sys.platform == "win32":
            _apply_windows(pending, self_path, remote_ver)
        else:
            _apply_unix(pending, self_path, remote_ver)
        _cleanup_old_backups()
        return True
    except Exception as e:
        _log.error("_do_apply: apply failed: %s", e)
        _report("apply_failed", exc=e, level="error",
                self_path=str(self_path), pending=str(pending),
                remote_ver=str(remote_ver), platform=sys.platform)
        return False


def _apply_unix(pending: Path, self_path: Path, new_version: int) -> None:
    """Linux/macOS: rename 原子替换 + 保留原文件权限"""
    import stat
    try:
        original_mode = self_path.stat().st_mode
        original_uid = self_path.stat().st_uid
        original_gid = self_path.stat().st_gid
    except Exception:
        original_mode = stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
        original_uid = original_gid = -1

    pending.chmod(pending.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    try:
        pending.replace(self_path)
        _log.info("_apply_unix: atomic rename succeeded")
    except OSError as e:
        _log.warning("_apply_unix: rename failed (%s), falling back to copy2", e)
        shutil.copy2(str(pending), str(self_path))
        pending.unlink(missing_ok=True)

    os.chmod(str(self_path), original_mode)
    if original_uid >= 0:
        try:
            os.chown(str(self_path), original_uid, original_gid)
        except PermissionError:
            pass

    _save_check_cache({"last_check": time.time(), "latest": new_version})


def _apply_windows(pending: Path, self_path: Path, new_version: int) -> None:
    """Windows: Rename-Then-Copy 策略 + 四层防御确保更新完成。

    策略优先级（业界标准，参考 Electron/Squirrel/Nuitka）：
    1. Rename-Then-Copy（主路径）：
       - PS 脚本 Wait-Process 等父进程退出，再 Rename-Item 当前 exe → .old
       - Rename-Item pending → exe（Vista+ 允许 rename 运行中 exe）
       - icacls 确保新 exe 有执行权限
    2. 跨驱动器降级：若 pending 与 exe 不在同一驱动器，rename 失败则 copy2
    3. MoveFileEx MOVEFILE_DELAY_UNTIL_REBOOT（需管理员权限）：
       - 若 PS 执行策略被 GPO 禁或权限不足，通过 ctypes 注册重启后替换
    4. update_pending_path.json 最终兜底：
       - 若以上均失败，写入标记文件，下次启动时再尝试
    """
    import subprocess
    pid = os.getpid()
    ps_path = pending.parent / "opskit_update.ps1"
    failed_json = str(pending.parent / "update_failed.json")
    old_path = self_path.parent / (self_path.stem + ".old.exe")
    exe_name = self_path.name

    # PowerShell 脚本：Rename-Then-Copy + Wait-Process + 跨驱动器降级 + icacls
    ps_content = f"""# OpsKit updater — Rename-Then-Copy strategy
$pid_to_wait = {pid}
$self_path   = '{self_path}'
$pending     = '{pending}'
$old_path    = '{old_path}'
$exe_name    = '{exe_name}'
$failed_json = '{failed_json}'

# 等待触发更新的父进程真正退出（防竞态）
if ($pid_to_wait -gt 0) {{
    Wait-Process -Id $pid_to_wait -Timeout 30 -ErrorAction SilentlyContinue
}}
Start-Sleep -Milliseconds 500

# 尝试 Rename-Then-Copy（主路径）
$maxWait = 15
$waited  = 0
# 等待目标文件解锁（杀毒软件短暂扫描等情况）
while ($waited -lt $maxWait) {{
    try {{
        $stream = [IO.File]::Open($self_path, 'Open', 'ReadWrite', 'None')
        $stream.Close()
        break
    }} catch {{
        Start-Sleep -Seconds 2
        $waited++
    }}
}}

try {{
    # 先把旧 exe rename（Windows Vista+ 允许 rename 运行中的 exe）
    if (Test-Path $old_path) {{ Remove-Item -Path $old_path -Force -ErrorAction SilentlyContinue }}
    Rename-Item -Path $self_path -NewName $old_path -Force -ErrorAction Stop
    # 再把 pending rename 到原路径
    Rename-Item -Path $pending -NewName (Split-Path $self_path -Leaf) -ErrorAction Stop
    # 确保新 exe 有执行权限
    icacls $self_path /grant 'Users:(RX)' /T /C /Q 2>$null
    Remove-Item -Path $old_path -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
    exit 0
}} catch {{
    # 跨驱动器降级：rename 失败则 copy2
    try {{
        Copy-Item -Path $pending -Destination $self_path -Force -ErrorAction Stop
        Remove-Item -Path $pending -Force -ErrorAction SilentlyContinue
        icacls $self_path /grant 'Users:(RX)' /T /C /Q 2>$null
        Remove-Item -Path $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
        exit 0
    }} catch {{
        @{{ error = 'rename_and_copy_failed'; time = (Get-Date -Format 'o') }} | ConvertTo-Json | Set-Content $failed_json
        exit 1
    }}
}}
"""
    try:
        with ps_path.open("w", encoding="utf-8") as f:
            f.write(ps_content)
        ps_exe = shutil.which("powershell") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        extra: dict = {}
        if sys.platform == "win32":
            extra["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            extra["close_fds"] = True
        subprocess.Popen(
            [ps_exe, "-ExecutionPolicy", "Bypass", "-NonInteractive", "-File", str(ps_path)],
            **extra,
        )
        _save_check_cache({"last_check": time.time(), "latest": new_version})
        _log.info("_apply_windows: update script launched (pid=%d)", pid)
        return
    except Exception as e:
        _log.error("_apply_windows: failed to launch PS script: %s", e)
        _report("apply_windows_ps_failed", exc=e, level="error",
                ps_exe=str(shutil.which("powershell") or "not_found"),
                ps_path=str(ps_path), new_version=str(new_version))

    # 兜底 3：MoveFileEx MOVEFILE_DELAY_UNTIL_REBOOT（需管理员权限）
    if sys.platform == "win32":
        try:
            import ctypes
            MOVEFILE_DELAY_UNTIL_REBOOT = 0x4
            MOVEFILE_REPLACE_EXISTING = 0x1
            ret = ctypes.windll.kernel32.MoveFileExW(  # type: ignore[attr-defined]
                str(pending), str(self_path),
                MOVEFILE_DELAY_UNTIL_REBOOT | MOVEFILE_REPLACE_EXISTING,
            )
            if ret:
                _log.info("_apply_windows: MoveFileEx scheduled on reboot")
                _save_check_cache({"last_check": time.time(), "latest": new_version})
                return
            else:
                err = ctypes.GetLastError()  # type: ignore[attr-defined]
                _log.warning("_apply_windows: MoveFileEx failed (err=%d)", err)
                _report("apply_windows_movefileex_failed", level="warning",
                        win_error=str(err), new_version=str(new_version))
        except Exception as e:
            _log.warning("_apply_windows: MoveFileEx exception: %s", e)
            _report("apply_windows_movefileex_exception", exc=e, level="warning",
                    new_version=str(new_version))

    # 兜底 4：写入标记文件，下次启动再尝试
    try:
        from core.config import get_data_dir
        marker = get_data_dir() / "cache" / "update_pending_path.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        with marker.open("w", encoding="utf-8") as f:
            json.dump({"pending": str(pending), "target": str(self_path), "version": new_version}, f)
        _log.info("_apply_windows: wrote update_pending_path.json for next-boot retry")
        _report("apply_windows_fallback4_marker", level="warning",
                new_version=str(new_version), pending=str(pending),
                self_path=str(self_path))
    except Exception as e:
        _log.error("_apply_windows: all fallbacks failed: %s", e)
        _report("apply_windows_all_fallbacks_failed", exc=e, level="fatal",
                new_version=str(new_version), pending=str(pending),
                self_path=str(self_path))
    raise RuntimeError("_apply_windows: all update strategies failed")


def _cleanup_old_backups() -> None:
    """保留最近 3 个备份，删除更旧的"""
    from core.config import get_data_dir
    bak_dir = get_data_dir() / "backups"
    if not bak_dir.exists():
        return
    baks = sorted(bak_dir.glob("opskit.v*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in baks[3:]:
        old.unlink(missing_ok=True)


# ─── 回滚 ─────────────────────────────────────────────────────────────────────

def rollback() -> bool:
    """
    回滚到最近一个备份版本。

    返回 True 表示回滚成功，False 表示失败。
    """
    from core.config import get_data_dir
    bak_dir = get_data_dir() / "backups"
    if not bak_dir.exists():
        return False
    baks = sorted(bak_dir.glob("opskit.v*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not baks:
        return False

    self_path = _self_path()
    try:
        shutil.copy2(baks[0], self_path)
        if sys.platform != "win32":
            import stat
            self_path.chmod(self_path.stat().st_mode | stat.S_IEXEC)
        return True
    except Exception:
        return False


def has_pending_update() -> bool:
    """是否有待应用的更新"""
    return _pending_version is not None


def pending_version() -> int | None:
    """返回待应用版本号，无则 None"""
    return _pending_version
