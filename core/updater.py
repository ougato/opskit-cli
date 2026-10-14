"""自动更新机制 — 后台检测、下载、热替换、回滚"""
from __future__ import annotations

import hashlib
import errno
import re
import json
import logging
import os
import shutil
import stat
import sys
import threading
import time
from contextvars import ContextVar
from functools import wraps
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

from core.constants import (
    APP_VERSION,
    BOOTSTRAP_TIMEOUT,
    BOOTSTRAP_URLS,
    DOWNLOAD_RETRY_BASE_DELAY,
    FILE_BOOTSTRAP_CACHE,
    FILE_MACHINE_ID,
    FILE_UPDATE_CACHE,
    FILE_UPDATE_HEALTH,
    GITHUB_API_RELEASES,
    GITHUB_BASE,
    MAX_HEALTH_FAILS,
    TIMEOUT_DOWNLOAD_READ,
    TIMEOUT_UPDATE_CHECK,
    UPDATE_RATELIMIT_BACKOFF,
    UPDATE_ROLLOUT_FULL,
)

_log = logging.getLogger("opskit.updater")

_cache_lock = threading.Lock()
_query_readonly = ContextVar("update_query_readonly", default=False)
_download_staging = ContextVar("update_download_staging", default=None)
MAX_UPDATE_BYTES = 1024 * 1024 * 1024


class _UpdateLimitError(RuntimeError):
    pass


def _update_lock_path():
    return _get_cache_path().parent / "locks" / "update.lock"


from core.resource_lock import ResourceLock
_update_resource = ResourceLock(lambda: _update_lock_path(), label="update staging area")


def _update_target_lock_path(target=None):
    target = Path(target if target is not None else _self_path()).resolve()
    identity = os.path.normcase(str(target))
    name = hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".lock"
    return target.parent / ".opskit-update-locks" / name


_update_target_resource = ResourceLock(lambda: _update_target_lock_path(), label="update executable")


def _update_target_mutation(operation):
    @wraps(operation)
    def locked(*args, **kwargs):
        with _update_target_resource.hold():
            return operation(*args, **kwargs)
    return locked


def _update_mutation(operation):
    @wraps(operation)
    def locked(*args, **kwargs):
        with _update_resource.hold():
            return operation(*args, **kwargs)
    return locked


def _readonly_json(client, url, *, headers=None):
    """Read a small uncompressed metadata response without caching it."""
    headers = {**(headers or {}), "Accept-Encoding": "identity"}
    with client.stream("GET", url, headers=headers) as response:
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity") != "identity":
            raise RuntimeError("compressed update metadata refused")
        body = bytearray()
        for chunk in response.iter_raw():
            if len(body) + len(chunk) > 262144:
                raise RuntimeError("update metadata exceeds limit")
            body.extend(chunk)
        value = json.loads(body)
        if not isinstance(value, dict):
            raise RuntimeError("invalid update metadata")
        return value


# ─── 路径辅助 ─────────────────────────────────────────────────────────────────

def _get_cache_path() -> Path:
    from core.config import get_data_dir
    if _download_staging.get() is not None:
        return _download_staging.get() / FILE_UPDATE_CACHE
    return get_data_dir() / "cache" / FILE_UPDATE_CACHE


def _get_pending_path() -> Path:
    """Resolve an atomically published candidate; retain legacy cache support."""
    root = _get_cache_path().parent
    if _download_staging.get() is not None:
        return root / "opskit.pending"
    try:
        cache = _read_update_record(_get_cache_path())
    except FileNotFoundError:
        return root / "opskit.pending"
    candidate = cache.get("candidate_id")
    if candidate is None:
        return root / "opskit.pending"
    if not isinstance(candidate, str) or not re.fullmatch(r"[a-f0-9]{32}", candidate):
        raise ValueError("invalid update candidate identity")
    result = root / "update-candidates" / candidate / "opskit.pending"
    _update_record_path(result)
    return result


def _get_backup_path() -> Path:
    """Unique backup identity, including multiple updates within one second."""
    from uuid import uuid4
    from core.config import get_data_dir
    ts = int(time.time())
    return get_data_dir() / "backups" / f"opskit.v{APP_VERSION}.{ts}.{uuid4().hex}.bak"


def _get_pending_tmp_path() -> Path:
    """下载临时文件路径，下载完成后才 rename 为 pending，防止部分下载污染"""
    return _get_cache_path().parent / "opskit.pending.tmp"


def _is_frozen() -> bool:
    """是否为打包后的可执行文件（Nuitka / PyInstaller）。

    源码 / 开发模式（直接跑 main.py）返回 False，此时自更新一律禁用，
    避免把 pending 二进制覆盖到 main.py 源文件上。
    """
    return getattr(sys, "frozen", False) or "__compiled__" in globals()


def _self_path() -> Path:
    """当前可执行文件路径（打包 or python 解释器）"""
    if _is_frozen():
        return Path(sys.executable).resolve()
    return Path(__file__).resolve().parent.parent / "main.py"


# ─── Bootstrap（动态更新源）─────────────────────────────────────────────────

def _get_bootstrap_cache_path() -> Path:
    from core.config import get_data_dir
    return get_data_dir() / "cache" / FILE_BOOTSTRAP_CACHE


def fetch_bootstrap() -> dict[str, Any] | None:
    """并发拉取 bootstrap.json，取最快返回的结果。

    成功后写入本地缓存，失败时用本地缓存。
    """
    if _query_readonly.get():
        # Parent process owns the total deadline; avoid a thread pool whose
        # shutdown could keep an explicit check waiting after a first result.
        for url in BOOTSTRAP_URLS:
            try:
                with httpx.Client(timeout=BOOTSTRAP_TIMEOUT) as client:
                    return _readonly_json(client, url)
            except (httpx.HTTPError, ValueError, RuntimeError):
                continue
        return None
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


# ─── 控制面（manifest）评估：kill_switch / min_build / rollout 灰度 ──────────

def _get_machine_id() -> str:
    """稳定的机器指纹（首次随机生成并持久化），用于灰度分桶。"""
    from core.config import get_data_dir
    path = get_data_dir() / "cache" / FILE_MACHINE_ID
    if _query_readonly.get():
        try:
            with path.open("rb") as source:
                raw = source.read(257)
            if len(raw) > 256:
                raise ValueError()
            mid = raw.decode("utf-8").strip()
            if mid:
                return mid
        except (OSError, ValueError):
            pass
        raise RuntimeError("update rollout identity is not initialized")
    try:
        if path.exists():
            mid = path.read_text(encoding="utf-8").strip()
            if mid:
                return mid
    except Exception:
        pass
    import uuid
    mid = uuid.uuid4().hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(mid, encoding="utf-8")
    except Exception:
        pass
    return mid


