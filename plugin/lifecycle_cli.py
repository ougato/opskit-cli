"""Official plugin lifecycle commands, never plugin business arguments."""
import hmac
import re
import subprocess
from pathlib import Path

import typer
from rich.markup import escape

from core.cli import emit, fail, guarded
from core.i18n import t
from core.plugin_integrity import CHECK_MISMATCH, verify_checksums
from core.plugin_trust import compute_fingerprint, invalidate
from plugin import commands


def resolve(name):
    matches = [item for item in commands.manifests() if item.name == name]
    if len(matches) != 1:
        fail(t("plugin_native.lifecycle.not_found"), 2)
    return matches[0]


def describe(manifest):
    from core.constants import PLUGIN_API_VERSION
    return {"name": manifest.name, "version": manifest.version,
            "kind": manifest.kind, "api_version": manifest.api_version,
            "compatible": manifest.api_version == PLUGIN_API_VERSION,
            "permissions": manifest.permissions, "enabled": commands.is_enabled(manifest.name),
            "provides": manifest.provides, "service_contracts": manifest.service_contracts,
            "service_requirements": [{key: value for key, value in item.items() if key != "source"}
                                     for item in manifest.uses],
            "trust": commands.trust_status(manifest),
            "fingerprint": compute_fingerprint(manifest.root)}


