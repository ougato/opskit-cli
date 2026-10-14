"""Official software CLI orchestration; menu and CLI share software.actions."""
from contextlib import redirect_stdout
import re
import sys

import typer

from core.cli import emit, fail
from core.i18n import t
from core.recipe_utils import recipe_display_name as _recipe_name

def _is_multi_version(cls, instance):
    return bool(getattr(cls, "has_switch", False) and hasattr(instance, "installed_versions"))


def _validate_version(version):
    if version is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,127}", version):
        fail("invalid version identifier", 2)


def _resolve_deps_or_exit(instance, breadcrumb):
    from software.resolver import resolve_deps
    try:
        with redirect_stdout(sys.stderr):
            resolve_deps(instance, breadcrumb)
    except typer.Exit:
        raise
    except Exception as exc:
        fail(f"dependency resolution failed: {type(exc).__name__}")


def _check_disk_or_exit():
    from core.platform import check_disk_space
    from core.constants import MIN_DISK_FREE_BYTES
    if not check_disk_space(MIN_DISK_FREE_BYTES):
        fail(t("error.disk_space", required=f"{MIN_DISK_FREE_BYTES // 1024 // 1024}MB", free=""))


def _execute(cls, action, operation, *args, **kwargs):
    with redirect_stdout(sys.stderr):
        result = operation(*args, **kwargs)
    if isinstance(result.error, typer.Exit):
        raise result.error
    if not result.ok:
        # Installer exceptions can include command arguments and credentials.
        fail(f"{cls.key} {action} failed: {type(result.error).__name__}")
    emit({"software": cls.key, "action": action, "version": result.version,
          "elapsed": result.elapsed, "switched": result.switched})


def _install_direct(breadcrumb, cls, instance, version=None, token=None):
    name = _recipe_name(cls)
    _validate_version(version)
    if token is not None and (cls.key != "wg_client" or version is not None):
        fail("token requires wg_client and conflicts with --version", 2)
    if getattr(cls, "has_submenu", False):
        fail(t("cli.error.submenu_recipe", name=cls.key), 2)
    if cls.key == "wg_client" and token is not None:
        if not token.strip():
            fail("token is empty", 2)
        # Validate before resolving dependencies or creating any system files.
        from wireguard.token import decode_client_token
        try:
            decode_client_token(token)
        except ValueError:
            fail("invalid WireGuard token", 2)
        _check_disk_or_exit()
        _resolve_deps_or_exit(instance, breadcrumb)
        from wireguard.client import install_client
        with redirect_stdout(sys.stderr):
            result = install_client(token=token)
        if result is None:
            fail("WireGuard client installation did not complete")
        emit({"software": cls.key, "action": "install", **result})
        return
    if getattr(cls, "has_wizard", False):
        fail(t("cli.error.wizard_requires_interactive", name=name), 2)
    if version and not getattr(cls, "has_install_version_selection", True):
        fail(t("cli.error.version_not_supported", name=name), 2)
    if not version:
        if getattr(cls, "has_install_version_selection", True):
            fail(t("cli.error.version_required", name=name), 2)
        version = "latest"
    _check_disk_or_exit()
    _resolve_deps_or_exit(instance, breadcrumb)
    from software.actions import execute_install
    _execute(cls, "install", execute_install, instance, version)


def _uninstall_direct(cls, instance, version=None, all_versions=False):
    name = _recipe_name(cls)
    _validate_version(version)
    if getattr(cls, "has_submenu", False):
        fail(t("cli.error.submenu_recipe", name=cls.key), 2)
    if version and all_versions:
        fail(t("cli.error.version_all_conflict"), 2)
    if _is_multi_version(cls, instance):
        if not version and not all_versions:
            fail(t("cli.error.uninstall_version_required", name=name), 2)
        installed = instance.installed_versions()
        if not installed or version and version not in installed:
            fail(t("software.not_installed_hint", name=name))
        target = None if all_versions else version
    else:
        if version or all_versions:
            fail(t("cli.error.version_not_supported", name=name), 2)
        if not instance.detect():
            fail(t("software.not_installed_hint", name=name))
        target = None
    from core.prompt import confirm
    if not confirm(breadcrumb=["OpsKit", "software"], prompt=f"Uninstall {cls.key}?"):
        fail("software uninstall cancelled")
    from software.actions import execute_uninstall
    _execute(cls, "uninstall", execute_uninstall, instance, target)


def _upgrade_direct(breadcrumb, cls, instance, version=None):
    name = _recipe_name(cls)
    _validate_version(version)
    if not getattr(cls, "has_upgrade", True):
        fail(t("software.not_supported"), 2)
    if not version:
        fail(t("cli.error.version_required", name=name), 2)
    if not instance.detect():
        fail(t("software.not_installed_hint", name=name))
    _check_disk_or_exit()
    _resolve_deps_or_exit(instance, breadcrumb)
    from software.actions import execute_upgrade
    already = _is_multi_version(cls, instance) and version in instance.installed_versions()
    _execute(cls, "upgrade", execute_upgrade, instance, version, already_installed=already)


def _switch_direct(cls, instance, version=None):
    name = _recipe_name(cls)
    _validate_version(version)
    if not getattr(cls, "has_switch", False) or not hasattr(instance, "switch"):
        fail(t("software.not_supported"), 2)
    if not version:
        fail(t("cli.error.version_required", name=name), 2)
    if version not in instance.installed_versions():
        fail(t("software.not_installed_hint", name=f"{name} {version}"))
    from software.actions import execute_switch
    _execute(cls, "switch", execute_switch, instance, version)
