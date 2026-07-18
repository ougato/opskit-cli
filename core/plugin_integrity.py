"""插件产物指纹清单 — CHECKSUMS.yaml 生成与校验

开发者发布插件时生成 CHECKSUMS.yaml（文件级 sha256 清单，随仓库提交），
平台在加载前校验目录实际内容与清单一致，不一致即拒绝加载并告警，
防发布后传输 / 本机篡改（仓库入侵防护由后续签名体系承担）。

清单格式：
    version: <plugin.yaml 版本>
    files:
      <相对路径>: <sha256>
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from core.constants import FILE_PLUGIN_CHECKSUMS, FILE_PLUGIN_MANIFEST
from core.logger import get_logger

# 与信任指纹一致：版本库元数据与运行时缓存不属于插件代码内容
_SKIP_DIRS = frozenset({".git", "__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache"})

# 校验结果
CHECK_OK = "ok"
CHECK_MISSING = "missing"
CHECK_MISMATCH = "mismatch"

_log = get_logger("opskit.plugin")


def _file_hashes(plugin_root: Path) -> dict[str, str]:
    """插件目录内所有代码文件的 {相对路径: sha256}（跳过清单自身）"""
    hashes: dict[str, str] = {}
    for f in sorted(plugin_root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(plugin_root)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if str(rel) == FILE_PLUGIN_CHECKSUMS:
            continue
        hashes[rel.as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    return hashes


def checksums_path(plugin_root: Path) -> Path:
    return plugin_root / FILE_PLUGIN_CHECKSUMS


def write_checksums(plugin_root: Path) -> Path:
    """生成 / 覆写 CHECKSUMS.yaml（开发者发布前调用）"""
    version = ""
    manifest_path = plugin_root / FILE_PLUGIN_MANIFEST
    if manifest_path.exists():
        try:
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                version = str(data.get("version") or "")
        except Exception:
            pass
    payload = {"version": version, "files": _file_hashes(plugin_root)}
    path = checksums_path(plugin_root)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=True)
    return path


def verify_checksums(plugin_root: Path) -> str:
    """校验目录实际内容与 CHECKSUMS.yaml 是否一致

    返回 ok / missing（无清单，回落 TOFU 信任模型）/ mismatch（不一致，视为可能被篡改）
    """
    path = checksums_path(plugin_root)
    if not path.exists():
        return CHECK_MISSING
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        declared = data.get("files") if isinstance(data, dict) else None
        if not isinstance(declared, dict):
            _log.warning("plugin %s: CHECKSUMS.yaml malformed", plugin_root.name)
            return CHECK_MISMATCH
        actual = _file_hashes(plugin_root)
    except Exception as e:
        _log.warning("plugin %s: checksum verify failed: %s", plugin_root.name, e)
        return CHECK_MISMATCH
    if {str(k): str(v) for k, v in declared.items()} != actual:
        _log.error("plugin %s: content does not match CHECKSUMS.yaml — possible tampering", plugin_root.name)
        return CHECK_MISMATCH
    return CHECK_OK
