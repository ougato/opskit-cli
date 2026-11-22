"""插件管理命令 — 安装 / 更新 / 卸载，支持热插拔（纯业务，供 menu.py 调用）"""
from __future__ import annotations

import re
import hmac
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import urlparse

from core.config import load_config, set_config_value
from core.constants import PLUGIN_API_VERSION
from core.directory_publish import publish_new_directory
from core.plugin_install_receipt import content_receipt
from core.resource_lock import ResourceLock
from core.i18n import t
from core.loader import builtin_module_keys
from core.module import ModuleInfo
from core.paths import plugins_dir
from core.plugin import (
    PluginManifest,
    PluginInventoryError,
    list_manifests,
    load_manifest,
    load_plugin,
    _module_info,
)
from core.plugin_trust import compute_fingerprint, grant, is_trusted, revoke, trusted_record, invalidate

# 插件安装目录名：URL 路径分段转小写连字符，避免不同仓库同 basename 冲突
_URL_NAME_PATTERN = re.compile(r"([^/\\]+?)(?:\.git)?[/\\]?$")
_SCP_URL_PATTERN = re.compile(r"^[^@\s]+@[^:\s]+:(?P<path>.+)$")
_INSTALL_DIR_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,95}$")
_REMOTE_URL_SCHEMES = {"http", "https", "ssh", "git", "file"}


def _log_install_failure(phase: str, exc: Exception) -> None:
    """Record useful diagnostics without logging source URLs or exception data."""
    category = next((kind.__name__ for kind in (PermissionError, FileNotFoundError,
                    TimeoutError, OSError, ValueError, TypeError, RuntimeError)
                     if isinstance(exc, kind)), "unexpected")
    location = "unknown"
    traceback = exc.__traceback__
    while traceback is not None:
        module = traceback.tb_frame.f_globals.get("__name__", "")
        if module.startswith(("core.", "plugin.")):
            location = f"{module}:{traceback.tb_lineno}"
        traceback = traceback.tb_next
    from core.logger import get_logger
    logger = get_logger("opskit.plugin.install")
    logger.warning(
        "Plugin installation failed: phase=%s category=%s location=%s", phase, category, location)
    # Wrappers must not hide the allocator's fixed diagnostic fields. Never log
    # exception messages, paths or transport/credential material from a cause.
    from core.plugin_workspace import WorkspaceAllocationFailure
    cause, seen = exc, set()
    while cause is not None and id(cause) not in seen and len(seen) < 8:
        seen.add(id(cause))
        if isinstance(cause, WorkspaceAllocationFailure):
            diagnostic = cause.diagnostic
            logger.warning(
                "Workspace allocation failed: phase=%s stage=%s category=%s errno=%s winerror=%s",
                phase, diagnostic["stage"], diagnostic["category"], diagnostic["errno"], diagnostic["winerror"])
        cause = cause.__cause__


def _workspace_failure_message(exc):
    from core.plugin_sandbox import WorkspaceError
    if isinstance(exc, WorkspaceError):
        key = {"capacity": "workspace_full", "busy": "workspace_busy"}.get(exc.reason, "workspace_failed")
        return t("plugin_native.errors." + key)
    return None

_installation_lock = ResourceLock(lambda: plugins_dir() / ".installation.lock", label="plugin installation")


@dataclass
class _InstalledManifest(PluginManifest):
    """Process-local receipt; discovery alone never authorizes install rollback."""
    _installation_identity: tuple[int, int] = (0, 0)
    _installation_parent_identity: tuple[int, int] = (0, 0)
    _installation_content: str = ""

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
    from core.plugin_sandbox import _read_inventory
    return [(manifest, _module_info(manifest))
            for manifest in _read_inventory(plugins_dir(), select="menu")]


def _loaded_plugins(manifest_list) -> list[tuple[PluginManifest, ModuleInfo]]:
    """Filter an already collected inventory without executing plugin code."""
    if not manifest_list:
        return []
    from core.plugin_trust import authorization_snapshot
    builtin = builtin_module_keys()
    pairs: list[tuple[PluginManifest, ModuleInfo]] = []
    seen: set[str] = set()
    config = None
    # These snapshots only control display; invocation rechecks live authority.
    with authorization_snapshot():
        for manifest in manifest_list:
            if manifest.name in builtin or manifest.name in seen:
                continue
            if config is None:
                config = load_config()
            if not _enabled_in_config(config, manifest.name):
                continue
            info = load_plugin(manifest)
            if info is None:
                continue
            seen.add(manifest.name)
            pairs.append((manifest, info))
    pairs.sort(key=lambda p: p[1].order)
    return pairs


