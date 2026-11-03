"""Official host capability inspection/revocation. Does not execute plugins."""
from __future__ import annotations

import typer

from core.cli import emit, fail, guarded
from core.plugin_capabilities import CapabilityAuthority, _key
from core.i18n import t


def register(parent, translate=None):
    translate = translate or (lambda key: t("plugin_native." + key))
    group = typer.Typer(help=translate("permissions_help"))
    parent.add_typer(group, name="permissions")

    @group.command("list", help=translate("list_help"))
    @guarded
    def list_permissions():
        from dataclasses import asdict
        authority = CapabilityAuthority()
        emit([{"id": _key(request), **asdict(request)} for request in authority.list_grants()])

    @group.command("revoke", help=translate("revoke_help"))
    @guarded
    def revoke_permission(grant_id: str = typer.Argument(..., help=translate("id_help"))):
        import re
        if not re.fullmatch(r"[0-9a-f]{64}", grant_id):
            fail(t("plugin_native.errors.invalid_id"), 2)
        authority = CapabilityAuthority()
        matches = [request for request in authority.list_grants() if _key(request) == grant_id]
        if len(matches) != 1:
            fail(t("plugin_native.errors.grant_not_found"), 2)
        authority.revoke(matches[0])
        emit({"revoked": grant_id})
