"""ClickHouse shared helpers."""
from __future__ import annotations

import platform
import tarfile
from pathlib import Path

from software._shared.snapshot import SnapshotStore
from .constants import (
    CLICKHOUSE_DL_URLS,
    CLICKHOUSE_PRIVATE_SUBDIR,
    CLICKHOUSE_RELEASES_API_URL,
    SNAPSHOT_CLICKHOUSE_FILE,
    SNAPSHOT_SUBDIR,
)


def clickhouse_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return "amd64"


def clickhouse_versions_dir() -> Path:
    return Path.home() / CLICKHOUSE_PRIVATE_SUBDIR


def clickhouse_version_dir(version: str) -> Path:
    return clickhouse_versions_dir() / f"clickhouse{version}"


def clickhouse_bin_dir(version: str) -> Path:
    return clickhouse_version_dir(version) / "bin"


def shim_dir() -> Path:
    return clickhouse_versions_dir() / "shims"


_store = SnapshotStore(SNAPSHOT_SUBDIR, SNAPSHOT_CLICKHOUSE_FILE)


def load_snapshot() -> dict:
    return _store.load()


def save_snapshot(data: dict) -> None:
    _store.save(data)


def delete_snapshot() -> None:
    _store.delete()


def _version_sort_key(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for item in version.split("."):
        try:
            parts.append(int(item))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def parse_release_version(tag: str) -> str | None:
    raw = tag.strip()
    if raw.startswith("v"):
        raw = raw[1:]
    for suffix in ("-stable", "-lts"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
            break
    if not raw or not raw[0].isdigit():
        return None
    return raw


def fetch_versions() -> list[str]:
    import httpx
    from core.constants import TIMEOUT_VERSION_FETCH

    resp = httpx.get(
        CLICKHOUSE_RELEASES_API_URL,
        params={"per_page": 30},
        headers={"User-Agent": "opskit"},
        timeout=TIMEOUT_VERSION_FETCH,
    )
    if resp.status_code != 200:
        return []
    versions: list[str] = []
    for item in resp.json():
        if not isinstance(item, dict):
            continue
        version = parse_release_version(str(item.get("tag_name", "")))
        if version and version not in versions:
            versions.append(version)
    versions.sort(key=_version_sort_key, reverse=True)
    return versions


def download_clickhouse_tarball(version: str, dest: Path, progress_callback=None) -> Path:
    from core import mirror
    from core.i18n import t
    from software.base import InstallError

    arch = clickhouse_arch()
    urls = [url.format(version=version, arch=arch) for url in CLICKHOUSE_DL_URLS]
    cache_path = mirror.get_download_cache_path(
        "clickhouse",
        version,
        urls[0].rsplit("/", 1)[-1],
    )
    try:
        return mirror.download_file(
            urls=urls,
            dest=dest,
            cache_path=cache_path,
            progress_callback=progress_callback,
        )
    except Exception as e:
        raise InstallError(t("software.clickhouse_error.download_failed", version=version, error=e)) from e


def extract_clickhouse_tarball(tarball: Path, dest: Path, version: str) -> Path:
    from core.i18n import t
    from software.base import InstallError

    bin_dir = dest / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(str(tarball), "r:gz") as tf:
            member = next(
                (
                    m for m in tf.getmembers()
                    if m.isfile() and m.name.replace("\\", "/").endswith("/usr/bin/clickhouse")
                ),
                None,
            )
            if member is None:
                member = next(
                    (
                        m for m in tf.getmembers()
                        if m.isfile() and Path(m.name).name == "clickhouse"
                    ),
                    None,
                )
            if member is None:
                raise InstallError(t("software.clickhouse_error.bad_structure", version=version))
            src = tf.extractfile(member)
            if src is None:
                raise InstallError(t("software.clickhouse_error.bad_structure", version=version))
            target = bin_dir / "clickhouse"
            target.write_bytes(src.read())
            target.chmod(0o755)
    except InstallError:
        raise
    except Exception as e:
        raise InstallError(t("software.clickhouse_error.extract_failed", version=version, error=e)) from e
    return bin_dir


def detect_clickhouse_version() -> str | None:
    import re
    import shutil
    import subprocess

    snap = load_snapshot()
    active = snap.get("active_version")
    if active and (clickhouse_bin_dir(active) / "clickhouse").exists():
        return active
    cmd = shutil.which("clickhouse")
    if not cmd:
        return None
    try:
        result = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    match = re.search(r"\b(\d+\.\d+\.\d+\.\d+)\b", result.stdout or result.stderr or "")
    return match.group(1) if match else None
