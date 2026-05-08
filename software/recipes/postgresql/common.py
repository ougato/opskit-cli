"""跨平台共用工具：路径、快照、架构映射、tarball 赛马下载（对齐 mongodb/common.py）"""
from __future__ import annotations

import json
import platform
import shutil
from pathlib import Path
from typing import Optional

from software.base import InstallError


# ─── theseus-rs 版本支持范围（实测 2025-05）────────────────────────────────────
#
# theseus-rs tag 格式：{pg_major}.{pg_minor}.0
# 与 PostgreSQL 官方版本号一一对应，但只覆盖以下 major 版本：
#   PG 12：12.0 ~ 12.20（最大 minor=20，12.21+ 不存在）
#   PG 13：13.0 ~ 13.23
#   PG 14：14.0 ~ 14.22
#   PG 15：15.0 ~ 15.17
#   PG 16：16.0 ~ 16.13
#   PG 17：17.0 ~ 17.9
#   PG 18：18.0 ~ 18.3
# PG 10/11 完全不支持（GitHub 无对应 release）

_THESEUS_MIN_MAJOR = 12
_THESEUS_MAX_MINOR_PG12 = 20  # PG 12 theseus-rs 最高只到 12.20


def _theseus_supported(version: str) -> bool:
    """判断给定 PG 版本是否有对应 theseus-rs 预编译包"""
    try:
        parts = version.split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except Exception:
        return False
    if major < _THESEUS_MIN_MAJOR:
        return False
    if major == 12 and minor > _THESEUS_MAX_MINOR_PG12:
        return False
    return True


def fetch_supported_versions(timeout: float = 8.0) -> Optional[list[str]]:
    """
    从 theseus-rs GitHub releases API 动态获取实际支持的 PG 版本列表。
    返回格式 ['18.3', '17.9', '16.13', ...] 降序排列，失败返回 None。
    """
    try:
        import httpx
        from collections import defaultdict
        r = httpx.get(
            "https://api.github.com/repos/theseus-rs/postgresql-binaries/releases?per_page=100",
            timeout=timeout,
        )
        if r.status_code != 200:
            return None
        tags = [rel["tag_name"] for rel in r.json()]
        by_major: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for tag in tags:
            parts = tag.split(".")
            if len(parts) < 2:
                continue
            try:
                major, minor = int(parts[0]), int(parts[1])
                by_major[major].append((major, minor))
            except Exception:
                continue
        result: list[str] = []
        for major in sorted(by_major.keys(), reverse=True):
            # 每个 major 只取最新 minor
            latest = max(by_major[major], key=lambda x: x[1])
            result.append(f"{latest[0]}.{latest[1]}")
        return result if result else None
    except Exception:
        return None


# ─── 架构映射 ─────────────────────────────────────────────────────────────────

def _pgsql_arch() -> str:
    """将 Python platform.machine() 映射为 theseus-rs 架构字符串"""
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "x86_64"
    if m in ("aarch64", "arm64"):
        return "aarch64"
    return "x86_64"


def _pgsql_arch_darwin() -> str:
    """macOS 专用：arm64 原生 / x86_64"""
    m = platform.machine().lower()
    if m in ("aarch64", "arm64"):
        return "aarch64"
    return "x86_64"


# ─── 路径工具 ─────────────────────────────────────────────────────────────────

def pgsql_versions_dir() -> Path:
    """所有 PostgreSQL 版本的根目录：~/.opskit/postgresql/"""
    from .constants import PGSQL_PRIVATE_SUBDIR
    return Path.home() / PGSQL_PRIVATE_SUBDIR


def pgsql_version_dir(version: str) -> Path:
    """指定版本的安装目录：~/.opskit/postgresql/postgresql{version}/"""
    return pgsql_versions_dir() / f"postgresql{version}"


def pgsql_bin_dir(version: str) -> Path:
    """指定版本的 bin 目录：~/.opskit/postgresql/postgresql{version}/bin/"""
    return pgsql_version_dir(version) / "bin"


