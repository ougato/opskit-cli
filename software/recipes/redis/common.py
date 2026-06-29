"""Redis 跨平台共用工具：架构映射、路径工具、快照管理、下载赛马（对齐 install-strategy.md）"""
from __future__ import annotations

import gzip
import platform
from pathlib import Path

from software._shared.snapshot import SnapshotStore
from .constants import SNAPSHOT_SUBDIR, SNAPSHOT_REDIS_FILE


# ─── 架构映射 ─────────────────────────────────────────────────────────────────

def _redis_arch() -> str:
    """Linux/macOS 架构字符串（用于 deb arch 和源码编译）"""
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "x86_64"
    if m in ("aarch64", "arm64"):
        return "aarch64"
    return "x86_64"


def _deb_arch() -> str:
    """deb 包架构字符串：x86_64 → amd64，aarch64 → arm64"""
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "amd64"
    if m in ("aarch64", "arm64"):
        return "arm64"
    return "amd64"


# ─── 路径工具 ─────────────────────────────────────────────────────────────────

def redis_versions_dir() -> Path:
    from .constants import REDIS_PRIVATE_SUBDIR
    return Path.home() / REDIS_PRIVATE_SUBDIR


def redis_version_dir(version: str) -> Path:
    return redis_versions_dir() / f"redis{version}"


def redis_bin_dir(version: str) -> Path:
    return redis_version_dir(version) / "bin"


def shim_dir() -> Path:
    return redis_versions_dir() / "shims"


# ─── 快照管理 ─────────────────────────────────────────────────────────────────

_store = SnapshotStore(SNAPSHOT_SUBDIR, SNAPSHOT_REDIS_FILE)


def _snapshot_path() -> Path:
    return _store.path


def load_snapshot() -> dict:
    return _store.load()


def save_snapshot(data: dict) -> None:
    _store.save(data)


def delete_snapshot() -> None:
    _store.delete()


# ─── Linux deb 包查询与下载 ───────────────────────────────────────────────────

