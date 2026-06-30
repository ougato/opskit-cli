"""智能源管理 — IP 归属地检测、并发测速、无感切换、断点续传"""
from __future__ import annotations

import json
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import yaml

from core.constants import (
    DIR_MIRRORS,
    DOWNLOAD_CACHE_DIR,
    DOWNLOAD_CHUNK_SIZE,
    DOWNLOAD_MIN_SPEED_KBPS,
    DOWNLOAD_SLOW_WINDOW,
    FILE_MIRROR_CACHE,
    GITHUB_API_RELEASES,
    MAX_RETRY_DOWNLOAD,
    MIRROR_CACHE_TTL,
    MIRROR_PROBE_COUNT,
    MIRROR_RACE_COUNT,
    TIMEOUT_DOWNLOAD_STALL,
    TIMEOUT_HTTP,
    TIMEOUT_MIRROR_PROBE,
    TIMEOUT_SOURCE_MAX,
    TIMEOUT_STALL_SECS,
)


# ─── 源加载 ───────────────────────────────────────────────────────────────────

def _get_sources_path() -> Path:
    from core.config import get_resource_dir
    return get_resource_dir(DIR_MIRRORS) / "sources.yaml"


def _load_sources() -> dict[str, Any]:
    path = _get_sources_path()
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_cache_path() -> Path:
    from core.config import get_data_dir
    return get_data_dir() / "cache" / FILE_MIRROR_CACHE


# ─── IP 归属地检测 ────────────────────────────────────────────────────────────

def detect_region() -> str:
    """
    检测当前出口 IP 的地区。

    依次尝试多个 IP 信息 API，超时或失败则降级，最终兜底返回 'global'。

    返回：'cn' 或 'global'
    """
    from core.constants import REGION_DETECT_APIS
    for url, extractor in REGION_DETECT_APIS:
        try:
            with httpx.Client(timeout=TIMEOUT_MIRROR_PROBE) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    country = extractor(resp.json()).upper()
                    if country == "CN":
                        return "cn"
                    elif country:
                        return "global"
        except Exception:
            continue
    return "global"


# ─── 并发测速 ─────────────────────────────────────────────────────────────────

def _probe_url(url: str, probe_path: str) -> tuple[str, float]:
    """发送 HEAD 请求，返回 (url, latency_ms)，失败返回 inf"""
    target = url.rstrip("/") + probe_path
    try:
        t0 = time.monotonic()
        with httpx.Client(timeout=TIMEOUT_MIRROR_PROBE) as client:
            resp = client.head(target)
        latency = (time.monotonic() - t0) * 1000
        if resp.status_code < 500:
            return url, latency
    except Exception:
        pass
    return url, float("inf")


def _probe_concurrent(items: list, worker, total_timeout: float | None = None) -> list:
    """在 daemon 线程上并发执行 worker(item)，收集已完成结果。

    不用 ThreadPoolExecutor：其在解释器退出时会无条件 join 所有工作线程，
    一旦某次 HEAD 探针卡在慢网络上，atexit 就会拖住进程退出（退出 hang）。
    改用 daemon 线程后进程可立即退出；total_timeout 再给总探测预算上限，
    超时未返回的探针被直接放弃，保证测速绝不阻塞退出。
    """
    results: list = []
    lock = threading.Lock()

    def _run(it) -> None:
        r = worker(it)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=_run, args=(it,), daemon=True) for it in items]
    for th in threads:
        th.start()
    deadline = None if total_timeout is None else time.monotonic() + total_timeout
    for th in threads:
        timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
        th.join(timeout=timeout)
    with lock:
        return list(results)