def shim_dir() -> Path:
    """shim 目录：~/.opskit/postgresql/shims/"""
    return pgsql_versions_dir() / "shims"


def active_link() -> Path:
    """active 链接路径：~/.opskit/postgresql/active"""
    return pgsql_versions_dir() / "active"


# ─── 快照管理 ─────────────────────────────────────────────────────────────────

def snapshot_path() -> Path:
    from .constants import SNAPSHOT_SUBDIR, SNAPSHOT_PGSQL_FILE
    return Path.home() / SNAPSHOT_SUBDIR / SNAPSHOT_PGSQL_FILE


def load_snapshot() -> dict:
    p = snapshot_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_snapshot(data: dict) -> None:
    p = snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def delete_snapshot() -> None:
    p = snapshot_path()
    if p.exists():
        p.unlink()


# ─── tarball 赛马下载 ─────────────────────────────────────────────────────────

def _detect_linux_codename() -> str:
    """
    检测 Debian/Ubuntu 发行版代号，返回 PGDG Packages.gz URL 所需的代号字符串。
    PGDG URL 格式：dists/{codename}-pgdg/，其中 codename 是发行版名称。
    Debian: bookworm / bullseye / buster
    Ubuntu: noble / jammy / focal / bionic
    """
    import subprocess
    try:
        out = subprocess.check_output(["lsb_release", "-cs"], text=True, timeout=5).strip().lower()
        if out:
            return out
    except Exception:
        pass
    try:
        import re
        content = Path("/etc/os-release").read_text(encoding="utf-8")
        m = re.search(r'VERSION_CODENAME=(\S+)', content)
        if m:
            return m.group(1).strip('"').lower()
    except Exception:
        pass
    return "bookworm"  # 默认 Debian 12 bookworm


def _pgdg_query_deb_paths(major: int, version: str, deb_arch: str) -> Optional[dict]:
    """
    从 PGDG Packages.gz 动态查询指定版本的 deb 包路径。
    返回 {'server': path, 'client': path, 'libpq': path} 或 None。
    """
    import gzip
    import httpx
    from .constants import PGSQL_PGDG_PACKAGES_URLS

    codename = _detect_linux_codename()
    pkg_urls = [u.format(codename=codename, arch=deb_arch)
                for u in PGSQL_PGDG_PACKAGES_URLS]

    data = None
    for pkg_url in pkg_urls:
        try:
            r = httpx.get(pkg_url, timeout=10, follow_redirects=True)
            if r.status_code == 200:
                data = gzip.decompress(r.content).decode("utf-8", errors="replace")
                break
        except Exception:
            continue

    if not data:
        return None

    current: dict = {}
    results: list[dict] = []
    for line in data.splitlines():
        if line.startswith("Package:"):
            current = {"Package": line.split(": ", 1)[1].strip()}
        elif line.startswith("Version:") and current:
            current["Version"] = line.split(": ", 1)[1].strip()
        elif line.startswith("Filename:") and current:
            current["Filename"] = line.split(": ", 1)[1].strip()
        elif line == "" and current.get("Package"):
            results.append(current)
            current = {}
    if current.get("Package"):
        results.append(current)

    server_path = client_path = libpq_path = None
    ver_prefix = f"{version}-"
    for pkg in results:
        name = pkg.get("Package", "")
        ver = pkg.get("Version", "")
        fname = pkg.get("Filename", "")
        if not ver.startswith(ver_prefix):
            continue
        if name == f"postgresql-{major}":
            server_path = fname
        elif name == f"postgresql-client-{major}":
            client_path = fname
    for pkg in results:
        name = pkg.get("Package", "")
        fname = pkg.get("Filename", "")
        if name == "libpq5":
            libpq_path = fname
            break

    if not server_path and not client_path:
        return None
    return {"server": server_path, "client": client_path, "libpq": libpq_path}