def is_enabled(key: str) -> bool:
    return _enabled_in_config(load_config(), key)


def _enabled_in_config(config: dict, key: str) -> bool:
    return bool(config.get("modules", {}).get(key, {}).get("enabled", True))


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
    scp_match = _SCP_URL_PATTERN.match(raw) if "://" not in raw else None
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
    """插件安装时记录的分支，更新时据此重新采集；未记录返回 None。"""
    record = trusted_record(name)
    return record.get("branch") if record else None


def trust_status(manifest: PluginManifest) -> str:
    """插件信任状态：trusted / untrusted / changed"""
    if is_trusted(manifest.name, compute_fingerprint(manifest.root)):
        return TRUST_OK
    if trusted_record(manifest.name) is not None:
        return TRUST_CHANGED
    return TRUST_NONE


def grant_trust(manifest: PluginManifest, source: str = "", branch: str | None = None, *,
                expected_fingerprint: str | None = None) -> None:
    """记录用户对插件当前内容的信任（branch 为 None 时沿用已存分支）"""
    if manifest.api_version != PLUGIN_API_VERSION:
        raise ValueError(t("plugin_native.errors.sdk_required"))
    record = trusted_record(manifest.name)
    if record is not None:
        stored_source = str(record.get("source", ""))
        if record.get("installation_receipt"):
            from core.native_acquisition import source_identity
            if (source and source_identity(source)[0] != stored_source
                    or branch is not None and branch != record.get("branch")):
                raise ValueError(t("plugin_native.lifecycle.repository_invalid"))
            source = stored_source
        elif not source:
            source = stored_source
    fingerprint = compute_fingerprint(manifest.root)
    if (not fingerprint or expected_fingerprint is not None
            and not hmac.compare_digest(fingerprint, expected_fingerprint)):
        raise ValueError(t("plugin_native.lifecycle.fingerprint_mismatch"))
    grant(manifest.name, fingerprint, manifest.version, source, branch)
    if expected_fingerprint is not None and compute_fingerprint(manifest.root) != fingerprint:
        invalidate(manifest.name)
        raise ValueError(t("plugin_native.lifecycle.changed"))


def is_trusted_source(url: str) -> bool:
    """URL 主机是否在配置的可信源白名单中（plugin.trusted_sources）"""
    cfg = load_config()
    sources = cfg.get("plugin", {}).get("trusted_sources", []) or []
    host = urlparse(url.strip()).hostname
    if not host and "@" in url:  # scp 形式 git@host:path
        host = url.split("@", 1)[1].split(":", 1)[0]
    return bool(host) and host in [str(s) for s in sources]


def _manifest_name_conflict(manifest: PluginManifest) -> PluginManifest | None:
    target = Path(manifest.root).resolve()
    for installed in list_manifests():
        if Path(installed.root).resolve() == target:
            continue
        if installed.name == manifest.name:
            return installed
    return None


def _remove_tree(path: Path) -> None:
    """移除调用方拥有的暂存树，兼容 Windows 旧安装中的只读文件。"""
    if not path.exists():
        return

    def _clear_readonly(func, target, _exc_info):
        Path(target).chmod(stat.S_IWRITE)
        func(target)

    try:
        shutil.rmtree(path, onerror=_clear_readonly)
    except FileNotFoundError:
        pass


