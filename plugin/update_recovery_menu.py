"""Host-owned interrupted update inspection and explicit rollback confirmation."""
from rich.markup import escape

from core.i18n import t
from core import plugin_update_recovery as recovery
from core.prompt import select, confirm, pause, UserCancel
from core.theme import print_error, print_info, print_success, get_icon


def entry():
    breadcrumb = ["OpsKit", t("menu.plugin"), t("plugin.manage"), t("plugin_native.maintenance"), t("plugin_native.update_recovery.menu")]
    try:
        records = recovery.inspect()
        valid = [item for item in records if item["valid"]]
        if len(valid) != len(records):
            print_info(t("plugin_native.update_recovery.invalid"))
        if not valid:
            print_info(t("plugin_native.update_recovery.empty"))
            pause()
            return
        choices = [{"key": str(index + 1), "label": get_icon("restore") + " " + escape(item["plugin"]) + " · " + item["transaction"]}
                   for index, item in enumerate(valid)]
        key = select(breadcrumb, t("plugin_native.update_recovery.select"), choices, back_label=f"{get_icon('back')} {t('plugin_native.back')}")
        if key is None or not key.isdecimal() or not 0 < int(key) <= len(valid):
            return
        item = valid[int(key) - 1]
        details = [t("plugin_native.update_recovery.details", plugin=escape(item["plugin"]),
                     directory=escape(item["directory"]), transaction=item["transaction"],
                     digest=item["record_sha256"])]
        if confirm(breadcrumb, t("confirm_ui.rollback"), info_lines=[*details, t("confirm_ui.rollback_detail")]):
            recovery.recover(item["transaction"], item["record_sha256"])
            print_success(t("plugin_native.update_recovery.completed"))
            pause()
    except (KeyboardInterrupt, UserCancel):
        return
    except (OSError, ValueError, RuntimeError):
        print_error(t("plugin_native.lifecycle.update_recovery_failed"))
        pause()