def _machine_bucket() -> int:
    """把机器指纹哈希到 0..99 的稳定分桶值，用于 rollout 百分比灰度判定。"""
    digest = hashlib.sha256(_get_machine_id().encode("utf-8")).hexdigest()
    return int(digest, 16) % UPDATE_ROLLOUT_FULL


def _evaluate_manifest(manifest: dict[str, Any]) -> int | None:
    """根据控制面 manifest 决定是否更新。

    返回目标 build（应更新）或 None（不更新：急停 / 非更新 / 未命中灰度）。
    """
    from core.version import is_newer
    if _query_readonly.get() and (type(manifest.get("latest_build")) is not int
                                 or manifest["latest_build"] <= 0):
        raise RuntimeError("invalid update manifest version")
    if manifest.get("kill_switch"):
        if not _query_readonly.get():
            _log.info("manifest: kill_switch on, skipping update")
        return None

    try:
        latest = int(manifest.get("latest_build", 0))
    except (TypeError, ValueError):
        return None
    if not is_newer(latest):
        _save_check_cache({"latest": latest})
        return None

    try:
        min_build = int(manifest.get("min_build", 0))
    except (TypeError, ValueError):
        min_build = 0
    forced = APP_VERSION < min_build
    if not forced:
        try:
            rollout = int(manifest.get("rollout", UPDATE_ROLLOUT_FULL))
        except (TypeError, ValueError):
            rollout = UPDATE_ROLLOUT_FULL
        bucket = 0 if _query_readonly.get() and rollout >= UPDATE_ROLLOUT_FULL else _machine_bucket()
        if bucket >= rollout:
            if not _query_readonly.get():
                _log.info("manifest: not in rollout cohort (bucket>=%d), skipping", rollout)
            _save_check_cache({"latest": latest})
            return None
    else:
        if not _query_readonly.get():
            _log.info("manifest: forced update (APP_VERSION %d < min_build %d)", APP_VERSION, min_build)
    return latest


# ─── 启动时 pending 检测 ──────────────────────────────────────────────────────