def install(url: str, branch: str | None = None, alias: str | None = None, *,
            fetch_profile: str | None = None, allow_private: bool = False, fetch_interaction=None,
            automatic: bool = False, progress=None, review_candidate=None) -> tuple[PluginManifest | None, str]:
    """原生隔离采集已提交内容，审核后发布；不执行宿主 Git。
    返回 (清单, 错误串)；信任确认由菜单层负责"""
    if alias is not None and not valid_install_dir_name(alias):
        return None, f"bad_alias:{alias}"
    name = alias or dir_name_from_url(url)
    if not name:
        return None, "invalid url"
    dest = plugins_dir() / name
    if dest.exists() or dest.is_symlink():
        return None, f"exists:{name}"
    for parent in (dest.parent, *dest.parent.parents):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            return None, "plugin installation parent must not be a link"
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".opskit-install-", dir=dest.parent))
    owned = work.lstat()
    staging = work / "payload"
    phase = "preparation"
    try:
        from core.native_acquisition import populate
        from core.native_sandbox import NativeSandboxError
        if automatic:
            from core.plugin_bootstrap import ensure_fetch
            try:
                ensure_fetch(progress or (lambda *args: None))
            except NativeSandboxError:
                raise
            except Exception:
                raise NativeSandboxError("fetch_setup_failed") from None
        if progress:
            progress("fetch", branch or "")
        phase = "acquisition"
        acquired = populate(url, staging, branch=branch, fetch_profile=fetch_profile, allow_private=allow_private, fetch_interaction=fetch_interaction)
        phase = "validation"
        manifest = load_manifest(staging)
        if manifest is None:
            return None, "no_manifest"
        if manifest.name in builtin_module_keys():
            return None, "official_name_conflict"
        if review_candidate is not None:
            if (content_receipt(staging) != acquired.receipt
                    or compute_fingerprint(staging) != acquired.fingerprint):
                raise ValueError("installation candidate changed before review")
            if not review_candidate(manifest, acquired.fingerprint):
                return None, "declined"
        # Acquire independently; serialize identity validation and publication.
        # Discovery never sees a partial or unreviewed candidate.
        with _installation_lock.hold():
            try:
                conflict = _manifest_name_conflict(manifest)
            except PluginInventoryError:
                return None, "plugin inventory scan failed"
            if conflict is not None:
                return None, f"manifest_name_exists:{manifest.name}:{conflict.root.name}"
            if (content_receipt(staging) != acquired.receipt
                    or compute_fingerprint(staging) != acquired.fingerprint):
                raise ValueError("installation candidate changed before publication")
            if automatic:
                from core.plugin_bootstrap import ensure_runtime
                try:
                    ensure_runtime(manifest, progress or (lambda *args: None))
                except NativeSandboxError:
                    raise
                except Exception:
                    raise NativeSandboxError("runtime_setup_failed") from None
            # Fail closed before making content discoverable, including stale trust
            # from an earlier installation of the same name. This grants nothing.
            phase = "publication"
            grant(manifest.name, "", manifest.version, acquired.source, branch,
                  installation_receipt=acquired.receipt, source_commit=acquired.commit)
            payload_identity = staging.lstat()
            parent_identity = dest.parent.lstat()
            payload_content = content_receipt(staging)
            if payload_content != acquired.receipt:
                raise ValueError("installation candidate changed during trust invalidation")
            publish_new_directory(staging, dest)
        values = {field.name: getattr(manifest, field.name) for field in fields(PluginManifest)}
        values["root"] = dest
        return _InstalledManifest(**values,
            _installation_identity=(payload_identity.st_dev, payload_identity.st_ino),
            _installation_parent_identity=(parent_identity.st_dev, parent_identity.st_ino),
            _installation_content=payload_content), ""
    except FileExistsError:
        return None, f"exists:{name}"
    except Exception as exc:
        from core.plugin_capabilities import CapabilityDenied
        from core.prompt import UserCancel
        if isinstance(exc, (UserCancel, CapabilityDenied)):
            raise
        from core.native_sandbox import NativeSandboxError
        if isinstance(exc, NativeSandboxError):
            return None, str(exc)
        _log_install_failure(phase, exc)
        workspace_error = _workspace_failure_message(exc)
        if workspace_error is not None:
            return None, workspace_error
        return None, t("plugin_native.errors.acquisition_failed")
    finally:
        # Never roll back dest: it may belong to a concurrent writer. The private
        # wrapper is our only cleanup target; replacement means retain and report.
        current = work.lstat()
        if ((current.st_dev, current.st_ino) != (owned.st_dev, owned.st_ino)
                or not stat.S_ISDIR(current.st_mode)
                or getattr(current, "st_file_attributes", 0) & 0x400):
            raise RuntimeError("plugin installation staging identity changed; cleanup refused")
        _remove_tree(work)