def download_pgsql_deb_linux(version: str, dest_dir: Path) -> Path:
    """
    Linux 专用：从 PGDG apt 仓库下载 deb 包（server + client + libpq5），
    解压合并到 dest_dir，返回 bin 目录路径。

    bin 路径：dest_dir/bin/（来自 deb 内的 usr/lib/postgresql/{major}/bin/）
    lib 路径：dest_dir/lib/（来自 deb 内的 usr/lib/{arch}-linux-gnu/）

    不需要 apt/root，纯下载解压。支持 Debian/Ubuntu 全系 glibc 环境。
    """
    import httpx
    import tarfile as _tarfile
    import tempfile
    from .constants import PGSQL_PGDG_DEB_BASE_URLS, PGSQL_LINUX_DEB_ARCH_MAP

    cpu_arch = _pgsql_arch()
    deb_arch = PGSQL_LINUX_DEB_ARCH_MAP.get(cpu_arch, "amd64")
    major = int(version.split(".")[0])

    from core.i18n import t as _t
    paths = _pgdg_query_deb_paths(major, version, deb_arch)
    if not paths:
        raise InstallError(_t("software.postgresql_error.deb_not_found", version=version))

    dest_dir.mkdir(parents=True, exist_ok=True)
    bin_dest = dest_dir / "bin"
    lib_dest = dest_dir / "lib"
    bin_dest.mkdir(exist_ok=True)
    lib_dest.mkdir(exist_ok=True)

    deb_entries = [
        ("server", paths.get("server")),
        ("client", paths.get("client")),
        ("libpq", paths.get("libpq")),
    ]

    for label, rel_path in deb_entries:
        if not rel_path:
            continue
        deb_urls = [u.format(path=rel_path) for u in PGSQL_PGDG_DEB_BASE_URLS]
        deb_data = None
        for url in deb_urls:
            try:
                r = httpx.get(url, timeout=120, follow_redirects=True)
                if r.status_code == 200 and len(r.content) > 10000:
                    deb_data = r.content
                    break
            except Exception:
                continue
        if not deb_data:
            if label == "libpq":
                continue
            raise InstallError(_t("software.postgresql_error.deb_download_failed", version=version, label=label))

        with tempfile.TemporaryDirectory(
            prefix=f"opskit-pgsql-deb-{label}-", ignore_cleanup_errors=True
        ) as tmpdir:
            tmp = Path(tmpdir)
            deb_file = tmp / "pkg.deb"
            deb_file.write_bytes(deb_data)

            import subprocess
            ar_result = subprocess.run(
                ["ar", "x", str(deb_file)],
                capture_output=True, cwd=str(tmp)
            )
            if ar_result.returncode != 0:
                raise InstallError(_t("software.postgresql_error.deb_ar_failed", label=label, error=ar_result.stderr.decode()[:200]))

            data_tar = None
            for candidate in ("data.tar.xz", "data.tar.gz", "data.tar.bz2", "data.tar.zst"):
                p = tmp / candidate
                if p.exists():
                    data_tar = p
                    break
            if not data_tar:
                raise InstallError(_t("software.postgresql_error.deb_no_data_tar", label=label))

            if data_tar.suffix == ".xz":
                import lzma
                raw = lzma.decompress(data_tar.read_bytes())
                tf = _tarfile.open(fileobj=__import__("io").BytesIO(raw))
            elif data_tar.suffix == ".zst":
                import zstandard
                raw = zstandard.ZstdDecompressor().decompress(data_tar.read_bytes())
                tf = _tarfile.open(fileobj=__import__("io").BytesIO(raw))
            else:
                tf = _tarfile.open(str(data_tar))

            with tf:
                for member in tf.getmembers():
                    mname = member.name.lstrip("./")
                    pg_bin_prefix = f"usr/lib/postgresql/{major}/bin/"
                    lib_prefix_amd64 = "usr/lib/x86_64-linux-gnu/"
                    lib_prefix_arm64 = "usr/lib/aarch64-linux-gnu/"

                    if mname.startswith(pg_bin_prefix) and member.isfile():
                        rel = mname[len(pg_bin_prefix):]
                        if not rel:
                            continue
                        target = bin_dest / rel
                        with tf.extractfile(member) as src, open(target, "wb") as out:
                            shutil.copyfileobj(src, out)
                        target.chmod(target.stat().st_mode | 0o111)
                    elif (mname.startswith(lib_prefix_amd64) or
                          mname.startswith(lib_prefix_arm64)) and member.isfile():
                        fname = Path(mname).name
                        if not fname or not (fname.endswith(".so") or ".so." in fname):
                            continue
                        target = lib_dest / fname
                        with tf.extractfile(member) as src, open(target, "wb") as out:
                            shutil.copyfileobj(src, out)

    psql_bin = bin_dest / "psql"
    if not psql_bin.exists():
        raise InstallError(_t("software.postgresql_error.deb_psql_missing", version=version))

    # 为 lib/ 目录下的 .so.x.y 文件创建 .so.x 和 .so symlink，确保动态链接器可找到
    import re as _re
    for lib_file in list(lib_dest.iterdir()):
        if not lib_file.is_file():
            continue
        m = _re.match(r'^(.+\.so)\.(\d+)\.(\d+)$', lib_file.name)
        if m:
            base_so = lib_dest / m.group(1)           # libpq.so
            ver_so  = lib_dest / f"{m.group(1)}.{m.group(2)}"  # libpq.so.5
            for link in (ver_so, base_so):
                if not link.exists() and not link.is_symlink():
                    try:
                        link.symlink_to(lib_file.name)
                    except Exception:
                        pass
        else:
            m2 = _re.match(r'^(.+\.so)\.(\d+)$', lib_file.name)
            if m2:
                base_so = lib_dest / m2.group(1)
                if not base_so.exists() and not base_so.is_symlink():
                    try:
                        base_so.symlink_to(lib_file.name)
                    except Exception:
                        pass

    return bin_dest