def _verify_binary(path: Path) -> bool:
    """验证下载的二进制文件基本可用"""
    try:
        if not 1024 * 100 <= path.stat().st_size <= MAX_UPDATE_BYTES:
            return False
        with path.open("rb") as f:
            header = f.read(4)
        if sys.platform == "win32":
            return header[:2] == b"MZ"  # PE 格式
        elif sys.platform == "darwin":
            # Mach-O (32/64-bit, both byte orders) and universal binaries.
            return header in (b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
                              b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
                              b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
                              b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca")
        else:
            return header == b"\x7fELF"  # ELF 格式
    except Exception:
        return False


def _cleanup_old_exe(self_path: Path) -> None:
    """Only clean the exact residual name created for this executable."""
    try:
        old = self_path.parent / (self_path.stem + ".old.exe")
        if old.is_file() and not old.is_symlink():
            old.unlink(missing_ok=True)
            _log.info("startup: cleaned up residual %s", old.name)
    except Exception as e:
        _log.warning("startup: failed to clean .old.exe: %s", e)


def _get_health_path() -> Path:
    from core.config import get_data_dir
    return get_data_dir() / "cache" / FILE_UPDATE_HEALTH


def _update_record_path(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise ValueError("update record path must not be a link")


def _read_update_record(path: Path) -> dict:
    """Bounded regular-file records; refuse links, FIFOs and hard links."""
    _update_record_path(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("update record must be a private regular file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as source:
        opened = os.fstat(source.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
            raise ValueError("update record changed during open")
        raw = source.read(65537)
    if len(raw) > 65536:
        raise ValueError("update record exceeds limit")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("update record must be an object")
    return result


def _write_update_record(path: Path, data: dict) -> None:
    from core.secrets import atomic_write, restrict_permissions
    _update_record_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    restrict_permissions(path.parent)
    atomic_write(path, json.dumps(data))


def mark_update_applied(build: int, *, backup: Path | None = None) -> None:
    """更新替换成功后写入健康探针：标记 build 刚升级、尚未确认健康。"""
    try:
        with _update_resource.hold():
            if type(build) is not int or build <= 0:
                raise ValueError("invalid applied update build")
            path = _get_health_path()
            try:
                previous = _read_health_record(path)
            except FileNotFoundError:
                previous = None
            if previous is not None:
                if previous["build"] > build:
                    raise ValueError("health belongs to a newer update")
                if previous["build"] == build:
                    if backup is not None and previous.get("backup_id") != backup.name:
                        raise ValueError("health belongs to another update backup")
                    return  # Replayed publication cannot reset fails/confirmation/job_id.
            data = {"build": build, "confirmed": False, "fails": 0, "time": time.time()}
            if backup is not None:
                data["backup_id"] = backup.name
            _write_update_record(path, data)
    except Exception:
        _log.warning("update health publication deferred; existing record retained")


def _read_health_record(path):
    data = _read_update_record(path)
    if (type(data.get("build")) is not int or data["build"] <= 0
            or type(data.get("confirmed")) is not bool
            or type(data.get("fails", 0)) is not int or data.get("fails", 0) < 0
            or ("job_id" in data and (not isinstance(data["job_id"], str)
                or not re.fullmatch(r"[a-f0-9]{32}", data["job_id"])))):
        raise ValueError("invalid update health record")
    data.setdefault("fails", 0)
    return data


def confirm_health() -> bool:
    """新版本平稳运行 HEALTH_CONFIRM_DELAY 秒后调用：确认当前 build 健康。"""
    try:
        if not _get_health_path().exists():
            return True
        with _update_resource.hold():
            _confirm_health_locked()
        return True
    except Exception:
        _log.warning("update health confirmation deferred; existing record retained")
        return False


def _confirm_health_locked():
    path = _get_health_path()
    try:
        data = _read_health_record(path)
    except FileNotFoundError:
        return
    if data["confirmed"] or data["build"] != APP_VERSION:
        return
    data["confirmed"] = True
    _write_update_record(path, data)
    try:
        _cleanup_old_backups()
    except Exception:
        _log.warning("update health confirmed; backup cleanup deferred")
    _log.info("confirm_health: build %d confirmed healthy", APP_VERSION)
    _report("update_confirmed", level="info", build=str(APP_VERSION))


def confirm_health_after(delay, *, retry_timeout=360):
    """Wait for the health interval, then retry only transient lock contention."""
    import math
    from core.resource_lock import ResourceBusyError
    if any(not math.isfinite(value) or value < 0 for value in (delay, retry_timeout)):
        raise ValueError("health confirmation timing must be finite and nonnegative")
    time.sleep(delay)
    if not _get_health_path().exists():
        return True
    deadline = time.monotonic() + retry_timeout
    while True:
        try:
            with _update_resource.hold():
                _confirm_health_locked()
            return True
        except ResourceBusyError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _log.warning("update health confirmation contention deadline exceeded")
                return False
            time.sleep(min(1, remaining))
        except Exception:
            _log.warning("update health confirmation refused; existing record retained")
            return False


def _check_health() -> bool:
    """崩溃回滚：基于健康探针判断新版本是否反复启动失败。

    机制：
    - 更新替换成功 → mark_update_applied 写 {build, confirmed:False, fails:0}
    - 新版本平稳运行后 → confirm_health 置 confirmed:True
    - 每次未确认健康的启动 fails+1；达到 MAX_HEALTH_FAILS 仍未确认 → 回滚

    返回 True 表示已执行回滚（调用方应 exec 重启进入回滚后的旧版本）。
    """
    if not _get_health_path().exists():
        return False
    try:
        with _update_resource.hold():
            return _check_health_locked()
    except Exception:
        _log.warning("update health check deferred; existing record retained")
        return False


def _check_health_locked():
    path = _get_health_path()
    try:
        data = _read_health_record(path)
    except FileNotFoundError:
        return False

    if data.get("confirmed"):
        return False
    # Another runtime's record remains recovery evidence, never stale garbage.
    if data["build"] != APP_VERSION:
        return False

    fails = int(data.get("fails", 0)) + 1
    if fails >= MAX_HEALTH_FAILS:
        _log.warning("_check_health: build %d unconfirmed after %d boots — rolling back", APP_VERSION, fails)
        ok = rollback()
        if ok:
            # Even an atomic restore may have lost its completion record.
            # Retain health/backup ownership for idempotent restart recovery.
            _report("update_rolled_back", level="error", build=str(APP_VERSION), fails=str(fails))
        else:
            _report("update_rollback_failed", level="error", build=str(APP_VERSION), fails=str(fails))
        return ok
    try:
        data["fails"] = fails
        _write_update_record(path, data)
    except Exception:
        _log.warning("update health failure count could not be persisted")
    return False


def _apply_update_pending_path() -> bool:
    """处理 update_pending_path.json 兜底标记：下次启动再尝试 rename。"""
    if not _is_frozen():
        return False
    from core.config import get_data_dir
    marker = get_data_dir() / "cache" / "update_pending_path.json"
    if not marker.exists():
        return False
    with _update_resource.hold():
        return _apply_marker_locked(marker)


def _apply_marker_locked(marker) -> bool:
    committed = False
    try:
        data = _read_update_record(marker)
        from core.update_jobs import _same_path
        pending, target = _get_pending_path(), _self_path()
        if (type(data.get("schema_version")) is not int or data["schema_version"] != 2
                or any(not isinstance(data.get(key), str) or not 0 < len(data[key]) <= 8192
                       or not os.path.isabs(data[key]) for key in ("pending", "target"))
                or not _same_path(data["pending"], str(pending))
                or not _same_path(data["target"], str(target))
                or not isinstance(data.get("transaction_id"), str)
                or not re.fullmatch(r"[a-f0-9]{32}", data["transaction_id"])
                or not isinstance(data.get("backup_id"), str)
                or not re.fullmatch(r"opskit\.v[0-9]+\.[0-9]+\.[a-f0-9]{32}\.bak", data["backup_id"])
                or any(not isinstance(data.get(key), str) or not re.fullmatch(r"[a-f0-9]{64}", data[key])
                       for key in ("old_sha256", "new_sha256"))):
            raise ValueError("unbound or unowned update replacement marker")
        version = data.get("version")
        if type(version) is not int or version < APP_VERSION:
            raise ValueError("update marker needs an explicit current or newer build")
        with _update_target_resource.hold():
            from core import update_target_state
            from core.update_reconcile import _backup
            from core.config import get_data_dir
            _update_record_path(target)
            transaction = update_target_state.read(target)
            if (transaction is None or transaction["operation"] != "upgrade"
                    or transaction["transaction_id"] != data["transaction_id"]
                    or transaction["to_build"] != version
                    or transaction["from_sha256"] != data["old_sha256"]
                    or transaction["to_sha256"] != data["new_sha256"]
                    or transaction["phase"] == "aborted"):
                raise ValueError("update marker transaction changed")
            _backup(data, transaction, target)
            backup = get_data_dir() / "backups" / data["backup_id"]
            try:
                health = _read_health_record(_get_health_path())
            except FileNotFoundError:
                health = None
            if health and (health["build"] > version or (health["build"] == version
                    and health.get("backup_id") != data["backup_id"])):
                raise ValueError("update marker health belongs to another update")
            original_digest = _sha256_file(target)
            if original_digest != data["new_sha256"]:
                if (original_digest != data["old_sha256"] or transaction["phase"] != "prepared"
                        or transaction["from_build"] != APP_VERSION or version <= APP_VERSION):
                    raise ValueError("update marker target changed")
                _update_record_path(pending)
                if not _verify_pending_signature(pending, version):
                    raise ValueError("update marker signature refused")
                update_target_state.validate_upgrade(target, version)
                _atomic_copy(pending, target, expected_digest=data["new_sha256"])
            committed = True
            update_target_state.completed(target, transaction)
            mark_update_applied(version, backup=backup)
            health = _read_health_record(_get_health_path())
            if health["build"] != version or health.get("backup_id") != backup.name:
                raise ValueError("update marker health publication incomplete")
    except Exception as e:
        _log.warning("update_pending_path: retry failed: %s", e)
        # Preserve the marker and backup after a committed replacement whose
        # completion/health write failed. Startup must still restart old code.
        return committed and version != APP_VERSION
    # The target has committed. Cleanup errors must not hide that fact from
    # startup, which must restart into the replacement rather than continue.
    for path in (pending, marker):
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            _log.warning("update_pending_path: cleanup deferred for %s: %s", path.name, e)
    try:
        _save_check_cache({"latest": version})
    except Exception as e:
        _log.warning("update_pending_path: cache refresh deferred: %s", e)
    _log.info("update_pending_path: replacement committed for v%d", version)
    return version != APP_VERSION


def _reconcile_indexed_job_on_boot() -> tuple[bool, bool]:
    """Return (defer automatic writes, restart after restored bytes)."""
    if not _is_frozen():
        return False, False
    try:
        cache = _read_update_record(_get_cache_path())
    except FileNotFoundError:
        cache = {}
    except (OSError, ValueError):
        _log.warning("update reconciliation unavailable; automatic update deferred")
        return True, False
    if cache.get("apply_job") is None:
        try:
            from core.update_reconcile import _has_work
            if not _has_work(_get_cache_path()):
                return False, False
        except (OSError, ValueError):
            return True, False
    try:
        from core.update_cli import reconcile_on_boot
        result = reconcile_on_boot(timeout=30)
        if result.get("reason") == "missing_job_evidence":
            marker = _get_cache_path().parent / "update_pending_path.json"
            _update_record_path(marker)
            if marker.exists():
                # The bound marker validates its transaction/backup itself.
                # It cannot override an active or unknown helper's identity.
                return False, False
        return result.get("state") == "deferred", result.get("restart_required") is True
    except Exception:
        _log.warning("update reconciliation incomplete; automatic update deferred")
        return True, False


def check_and_apply_pending() -> bool:
    """启动时检测 pending 文件，如果存在则替换并返回 True（调用方应 exec 重启）。

    完整流程：
    1. --post-update 防循环检测
    2. .old.exe 残留清理
    3. 健康探针崩溃回滚检测
    4. update_pending_path.json 兜底标记处理
    5. pending/cache 同步（pending 被手动删除时清理 cache）
    6. 常规 pending 检测与应用
    """
    global _pending_version
    defer, restored = _reconcile_indexed_job_on_boot()
    if restored:
        _pending_version = APP_VERSION
        return True
    if defer:
        return False  # Do not launch another helper or erase recovery artifacts.
    if "--post-update" in sys.argv:
        marker = _get_cache_path().parent / "update_pending_path.json"
        _update_record_path(marker)
        if marker.exists():
            restarted = _apply_update_pending_path()
            if restarted:
                _pending_version = _load_check_cache().get("latest")
            return restarted  # Do not erase evidence after incomplete health publication.
        _get_pending_path().unlink(missing_ok=True)
        _get_pending_tmp_path().unlink(missing_ok=True)
        _log.info("post-update boot, cleared pending files")
        return False

    self_path = _self_path()

    # 清理 .old.exe 残留
    _cleanup_old_exe(self_path)

    # 崩溃回滚检测：新版本反复启动失败则回滚到旧版本并重启
    if _check_health():
        _pending_version = APP_VERSION
        return True

    # 兜底标记处理（上次 _apply_windows 所有策略失败后写入的标记）
    marker = _get_cache_path().parent / "update_pending_path.json"
    _update_record_path(marker)
    if marker.exists():
        if _apply_update_pending_path():
            cache = _load_check_cache()
            _pending_version = cache.get("latest")
            return True
        return False  # Retained/unproven marker blocks another automatic apply.

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

    # 源码 / 开发模式：不把 pending 二进制替换到 main.py 源文件上，清理后返回
    if not _is_frozen():
        _log.info("dev mode: skip applying pending v%s, removing", remote_ver)
        pending.unlink(missing_ok=True)
        _save_check_cache({"pending_version": None})
        return False

    _log.info("startup: found pending v%s, applying", remote_ver)
    ok = _do_apply(pending, remote_ver)
    if ok:
        _pending_version = remote_ver
    # A Windows helper has only been scheduled; restarting now would launch
    # another copy of the old executable before its parent exits.
    return ok and sys.platform != "win32"


# ─── 版本检测 ─────────────────────────────────────────────────────────────────

def _load_check_cache() -> dict[str, Any]:
    """读取更新缓存。schema: {pending_version, latest, etag, download_version, backoff_until}"""
    path = _get_cache_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_check_cache(data: dict[str, Any]) -> None:
    """Merge 写入 cache（线程安全 + 原子落盘），永不丢失已有字段。

    后台检测线程与主线程/退出钩子可能并发写同一缓存，用进程内锁串行化；
    先写临时文件再 os.replace，避免写入中途被打断导致 JSON 损坏。
    """
    if _query_readonly.get():
        return
    path = _get_cache_path()
    with _cache_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = _load_check_cache()
        existing.update(data)
        if "pending_version" in data and data["pending_version"] is None:
            existing["candidate_id"] = None
        try:
            tmp = path.with_suffix(path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(existing, f)
            os.replace(tmp, path)
        except Exception as e:
            _log.warning("save_check_cache failed: %s", e)


def _update_allowed() -> bool:
    """是否允许本次启动联网检查更新。

    高频场景下每次启动都检查，唯一约束是 GitHub 限流退避：
    命中 403/429 后写入 backoff_until，退避期内跳过，到期自动恢复。
    """
    backoff_until = _load_check_cache().get("backoff_until", 0)
    return not (backoff_until and time.time() < backoff_until)


def _fetch_latest(repo: str, token: str = "") -> dict[str, Any] | None:
    """
    调用 GitHub Releases API 获取最新版本信息。

    处理速率限制：收到 403 / 429 → 写入 backoff_until 退避并返回 None（静默跳过）。
    """
    url = GITHUB_API_RELEASES.format(repo=repo)
    headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    try:
        with httpx.Client(timeout=TIMEOUT_UPDATE_CHECK) as client:
            if _query_readonly.get():
                return _readonly_json(client, url, headers=headers)
            resp = client.get(url, headers=headers)

        remaining = int(resp.headers.get("X-RateLimit-Remaining", 99))
        if resp.status_code in (403, 429):
            _save_check_cache({"backoff_until": time.time() + UPDATE_RATELIMIT_BACKOFF})
            _log.info("_fetch_latest: rate limited (%s), backing off %ss", resp.status_code, UPDATE_RATELIMIT_BACKOFF)
            _report("fetch_latest_rate_limited", level="info",
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
        if _query_readonly.get():
            raise RuntimeError("update release query failed") from None
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

@contextmanager
def _https_update_stream(url, *, client=None, timeout=None, headers=None):
    """Check every redirect before requesting it; never downgrade release TLS."""
    from urllib.parse import urlsplit, urljoin
    for _ in range(6):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or len(url) > 8192):
            raise ValueError("invalid update artifact URL")
        stream = client.stream if client is not None else httpx.stream
        options = {"follow_redirects": False, "headers": headers or {"Accept-Encoding": "identity"}}
        if timeout is not None:
            options["timeout"] = timeout
        with stream("GET", url, **options) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location or len(location) > 8192:
                    raise ValueError("invalid update redirect")
                url = urljoin(url, location)
                continue
            yield response
            return
    raise ValueError("too many update redirects")


def _small_update_body(response):
    response.raise_for_status()
    if response.headers.get("Content-Encoding", "identity").strip().lower() != "identity":
        raise ValueError("compressed update signature/checksum refused")
    body = bytearray()
    for chunk in response.iter_bytes(4096):
        if len(body) + len(chunk) > 4096:
            raise ValueError("update signature/checksum exceeds limit")
        body.extend(chunk)
    return bytes(body)

def _signing_key(cfg: dict) -> str:
    from core.constants import UPDATE_SIGNING_PUBLIC_KEY
    return cfg.get("signing_public_key", "") or UPDATE_SIGNING_PUBLIC_KEY


@_update_mutation
def _fetch_release_signature(pending: Path, asset_url: str, build: int, cfg: dict) -> bool:
    from urllib.parse import urlparse
    from core.release_signature import verify
    from core.secrets import atomic_write
    key = _signing_key(cfg)
    if not key or urlparse(asset_url).scheme != "https" or build <= APP_VERSION:
        return False
    try:
        with httpx.Client(timeout=10) as client:
            with _https_update_stream(asset_url + ".sig.json", client=client) as response:
                envelope = json.loads(_small_update_body(response))
        if not verify(envelope, key, build=build, filename=_asset_filename(), digest=_sha256_file(pending)):
            return False
        atomic_write(pending.with_name(pending.name + ".sig.json"), json.dumps(envelope))
        return True
    except Exception:
        return False


def _verify_pending_signature(pending: Path, build: int) -> bool:
    from core.config import load_config
    from core.release_signature import verify
    if build <= APP_VERSION:
        return False
    try:
        sidecar = pending.with_name(pending.name + ".sig.json")
        if sidecar.stat().st_size > 4096:
            return False
        envelope = json.loads(sidecar.read_text(encoding="utf-8"))
        return verify(envelope, _signing_key(load_config().get("update", {})),
                      build=build, filename=_asset_filename(), digest=_sha256_file(pending))
    except (OSError, ValueError, TypeError):
        return False


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
            with _https_update_stream(sha256_url, client=client) as response:
                text = _small_update_body(response).decode("ascii").strip().split()[0]
                if re.fullmatch(r"[a-fA-F0-9]{64}", text):
                    return text.lower()
    except Exception:
        pass
    return ""


def _report(msg: str, exc: Exception | None = None, level: str = "warning", **ctx) -> None:
    """统一上报入口：附加更新通用上下文后转发给 telemetry"""
    if _query_readonly.get():
        return
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


@_update_mutation
def _download_update(asset_url: str, sha256_expected: str, remote_ver: int = 0) -> Path | None:
    """
    下载新版本到临时文件，校验 SHA256 后原子 rename 为 pending。

    优化项：
    - 断点续传：检测 .tmp 已有字节，发送 Range: bytes={offset}-
    - stall 超时：每块读取使用 TIMEOUT_DOWNLOAD_READ，防止连接挂死
    - 指数退避：第 n 次失败后等待 2^(n-1) * DOWNLOAD_RETRY_BASE_DELAY 秒
    - ETag 缓存：成功下载后缓存 ETag，下次发送 If-None-Match 协商
    - 磁盘检查：下载前检测剩余空间是否充足
    - 私有暂存目录不可写时明确失败，不回退到共享系统临时路径
    - SHA256 双源：Release Body 解析失败时尝试 {asset_url}.sha256 文件
    """
    import tempfile
    from core.mirror import get_sources
    from core.constants import MAX_RETRY_DOWNLOAD, DOWNLOAD_CHUNK_SIZE, TIMEOUT_HTTP

    tmp_path = _get_pending_tmp_path()
    pending_path = _get_pending_path()

    def _ensure_tmp_dir() -> Path:
        """Use a private writable staging directory, never a shared filename."""
        try:
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            from core.secrets import restrict_permissions
            restrict_permissions(tmp_path.parent)
            descriptor, probe = tempfile.mkstemp(prefix=".update-probe-", dir=tmp_path.parent)
            os.close(descriptor)
            Path(probe).unlink()
            for artifact in (tmp_path, pending_path):
                if artifact.is_symlink() or (hasattr(artifact, "is_junction") and artifact.is_junction()):
                    raise RuntimeError("update artifact path must not be a link")
                if artifact.exists():
                    import stat
                    info = artifact.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise RuntimeError("update artifact must be a regular file without hard links")
            return tmp_path
        except OSError:
            raise RuntimeError("update staging directory is not writable") from None

    actual_tmp = _ensure_tmp_dir()

    # 版本感知断点续传：若 .tmp 残包属于旧目标版本，丢弃重下，
    # 避免把旧版本（如 v2）的半包接到新版本（如 v4）的下载上导致损坏
    if remote_ver:
        _dl_cache = _load_check_cache()
        if _dl_cache.get("download_version") not in (None, remote_ver):
            _log.info("download: target changed to v%s, discarding stale partial (was v%s)",
                      remote_ver, _dl_cache.get("download_version"))
            actual_tmp.unlink(missing_ok=True)
            _save_check_cache({"download_version": remote_ver, "etag": ""})
        else:
            _save_check_cache({"download_version": remote_ver})

    mirrors = get_sources("github_releases")
    urls: list[str] = []
    for m in mirrors:
        if "github.com" in m:
            urls.append(asset_url)
        else:
            proxied = f"{m.rstrip('/')}/{asset_url.lstrip('/')}"
            urls.append(proxied)
    # GitHub 直连始终作为兜底候选：镜像排名坍缩到单一坏源时（例如被封禁、
    # 只对 HEAD 探针秒回却对真实下载吐错误页的代理），仍保留可用回退路径。
    if asset_url not in urls:
        urls.append(asset_url)
    # 去重保序，避免同一坏源占满整个重试预算、挤掉其它可用源
    seen_urls: set[str] = set()
    urls = [u for u in urls if not (u in seen_urls or seen_urls.add(u))]
    if not urls:
        urls = [asset_url]

    # SHA256 双源：若 Release Body 无校验值，尝试下载独立 .sha256 文件
    if not sha256_expected:
        _log.info("download: no SHA256 in release body, trying %s.sha256", asset_url)
        sha256_expected = _get_sha256_from_url(asset_url + ".sha256")
        if sha256_expected:
            _log.info("download: got SHA256 from .sha256 file: %s", sha256_expected)

    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256_expected):
        _log.error("download: valid SHA256 required; update refused")
        _report("download_no_sha256", level="error")
        return None

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

    last_url: str | None = None
    for attempt in range(MAX_RETRY_DOWNLOAD):
        url = urls[attempt % len(urls)]
        _log.info("download attempt %d/%d: %s", attempt + 1, MAX_RETRY_DOWNLOAD, url)

        # 切换到不同候选源时丢弃残包：跨源 Range 续传会把不同来源的字节拼在
        # 一起，导致 SHA256 必然失败、白白耗费重试预算。
        if last_url is not None and url != last_url:
            actual_tmp.unlink(missing_ok=True)
        last_url = url

        # 指数退避（第 0 次不等待）
        if attempt > 0:
            delay = DOWNLOAD_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            _log.info("download: backoff %ds before attempt %d", delay, attempt + 1)
            time.sleep(delay)

        try:
            # 断点续传：检测已下载字节
            offset = actual_tmp.stat().st_size if actual_tmp.exists() else 0
            if offset > MAX_UPDATE_BYTES:
                raise _UpdateLimitError("update partial exceeds limit")
            headers: dict[str, str] = {"Accept-Encoding": "identity"}
            if offset > 0:
                headers["Range"] = f"bytes={offset}-"
                _log.info("download: resuming from byte %d", offset)
            if cached_etag and attempt == 0 and offset == 0:
                headers["If-None-Match"] = cached_etag

            with _https_update_stream(url, timeout=stream_timeout, headers=headers) as resp:
                if resp.status_code == 304:
                    _log.info("download: ETag matched (304), pending still valid")
                    if pending_path.exists() and pending_path.stat().st_size <= MAX_UPDATE_BYTES and _sha256_file(pending_path) == sha256_expected.lower():
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
                if resp.headers.get("Content-Encoding", "identity").strip().lower() != "identity":
                    raise _UpdateLimitError("compressed update artifact refused")
                declared = resp.headers.get("Content-Length")
                if declared is not None and (not declared.isdecimal() or int(declared) + offset > MAX_UPDATE_BYTES):
                    raise _UpdateLimitError("update content length exceeds limit or is invalid")
                written = offset
                with actual_tmp.open(mode) as f:
                    for chunk in resp.iter_bytes(DOWNLOAD_CHUNK_SIZE):
                        if written + len(chunk) > MAX_UPDATE_BYTES:
                            raise _UpdateLimitError("update artifact exceeds limit")
                        f.write(chunk)
                        written += len(chunk)

            actual_hash = _sha256_file(actual_tmp)
            if actual_hash != sha256_expected.lower():
                _log.warning("SHA256 mismatch: expected %s got %s", sha256_expected, actual_hash)
                _report("download_sha256_mismatch", level="error",
                        expected_sha256=sha256_expected, actual_sha256=actual_hash,
                        mirror_url=url, asset_url=asset_url, attempt=str(attempt + 1),
                        file_size=str(actual_tmp.stat().st_size) if actual_tmp.exists() else "0")
                actual_tmp.unlink(missing_ok=True)
                continue

            actual_tmp.replace(pending_path)
            if new_etag:
                _save_check_cache({"etag": new_etag})
            _log.info("download complete, pending ready at %s", pending_path)
            return pending_path

        except _UpdateLimitError:
            actual_tmp.unlink(missing_ok=True)
            _log.warning("update artifact size/encoding refused")
            return None
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
        try:
            # 源码 / 开发模式：不下载 pending，避免后续覆盖 main.py 源文件
            if not _is_frozen():
                return
            update_cfg = cfg.get("update", {})
            if not update_cfg.get("enabled", True):
                return

            # 高频启动场景：每次启动都检测更新，仅受限流退避（backoff_until）约束
            if not _update_allowed():
                # 检查是否有已下载但未应用的版本
                pending = _get_pending_path()
                if pending.exists():
                    cache = _load_check_cache()
                    _pending_version = cache.get("pending_version")
                return

            from core.update_download import download
            result = download(timeout=300, update_cfg=update_cfg)
            if result.get("available"):
                _pending_version = result["candidate_version"]
        except Exception as e:
            _log.warning("update worker failed: %s", e)
            _report("update_worker_exception", exc=e, level="warning")

    t = threading.Thread(target=_worker, daemon=True, name="opskit-updater")
    t.start()


def _resolve_update_target(update_cfg: dict) -> tuple[int, str, str] | None:
    """决定更新目标：(目标 build, 资产下载 URL, SHA256)。

    优先读控制面 manifest（CDN/GitHub raw，含 kill_switch/min_build/rollout）；
    manifest 不可用时降级到 GitHub Releases API 直连。返回 None 表示无需更新。
    """
    repo: str = update_cfg.get("repo", "ougato/opskit-cli")
    token: str = update_cfg.get("github_token", "")
    filename = _asset_filename()

    # 控制面优先
    manifest = fetch_bootstrap()
    if manifest and isinstance(manifest, dict) and "latest_build" in manifest:
        build = _evaluate_manifest(manifest)
        if build is None:
            return None
        asset_url = f"{GITHUB_BASE}/{repo}/releases/download/v{build}/{filename}"
        return build, asset_url, ""  # SHA256 走 {asset_url}.sha256 旁车

    # 降级：GitHub Releases API
    from core.version import parse_version, is_newer
    data = _fetch_latest(repo, token)
    if data is None:
        return None
    tag = data.get("tag_name", "")
    if _query_readonly.get() and (not isinstance(tag, str) or len(tag) > 128
                                 or not re.fullmatch(r"v?[1-9][0-9]*(?:\.0)?", tag.strip())):
        raise RuntimeError("invalid update release version")
    remote_ver = parse_version(tag)
    if not is_newer(remote_ver):
        _save_check_cache({"latest": remote_ver})
        return None

    body = data.get("body", "")
    sha256 = _parse_sha256(body, filename)
    assets = data.get("assets", [])
    asset_url = ""
    for a in assets:
        if a.get("name") == filename:
            asset_url = a.get("browser_download_url", "")
            break
    if not asset_url:
        if _query_readonly.get():
            raise RuntimeError("update release has no matching platform artifact")
        _report("update_no_matching_asset", level="warning",
                filename=filename, tag=tag,
                available_assets=",".join(a.get("name", "") for a in assets))
        return None
    if _query_readonly.get():
        from urllib.parse import urlsplit
        parsed = urlsplit(asset_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise RuntimeError("invalid update artifact URL")
    return remote_ver, asset_url, sha256


def query_update_target(update_cfg: dict):
    """Explicit check using existing source/rollout policy with no local writes."""
    repo = update_cfg.get("repo", "ougato/opskit-cli")
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid update repository")
    token = _query_readonly.set(True)
    try:
        return _resolve_update_target(update_cfg)
    finally:
        _query_readonly.reset(token)


def apply_verified_pending():
    """Explicit application; no guessed build or source-mode replacement."""
    if not _is_frozen():
        raise ValueError("binary updates require a packaged executable")
    with _update_resource.hold():
        cache = _load_check_cache()
        if not isinstance(cache, dict):
            raise RuntimeError("invalid update cache")
        version = cache.get("pending_version")
        if type(version) is not int or version <= APP_VERSION:
            raise RuntimeError("pending update version is missing or outdated")
        pending = _get_pending_path()
        if pending.is_symlink() or not pending.is_file() or not _verify_binary(pending):
            raise RuntimeError("pending update artifact is invalid")
        if not _do_apply(pending, version):
            raise RuntimeError("pending update verification or application failed")
        return {"version": version, "state": "scheduled" if sys.platform == "win32" else "applied",
                "health_confirmed": False}


# ─── 热替换（退出时应用）─────────────────────────────────────────────────────

def apply_pending_update() -> None:
    """退出时应用已下载的更新（与 check_and_apply_pending 共享 _do_apply 逻辑）"""
    # 源码 / 开发模式：禁用自更新替换，避免覆盖 main.py 源文件
    if not _is_frozen():
        return
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


@_update_mutation
def _do_apply(pending: Path, remote_ver: int) -> bool:
    """
    统一替换逻辑（被 check_and_apply_pending 和 apply_pending_update 共用）。

    流程：
    1. 备份当前版本到时间戳路径，并校验备份完整性
    2. Unix: 原子替换，跨文件系统使用已校验的原子暂存复制
       Windows: PowerShell 脚本延迟替换（进程退出后执行）
    3. 清理旧备份（保留最近 3 个）
    4. 更新 cache（merge 写，保留 pending_version 等字段）
    返回 True 表示替换已成功（Unix 同步）或已调度（Windows 异步）
    """
    if not _is_frozen():
        return False
    if not _verify_pending_signature(pending, remote_ver):
        _log.warning("unsigned, changed or outdated pending release refused")
        return False
    return _do_apply_verified(pending, remote_ver)


@_update_target_mutation
def _do_apply_verified(pending: Path, remote_ver: int) -> bool:
    if not _verify_pending_signature(pending, remote_ver):
        return False
    self_path = _self_path()
    backup = _get_backup_path()

    try:
        from core import update_target_state
        original_digest = _sha256_file(self_path)
        installed_build = update_target_state.current_build(self_path, original_digest, APP_VERSION)
        if installed_build != APP_VERSION or remote_ver <= installed_build:
            raise ValueError("executable version changed; restart before updating")
        update_target_state.validate_upgrade(self_path, remote_ver)
        from core.secrets import restrict_permissions
        _update_record_path(backup)
        backup.parent.mkdir(parents=True, exist_ok=True)
        restrict_permissions(backup.parent)
        pending_digest = _sha256_file(pending)
        shutil.copy2(self_path, backup)
        if _sha256_file(backup) != original_digest or _sha256_file(self_path) != original_digest:
            backup.unlink(missing_ok=True)
            _log.error("_do_apply: backup integrity check failed, aborting")
            _report("apply_backup_integrity_fail", level="error",
                    self_path=str(self_path), backup=str(backup),
                    remote_ver=str(remote_ver))
            return False
        _write_update_record(backup.with_name(backup.name + ".json"), {
            "schema_version": 1, "backup_id": backup.name,
            "target": str(self_path.resolve()), "from_build": APP_VERSION,
            "to_build": remote_ver, "sha256": original_digest,
            "replacement_sha256": pending_digest,
        })
        transaction = update_target_state.prepare(self_path, operation="upgrade", from_build=installed_build,
            to_build=remote_ver, from_digest=original_digest, to_digest=pending_digest)
    except Exception as e:
        _log.error("_do_apply: backup failed: %s", e)
        _report("apply_backup_failed", exc=e, level="error",
                self_path=str(self_path), backup=str(backup),
                remote_ver=str(remote_ver))
        return False

    try:
        if sys.platform == "win32":
            _apply_windows(pending, self_path, remote_ver, {
                "backup_id": backup.name, "sha256": original_digest,
                "replacement_sha256": pending_digest,
                "transaction": transaction,
            })
        else:
            _apply_unix(pending, self_path, remote_ver)
            update_target_state.completed(self_path, transaction)
    except BaseException as e:
        # Unix may already have consumed pending before a permission/cache
        # error or cancellation. Restore only our own replacement, using the
        # verified backup; never truncate the running executable with copy2.
        if sys.platform != "win32":
            try:
                current_digest = _sha256_file(self_path)
                if current_digest == pending_digest:
                    try:
                        if not pending.exists():
                            _atomic_copy(self_path, pending)
                    except Exception as retry_error:
                        _log.error("_do_apply: retry artifact preservation failed: %s", retry_error)
                    if _sha256_file(backup) != original_digest:
                        raise RuntimeError("update recovery backup changed")
                    _atomic_copy(backup, self_path)
                elif current_digest != original_digest:
                    raise RuntimeError("update target changed outside this operation")
            except Exception as recovery_error:
                _log.critical("_do_apply: recovery incomplete; backup retained at %s: %s",
                              backup, recovery_error)
        _log.error("_do_apply: apply failed: %s", e)
        _report("apply_failed", exc=e, level="error",
                self_path=str(self_path), pending=str(pending),
                remote_ver=str(remote_ver), platform=sys.platform)
        if not isinstance(e, Exception):
            raise
        return False

    # Maintenance after a successful commit must not report that replacement
    # failed. Windows is still only scheduled here, not health-confirmed.
    if sys.platform != "win32":
        try:
            _cleanup_old_backups()
        except Exception as e:
            _log.warning("_do_apply: backup cleanup deferred: %s", e)
    if sys.platform != "win32":
        mark_update_applied(remote_ver, backup=backup)
    _report("update_applied", level="info",
            from_version=str(APP_VERSION), to_version=str(remote_ver))
    return True


def _atomic_copy(source: Path, target: Path, *, expected_digest: str | None = None):
    """Copy, flush and verify beside the target, then atomically replace it."""
    import tempfile
    descriptor, temporary = tempfile.mkstemp(prefix=".opskit-replace-", dir=target.parent)
    staged = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        shutil.copystat(source, staged)
        staged_digest = _sha256_file(staged)
        if staged_digest != _sha256_file(source) or (expected_digest is not None and staged_digest != expected_digest):
            raise RuntimeError("replacement copy integrity check failed")
        if os.name == "nt" and target.is_file():
            from core.windows_permissions import copy_dacl
            copy_dacl(target, staged)
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)


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
        if e.errno != errno.EXDEV:
            raise
        _log.info("_apply_unix: cross-device update, using atomic staged copy")
        _atomic_copy(pending, self_path)
        pending.unlink(missing_ok=True)

    os.chmod(str(self_path), original_mode)
    if original_uid >= 0:
        try:
            os.chown(str(self_path), original_uid, original_gid)
        except PermissionError:
            pass

    _save_check_cache({"latest": new_version})


@_update_mutation
def _apply_windows(pending: Path, self_path: Path, new_version: int, recovery=None) -> None:
    """Schedule a digest-bound private job; never register a mutable reboot move."""
    from core.update_windows import schedule
    try:
        schedule(pending, self_path, new_version, recovery)
        _log.info("_apply_windows: private replacement job scheduled")
        return
    except Exception as e:
        _log.error("_apply_windows: scheduling failed")
        # Startup can retry through the same signature/identity gates. No
        # MoveFileEx reboot fallback can bypass verification using shared pending.
        marker = _get_cache_path().parent / "update_pending_path.json"
        record = {"pending": str(pending), "target": str(self_path), "version": new_version}
        if recovery and recovery.get("backup_id"):
            record["backup_id"] = recovery["backup_id"]
        transaction = (recovery or {}).get("transaction")
        if transaction:
            record.update({"schema_version": 2, "transaction_id": transaction["transaction_id"],
                "old_sha256": transaction["from_sha256"], "new_sha256": transaction["to_sha256"]})
        else:
            record["schema_version"] = 1  # Retained legacy evidence, never auto-executed.
        _write_update_record(marker, record)
        raise RuntimeError("Windows replacement job could not be scheduled") from e


def _cleanup_old_backups() -> None:
    """Retain three owned backups plus all known recovery references."""
    with _update_resource.hold():
        _cleanup_old_backups_locked()


def _cleanup_old_backups_locked() -> None:
    from core.config import get_data_dir
    bak_dir = get_data_dir() / "backups"
    if not bak_dir.exists():
        return
    _update_record_path(bak_dir)
    from core.update_reconcile import backup_pins
    pinned = backup_pins(_get_cache_path())
    if pinned is None:
        return  # Legacy/unknown jobs need explicit migration before deletion.
    from core.update_storage import entries, info as storage_info, unlink_owned
    baks = []
    for candidate in entries(bak_dir, limit=256):
        if not re.fullmatch(r"opskit\.v[0-9]+\.[0-9]+\.[a-f0-9]{32}\.bak", candidate.name):
            continue  # Legacy or unrelated files need an explicit migration.
        try:
            _update_record_path(candidate)
            info = storage_info(candidate)
            if not stat.S_ISREG(info.st_mode):
                continue
            sidecar = candidate.with_name(candidate.name + ".json")
            sidecar_info = storage_info(sidecar)
            record = _read_update_record(sidecar)
            if record.get("backup_id") != candidate.name or record.get("target") != str(_self_path().resolve()):
                continue
            baks.append((candidate, info, sidecar, sidecar_info))
        except (OSError, ValueError):
            continue
    baks.sort(key=lambda row: (row[1].st_mtime_ns, row[0].name), reverse=True)
    for old, info, sidecar, sidecar_info in baks[3:]:
        if old.name not in pinned:
            unlink_owned(old, info)
            unlink_owned(sidecar, sidecar_info)


# ─── 回滚 ─────────────────────────────────────────────────────────────────────

def rollback() -> bool:
    """
    回滚到健康记录指定且摘要未变更的本地备份，不猜最新文件。

    返回 True 表示回滚成功，False 表示失败。
    """
    if not _is_frozen():
        return False
    return _rollback_locked()


@_update_mutation
@_update_target_mutation
def _rollback_locked() -> bool:
    committed = False
    try:
        from core.config import get_data_dir
        health = _read_health_record(_get_health_path())
        backup_id = health.get("backup_id")
        if not isinstance(backup_id, str) or not re.fullmatch(r"opskit\.v[0-9]+\.[0-9]+\.[a-f0-9]{32}\.bak", backup_id):
            raise ValueError("rollback needs a recorded backup identity")
        backup = get_data_dir() / "backups" / backup_id
        _update_record_path(backup)
        info = backup.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_UPDATE_BYTES:
            raise ValueError("invalid rollback backup file")
        record = _read_update_record(backup.with_name(backup.name + ".json"))
        self_path = _self_path()
        _update_record_path(self_path)
        if (type(record.get("schema_version")) is not int or record["schema_version"] != 1
                or record.get("backup_id") != backup_id
                or record.get("target") != str(self_path.resolve())
                or type(record.get("to_build")) is not int or record["to_build"] != health.get("build")
                or type(record.get("from_build")) is not int or not 0 < record["from_build"] < record["to_build"]
                or not isinstance(record.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])
                or not isinstance(record.get("replacement_sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", record["replacement_sha256"])
                or _sha256_file(backup) != record["sha256"]):
            raise ValueError("rollback backup identity or integrity mismatch")
        current_digest = _sha256_file(self_path)
        from core import update_target_state
        if current_digest == record["sha256"]:
            latest = update_target_state.read(self_path)
            if (latest is None or latest["operation"] != "rollback"
                    or latest["from_build"] != record["to_build"] or latest["to_build"] != record["from_build"]
                    or latest["from_sha256"] != record["replacement_sha256"] or latest["to_sha256"] != record["sha256"]):
                raise ValueError("rollback restore has no matching transaction")
            update_target_state.completed(self_path, latest)
            return True  # Also repair a completion record lost after atomic restore.
        if current_digest != record.get("replacement_sha256"):
            raise ValueError("rollback target changed outside this update")
        installed_build = update_target_state.current_build(self_path, current_digest, record["to_build"])
        if installed_build != record["to_build"]:
            raise ValueError("rollback version changed outside this update")
        latest = update_target_state.read(self_path)
        if latest is not None and latest["operation"] == "rollback":
            if (latest["phase"] != "prepared" or latest["from_build"] != installed_build
                    or latest["to_build"] != record["from_build"]
                    or latest["from_sha256"] != current_digest or latest["to_sha256"] != record["sha256"]):
                raise ValueError("rollback retry transaction changed")
            transaction = latest
        else:
            transaction = update_target_state.prepare(self_path, operation="rollback", from_build=installed_build,
                to_build=record["from_build"], from_digest=current_digest, to_digest=record["sha256"])
        _atomic_copy(backup, self_path, expected_digest=record["sha256"])
        committed = True
        update_target_state.completed(self_path, transaction)
        return True
    except Exception as e:
        _log.error("rollback refused or failed; backup and health retained: %s", e)
        return committed


def has_pending_update() -> bool:
    """是否有待应用的更新"""
    return _pending_version is not None


def pending_version() -> int | None:
    """返回待应用版本号，无则 None"""
    return _pending_version
