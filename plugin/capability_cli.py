"""Official host capability inspection and revocation."""
from __future__ import annotations

from dataclasses import asdict
import re

import typer

from core.cli import emit, fail, guarded
from core.i18n import t
from core.plugin_capabilities import CapabilityAuthority, _key


def _describe(request):
    return {"id": _key(request), **asdict(request)}


def _resolve(authority, grant_id):
    matches = [request for request in authority.list_grants() if _key(request) == grant_id]
    if len(matches) != 1:
        fail(t("plugin_native.errors.grant_not_found"), 2)
    return matches[0]


def register(parent, translate=None):
    translate = translate or (lambda key: t("plugin_native." + key))
    group = typer.Typer(help=translate("permissions_help"))
    parent.add_typer(group, name="permissions")

    def validate_id(grant_id):
        if not re.fullmatch(r"[0-9a-f]{64}", grant_id):
            fail(t("plugin_native.errors.invalid_id"), 2)

    @group.command("list", help=translate("list_help"))
    @guarded
    def list_permissions(plugin: str = typer.Option(None, "--plugin", help=translate("permissions_plugin_help"))):
        authority = CapabilityAuthority()
        try:
            emit([_describe(request) for request in authority.list_grants()
                  if plugin is None or request.plugin == plugin])
        finally:
            authority.close()

    @group.command("show", help=translate("permissions_show_help"))
    @guarded
    def show_permission(grant_id: str = typer.Argument(..., help=translate("id_help"))):
        validate_id(grant_id)
        authority = CapabilityAuthority()
        try:
            emit(_describe(_resolve(authority, grant_id)))
        finally:
            authority.close()

    @group.command("revoke", help=translate("revoke_help"))
    @guarded
    def revoke_permission(grant_id: str = typer.Argument(..., help=translate("id_help"))):
        validate_id(grant_id)
        authority = CapabilityAuthority()
        try:
            authority.revoke(_resolve(authority, grant_id))
            emit({"revoked": grant_id})
        finally:
            authority.close()