def rank_sources(category: str, region: str, sources: dict[str, Any]) -> list[str]:
    """
    对指定分类的所有源并发测速，返回按延迟排序的 URL 列表。

    最多返回 MIRROR_PROBE_COUNT 个可达源。
    """
    cat = sources.get(category, {})
    probe_path = sources.get("probe", {}).get(category, {}).get("path", "/")

    urls: list[str] = []
    # 优先当前地区的源，然后追加其他地区
    for r in (region, "global" if region == "cn" else "cn"):
        for entry in cat.get(r, []):
            u = entry.get("url", "")
            if u and u not in urls:
                urls.append(u)

    results: list[tuple[str, float]] = _probe_concurrent(
        urls,
        lambda u: _probe_url(u, probe_path),
        total_timeout=TIMEOUT_MIRROR_PROBE + 1,
    )

    results.sort(key=lambda x: x[1])
    ranked = [u for u, _ in results if _ < float("inf")]
    return ranked[:MIRROR_PROBE_COUNT] if ranked else urls[:MIRROR_PROBE_COUNT]


# ─── 缓存 ─────────────────────────────────────────────────────────────────────

def _load_cache() -> dict[str, Any]:
    path = _get_cache_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _save_cache(data: dict[str, Any]) -> None:
    path = _get_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


# ─── 全局状态 ─────────────────────────────────────────────────────────────────

_cache: dict[str, Any] = {}
_sources: dict[str, Any] = {}
_region: str = "global"
_lock = threading.Lock()
_initialized = False


def init(region: str | None = None) -> None:
    """
    初始化：

    1. 加载 sources.yaml
    2. 检查缓存是否有效（TTL 24h）
    3. 缓存无效 → 检测 IP 地区 → 并发测速 → 写入缓存
    4. 缓存有效 → 直接加载
    """
    global _cache, _sources, _region, _initialized

    with _lock:
        if _initialized:
            return

        _sources = _load_sources()

        if region is None:
            from core.config import load_config
            cfg = load_config()
            region = cfg.get("mirror", {}).get("region", "auto")

        cached = _load_cache()
        cache_age = time.time() - cached.get("timestamp", 0)

        if cache_age < MIRROR_CACHE_TTL and cached.get("ranked"):
            _region = cached.get("region", "global")
            _cache = cached
        else:
            _region = detect_region() if region == "auto" else region
            ranked: dict[str, list[str]] = {}
            for cat in ("pip", "docker", "apt", "yum", "brew", "github_releases"):
                ranked[cat] = rank_sources(cat, _region, _sources)
            # version_api 是嵌套结构，按子分类分别测速
            version_api = _sources.get("version_api", {})
            for sub_key, sub_regions in version_api.items():
                flat_cat = f"version_api.{sub_key}"
                urls: list[str] = []
                for r in (_region, "global" if _region == "cn" else "cn"):
                    for entry in sub_regions.get(r, []):
                        u = entry.get("url", "")
                        if u and u not in urls:
                            urls.append(u)
                ranked[flat_cat] = urls  # version_api 不测速，按区域优先级排序即可
            _cache = {
                "region": _region,
                "timestamp": time.time(),
                "ranked": ranked,
            }
            _save_cache(_cache)

        _initialized = True

        # 通知版本缓存层源管理已就绪
        try:
            from core.version_cache import notify_mirrors_ready
            notify_mirrors_ready()
        except Exception:
            pass


def get_sources(category: str) -> list[str]:
    """
    获取指定分类的已排序源列表。

    未初始化时自动调用 init()。
    """
    if not _initialized:
        init()
    ranked = _cache.get("ranked", {})
    if category in ranked and ranked[category]:
        return ranked[category]
    # 回退到原始列表
    cat = _sources.get(category, {})
    urls: list[str] = []
    for r in (_region, "global" if _region == "cn" else "cn"):
        for entry in cat.get(r, []):
            u = entry.get("url", "")
            if u and u not in urls:
                urls.append(u)
    return urls


# ─── 下载缓存工具 ────────────────────────────────────────────────────────────

