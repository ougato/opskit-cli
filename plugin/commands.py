"""插件管理命令 — 安装 / 更新 / 卸载，支持热插拔（纯业务，供 menu.py 调用）"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

from core.config import load_config, set_config_value
from core.loader import builtin_module_keys
from core.module import ModuleInfo
from core.paths import plugins_dir
from core.plugin import (
    PluginManifest,
    list_manifests,
    load_manifest,
    load_plugin,
    unload_plugin,
)
from core.plugin_trust import compute_fingerprint, grant, is_trusted, revoke, trusted_record
from core.runner import run

# git URL 末段提取插件目录名：去掉 .git 后缀
_URL_NAME_PATTERN = re.compile(r"([^/]+?)(?:\.git)?/?$")

# 信任状态
TRUST_OK = "trusted"
TRUST_NONE = "untrusted"
TRUST_CHANGED = "changed"


def manifests() -> list[PluginManifest]:
    """当前插件目录下所有合法插件清单"""
    return list_manifests()


def loaded_plugins() -> list[tuple[PluginManifest, ModuleInfo]]:
    """实时扫描并加载全部已信任且启用的插件（热插拔：每次进插件工具菜单调用）"""
    builtin = builtin_module_keys()
    pairs: list[tuple[PluginManifest, ModuleInfo]] = []
    seen: set[str] = set()
    for manifest in manifests():
        if manifest.name in builtin or manifest.name in seen or not is_enabled(manifest.name):
            continue
        info = load_plugin(manifest)
        if info is None:
            continue
        seen.add(manifest.name)
        pairs.append((manifest, info))
    pairs.sort(key=lambda p: p[1].order)
    return pairs


def reload(manifest: PluginManifest) -> None:
    """清除插件旧模块缓存，下次扫描时重新 import 新代码"""
    unload_plugin(manifest)


def is_enabled(key: str) -> bool:
    cfg = load_config()
    return bool(cfg.get("modules", {}).get(key, {}).get("enabled", True))


def set_enabled(key: str, enabled: bool) -> None:
    cfg = load_config()
    set_config_value(cfg, f"modules.{key}.enabled", enabled)


def dir_name_from_url(url: str) -> str | None:
    """从 git URL 提取插件目录名"""
    m = _URL_NAME_PATTERN.search(url.strip())
    return m.group(1) if m else None


def trust_status(manifest: PluginManifest) -> str:
    """插件信任状态：trusted / untrusted / changed"""
    if is_trusted(manifest.name, compute_fingerprint(manifest.root)):
        return TRUST_OK
    if trusted_record(manifest.name) is not None:
        return TRUST_CHANGED
    return TRUST_NONE


def grant_trust(manifest: PluginManifest, source: str = "") -> None:
    """记录用户对插件当前内容的信任"""
    record = trusted_record(manifest.name)
    if not source and record is not None:
        source = str(record.get("source", ""))
    grant(manifest.name, compute_fingerprint(manifest.root), manifest.version, source)


def is_trusted_source(url: str) -> bool:
    """URL 主机是否在配置的可信源白名单中（plugin.trusted_sources）"""
    cfg = load_config()
    sources = cfg.get("plugin", {}).get("trusted_sources", []) or []
    host = urlparse(url.strip()).hostname
    if not host and "@" in url:  # scp 形式 git@host:path
        host = url.split("@", 1)[1].split(":", 1)[0]
    return bool(host) and host in [str(s) for s in sources]


def install(url: str) -> tuple[PluginManifest | None, str]:
    """git clone 到插件目录并校验清单。返回 (清单, 错误串)；信任确认由菜单层负责"""
    name = dir_name_from_url(url)
    if not name:
        return None, "invalid url"
    dest = plugins_dir() / name
    if dest.exists():
        return None, f"exists:{name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        run(["git", "clone", "--depth", "1", url, str(dest)], capture=True)
    except Exception as e:
        shutil.rmtree(dest, ignore_errors=True)
        return None, str(e)
    manifest = load_manifest(dest)
    if manifest is None:
        shutil.rmtree(dest, ignore_errors=True)
        return None, "no_manifest"
    return manifest, ""


def rollback_install(manifest: PluginManifest) -> None:
    """用户拒绝信任时回滚删除刚安装的插件目录"""
    root = Path(manifest.root).resolve()
    if root.parent == plugins_dir().resolve():
        shutil.rmtree(root, ignore_errors=True)


def update(manifest: PluginManifest) -> tuple[bool, str]:
    """git pull 更新插件目录；内容变化后信任失效，需重新确认"""
    root = Path(manifest.root)
    if not (root / ".git").exists():
        return False, "not_git"
    try:
        run(["git", "-C", str(root), "pull", "--ff-only"], capture=True)
    except Exception as e:
        return False, str(e)
    return True, manifest.name


def remove(manifest: PluginManifest) -> None:
    """删除插件目录、移除信任记录并清除进程内残留（立即生效）"""
    unload_plugin(manifest)
    shutil.rmtree(manifest.root, ignore_errors=True)
    revoke(manifest.name)
