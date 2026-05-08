"""MySQL Linux APT deb 包下载：解析官方 APT repo Packages.gz 获取 deb URL，
比 tarball（933MB）体积小 95%（~50MB），境内走清华镜像，境外走官方 APT repo。"""
from __future__ import annotations

import gzip
import subprocess
import tarfile
import tempfile
from pathlib import Path


# ─── APT 源配置 ─────────────────────────────────────────────────────────────

_TSINGHUA_BASE = "https://mirrors.tuna.tsinghua.edu.cn/mysql/apt"
_OFFICIAL_BASE = "https://repo.mysql.com/apt"

# 版本段 → APT 组件名
_COMPONENT_MAP: list[tuple[tuple[int, int], tuple[int, int], str]] = [
    ((9, 7), (9, 99), "mysql-9.7-lts"),
    ((9, 0), (9, 6),  "mysql-innovation"),
    ((8, 4), (8, 4),  "mysql-8.4-lts"),
    ((8, 0), (8, 3),  "mysql-8.0"),
    ((5, 7), (5, 7),  "mysql-5.7"),
    ((5, 6), (5, 6),  "mysql-5.6"),
]

# 需要提取的 deb 包名（顺序决定优先级，server-core 最大）
_TARGET_PKGS = [
    "mysql-community-server-core",
    "mysql-community-client-core",
    "mysql-community-client",
    "mysql-community-server",
]

# 只从这些路径提取二进制
_BIN_DIRS = {"usr/bin/", "usr/sbin/"}
_BINARY_NAMES = {
    "mysql", "mysqladmin", "mysqldump", "mysqlcheck",
    "mysqlimport", "mysqlshow", "mysqlslap",
    "mysqld", "mysqld_safe", "mysql_safe",
    "my_print_defaults", "mysql_config",
}


# ─── 工具函数 ────────────────────────────────────────────────────────────────

def _get_apt_component(version: str) -> str:
    """版本号 → APT 组件名，如 '9.7.0' → 'mysql-9.7-lts'"""
    parts = version.split(".")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return "mysql-innovation"
    for (lo_maj, lo_min), (hi_maj, hi_min), comp in _COMPONENT_MAP:
        if (lo_maj, lo_min) <= (major, minor) <= (hi_maj, hi_min):
            return comp
    return "mysql-innovation"


def _parse_packages_gz(content: bytes, target_pkgs: list[str]) -> dict[str, str]:
    """解析 Packages.gz，返回 {pkg_name: relative_path}"""
    try:
        text = gzip.decompress(content).decode("utf-8", errors="ignore")
    except Exception:
        text = content.decode("utf-8", errors="ignore")

    result: dict[str, str] = {}
    pkg, fname = "", ""
    for line in text.split("\n"):
        if line.startswith("Package:"):
            pkg = line.split(":", 1)[1].strip()
            fname = ""
        elif line.startswith("Filename:"):
            fname = line.split(":", 1)[1].strip()
        elif line == "" and pkg in target_pkgs and fname and pkg not in result:
            result[pkg] = fname
    return result


def get_mysql_apt_deb_urls(version: str) -> list[tuple[str, str]]:
    """
    解析 APT Packages.gz，返回 [(pkg_name, url), ...] 列表（优先清华，fallback 官方）。
    清华只同步 8.4/8.0，9.7+ 只有官方源。
    """
    import httpx

    from .common import get_distro_codename
    codename = get_distro_codename() or "bookworm"
    component = _get_apt_component(version)

    # 清华 → 官方 按顺序尝试
    bases = [_TSINGHUA_BASE, _OFFICIAL_BASE]

    for base in bases:
        pkgs_url = f"{base}/debian/dists/{codename}/{component}/binary-amd64/Packages.gz"
        try:
            resp = httpx.get(pkgs_url, timeout=15, follow_redirects=True)
            if resp.status_code != 200:
                continue
            found = _parse_packages_gz(resp.content, _TARGET_PKGS)
            if not found:
                continue
            result = []
            for pkg in _TARGET_PKGS:
                if pkg in found:
                    rel = found[pkg].lstrip("/")
                    result.append((pkg, f"{base}/debian/{rel}"))
            if result:
                return result
        except Exception:
            continue

    return []


# ─── 提取二进制 ──────────────────────────────────────────────────────────────

def _extract_data_tar_from_deb(deb_path: Path, work_dir: Path) -> Path | None:
    """
    ar x 解压 deb，返回 data.tar.* 路径。
    优先用系统 ar，失败则用纯 Python（zipimport ar 格式）。
    """
    # 方法一：系统 ar
    try:
        result = subprocess.run(
            ["ar", "x", str(deb_path)],
            cwd=str(work_dir),
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0:
            for p in work_dir.iterdir():
                if p.name.startswith("data.tar"):
                    return p
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 方法二：纯 Python 解析 ar 格式
    try:
        _ar_extract_data(deb_path, work_dir)
        for p in work_dir.iterdir():
            if p.name.startswith("data.tar"):
                return p
    except Exception:
        pass

    return None


def _ar_extract_data(deb_path: Path, work_dir: Path) -> None:
    """纯 Python 解析 ar(1) 格式，只提取 data.tar.* 成员"""
    with deb_path.open("rb") as f:
        magic = f.read(8)
        if magic != b"!<arch>\n":
            raise ValueError("不是有效的 ar/deb 文件")
        while True:
            header = f.read(60)
            if len(header) < 60:
                break
            name_raw = header[0:16].rstrip()
            size_raw = header[48:58].rstrip()
            try:
                size = int(size_raw)
            except ValueError:
                break
            name = name_raw.decode("latin-1").rstrip("/").strip()
            data = f.read(size)
            if size % 2 == 1:
                f.read(1)
            if name.startswith("data.tar"):
                out = work_dir / name
                out.write_bytes(data)
                return


def extract_mysql_from_debs(deb_paths: list[Path], bin_dir: Path) -> None:
    """
    从 deb 包列表中提取 MySQL 二进制到 bin_dir。
    只提取 usr/bin/ 和 usr/sbin/ 下的目标二进制。
    """
    bin_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="mysql_deb_") as tmp_str:
        tmp = Path(tmp_str)

        for deb in deb_paths:
            work = tmp / deb.stem
            work.mkdir(parents=True, exist_ok=True)

            data_tar = _extract_data_tar_from_deb(deb, work)
            if not data_tar:
                continue

            try:
                with tarfile.open(str(data_tar)) as tf:
                    for member in tf.getmembers():
                        if not member.isfile():
                            continue
                        norm = member.name.lstrip("./")
                        base = Path(norm).name
                        # 只提取目标路径下的目标二进制
                        in_bin = any(norm.startswith(d) for d in _BIN_DIRS)
                        if not in_bin or base not in _BINARY_NAMES:
                            continue
                        fobj = tf.extractfile(member)
                        if fobj is None:
                            continue
                        out = bin_dir / base
                        out.write_bytes(fobj.read())
                        out.chmod(0o755)
            except Exception:
                continue
