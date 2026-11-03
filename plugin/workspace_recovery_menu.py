"""Explicit, identity-bound cleanup of abandoned native workspaces."""
from core import plugin_workspace_recovery as recovery
from core.i18n import t
from core.prompt import select, confirm, pause, UserCancel
from core.theme import print_info, print_error, print_success


def entry():
    breadcrumb = ["OpsKit", t("menu.plugin"), t("plugin_native.workspace_recovery.menu")]
    try:
        records = recovery.inspect()
        confirmed = [item for item in records if item["valid"]]
        if len(confirmed) != len(records):
            print_info(t("plugin_native.workspace_recovery.uncertain"))
        if not confirmed:
            print_info(t("plugin_native.workspace_recovery.empty"))
            pause()
            return
        key = select(breadcrumb, t("plugin_native.workspace_recovery.select"),
                     [{"key": str(index + 1), "label": item["directory"]}
                      for index, item in enumerate(confirmed)])
        if key is None or not key.isdecimal() or not 0 < int(key) <= len(confirmed):
            return
        item = confirmed[int(key) - 1]
        if confirm(breadcrumb, t("plugin_native.workspace_recovery.confirm"), info_lines=[
                t("plugin_native.workspace_recovery.details", directory=item["directory"],
                  transaction=item["transaction"], digest=item["record_sha256"])]):
            recovery.recover(item["transaction"], item["record_sha256"])
            print_success(t("plugin_native.workspace_recovery.completed"))
            pause()
    except (KeyboardInterrupt, UserCancel):
        return
    except (OSError, ValueError, RuntimeError):
        print_error(t("plugin_native.lifecycle.workspace_recovery_failed"))
        pause()