def rollback_install(manifest: PluginManifest) -> None:
    """Withdraw only the directory bound to this successful install receipt."""
    if not isinstance(manifest, _InstalledManifest):
        raise ValueError("installation receipt required; rollback refused")
    root = Path(manifest.root).absolute()
    if root.parent != plugins_dir().absolute():
        raise ValueError("plugin installation root changed; rollback refused")
    with _installation_lock.hold():
        parent = root.parent.lstat()
        current = root.lstat()
        if ((parent.st_dev, parent.st_ino) != manifest._installation_parent_identity
                or (current.st_dev, current.st_ino) != manifest._installation_identity
                or not stat.S_ISDIR(current.st_mode)
                or getattr(current, "st_file_attributes", 0) & 0x400):
            raise ValueError("plugin installation identity changed; rollback refused")
        if content_receipt(root) != manifest._installation_content:
            raise ValueError("plugin installation content changed; rollback refused")
        wrapper = Path(tempfile.mkdtemp(prefix=".opskit-rollback-", dir=root.parent))
        withdrawn = wrapper / "payload"
        try:
            # Bind the object actually moved, not merely the prior path lookup.
            publish_new_directory(root, withdrawn)
            moved = withdrawn.lstat()
            identity_matches = ((moved.st_dev, moved.st_ino) == manifest._installation_identity
                                and stat.S_ISDIR(moved.st_mode)
                                and not getattr(moved, "st_file_attributes", 0) & 0x400)
            try:
                content_matches = identity_matches and content_receipt(withdrawn) == manifest._installation_content
            except (OSError, ValueError, RuntimeError):
                content_matches = False
            if not identity_matches or not content_matches:
                try:
                    publish_new_directory(withdrawn, root)
                except OSError as exc:
                    raise RuntimeError("rollback identity changed; moved object retained at " + str(withdrawn)) from exc
                raise ValueError("plugin installation identity changed; replacement restored")
            _remove_tree(withdrawn)
        finally:
            # An unexpected object or failed cleanup remains available for recovery.
            if not any(wrapper.iterdir()):
                wrapper.rmdir()


def update(manifest: PluginManifest, *, fetch_profile: str | None = None,
           allow_private: bool = False, fetch_interaction=None, automatic: bool = False,
           progress=None) -> tuple[bool, str]:
    """Exclude active invocations before reading or changing plugin sources."""
    from core.plugin_invocation_lock import hold
    if progress is not None:
        progress("update_wait")
    try:
        with hold(plugin=manifest.name, timeout=30):
            return _update_locked(manifest, fetch_profile=fetch_profile, allow_private=allow_private,
                                  fetch_interaction=fetch_interaction, automatic=automatic, progress=progress)
    except (OSError, ValueError, RuntimeError) as exc:
        _log_install_failure("update_guard", exc)
        from core.resource_lock import ResourceBusyError
        if isinstance(exc, ResourceBusyError):
            return False, t("plugin_native.lifecycle.plugin_busy")
        return False, str(exc)