def download_pgsql_tarball(version: str, dest: Path) -> Path:
    """
    跨平台下载 PostgreSQL 预编译包：

    - Windows：EDB CDN zip（主）→ theseus-rs ghfast zip（fallback）
    - macOS：  EDB CDN zip（主）→ theseus-rs ghfast tar.gz（fallback）
    - Linux：  PGDG apt deb 直接下载解压（glibc 原生，Aliyun 主 + PGDG 官方 fallback）
               dest 参数在 Linux 上被忽略，直接安装到版本目录
    """
    import sys
    from core import mirror
    from .constants import (
        PGSQL_DL_WINDOWS_URLS,
        PGSQL_DL_DARWIN_URLS,
    )

    if sys.platform == "linux":
        major = int(version.split(".")[0])
        from .constants import PGSQL_PGDG_SUPPORTED_MAJORS
        if major not in PGSQL_PGDG_SUPPORTED_MAJORS:
            from core.i18n import t as _t2
            raise InstallError(
                _t2("software.postgresql_error.pgdg_unsupported",
                    version=version, major=major,
                    supported=', '.join(str(m) for m in PGSQL_PGDG_SUPPORTED_MAJORS))
            )
        dest_dir = pgsql_version_dir(version)
        return download_pgsql_deb_linux(version, dest_dir)

    if sys.platform == "darwin" and not _theseus_supported(version):
        from core.i18n import t as _t3
        raise InstallError(_t3("software.postgresql_error.macos_unsupported", version=version))

    if sys.platform == "win32":
        url_templates = PGSQL_DL_WINDOWS_URLS
        arch = "x86_64"
    else:
        url_templates = PGSQL_DL_DARWIN_URLS
        arch = _pgsql_arch_darwin()

    urls = [u.format(version=version, arch=arch) for u in url_templates]

    try:
        return mirror.download_file(
            urls=urls,
            dest=dest,
        )
    except Exception as e:
        from core.i18n import t as _t4
        raise InstallError(_t4("software.postgresql_error.download_failed", version=version, error=e)) from e
