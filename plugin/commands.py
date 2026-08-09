"""插件管理命令 — 安装 / 更新 / 卸载，支持热插拔（纯业务，供 menu.py 调用）"""
from __future__ import annotations

import re
import shutil
import stat
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from core.config import load_config, set_config_value
from core.i18n import t
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
from core.plugin_integrity import CHECK_MISMATCH, verify_checksums
from core.plugin_services import invalidate_service_cache
from core.plugin_trust import compute_fingerprint, grant, is_trusted, revoke, trusted_record
from core.runner import run

# 插件安装目录名：URL 路径分段转小写连字符，避免不同仓库同 basename 冲突
_URL_NAME_PATTERN = re.compile(r"([^/\\]+?)(?:\.git)?[/\\]?$")
_SCP_URL_PATTERN = re.compile(r"^[^@\s]+@[^:\s]+:(?P<path>.+)$")
_INSTALL_DIR_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,95}$")
_REMOTE_URL_SCHEMES = {"http", "https", "ssh", "git", "file"}

# 信任状态
TRUST_OK = "trusted"
TRUST_NONE = "untrusted"
TRUST_CHANGED = "changed"

_SEMVER_CORE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def semver_key(version: str) -> tuple[int, int, int] | None:
    """semver 主体部分 (x, y, z)，非法返回 None"""
    m = _SEMVER_CORE.match(version.strip())
    return tuple(int(g) for g in m.groups()) if m else None


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


def _strip_git_suffix(value: str) -> str:
    return value[:-4] if value.endswith(".git") else value


def _slug_part(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


def valid_install_dir_name(name: str | None) -> bool:
    """安装目录别名是否合法。目录名仅作为本地容器，不等同于 plugin.yaml name。"""
    return bool(name and _INSTALL_DIR_PATTERN.match(name))


def dir_name_from_url(url: str) -> str | None:
    """从 git URL 生成稳定安装目录名。

    远端 URL 使用仓库路径分段生成目录，例如 org/team/tool.git -> org-team-tool。
    本地路径保留历史行为，仅使用路径末段，方便测试和本地开发。
    """
    raw = url.strip().rstrip("/\\")
    if not raw:
        return None

    path_text: str | None = None
    scp_match = _SCP_URL_PATTERN.match(raw)
    if scp_match:
        path_text = scp_match.group("path")
    else:
        parsed = urlparse(raw)
        if parsed.scheme in _REMOTE_URL_SCHEMES and parsed.path:
            path_text = parsed.path

    if path_text:
        parts = [_slug_part(_strip_git_suffix(part)) for part in path_text.replace("\\", "/").split("/")]
        name = "-".join(part for part in parts if part)
        return name if valid_install_dir_name(name) else None

    m = _URL_NAME_PATTERN.search(raw)
    if not m:
        return None
    name = _slug_part(_strip_git_suffix(m.group(1)))
    return name if valid_install_dir_name(name) else None


def parse_install_input(raw: str) -> tuple[str, str | None, str | None]:
    """解析安装输入，支持 `-b/--branch` 指定分支、`--as` 指定本地安装目录。

    返回 (仓库地址, 分支或 None, 安装目录别名或 None)。分支省略时按远端默认分支克隆。
    """
    tokens = raw.split()
    url = ""
    branch: str | None = None
    alias: str | None = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-b", "--branch") and i + 1 < len(tokens):
            branch = tokens[i + 1]
            i += 2
            continue
        if tok == "--as" and i + 1 < len(tokens):
            alias = tokens[i + 1]
            i += 2
            continue
        if tok.startswith("--branch="):
            branch = tok.split("=", 1)[1]
        elif tok.startswith("--as="):
            alias = tok.split("=", 1)[1]
        elif not url:
            url = tok
        i += 1
    return url, (branch or None), (alias or None)


def plugin_branch(name: str) -> str | None:
    """插件安装时记录的分支（更新时据此 pull），未记录返回 None"""
    record = trusted_record(name)
    return record.get("branch") if record else None


def trust_status(manifest: PluginManifest) -> str:
    """插件信任状态：trusted / untrusted / changed"""
    if is_trusted(manifest.name, compute_fingerprint(manifest.root)):
        return TRUST_OK
    if trusted_record(manifest.name) is not None:
        return TRUST_CHANGED
    return TRUST_NONE


def grant_trust(manifest: PluginManifest, source: str = "", branch: str | None = None) -> None:
    """记录用户对插件当前内容的信任（branch 为 None 时沿用已存分支）"""
    record = trusted_record(manifest.name)
    if not source and record is not None:
        source = str(record.get("source", ""))
    grant(manifest.name, compute_fingerprint(manifest.root), manifest.version, source, branch)


def is_trusted_source(url: str) -> bool:
    """URL 主机是否在配置的可信源白名单中（plugin.trusted_sources）"""
    cfg = load_config()
    sources = cfg.get("plugin", {}).get("trusted_sources", []) or []
    host = urlparse(url.strip()).hostname
    if not host and "@" in url:  # scp 形式 git@host:path
        host = url.split("@", 1)[1].split(":", 1)[0]
    return bool(host) and host in [str(s) for s in sources]


# git stderr 常见模式 → 可读原因的文案 key
_GIT_ERROR_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("permission denied", "authentication failed", "could not read username", "could not read password"), "plugin.git_auth_failed"),
    (("repository not found", "does not appear to be a git repository"), "plugin.git_repo_not_found"),
    (("could not resolve", "connection reset", "connection refused", "timed out", "unable to access", "network is unreachable"), "plugin.git_network_failed"),
    (("dubious ownership",), "plugin.git_dubious_ownership"),
    (("usage: git",), "plugin.git_too_old"),
)


