"""插件管理命令 — 安装 / 更新 / 启停 / 卸载（纯业务，供 menu.py 调用）"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from core.config import load_config, set_config_value
from core.paths import plugins_dir
from core.plugin import PluginManifest, list_manifests, load_manifest
from core.runner import run

# git URL 末段提取插件目录名：去掉 .git 后缀
_URL_NAME_PATTERN = re.compile(r"([^/]+?)(?:\.git)?/?$")


def manifests() -> list[PluginManifest]:
    """当前插件目录下所有合法插件清单"""
    return list_manifests()


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


def install(url: str) -> tuple[bool, str]:
    """git clone 到插件目录并校验清单。返回 (成功, 消息 key 参数或错误串)"""
    name = dir_name_from_url(url)
    if not name:
        return False, "invalid url"
    dest = plugins_dir() / name
    if dest.exists():
        return False, f"exists:{name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        run(["git", "clone", "--depth", "1", url, str(dest)], capture=True)
    except Exception as e:
        shutil.rmtree(dest, ignore_errors=True)
        return False, str(e)
    if load_manifest(dest) is None:
        shutil.rmtree(dest, ignore_errors=True)
        return False, "no_manifest"
    return True, name


def update(manifest: PluginManifest) -> tuple[bool, str]:
    """git pull 更新插件目录"""
    root = Path(manifest.root)
    if not (root / ".git").exists():
        return False, "not_git"
    try:
        run(["git", "-C", str(root), "pull", "--ff-only"], capture=True)
    except Exception as e:
        return False, str(e)
    return True, manifest.name


def remove(manifest: PluginManifest) -> None:
    """删除插件目录"""
    shutil.rmtree(manifest.root, ignore_errors=True)