def get_download_cache_path(name: str, version: str, filename: str) -> Path:
    """
    返回下载缓存路径：{系统临时目录}/opskit/{name}/v{version}/{filename}
    name:     软件标识，如 'xray'
    version:  版本号，如 '25.3.6'
    filename: 文件名，如 'Xray-linux-64.zip'
    """
    base = Path(tempfile.gettempdir()) / DOWNLOAD_CACHE_DIR / name / f"v{version}"
    base.mkdir(parents=True, exist_ok=True)
    return base / filename


def _is_zip_valid(path: Path) -> bool:
    """校验 ZIP 文件完整性：文件存在 + is_zipfile + testzip 无损坏块"""
    try:
        if not path.exists() or path.stat().st_size < 22:
            return False
        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path, "r") as zf:
            bad = zf.testzip()
            return bad is None
    except Exception:
        return False


def _is_cached_valid(path: Path, suffix: str | None = None) -> bool:
    """
    通用缓存完整性校验：
    - .zip 文件：用 _is_zip_valid
    - 其他文件：仅检查 size > 0
    """
    if not path.exists():
        return False
    ext = (suffix or path.suffix).lower()
    if ext == ".zip":
        return _is_zip_valid(path)
    return path.stat().st_size > 0


# ─── 下载（两阶段：HEAD 探针 + 赛马下载 + 低速切换 + fallback 兜底） ──────────

def _probe_reachable(full_url: str) -> tuple[str, float]:
    """
    HEAD 探针（跟随重定向），通过以下任一条件判断文件真实可达：
    1. Content-Length >= 1MB（直接给出大小，说明是真实文件）
    2. Content-Type 为二进制类型（application/* 或 octet-stream）
    3. 重定向到 objects.githubusercontent.com（github 真实下载域名）
    封禁页面（text/plain 且 Content-Length 极小）会被过滤。
    返回 (url, latency_ms)，不可达/被封禁返回 inf。
    """
    _BIN_TYPES = ("application/", "octet-stream", "binary/")
    _GITHUB_CDN = ("objects.githubusercontent.com", "githubusercontent.com")
    try:
        t0 = time.monotonic()
        with httpx.Client(timeout=TIMEOUT_MIRROR_PROBE, follow_redirects=True) as client:
            resp = client.head(full_url)
        latency = (time.monotonic() - t0) * 1000
        if resp.status_code >= 400:
            return full_url, float("inf")
        ct = resp.headers.get("content-type", "").lower()
        cl_str = resp.headers.get("content-length", "0")
        try:
            cl = int(cl_str)
        except ValueError:
            cl = 0
        final_url = str(resp.url)
        if any(cdn in final_url for cdn in _GITHUB_CDN):
            return full_url, latency
        if cl >= 1_000_000:
            return full_url, latency
        if any(t in ct for t in _BIN_TYPES):
            return full_url, latency
        return full_url, float("inf")
    except Exception:
        pass
    return full_url, float("inf")


def _download_single(
    full_url: str,
    dest: Path,
    done_event: threading.Event,
    result_holder: list,
    progress_callback=None,
) -> None:
    """
    单源下载线程：
    - done_event 置位时立即退出（其他源已成功）
    - 连接建立超时 TIMEOUT_HTTP
    - 低速检测：近 DOWNLOAD_SLOW_WINDOW 秒内速度 < DOWNLOAD_MIN_SPEED_KBPS KB/s 则放弃
    - 无进度停滞超时 TIMEOUT_SOURCE_MAX（连续无任何 chunk 超过此秒数则放弃）
    """
    min_bytes_per_window = DOWNLOAD_MIN_SPEED_KBPS * 1024 * DOWNLOAD_SLOW_WINDOW

    tmp = dest.parent / (dest.name + f".part.{threading.current_thread().ident}")
    try:
        window_start = time.monotonic()
        window_bytes = 0
        last_chunk_time = time.monotonic()

        with httpx.stream(
            "GET", full_url,
            timeout=httpx.Timeout(
                connect=TIMEOUT_HTTP,
                read=TIMEOUT_SOURCE_MAX,
                write=None,
                pool=None,
            ),
            follow_redirects=True,
        ) as resp:
            if resp.status_code not in (200, 206):
                return
            with tmp.open("wb") as f:
                for chunk in resp.iter_bytes(DOWNLOAD_CHUNK_SIZE):
                    if done_event.is_set():
                        return
                    now = time.monotonic()
                    if now - last_chunk_time > TIMEOUT_SOURCE_MAX:
                        return
                    last_chunk_time = now
                    f.write(chunk)
                    window_bytes += len(chunk)
                    if progress_callback:
                        progress_callback(len(chunk))
                    elapsed_window = now - window_start
                    if elapsed_window >= DOWNLOAD_SLOW_WINDOW:
                        if window_bytes < min_bytes_per_window:
                            return
                        window_start = now
                        window_bytes = 0

        if done_event.is_set():
            return
        if tmp.exists() and tmp.stat().st_size > 0:
            done_event.set()
            result_holder.append(tmp)
    except Exception:
        pass
    finally:
        if tmp not in result_holder and tmp.exists():
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass


