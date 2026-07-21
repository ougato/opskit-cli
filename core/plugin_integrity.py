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


def content_digest(path: Path) -> bytes:
    """文件内容 sha256（raw digest）。

    文本文件先把 CRLF / 单独 CR 规范化为 LF 再计算，消除同一份代码在 Windows
    （git autocrlf 检出为 CRLF）与 Unix（LF）之间的字节差异，避免跨平台校验误报
    「内容与清单不符」。含 NUL 字节者按二进制原样计算，不做规范化。
    对 LF 内容而言规范化是空操作，因此与旧清单/信任记录完全向后兼容。
    """
    data = path.read_bytes()
    if b"\x00" not in data:
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).digest()


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
        hashes[rel.as_posix()] = content_digest(f).hex()
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
