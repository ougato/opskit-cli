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
from core.bounded_yaml import load_bounded_yaml as _metadata
from core.plugin_tree import bounded_paths as _bounded_paths, SKIP_DIRS as _SKIP_DIRS

# 校验结果
CHECK_OK = "ok"
CHECK_MISSING = "missing"
CHECK_MISMATCH = "mismatch"

_log = get_logger("opskit.plugin")


def content_digest(path: Path, *, max_bytes=128 * 1024**2) -> bytes:
    """文件内容 sha256（raw digest）。

    文本文件先把 CRLF / 单独 CR 规范化为 LF 再计算，消除同一份代码在 Windows
    （git autocrlf 检出为 CRLF）与 Unix（LF）之间的字节差异，避免跨平台校验误报
    「内容与清单不符」。含 NUL 字节者按二进制原样计算，不做规范化。
    对 LF 内容而言规范化是空操作，因此与旧清单/信任记录完全向后兼容。
    """
    return _content_digest(path, max_bytes)[0]


def _content_digest(path, max_bytes):
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("invalid plugin digest budget")
    raw, text = hashlib.sha256(), hashlib.sha256()
    size, binary, pending_cr = 0, False, False
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(min(65536, max_bytes - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("plugin digest exceeds limit")
            raw.update(chunk)
            binary = binary or b"\x00" in chunk
            if pending_cr:
                chunk = b"\r" + chunk
            pending_cr = chunk.endswith(b"\r")
            if pending_cr:
                chunk = chunk[:-1]
            text.update(chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    if pending_cr:
        text.update(b"\n")
    return (raw.digest() if binary else text.digest()), size


def _file_hashes(plugin_root: Path) -> dict[str, str]:
    """插件目录内所有代码文件的 {相对路径: sha256}（跳过清单自身）"""
    if plugin_root.is_symlink() or (hasattr(plugin_root, "is_junction") and plugin_root.is_junction()):
        raise ValueError("linked plugin roots forbidden")
    size = 0
    hashes: dict[str, str] = {}
    for f in sorted(_bounded_paths(plugin_root)):
        if f.is_symlink() or (hasattr(f, "is_junction") and f.is_junction()):
            raise ValueError("plugin links forbidden")
        if not f.is_file():
            continue
        rel = f.relative_to(plugin_root)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if str(rel) == FILE_PLUGIN_CHECKSUMS:
            continue
        if f.stat().st_size > 128 * 1024**2 - size or len(hashes) >= 4096:
            raise ValueError("plugin integrity limits exceeded")
        checksum, actual_size = _content_digest(f, 128 * 1024**2 - size)
        size += actual_size
        hashes[rel.as_posix()] = checksum.hex()
    return hashes


def checksums_path(plugin_root: Path) -> Path:
    return plugin_root / FILE_PLUGIN_CHECKSUMS


def write_checksums(plugin_root: Path) -> Path:
    """生成 / 覆写 CHECKSUMS.yaml（开发者发布前调用）"""
    version = ""
    manifest_path = plugin_root / FILE_PLUGIN_MANIFEST
    if manifest_path.exists():
        try:
            data = _metadata(manifest_path)
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
        data = _metadata(path)
        declared = data.get("files") if isinstance(data, dict) else None
        if not isinstance(declared, dict):
            _log.warning("plugin %s: CHECKSUMS.yaml malformed", plugin_root.name)
            return CHECK_MISMATCH
        actual = _file_hashes(plugin_root)
    except Exception as e:
        _log.warning("plugin %s: checksum verify failed (%s)", plugin_root.name, type(e).__name__)
        return CHECK_MISMATCH
    if {str(k): str(v) for k, v in declared.items()} != actual:
        _log.error("plugin %s: content does not match CHECKSUMS.yaml — possible tampering", plugin_root.name)
        return CHECK_MISMATCH
    return CHECK_OK
