"""插件信任存储 — 首次信任确认 + 指纹变更重确认

信任模型（同 Homebrew tap）：插件代码在加载前必须经用户明确信任；
信任时记录插件目录内容指纹，之后内容一旦变化（如 git pull 更新）即失效，
需要用户重新确认，防止「先发好版本、后续更新投毒」。

存储位置：<data_dir>/plugin_trust.yaml
    <name>:
      fingerprint: <sha256>
      version: <清单版本>
      source: <安装来源 URL，可为空>
      branch: <安装时指定的分支，可为空；更新时按此分支 pull>
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from core.constants import FILE_PLUGIN_TRUST
from core.logger import get_logger
from core.paths import data_dir
from core.plugin_integrity import content_digest

_log = get_logger("opskit.plugin")


def compute_fingerprint(plugin_root: Path) -> str:
    """插件目录内容指纹：所有文件（相对路径 + 内容 sha256）汇总 hash"""
    digest = hashlib.sha256()
    try:
        from core.plugin_integrity import _file_hashes
        for name, checksum in _file_hashes(plugin_root).items():
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(bytes.fromhex(checksum))
        checksums = plugin_root / "CHECKSUMS.yaml"
        if checksums.exists():
            if checksums.is_symlink() or checksums.stat().st_size > 1024 * 1024:
                raise ValueError("invalid checksum manifest")
            digest.update(b"CHECKSUMS.yaml\0")
            digest.update(content_digest(checksums))
    except (OSError, ValueError) as e:
        _log.warning("plugin %s: fingerprint failed: %s", plugin_root.name, e)
        return ""
    return digest.hexdigest()


def _trust_file() -> Path:
    return data_dir() / FILE_PLUGIN_TRUST


def _load() -> dict[str, dict]:
    path = _trust_file()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        _log.warning("plugin trust store read failed: %s", e)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _save(data: dict[str, dict]) -> None:
    path = _trust_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from core.secrets import atomic_write
        atomic_write(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=True))
    except OSError as e:
        _log.warning("plugin trust store write failed: %s", e)


def is_trusted(name: str, fingerprint: str) -> bool:
    """插件是否已被信任且内容未变化"""
    if not fingerprint:
        return False
    record = _load().get(name)
    return record is not None and record.get("fingerprint") == fingerprint


def trusted_record(name: str) -> dict | None:
    """已存信任记录（可能与当前内容不一致，用于区分「未信任」和「已变化」）"""
    return _load().get(name)


def grant(name: str, fingerprint: str, version: str, source: str = "", branch: str | None = None) -> None:
    """记录用户对插件当前内容的信任。

    branch 为 None 时保留原记录已存的分支（更新重确认不丢失安装分支）。
    """
    data = _load()
    if branch is None:
        branch = (data.get(name) or {}).get("branch")
    record = {"fingerprint": fingerprint, "version": version, "source": source}
    if branch:
        record["branch"] = branch
    data[name] = record
    _save(data)
    _log.info("plugin %s: trusted (version %s)", name, version)


def revoke(name: str) -> None:
    """移除信任记录（卸载或内容变化后调用）"""
    data = _load()
    if name in data:
        del data[name]
        _save(data)


def invalidate(name: str) -> None:
    """Revoke authorization while preserving installation source and branch."""
    data = _load()
    if name in data:
        data[name]["fingerprint"] = ""
        _save(data)