def register(app, translate=None):
    translate = translate or (lambda key: t("plugin_native." + key))
    @app.command("disk-policy", help=translate("disk.help"))
    @guarded
    def disk_policy(minimum_free_mib: int = typer.Option(None, "--minimum-free-mib", help=translate("disk.minimum"))):
        from core.plugin_disk_policy import policy, configure
        try:
            value = policy() if minimum_free_mib is None else configure(minimum_free_mib)
            emit({**value, "hard_quota": False})
        except (OSError, ValueError, RuntimeError):
            fail(t("plugin_native.disk.invalid"), 2)

    def acquisition_error(message, default):
        # Only fixed host-generated translations are safe to show. Never render
        # raw process, repository, credential or filesystem diagnostics.
        keys = ("fetch_dependency_missing", "fetch_python_failed", "fetch_setup_failed", "runtime_setup_failed", "fetch_tools_missing", "fetch_tools_invalid", "acquisition_failed", "workspace_failed", "workspace_full", "workspace_busy",
                "update_failed", "update_modified", "execution_timeout", "platform_unsupported",
                "fetch_profile_invalid", "fetch_private_denied", "fetch_tls_failed", "fetch_protocol_failed", "fetch_transfer_failed",
                "fetch_ssh_host_failed", "fetch_ssh_auth_failed", "fetch_http_auth_failed", "fetch_ssh_connection_failed")
        safe = {t("plugin_native.errors." + key) for key in keys}
        safe.update(t("plugin_native.errors.platform_pending", platform=platform) for platform in ("linux", "darwin"))
        return message if message in safe else t("plugin_native.errors." + default)
    def state_command(name, caller, digest=None):
        from core.plugin_state import _binding
        from core.plugin_state_operator import state_operation
        from core.plugin_sandbox import CONTROL_TIMEOUT, _execution_budget, _remaining, _state_lock
        try:
            _binding(name, "platform" if caller is None else caller)
            if digest is not None and re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("invalid SHA256")
        except ValueError:
            fail(t("plugin_native.lifecycle.state_invalid"), 2)
        try:
            with _execution_budget():
                if digest is None:
                    result = state_operation(name, caller=caller, timeout=_remaining(CONTROL_TIMEOUT))
                else:
                    with _state_lock(name):
                        result = state_operation(name, caller=caller, digest=digest, timeout=_remaining(CONTROL_TIMEOUT))
        except (ValueError, OSError, RuntimeError):
            fail(t("plugin_native.lifecycle.state_failed"), 1)
        emit(result)

    @app.command("inspect-updates", help=translate("lifecycle.inspect_updates_help"))
    @guarded
    def inspect_updates(limit: int = typer.Option(256, "--limit", min=1, max=256)):
        from core.plugin_update_recovery import inspect
        try:
            emit(inspect(limit=limit))
        except (OSError, ValueError, RuntimeError):
            fail(t("plugin_native.lifecycle.update_recovery_failed"), 1)

    @app.command("recover-update", help=translate("lifecycle.recover_update_help"))
    @guarded
    def recover_update(transaction: str, record_sha256: str = typer.Option(..., "--record-sha256")):
        from core.plugin_update_recovery import recover
        try:
            emit(recover(transaction, record_sha256))
        except (OSError, ValueError, RuntimeError):
            fail(t("plugin_native.lifecycle.update_recovery_failed"), 1)

    @app.command("inspect-native-profiles", help=translate("lifecycle.inspect_profiles_help"))
    @guarded
    def inspect_native_profiles(limit: int = typer.Option(256, "--limit", min=1, max=256)):
        from core.windows_profile_recovery import inspect
        from core.native_sandbox import NativeSandboxError
        try:
            emit(inspect(limit=limit))
        except NativeSandboxError as error:
            fail(str(error), 1)
        except (OSError, ValueError, RuntimeError):
            fail(t("plugin_native.lifecycle.profile_recovery_failed"), 1)

    @app.command("recover-native-profile", help=translate("lifecycle.recover_profile_help"))
    @guarded
    def recover_native_profile(transaction: str, record_sha256: str = typer.Option(..., "--record-sha256")):
        from core.windows_profile_recovery import recover
        from core.native_sandbox import NativeSandboxError
        try:
            emit(recover(transaction, record_sha256))
        except NativeSandboxError as error:
            fail(str(error), 1)
        except (OSError, ValueError, RuntimeError):
            fail(t("plugin_native.lifecycle.profile_recovery_failed"), 1)

    @app.command("inspect-native-workspaces", help=translate("lifecycle.inspect_native_workspaces_help"))
    @guarded
    def inspect_native_workspaces(limit: int = typer.Option(256, "--limit", min=1, max=256)):
        from core.plugin_workspace_recovery import inspect
        try:
            emit(inspect(limit=limit))
        except (OSError, ValueError, RuntimeError):
            fail(t("plugin_native.lifecycle.workspace_recovery_failed"), 1)

    @app.command("recover-native-workspace", help=translate("lifecycle.recover_workspace_help"))
    @guarded
    def recover_native_workspace(transaction: str, record_sha256: str = typer.Option(..., "--record-sha256")):
        from core.plugin_workspace_recovery import recover
        try:
            emit(recover(transaction, record_sha256))
        except (OSError, ValueError, RuntimeError):
            fail(t("plugin_native.lifecycle.workspace_recovery_failed"), 1)

    @app.command("inspect-native-cache", help=translate("lifecycle.inspect_cache_help"))
    @guarded
    def inspect_native_cache(catalog: str, limit: int = typer.Option(256, "--limit", min=1, max=256)):
        from core.native_catalog_cleanup import inspect
        try:
            emit(inspect(catalog, limit=limit))
        except (OSError, ValueError, RuntimeError):
            fail(t("plugin_native.lifecycle.cache_cleanup_failed"), 1)

    @app.command("clean-native-cache", help=translate("lifecycle.clean_cache_help"))
    @guarded
    def clean_native_cache(catalog: str, object_id: str,
                           record_sha256: str = typer.Option(..., "--record-sha256")):
        from core.native_catalog_cleanup import collect
        try:
            emit(collect(catalog, object_id, record_sha256))
        except (OSError, ValueError, RuntimeError):
            fail(t("plugin_native.lifecycle.cache_cleanup_failed"), 1)

    @app.command("inspect-state", help=translate("lifecycle.inspect_state_help"))
    @guarded
    def inspect_state(name: str, caller: str = typer.Option(None, "--caller")):
        state_command(name, caller)

    @app.command("repair-state", help=translate("lifecycle.repair_state_help"))
    @guarded
    def repair_state(name: str, record_sha256: str = typer.Option(..., "--record-sha256"),
                     caller: str = typer.Option(None, "--caller")):
        state_command(name, caller, record_sha256)


    @app.command("inspect-workspaces", help=translate("lifecycle.inspect_workspaces_help"))
    @guarded
    def inspect_workspaces(limit: int = typer.Option(256, "--limit", min=1, max=256)):
        from core.plugin_workspace import inspect_workspaces as inspect
        from core.plugin_sandbox import CONTROL_TIMEOUT, _execution_budget, _remaining
        try:
            with _execution_budget():
                result = inspect(timeout=_remaining(CONTROL_TIMEOUT), limit=limit)
        except (ValueError, OSError, RuntimeError):
            fail(t("plugin_native.lifecycle.workspaces_failed"), 1)
        emit(result)


    @app.command("prepare-fetch", help=translate("prepare_fetch_help"))
    def prepare_fetch(python_runtime: Path = typer.Option(..., "--python-runtime", help=translate("python_runtime_help"))):
        from core.native_fetch_catalog import FetchCatalog
        try:
            identity = FetchCatalog().prepare(python_runtime)
        except ValueError:
            fail(t("plugin_native.errors.fetch_prepare_invalid"), 2)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            fail(t("plugin_native.errors.fetch_prepare_failed"))
        except KeyboardInterrupt:
            raise typer.Exit(130)
        emit({"toolchain": identity, "backend": "native", "prepared": True, "authorized": False})

    @app.command("prepare", help=translate("prepare_help"))
    def prepare(path: str = typer.Argument(..., help=translate("plugin_path_help")),
                dependency: list[str] = typer.Option([], "--dependency", help=translate("dependency_help")),
                python_runtime: Path = typer.Option(..., "--python-runtime", help=translate("python_runtime_help")),
                wheels: Path = typer.Option(..., "--wheels", help=translate("wheels_help")),
                lock_file: str = typer.Option(..., "--lock-file", help=translate("lock_file_help"))):
        from core.plugin_runtime import build
        root = Path(path)
        if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()) or not root.is_dir():
            fail(t("plugin_native.lifecycle.path_invalid"), 2)
        try:
            identity = build(root.resolve(), dependency, python_runtime,
                             wheels=wheels, lock_file=Path(lock_file))
        except ValueError:
            fail(t("plugin_native.errors.prepare_invalid"), 2)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            fail(t("plugin_native.errors.prepare_failed"))
        except KeyboardInterrupt:
            raise typer.Exit(130)
        emit({"runtime": identity, "backend": "native", "prepared": True, "authorized": False})

    @app.command("fingerprint", help=translate("lifecycle.fingerprint_help"))
    def fingerprint(path: str = typer.Argument("."), check: bool = typer.Option(False, "--check", "-c")):
        from core.plugin_integrity import CHECK_OK, verify_checksums, write_checksums
        root = Path(path)
        if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()) or not root.is_dir():
            fail(t("plugin_native.lifecycle.path_invalid"), 2)
        try:
            root = root.resolve()
            if check:
                status = verify_checksums(root)
                if status != CHECK_OK:
                    emit({"status": status, "checked": True},
                         error={"code": 1, "message": t("plugin_native.lifecycle.checksum_failed")})
                    raise typer.Exit(1)
                emit({"status": status, "checked": True})
            else:
                write_checksums(root)
                emit({"status": "written", "file": "CHECKSUMS.yaml"})
        except (ValueError, OSError):
            fail(t("plugin_native.lifecycle.checksum_operation_failed"))
        except KeyboardInterrupt:
            raise typer.Exit(130)

    @app.command("list", help=translate("lifecycle.list_help"))
    @guarded
    def list_plugins():
        emit([describe(item) for item in commands.manifests()])

    @app.command("show", help=translate("lifecycle.show_help"))
    @guarded
    def show(name: str):
        emit(describe(resolve(name)))

    @app.command("install", help=translate("lifecycle.install_help"))
    @guarded
    def install(url: str, branch: str = typer.Option("", "--branch", "-b"), alias: str = typer.Option("", "--as"),
                fetch_profile: str = typer.Option(None, "--fetch-profile", help=translate("fetch_profile_help")),
                allow_private: bool = typer.Option(False, "--allow-private", help=translate("allow_private_help"))):
        if url.startswith("-") or branch.startswith("-"):
            fail(t("plugin_native.lifecycle.repository_invalid"), 2)
        manifest, error = commands.install(url, branch or None, alias or None,
                                           fetch_profile=fetch_profile, allow_private=allow_private, automatic=True)
        if manifest is None:
            # Git output may include URLs and credentials; only expose stable categories.
            fail(acquisition_error(error, "acquisition_failed"))
        emit(describe(manifest))

    @app.command("update", help=translate("lifecycle.update_help"))
    @guarded
    def update(name: str,
               fetch_profile: str = typer.Option(None, "--fetch-profile", help=translate("fetch_profile_help")),
               allow_private: bool = typer.Option(False, "--allow-private", help=translate("allow_private_help"))):
        manifest = resolve(name)
        ok, status = commands.update(manifest, fetch_profile=fetch_profile, allow_private=allow_private)
        if not ok:
            fail(acquisition_error(status, "update_failed"))
        if status in {"tampered", "downgrade"}:
            fail(t("plugin_native.lifecycle.update_rejected", status=status))
        emit({"name": name, "status": status, "authorization_required": status != "unchanged"})

    @app.command("remove", help=translate("lifecycle.remove_help"))
    @guarded
    def remove(name: str):
        manifest = resolve(name)
        from core.prompt import confirm
        if not confirm(breadcrumb=["OpsKit", t("menu.plugin")], prompt=t("confirm_ui.uninstall"), info_lines=[escape(name), t("confirm_ui.plugin_delete")]):
            fail(t("plugin_native.lifecycle.remove_cancelled"))
        commands.remove(manifest)
        emit({"name": name, "removed": True, "state_preserved": True})

    @app.command("enable", help=translate("lifecycle.enable_help"))
    @guarded
    def enable(name: str):
        resolve(name)
        commands.set_enabled(name, True)
        emit({"name": name, "enabled": True})

    @app.command("disable", help=translate("lifecycle.disable_help"))
    @guarded
    def disable(name: str):
        resolve(name)
        commands.set_enabled(name, False)
        emit({"name": name, "enabled": False})

    @app.command("trust", help=translate("lifecycle.trust_help"))
    @guarded
    def trust(name: str, fingerprint: str = typer.Option(..., "--fingerprint")):
        manifest = resolve(name)
        from core.constants import PLUGIN_API_VERSION
        if manifest.api_version != PLUGIN_API_VERSION:
            fail(t("plugin_native.errors.sdk_required"), 2)
        actual = compute_fingerprint(manifest.root)
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or not actual or not hmac.compare_digest(fingerprint, actual):
            fail(t("plugin_native.lifecycle.fingerprint_mismatch"), 2)
        if verify_checksums(manifest.root) == CHECK_MISMATCH:
            fail(t("plugin_native.lifecycle.checksum_mismatch"))
        commands.grant_trust(manifest, expected_fingerprint=actual)
        emit(describe(manifest))

    @app.command("revoke", help=translate("lifecycle.revoke_help"))
    @guarded
    def revoke_trust(name: str):
        resolve(name)
        invalidate(name)
        emit({"name": name, "authorized": False})

    @app.command("authorize-service", help=translate("lifecycle.authorize_service_help"))
    @guarded
    def authorize_service(caller: str, service: str,
                          caller_fingerprint: str = typer.Option(..., "--caller-fingerprint"),
                          provider_fingerprint: str = typer.Option(..., "--provider-fingerprint")):
        from core.plugin_services import authorize_service as approve
        emit(approve(caller, service, caller_fingerprint, provider_fingerprint))

    @app.command("revoke-service", help=translate("lifecycle.revoke_service_help"))
    @guarded
    def revoke_service(caller: str, service: str):
        from core.plugin_trust import revoke_service as revoke_edge
        resolve(caller)
        revoke_edge(caller, service)
        emit({"caller": caller, "service": service, "authorized": False})
