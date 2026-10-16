"""Official plugin lifecycle commands, never plugin business arguments."""
import hmac
import re
import subprocess
from pathlib import Path

import typer

from core.cli import emit, fail, guarded
from core.plugin_integrity import CHECK_MISMATCH, verify_checksums
from core.plugin_trust import compute_fingerprint, invalidate
from plugin import commands


def resolve(name):
    matches = [item for item in commands.manifests() if item.name == name]
    if len(matches) != 1:
        fail("plugin not found or name ambiguous", 2)
    return matches[0]


def describe(manifest):
    return {"name": manifest.name, "version": manifest.version,
            "kind": manifest.kind, "api_version": manifest.api_version,
            "permissions": manifest.permissions, "enabled": commands.is_enabled(manifest.name),
            "provides": manifest.provides, "service_contracts": manifest.service_contracts,
            "service_requirements": [{key: value for key, value in item.items() if key != "source"}
                                     for item in manifest.uses],
            "trust": commands.trust_status(manifest),
            "fingerprint": compute_fingerprint(manifest.root)}


def register(app):
    def state_command(name, caller, digest=None):
        from core.plugin_state import _binding
        from core.plugin_state_operator import state_operation
        from core.plugin_sandbox import CONTROL_TIMEOUT, _execution_budget, _remaining, _state_lock
        try:
            _binding(name, "platform" if caller is None else caller)
            if digest is not None and re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("invalid SHA256")
        except ValueError:
            fail("invalid plugin, caller or record SHA256", 2)
        try:
            with _execution_budget():
                if digest is None:
                    result = state_operation(name, caller=caller, timeout=_remaining(CONTROL_TIMEOUT))
                else:
                    with _state_lock(name):
                        result = state_operation(name, caller=caller, digest=digest, timeout=_remaining(CONTROL_TIMEOUT))
        except (ValueError, OSError, RuntimeError):
            fail("state operation unconfirmed; inspect before retry", 1)
        emit(result)

    @app.command("inspect-state", help="Inspect transaction metadata; omitted caller selects self, explicit caller selects a service partition")
    @guarded
    def inspect_state(name: str, caller: str = typer.Option(None, "--caller")):
        state_command(name, caller)

    @app.command("repair-state", help="Repair a torn transaction tail matching the observed SHA256; preserve original evidence")
    @guarded
    def repair_state(name: str, record_sha256: str = typer.Option(..., "--record-sha256"),
                     caller: str = typer.Option(None, "--caller")):
        state_command(name, caller, record_sha256)

    @app.command("inspect-locks", help="Inspect bounded resource metadata without changing files; absence of a journal is not retirement permission")
    @guarded
    def inspect_locks(limit: int = typer.Option(256, "--limit", min=1, max=256)):
        from core.plugin_resource_io import inspect_lock_metadata
        from core.plugin_sandbox import CONTROL_TIMEOUT, _execution_budget, _remaining
        try:
            with _execution_budget():
                result = inspect_lock_metadata(timeout=_remaining(CONTROL_TIMEOUT), limit=limit)
        except (ValueError, OSError, RuntimeError):
            fail("resource metadata inspection unconfirmed; no retirement performed", 1)
        emit(result)

    @app.command("inspect-workspaces", help="Observe bounded workspace entries without reading contents; observations do not establish ownership or authorize deletion")
    @guarded
    def inspect_workspaces(limit: int = typer.Option(256, "--limit", min=1, max=256)):
        from core.plugin_workspace import inspect_workspaces as inspect
        from core.plugin_sandbox import CONTROL_TIMEOUT, _execution_budget, _remaining
        try:
            with _execution_budget():
                result = inspect(timeout=_remaining(CONTROL_TIMEOUT), limit=limit)
        except (ValueError, OSError, RuntimeError):
            fail("workspace inspection unconfirmed; no cleanup performed", 1)
        emit(result)

    @app.command("retire-lock", help="Retire one inactive resource metadata lock by its exact token; retain journal evidence")
    @guarded
    def retire_lock(token: str):
        if re.fullmatch(r"[0-9a-f]{32}", token) is None:
            fail("resource token must be 32 lowercase hexadecimal characters", 2)
        from core.plugin_resource_io import retire_orphan_lock
        from core.plugin_sandbox import CONTROL_TIMEOUT, _execution_budget, _remaining
        try:
            with _execution_budget():
                status = retire_orphan_lock(token, timeout=_remaining(CONTROL_TIMEOUT))
        except (ValueError, OSError, RuntimeError):
            fail("resource metadata retirement unconfirmed; inspect before retry", 1)
        result = {"token": token, "status": status}
        if status == "retained":
            emit(result, error={"code": 1, "message": "resource metadata retained; journal or active lock requires recovery"})
            raise typer.Exit(1)
        emit(result)

    @app.command("reconcile", help="Recover inactive, owned sandbox resources")
    @guarded
    def reconcile():
        from core.plugin_resources import reconcile as recover
        from core.plugin_sandbox import _control, _execution_budget, docker_command
        with _execution_budget():
            command = docker_command()
            result = recover(command, lambda *args: _control(command, *args))
        if result["pending"] or result["invalid"]:
            emit(result, error={"code": 1, "message": "sandbox resource recovery incomplete"})
            raise typer.Exit(1)
        emit(result)

    @app.command("prepare-fetch", help="Prepare the platform's isolated Git/SSH tool from reviewed offline packages")
    def prepare_fetch(packages: Path = typer.Option(..., "--packages")):
        from core.plugin_fetch import prepare_tool
        try:
            image = prepare_tool(packages)
        except ValueError:
            fail("invalid fetch preparation inputs or tool identity", 2)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            fail("isolated fetch preparation failed")
        except KeyboardInterrupt:
            raise typer.Exit(130)
        emit({"image": image, "prepared": True, "authorized": False})

    @app.command("prepare", help="Build an isolated plugin runtime with operator-approved pinned dependencies")
    def prepare(path: str, dependency: list[str] = typer.Option([], "--dependency"),
                base_image: str = typer.Option(..., "--base-image"),
                lock_file: str = typer.Option(..., "--lock-file")):
        from core.plugin_runtime import build
        root = Path(path)
        if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()) or not root.is_dir():
            fail("plugin path must be an ordinary directory", 2)
        try:
            image = build(root.resolve(), dependency, base_image, lock_file=Path(lock_file))
        except ValueError:
            fail("invalid plugin manifest or runtime parameters", 2)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            # Docker/pip errors can contain credentials, URLs or private paths.
            fail("plugin runtime preparation failed")
        except KeyboardInterrupt:
            raise typer.Exit(130)
        emit({"image": image, "prepared": True, "authorized": False})

    @app.command("fingerprint", help="Generate or verify a plugin CHECKSUMS.yaml manifest")
    def fingerprint(path: str = typer.Argument("."), check: bool = typer.Option(False, "--check", "-c")):
        from core.plugin_integrity import CHECK_OK, verify_checksums, write_checksums
        root = Path(path)
        if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()) or not root.is_dir():
            fail("plugin path must be an ordinary directory", 2)
        try:
            root = root.resolve()
            if check:
                status = verify_checksums(root)
                if status != CHECK_OK:
                    emit({"status": status, "checked": True},
                         error={"code": 1, "message": "plugin checksum verification failed"})
                    raise typer.Exit(1)
                emit({"status": status, "checked": True})
            else:
                write_checksums(root)
                emit({"status": "written", "file": "CHECKSUMS.yaml"})
        except (ValueError, OSError):
            fail("plugin checksum operation failed")
        except KeyboardInterrupt:
            raise typer.Exit(130)

    @app.command("list")
    @guarded
    def list_plugins():
        emit([describe(item) for item in commands.manifests()])

    @app.command("show")
    @guarded
    def show(name: str):
        emit(describe(resolve(name)))

    @app.command("install")
    @guarded
    def install(url: str, branch: str = typer.Option("", "--branch", "-b"), alias: str = typer.Option("", "--as")):
        if url.startswith("-") or branch.startswith("-"):
            fail("invalid repository or branch", 2)
        manifest, error = commands.install(url, branch or None, alias or None)
        if manifest is None:
            # Git output may include URLs and credentials; only expose stable categories.
            fail(error if error in {"no_manifest", "official_name_conflict", "invalid url"} else "plugin installation failed")
        emit(describe(manifest))

    @app.command("update")
    @guarded
    def update(name: str):
        manifest = resolve(name)
        ok, status = commands.update(manifest)
        if not ok:
            fail("plugin update failed")
        if status in {"tampered", "downgrade"}:
            fail("plugin update rejected: " + status)
        emit({"name": name, "status": status, "authorization_required": status != "unchanged"})

    @app.command("remove")
    @guarded
    def remove(name: str):
        manifest = resolve(name)
        from core.prompt import confirm
        if not confirm(breadcrumb=["OpsKit", "plugin"], prompt="Remove plugin " + name + "? (state is preserved)"):
            fail("plugin removal cancelled")
        commands.remove(manifest)
        emit({"name": name, "removed": True, "state_preserved": True})

    @app.command("enable")
    @guarded
    def enable(name: str):
        resolve(name)
        commands.set_enabled(name, True)
        emit({"name": name, "enabled": True})

    @app.command("disable")
    @guarded
    def disable(name: str):
        resolve(name)
        commands.set_enabled(name, False)
        emit({"name": name, "enabled": False})

    @app.command("trust")
    @guarded
    def trust(name: str, fingerprint: str = typer.Option(..., "--fingerprint")):
        manifest = resolve(name)
        actual = compute_fingerprint(manifest.root)
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or not actual or not hmac.compare_digest(fingerprint, actual):
            fail("fingerprint does not match current plugin content", 2)
        if verify_checksums(manifest.root) == CHECK_MISMATCH:
            fail("plugin checksum mismatch")
        commands.grant_trust(manifest)
        # Recheck after the persisted grant to reject concurrent content changes.
        if compute_fingerprint(manifest.root) != actual:
            invalidate(name)
            fail("plugin changed during authorization")
        emit(describe(manifest))

    @app.command("revoke")
    @guarded
    def revoke_trust(name: str):
        resolve(name)
        invalidate(name)
        emit({"name": name, "authorized": False})

    @app.command("authorize-service", help="Approve the caller's exact declared service contract")
    @guarded
    def authorize_service(caller: str, service: str,
                          caller_fingerprint: str = typer.Option(..., "--caller-fingerprint"),
                          provider_fingerprint: str = typer.Option(..., "--provider-fingerprint")):
        from core.plugin_services import authorize_service as approve
        emit(approve(caller, service, caller_fingerprint, provider_fingerprint))

    @app.command("revoke-service", help="Revoke one service edge while preserving independent plugin trust")
    @guarded
    def revoke_service(caller: str, service: str):
        from core.plugin_trust import revoke_service as revoke_edge
        resolve(caller)
        revoke_edge(caller, service)
        emit({"caller": caller, "service": service, "authorized": False})