def _update_locked(manifest: PluginManifest, *, fetch_profile: str | None = None,
                   allow_private: bool = False, fetch_interaction=None, automatic: bool = False,
                   progress=None) -> tuple[bool, str]:
    """Publish a verified update and advance trust for the same installation."""
    from core.native_acquisition import populate
    from core.native_sandbox import NativeSandboxError
    from core.plugin_state import _directory
    from core.plugin_trust import authorization_transaction, authorization_snapshot
    if progress is not None:
        progress("update_check")
    root = Path(manifest.root).absolute()
    if root.parent != plugins_dir().absolute():
        raise ValueError("update must target a direct installed plugin")
    initial = _directory(root)
    for parent in root.parents:
        _directory(parent)
    record = trusted_record(manifest.name)
    if not record or not record.get("source"):
        return False, "not_git"
    before = content_receipt(root)
    if record.get("installation_receipt") != before:
        return False, t("plugin_native.errors.update_modified")
    current = load_manifest(root)
    if current is None or current.name != manifest.name:
        return False, t("plugin_native.errors.update_modified")
    current_fingerprint = compute_fingerprint(root)
    if not current_fingerprint or record.get("fingerprint") != current_fingerprint:
        return False, t("plugin_native.errors.update_trust_required")
    work = root.parent / (".opskit-update-" + uuid.uuid4().hex)
    work.mkdir(mode=0o700)
    owned = _directory(work)
    payload, previous = work / "payload", work / "previous"
    try:
        from core.plugin_provider_install import provider_update_branch
        branch = provider_update_branch(manifest.name, record["source"], record.get("branch"))
        if progress is not None:
            progress("update_fetch")
        acquired = populate(record["source"], payload, branch=branch,
                            fetch_profile=fetch_profile, allow_private=allow_private, fetch_interaction=fetch_interaction,
                            copy_progress=getattr(progress, "copy_files", None),
                            download_progress=getattr(progress, "download_bytes", None))
        if progress is not None:
            progress("update_verify")
        refreshed = load_manifest(payload)
        if refreshed is None or refreshed.name != manifest.name:
            raise ValueError("update plugin identity changed")
        if acquired.source != record["source"]:
            raise ValueError("update source identity changed")
        old_key, new_key = semver_key(current.version), semver_key(refreshed.version)
        if old_key is not None and new_key is not None and new_key < old_key:
            return False, t("plugin_native.errors.update_downgrade_blocked")
        if _directory(root) != initial or content_receipt(root) != before:
            raise ValueError("installed plugin changed during acquisition")
        if before == acquired.receipt:
            if automatic:
                if progress is not None:
                    progress("runtime")
                from core.plugin_runtime_manager import prepare_update
                prepare_update(manifest, before)
            return True, "unchanged"
        if progress is not None:
            progress("update_publish")
        replacement = _directory(payload)
        with _installation_lock.hold(), authorization_transaction():
            with authorization_snapshot():
                if trusted_record(manifest.name) != record:
                    raise ValueError("installation authority changed during acquisition")
            if (_directory(root) != initial or content_receipt(root) != before
                    or content_receipt(payload) != acquired.receipt):
                raise ValueError("update content changed before publication")
            # An inherited approval still pins the old bytes until publication
            # succeeds. The invocation lock excludes execution during replacement.
            from core.plugin_update_recovery import record as record_recovery
            record_recovery(work, plugin=manifest.name, root=root, old=initial, candidate=replacement,
                            old_receipt=before, candidate_receipt=acquired.receipt, installation=record)
            published = False
            try:
                publish_new_directory(root, previous)
                if _directory(previous) != initial or content_receipt(previous) != before:
                    raise ValueError("update withdrawal identity changed")
                publish_new_directory(payload, root)
                published = True
                if _directory(root) != replacement or content_receipt(root) != acquired.receipt:
                    raise ValueError("update publication identity changed")
                if automatic:
                    if progress is not None:
                        progress("runtime")
                    from core.plugin_runtime_manager import prepare_update
                    installed = load_manifest(root)
                    if installed is None or installed.name != manifest.name:
                        raise ValueError("updated plugin manifest is invalid")
                    prepare_update(installed, acquired.receipt)
                fingerprint = compute_fingerprint(root)
                if not fingerprint or fingerprint != acquired.fingerprint:
                    raise ValueError("authenticated update fingerprint changed")
                from core.plugin_capabilities import CapabilityAuthority
                with CapabilityAuthority().inherit_directory_grants(manifest.name, current_fingerprint, fingerprint):
                    grant(manifest.name, fingerprint, refreshed.version, acquired.source, branch,
                          installation_receipt=acquired.receipt, source_commit=acquired.commit,
                          verified_update=(current, refreshed, current_fingerprint))
            except BaseException:
                # Only withdraw the exact newly published object. Never overwrite
                # an independent writer, and retain old bytes if restoration fails.
                if published:
                    if _directory(root) != replacement or content_receipt(root) != acquired.receipt:
                        raise RuntimeError("changed update retained for recovery") from None
                    publish_new_directory(root, work / "failed-candidate")
                if previous.exists():
                    if _directory(previous) != initial or content_receipt(previous) != before:
                        raise RuntimeError("changed update backup retained for recovery") from None
                    publish_new_directory(previous, root)
                raise
        _remove_tree(previous)
        return True, "updated"
    except Exception as exc:
        if not isinstance(exc, NativeSandboxError):
            _log_install_failure("update", exc)
        return False, (str(exc) if isinstance(exc, NativeSandboxError) else
                       _workspace_failure_message(exc) or t("plugin_native.errors.update_failed"))
    finally:
        if _directory(work) != owned:
            raise RuntimeError("update staging identity changed; cleanup refused")
        # A failed restoration leaves its backup and evidence for recovery.
        if not previous.exists():
            _remove_tree(work)


def remove(manifest: PluginManifest) -> None:
    """Exclude active invocations through source removal and trust revocation."""
    from core.plugin_invocation_lock import hold
    with hold(plugin=manifest.name, timeout=30):
        _remove_locked(manifest)


def _remove_locked(manifest: PluginManifest) -> None:
    """删除插件目录、移除信任记录并清除进程内残留（立即生效）"""
    root = Path(manifest.root)
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()) or root.resolve().parent != plugins_dir().resolve():
        raise ValueError("plugin removal must target a direct directory inside plugins")
    _remove_tree(manifest.root)
    revoke(manifest.name)