def _get_distro_codename() -> str:
    """获取发行版代名：bookworm / bullseye / jammy / noble 等"""
    import subprocess
    try:
        return subprocess.check_output(
            ["lsb_release", "-cs"], text=True, timeout=5
        ).strip()
    except Exception:
        pass
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("VERSION_CODENAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return "bookworm"


def _parse_packages_gz(data: bytes, pkg_name: str, version_prefix: str) -> str | None:
    """
    从 Packages.gz 内容中查找 redis 包的 Filename 字段。
    version_prefix: 如 "7.4" 匹配 7.4.x。
    """
    try:
        text = gzip.decompress(data).decode("utf-8", errors="replace")
    except Exception:
        return None
    current_pkg = None
    current_ver = None
    current_filename = None
    best_filename = None
    best_ver = None
    for line in text.splitlines():
        if line.startswith("Package:"):
            current_pkg = line.split(":", 1)[1].strip()
            current_ver = None
            current_filename = None
        elif line.startswith("Version:"):
            current_ver = line.split(":", 1)[1].strip()
        elif line.startswith("Filename:"):
            current_filename = line.split(":", 1)[1].strip()
        elif line == "" and current_pkg == pkg_name and current_filename:
            if version_prefix and current_ver and current_ver.startswith(version_prefix):
                if best_ver is None or current_ver > best_ver:
                    best_ver = current_ver
                    best_filename = current_filename
            current_pkg = None
    if best_filename:
        return best_filename
    # 再次扫描，不过滤版本，取最新
    current_pkg = None
    current_ver = None
    current_filename = None
    for line in text.splitlines():
        if line.startswith("Package:"):
            current_pkg = line.split(":", 1)[1].strip()
            current_ver = None
            current_filename = None
        elif line.startswith("Version:"):
            current_ver = line.split(":", 1)[1].strip()
        elif line.startswith("Filename:"):
            current_filename = line.split(":", 1)[1].strip()
        elif line == "" and current_pkg == pkg_name and current_filename:
            if best_ver is None or (current_ver and current_ver > best_ver):
                best_ver = current_ver
                best_filename = current_filename
            current_pkg = None
    return best_filename


def download_redis_linux(version: str, dest: Path) -> list[Path]:
    """
    Linux: 从 packages.redis.io / 阿里云 deb 源同时下载 redis-server + redis-tools
    两个 deb（二进制分布在 redis-tools，服务配置在 redis-server），
    fallback 到 download.redis.io 源码 tarball。
    返回下载文件路径列表（多个 deb 或单个 tar.gz）。
    """
    from core import mirror
    from software.base import InstallError
    from .constants import (
        REDIS_DEB_PACKAGES_URLS,
        REDIS_DEB_BASE_URLS,
        REDIS_DL_LINUX_SRC_URLS,
    )
    import httpx

    codename = _get_distro_codename()
    arch = _deb_arch()
    ver_prefix = ".".join(version.split(".")[:2])

    # 尝试通过 Packages.gz 找 deb 路径：优先 packages.redis.io（有新版），次选阿里云
    # 注意：packages.redis.io 排在 REDIS_DEB_PACKAGES_URLS[1]，调整为优先尝试
    sources = list(zip(REDIS_DEB_PACKAGES_URLS, REDIS_DEB_BASE_URLS))
    # 把 packages.redis.io 排到前面（支持 7.x/8.x 全版本）
    sources.sort(key=lambda x: (0 if "packages.redis.io" in x[0] else 1))

    for pkg_url_tpl, base_url in sources:
        try:
            pkg_url = pkg_url_tpl.format(codename=codename, arch=arch)
            resp = httpx.get(pkg_url, timeout=15, follow_redirects=True, verify=False)
            if resp.status_code != 200:
                continue

            # 同时解析 redis-server 和 redis-tools 路径
            rel_server = _parse_packages_gz(resp.content, "redis-server", ver_prefix)
            rel_tools  = _parse_packages_gz(resp.content, "redis-tools",  ver_prefix)

            if not rel_server and not rel_tools:
                # 当前源没有目标版本，尝试下一个
                continue

            downloaded: list[Path] = []
            for pkg_name, rel in (("redis-server", rel_server), ("redis-tools", rel_tools)):
                if not rel:
                    continue
                deb_url = f"{base_url}/{rel.lstrip('/')}"
                dest_deb = dest.parent / f"{pkg_name}.deb"
                try:
                    p = mirror.download_file(
                        urls=[deb_url],
                        dest=dest_deb,
                    )
                    downloaded.append(p)
                except Exception:
                    pass

            if downloaded:
                return downloaded
        except Exception:
            continue

    # fallback: 源码 tarball
    src_urls = [u.format(version=version) for u in REDIS_DL_LINUX_SRC_URLS]
    dest_src = dest.with_suffix(".tar.gz")
    try:
        p = mirror.download_file(
            urls=src_urls,
            dest=dest_src,
        )
        return [p]
    except Exception as e:
        from core.i18n import t
        raise InstallError(t("software.redis_error.linux_download_failed", version=version, error=e)) from e


def _pick_fastest_url(urls: list[str], probe_bytes: int = 65536) -> str:
    """
    并发下载前 probe_bytes 字节测速，选实际带宽最快的可达 URL。
    若所有源均不可达则返回第一条。
    """
    import time as _time
    import httpx as _httpx
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def probe(u: str) -> tuple[str, float]:
        """返回 (url, cost_per_byte)，越小越快；不可达返回 inf"""
        tmp_chunks: list[bytes] = []
        total = 0
        try:
            t0 = _time.monotonic()
            with _httpx.stream(
                "GET", u,
                timeout=_httpx.Timeout(connect=6, read=10, write=None, pool=None),
                follow_redirects=True,
                headers={"Range": f"bytes=0-{probe_bytes - 1}"},
            ) as resp:
                if resp.status_code not in (200, 206):
                    return u, float("inf")
                for chunk in resp.iter_bytes(16384):
                    total += len(chunk)
                    if total >= probe_bytes:
                        break
            elapsed = _time.monotonic() - t0
            if total == 0 or elapsed == 0:
                return u, float("inf")
            return u, elapsed / total
        except Exception:
            return u, float("inf")

    results: list[tuple[str, float]] = []
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        futures = [pool.submit(probe, u) for u in urls]
        for fut in as_completed(futures):
            results.append(fut.result())
    results.sort(key=lambda x: x[1])
    reachable = [u for u, cost in results if cost < float("inf")]
    return reachable[0] if reachable else urls[0]


def download_redis_windows(version: str, dest: Path) -> Path:
    """Windows: redis-windows/redis-windows GitHub Releases zip（支持 6.x/7.x/8.x）
    先实际带宽测速选最快可达源，再单线程下载。
    """
    import httpx as _httpx
    import shutil as _shutil
    from software.base import InstallError
    from .constants import REDIS_DL_WINDOWS_URLS, REDIS_WINDOWS_FALLBACK_VERSION

    use_ver = version
    urls = [u.format(version=use_ver) for u in REDIS_DL_WINDOWS_URLS]
    dest_zip = dest.with_suffix(".zip")
    dest_zip.parent.mkdir(parents=True, exist_ok=True)

    best_url = _pick_fastest_url(urls)
    ordered = [best_url] + [u for u in urls if u != best_url]

    for url in ordered:
        tmp = dest_zip.parent / (dest_zip.name + ".part")
        try:
            with _httpx.stream(
                "GET", url,
                timeout=_httpx.Timeout(connect=10, read=120, write=None, pool=None),
                follow_redirects=True,
            ) as resp:
                if resp.status_code not in (200, 206):
                    continue
                with tmp.open("wb") as f:
                    for chunk in resp.iter_bytes(65536):
                        f.write(chunk)
            if tmp.exists() and tmp.stat().st_size > 1_000_000:
                tmp.replace(dest_zip)
                return dest_zip
        except Exception:
            pass
        finally:
            try:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
            except Exception:
                pass

    from core.i18n import t
    raise InstallError(t("software.redis_error.win_download_failed", version=version))


def download_redis_macos(version: str, dest: Path) -> Path:
    """macOS: download.redis.io 源码 tarball"""
    from core import mirror
    from software.base import InstallError
    from .constants import REDIS_DL_MACOS_URLS

    urls = [u.format(version=version) for u in REDIS_DL_MACOS_URLS]
    dest_tar = dest.with_suffix(".tar.gz")
    try:
        return mirror.download_file(
            urls=urls,
            dest=dest_tar,
        )
    except Exception as e:
        from core.i18n import t
        raise InstallError(t("software.redis_error.macos_download_failed", version=version, error=e)) from e


# ─── 版本列表 ─────────────────────────────────────────────────────────────────

def version_list() -> list[str]:
    from core.version_cache import get_cached_versions, get_cached_versions_stale, update_cache
    from core.constants import TIMEOUT_VERSION_FETCH
    from .constants import REDIS_VERSIONS_API_URL, REDIS_VERSIONS_FALLBACK

    _KEY = "redis"

    # 1. 缓存未过期直接返回（过滤无效占位数据 ['latest']）
    cached = get_cached_versions(_KEY)
    if cached and any(v[0].isdigit() for v in cached if v):
        return cached

    # 2. 在线获取
    try:
        import httpx
        resp = httpx.get(REDIS_VERSIONS_API_URL, timeout=TIMEOUT_VERSION_FETCH)
        if resp.status_code == 200:
            vers = [item.get("latest", "") for item in resp.json() if item.get("latest", "")]
            vers = [v for v in vers if v and v[0].isdigit()]
            if vers:
                update_cache(_KEY, vers)
                return vers
    except Exception:
        pass

    # 3. API 失败：用过期缓存兜底（比 fallback 更新，过滤无效占位数据）
    stale = get_cached_versions_stale(_KEY)
    if stale and any(v[0].isdigit() for v in stale if v):
        return stale

    # 4. 彻底无缓存：用内置 fallback
    return list(REDIS_VERSIONS_FALLBACK)