def download(
    url_template: str,
    dest: Path,
    category: str = "github_releases",
    progress_callback=None,
    total_size: int | None = None,
    fallback_url: str | None = None,
    cache_path: Path | None = None,
    direct_urls: list[str] | None = None,
) -> Path:
    """
    两阶段下载（含缓存命中）：
    0. 若 cache_path 指定且文件完整，直接复用缓存，跳过下载
    1. HEAD 探针并发过滤不可达/404 的镜像源（使用真实文件路径）
    2. 赛马下载：前 MIRROR_RACE_COUNT 个可达源同时下载，第一个成功立即取消其余
    3. 所有镜像失败 → fallback_url 兜底（单线程，TIMEOUT_SOURCE_MAX 超时）
    4. 下载成功后将文件复制到 cache_path 供下次复用

    url_template: 含 {mirror} 占位符，如
        '{mirror}/XTLS/Xray-core/releases/download/v1.0/Xray-linux-64.zip'
    fallback_url: 不含 {mirror} 的直连 URL，作为最后兜底
    cache_path:   可选缓存路径，命中则直接返回，下载完成后回写
    direct_urls:  完整 URL 列表（无需 {mirror} 拼接），传入时跳过 sources.yaml 查找，
                  直接对列表做 HEAD 探针 + 赛马下载。适用于 URL 无法拆分为 mirror+path 的场景。
    """
    import shutil

    # ── 阶段零：缓存命中检测 ────────────────────────────────────────────────────
    if cache_path is not None and _is_cached_valid(cache_path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if cache_path != dest:
            shutil.copy2(cache_path, dest)
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)

    # ── 阶段一：HEAD 探针，过滤不可达源 ────────────────────────────────────────
    candidates: list[str] = []

    if direct_urls:
        # direct_urls 模式：完整 URL 列表，直接探针排序
        probe_results: list[tuple[str, float]] = _probe_concurrent(
            list(direct_urls), _probe_reachable,
            total_timeout=TIMEOUT_MIRROR_PROBE + 1,
        )
        probe_results.sort(key=lambda x: x[1])
        candidates = [u for u, lat in probe_results if lat < float("inf")]
        if not candidates:
            candidates = list(direct_urls)
    else:
        sources = get_sources(category)
        if "{mirror}" in url_template and sources:
            probe_urls = [
                (url_template.format(mirror=m.rstrip("/")), m)
                for m in sources
            ]

            def _probe_with_mirror(pair: tuple[str, str]) -> tuple[str, float, str]:
                pu, m = pair
                full_url, latency = _probe_reachable(pu)
                return full_url, latency, m

            results: list[tuple[str, float, str]] = _probe_concurrent(
                probe_urls, _probe_with_mirror,
                total_timeout=TIMEOUT_MIRROR_PROBE + 1,
            )
            results.sort(key=lambda x: x[1])
            candidates = [
                url_template.format(mirror=m.rstrip("/"))
                for _, lat, m in results
                if lat < float("inf")
            ]

        if not candidates and sources:
            candidates = [url_template.format(mirror=m.rstrip("/")) for m in sources[:MIRROR_RACE_COUNT]]

    race_urls = candidates[:MIRROR_RACE_COUNT]

    # ── 阶段二：赛马下载 ────────────────────────────────────────────────────────
    if race_urls:
        done_event = threading.Event()
        result_holder: list[Path] = []
        threads = []
        for url in race_urls:
            t = threading.Thread(
                target=_download_single,
                args=(url, dest, done_event, result_holder, progress_callback),
                daemon=True,
            )
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=TIMEOUT_SOURCE_MAX + 5)

        if result_holder:
            winner = result_holder[0]
            if winner != dest:
                # join 之后再 replace，确保所有线程的文件句柄已释放（Windows WinError 32 防护）
                for _retry in range(5):
                    try:
                        winner.replace(dest)
                        break
                    except PermissionError:
                        import time as _time
                        _time.sleep(0.3)
                else:
                    winner.replace(dest)  # 最后一次，失败则正常抛出
            for t_path in result_holder[1:]:
                try:
                    t_path.unlink(missing_ok=True)
                except Exception:
                    pass
            if cache_path is not None and cache_path != dest:
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dest, cache_path)
                except Exception:
                    pass
            return dest

    # ── 阶段三：fallback_url 兜底 ───────────────────────────────────────────────
    if fallback_url:
        tmp = dest.parent / (dest.name + ".fallback")
        try:
            min_bytes_per_window = DOWNLOAD_MIN_SPEED_KBPS * 1024 * DOWNLOAD_SLOW_WINDOW
            window_start = time.monotonic()
            window_bytes = 0
            last_chunk_time = time.monotonic()
            with httpx.stream(
                "GET", fallback_url,
                timeout=httpx.Timeout(
                    connect=TIMEOUT_HTTP,
                    read=TIMEOUT_SOURCE_MAX,
                    write=None,
                    pool=None,
                ),
                follow_redirects=True,
            ) as resp:
                if resp.status_code in (200, 206):
                    with tmp.open("wb") as f:
                        for chunk in resp.iter_bytes(DOWNLOAD_CHUNK_SIZE):
                            now = time.monotonic()
                            if now - last_chunk_time > TIMEOUT_SOURCE_MAX:
                                break
                            last_chunk_time = now
                            f.write(chunk)
                            window_bytes += len(chunk)
                            if progress_callback:
                                progress_callback(len(chunk))
                            elapsed_window = now - window_start
                            if elapsed_window >= DOWNLOAD_SLOW_WINDOW:
                                if window_bytes < min_bytes_per_window:
                                    break
                                window_start = now
                                window_bytes = 0
            if tmp.exists() and tmp.stat().st_size > 0:
                tmp.replace(dest)
                if cache_path is not None and cache_path != dest:
                    try:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(dest, cache_path)
                    except Exception:
                        pass
                return dest
        except Exception:
            pass
        finally:
            if tmp.exists():
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass

    raise IOError("下载失败：所有镜像源和兜底地址均不可用")


