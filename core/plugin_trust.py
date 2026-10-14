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
import re
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

import yaml

from core.constants import FILE_PLUGIN_TRUST
from core.logger import get_logger
from core.paths import data_dir
from core.plugin_integrity import content_digest
from core.resource_lock import ResourceLock

_log = get_logger("opskit.plugin")

_mutation_lock = ResourceLock(lambda: data_dir() / ".plugin-trust.lock", label="plugin trust store")
_reads = threading.local()


class TrustStoreError(RuntimeError):
    """The authority cannot be read safely; mutations must preserve it."""


@contextmanager
def authorization_transaction():
    """Serialize final authorization checks and publication with trust mutations."""
    with _mutation_lock.hold():
        yield


def _serialized_mutation(function):
    @wraps(function)
    def mutate(*args, **kwargs):
        # Hold across load, mutation and atomic save, not just the write.
        with _mutation_lock.hold():
            return function(*args, **kwargs)
    return mutate


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
            digest.update(content_digest(checksums, max_bytes=1024 * 1024))
    except (OSError, ValueError) as e:
        _log.warning("plugin %s: fingerprint failed: %s", plugin_root.name, e)
        return ""
    return digest.hexdigest()


def _trust_file() -> Path:
    return data_dir() / FILE_PLUGIN_TRUST


def _load() -> dict[str, dict]:
    return _load_path(_trust_file())


def _load_path(path: Path) -> dict[str, dict]:
    try:
        # lstat distinguishes an absent store from broken links and directories.
        path.lstat()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise TrustStoreError("plugin trust store is unavailable") from exc
    try:
        from core.bounded_yaml import load_bounded_yaml
        data = load_bounded_yaml(path)
        if not isinstance(data, dict) or any(
                not isinstance(key, str) or not isinstance(value, dict)
                for key, value in data.items()):
            raise ValueError("invalid trust records")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        _log.warning("plugin trust store read failed: %s", type(exc).__name__)
        raise TrustStoreError("plugin trust store is invalid or unreadable") from None
    return data


def _read_authorizations() -> dict[str, dict]:
    """Read-only checks deny access; writers use _load and propagate failures."""
    records = getattr(_reads, "records", None)
    if records is not None:
        return records
    try:
        return _load()
    except TrustStoreError:
        return {}


@contextmanager
def authorization_snapshot(*, path=None):
    """One fresh store per validation, never cached across checks or threads."""
    previous = getattr(_reads, "records", None)
    try:
        try:
            records = _load() if path is None else _load_path(Path(path))
        except TrustStoreError:
            records = {}
        _reads.records = records
        yield
    finally:
        _reads.records = previous


def _save(data: dict[str, dict]) -> None:
    path = _trust_file()
    from core.bounded_yaml import parse_bounded_yaml
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=True)
    try:
        # Never publish a store that the bounded reader would reject.
        parse_bounded_yaml(text.encode("utf-8"))
    except (ValueError, yaml.YAMLError):
        raise TrustStoreError("plugin trust store exceeds publication limits") from None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from core.secrets import atomic_write
        atomic_write(path, text)
    except OSError as e:
        _log.warning("plugin trust store write failed: %s", e)
        raise


def is_trusted(name: str, fingerprint: str) -> bool:
    """插件是否已被信任且内容未变化"""
    if not fingerprint:
        return False
    record = _read_authorizations().get(name)
    return record is not None and record.get("fingerprint") == fingerprint


def trusted_record(name: str) -> dict | None:
    """已存信任记录（可能与当前内容不一致，用于区分「未信任」和「已变化」）"""
    from copy import deepcopy
    return deepcopy(_read_authorizations().get(name))


def _drop_service_edges(data, identity):
    for name, record in data.items():
        if name == identity:
            record.pop("service_grants", None)
            continue
        grants = record.get("service_grants", {})
        if isinstance(grants, dict):
            for service, edge in list(grants.items()):
                if isinstance(edge, dict) and edge.get("provider") == identity:
                    del grants[service]


@_serialized_mutation
def grant(name: str, fingerprint: str, version: str, source: str = "", branch: str | None = None) -> None:
    """记录用户对插件当前内容的信任。

    branch 为 None 时保留原记录已存的分支（更新重确认不丢失安装分支）。
    """
    data = _load()
    _drop_service_edges(data, name)
    if branch is None:
        branch = (data.get(name) or {}).get("branch")
    record = {"fingerprint": fingerprint, "version": version, "source": source}
    if branch:
        record["branch"] = branch
    data[name] = record
    _save(data)
    _log.info("plugin %s: trusted (version %s)", name, version)


@_serialized_mutation
def revoke(name: str) -> None:
    """移除信任记录（卸载或内容变化后调用）"""
    data = _load()
    if name in data:
        _drop_service_edges(data, name)
        del data[name]
        _save(data)


@_serialized_mutation
def invalidate(name: str) -> None:
    """Revoke authorization while preserving installation source and branch."""
    data = _load()
    if name in data:
        _drop_service_edges(data, name)
        data[name]["fingerprint"] = ""
        _save(data)


@_serialized_mutation
def grant_service(caller: str, caller_fingerprint: str, service: str, provider: str,
                  provider_fingerprint: str, api_version: int, methods: list[str]) -> None:
    """Persist an operator-approved RPC edge; independent trust is insufficient."""
    from core.plugin import _service_contract
    if (any(not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name)
            for name in (caller, provider, service)) or "platform" in (caller, provider)):
        raise ValueError("invalid service grant identities")
    contract = _service_contract({"api_version": api_version, "methods": methods})
    data = _load()
    if (not caller_fingerprint or not provider_fingerprint
            or data.get(caller, {}).get("fingerprint") != caller_fingerprint
            or data.get(provider, {}).get("fingerprint") != provider_fingerprint):
        raise ValueError("service grant identities are not currently trusted")
    grants = data[caller].setdefault("service_grants", {})
    if not isinstance(grants, dict) or (service not in grants and len(grants) >= 64):
        raise ValueError("invalid or excessive service grants")
    grants[service] = {"provider": provider, "provider_fingerprint": provider_fingerprint, **contract}
    _save(data)


def is_service_authorized(caller: str, caller_fingerprint: str, service: str, provider: str,
                          provider_fingerprint: str, api_version: int, method: str) -> bool:
    data = _read_authorizations()
    if (not caller_fingerprint or not provider_fingerprint
            or data.get(caller, {}).get("fingerprint") != caller_fingerprint
            or data.get(provider, {}).get("fingerprint") != provider_fingerprint):
        return False
    grants = data[caller].get("service_grants", {})
    if not isinstance(grants, dict):
        return False
    grant_record = grants.get(service)
    if not isinstance(grant_record, dict):
        return False
    return (grant_record.get("provider") == provider
            and grant_record.get("provider_fingerprint") == provider_fingerprint
            and type(api_version) is int
            and type(grant_record.get("api_version")) is int
            and grant_record["api_version"] == api_version
            and isinstance(grant_record.get("methods"), list)
            and method in grant_record["methods"])


@_serialized_mutation
def revoke_service(caller: str, service: str) -> None:
    """Revoke a single edge without authorizing or revoking independent execution."""
    data = _load()
    grants = data.get(caller, {}).get("service_grants", {})
    if not isinstance(grants, dict):
        raise ValueError("invalid service grant store")
    if service in grants:
        del grants[service]
        _save(data)