def git_error_reason(result: subprocess.CompletedProcess) -> str:
    """git 非 0 退出转可读原因：识别常见 stderr 模式，未识别取 stderr 末行"""
    stderr = (result.stderr or "").strip()
    low = stderr.lower()
    for keywords, key in _GIT_ERROR_PATTERNS:
        if any(k in low for k in keywords):
            return t(key)
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    return lines[-1] if lines else t("plugin.git_exit", code=result.returncode)


def _manifest_name_conflict(manifest: PluginManifest) -> PluginManifest | None:
    target = Path(manifest.root).resolve()
    for installed in list_manifests():
        if Path(installed.root).resolve() == target:
            continue
        if installed.name == manifest.name:
            return installed
    return None


def _remove_tree(path: Path) -> None:
    """删除 git clone 目录；Windows 上 .git 内只读文件也要能回滚。"""
    if not path.exists():
        return

    def _clear_readonly(func, target, _exc_info):
        Path(target).chmod(stat.S_IWRITE)
        func(target)

    try:
        shutil.rmtree(path, onerror=_clear_readonly)
    except FileNotFoundError:
        pass


def install(url: str, branch: str | None = None, alias: str | None = None) -> tuple[PluginManifest | None, str]:
    """git clone 到插件目录并校验清单。指定 branch 时克隆该分支（git clone --branch）。
    返回 (清单, 错误串)；信任确认由菜单层负责"""
    if alias is not None and not valid_install_dir_name(alias):
        return None, f"bad_alias:{alias}"
    name = alias or dir_name_from_url(url)
    if not name:
        return None, "invalid url"
    dest = plugins_dir() / name
    if dest.exists():
        return None, f"exists:{name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [url, str(dest)]
    try:
        result = run(cmd, capture=True, check=False)
    except Exception as e:
        _remove_tree(dest)
        return None, str(e)
    if result.returncode != 0:
        _remove_tree(dest)
        return None, git_error_reason(result)
    manifest = load_manifest(dest)
    if manifest is None:
        _remove_tree(dest)
        return None, "no_manifest"
    conflict = _manifest_name_conflict(manifest)
    if conflict is not None:
        _remove_tree(dest)
        return None, f"manifest_name_exists:{manifest.name}:{conflict.root.name}"
    invalidate_service_cache()
    return manifest, ""


def rollback_install(manifest: PluginManifest) -> None:
    """用户拒绝信任时回滚删除刚安装的插件目录"""
    root = Path(manifest.root).resolve()
    if root.parent == plugins_dir().resolve():
        _remove_tree(root)


def update(manifest: PluginManifest) -> tuple[bool, str]:
    """git pull 更新插件目录（安装时指定过分支则固定 pull 该分支）。

    已信任插件经平台更新流程拉取的新内容自动继承信任（首次确认后
    更新不再重复询问）；以下情况不继承，交回菜单层处置：
      - tampered：内容与 CHECKSUMS.yaml 发布清单不符（可能被篡改）
      - downgrade：版本回退（防降级攻击，需用户显式确认）
    返回 (是否成功, "updated" / "unchanged" / "tampered" / "downgrade" / 错误串)。
    """
    root = Path(manifest.root)
    if not (root / ".git").exists():
        return False, "not_git"
    was_trusted = trust_status(manifest) == TRUST_OK
    branch = plugin_branch(manifest.name)
    pull_cmd = ["git", "pull", "--ff-only"]
    if branch:
        pull_cmd += ["origin", branch]
    try:
        head = run(["git", "rev-parse", "HEAD"], cwd=root, capture=True, check=False)
        if head.returncode != 0:
            return False, git_error_reason(head)
        before = head.stdout.strip()
        pulled = run(pull_cmd, cwd=root, capture=True, check=False)
        if pulled.returncode != 0:
            return False, git_error_reason(pulled)
        head = run(["git", "rev-parse", "HEAD"], cwd=root, capture=True, check=False)
        if head.returncode != 0:
            return False, git_error_reason(head)
        after = head.stdout.strip()
    except Exception as e:
        return False, str(e)
    if before == after:
        return True, "unchanged"
    refreshed = load_manifest(root)
    if refreshed is None:
        return True, "updated"
    if verify_checksums(root) == CHECK_MISMATCH:
        return True, "tampered"
    old_key, new_key = semver_key(manifest.version), semver_key(refreshed.version)
    if old_key is not None and new_key is not None and new_key < old_key:
        return True, "downgrade"
    if was_trusted:
        grant_trust(refreshed)
    invalidate_service_cache()
    return True, "updated"


def remove(manifest: PluginManifest) -> None:
    """删除插件目录、移除信任记录并清除进程内残留（立即生效）"""
    unload_plugin(manifest)
    _remove_tree(manifest.root)
    revoke(manifest.name)
    invalidate_service_cache()