# ─── 统一大文件下载：Sequential-with-Probe + Range 断点续传 ───────────────────

def download_file(
    urls: list[str],
    dest: Path,
    cache_path: Path | None = None,
    progress_callback=None,
    stall_timeout: int = TIMEOUT_STALL_SECS,
    min_speed_kbps: int = DOWNLOAD_MIN_SPEED_KBPS,
    slow_window: int = DOWNLOAD_SLOW_WINDOW,
) -> Path:
    """
    统一下载函数（Sequential-with-Probe 策略）：

    1. 缓存命中检查 → 直接返回
    2. 并发 HEAD 探针 → 过滤 404/不可达，按延迟排序
    3. 逐源顺序尝试（无总时长限制）：
       - 支持 Range: bytes=N- 断点续传（.part 文件已存在时自动续传）
       - 停滞超时：stall_timeout 秒无任何字节 → 切换下一源
       - 低速检测：slow_window 秒内 < min_speed_kbps KB/s → 切换
    4. 全部源失败 → IOError

    优于赛马方案：
    - 无 join(timeout) 上限，适合任意大小文件
    - 单线程无带宽浪费
    - 断点续传减少重试成本
    """
    import shutil as _shutil

    # 阶段零：缓存命中
    if cache_path is not None and _is_cached_valid(cache_path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if cache_path != dest:
            _shutil.copy2(cache_path, dest)
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)

    if not urls:
        raise IOError("下载失败：URL 列表为空")

    # 阶段一：并发 HEAD 探针，按延迟排序
    probe_results = _probe_concurrent(
        list(urls), _probe_reachable,
        total_timeout=TIMEOUT_MIRROR_PROBE + 1,
    )
    probe_results.sort(key=lambda x: x[1])
    ordered = [u for u, lat in probe_results if lat < float("inf")]
    if not ordered:
        ordered = list(urls)  # 探针全失败时仍尝试全部

    min_bytes_per_window = min_speed_kbps * 1024 * slow_window
    tmp = dest.parent / (dest.name + ".part")

    last_err: str = "未知错误"

    # 阶段二：逐源顺序尝试
    for url in ordered:
        # 断点续传：读取已下载大小
        resume_pos = tmp.stat().st_size if tmp.exists() else 0
        headers = {}
        if resume_pos > 0:
            headers["Range"] = f"bytes={resume_pos}-"

        try:
            window_start = time.monotonic()
            window_bytes = 0
            last_chunk_time = time.monotonic()
            got_any = False

            with httpx.stream(
                "GET", url,
                headers=headers,
                timeout=httpx.Timeout(connect=TIMEOUT_HTTP, read=None, write=None, pool=None),
                follow_redirects=True,
            ) as resp:
                # 206 = 续传，200 = 全量（服务器不支持 Range 时重置）
                if resp.status_code == 200 and resume_pos > 0:
                    resume_pos = 0  # 服务器不支持 Range，从头下载
                if resp.status_code not in (200, 206):
                    last_err = f"HTTP {resp.status_code}"
                    continue

                mode = "ab" if resume_pos > 0 else "wb"
                with tmp.open(mode) as f:
                    for chunk in resp.iter_bytes(DOWNLOAD_CHUNK_SIZE):
                        got_any = True
                        f.write(chunk)
                        window_bytes += len(chunk)
                        if progress_callback:
                            progress_callback(len(chunk))
                        now = time.monotonic()
                        # 停滞超时
                        if now - last_chunk_time > stall_timeout:
                            last_err = f"停滞超时 {stall_timeout}s 无数据"
                            break
                        last_chunk_time = now
                        # 低速检测
                        elapsed = now - window_start
                        if elapsed >= slow_window:
                            if window_bytes < min_bytes_per_window:
                                last_err = (
                                    f"速度过慢 ({window_bytes // 1024}KB/{elapsed:.0f}s"
                                    f" < {min_speed_kbps}KB/s)"
                                )
                                break
                            window_start = now
                            window_bytes = 0

            # 验证下载结果
            if tmp.exists() and tmp.stat().st_size > 1024:
                for _retry in range(5):
                    try:
                        tmp.replace(dest)
                        break
                    except PermissionError:
                        time.sleep(0.3)
                else:
                    tmp.replace(dest)

                if cache_path is not None and cache_path != dest:
                    try:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        _shutil.copy2(dest, cache_path)
                    except Exception:
                        pass
                return dest
            else:
                last_err = "文件不完整（下载后大小异常）"

        except Exception as exc:
            last_err = str(exc)
            continue

    # 清理残留 .part 文件
    if tmp.exists():
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass

    raise IOError(f"下载失败：所有源均不可用（最后错误：{last_err}）")
