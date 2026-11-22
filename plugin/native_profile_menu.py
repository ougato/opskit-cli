"""Explicit native profile recovery; never offers uncertain creation intents."""
from core import windows_profile_recovery as recovery
from core.i18n import t
from core.prompt import select, confirm, pause, UserCancel, clear_screen, print_header
from core.sdk import waiting
from core.theme import print_info, print_error, print_success, get_icon


def entry():
    breadcrumb = ["OpsKit", t("menu.plugin"), t("plugin.manage"), t("plugin_native.maintenance"), t("plugin_native.profile_recovery.menu")]
    try:
        clear_screen()
        print_header(breadcrumb)
        with waiting(t("plugin_native.maintenance_scan")):
            records = recovery.inspect()
        confirmed = [item for item in records if item["valid"] and item["phase"] in {"created", "removed"}]
        if len(confirmed) != len(records):
            print_info(t("plugin_native.profile_recovery.uncertain"))
        if not confirmed:
            print_info(t("plugin_native.profile_recovery.empty"))
            pause()
            return
        key = select(breadcrumb, t("plugin_native.profile_recovery.select"),
                     [{"key": str(index + 1), "label": f"{get_icon('sandbox_cleanup')} {item['profile']}"}
                      for index, item in enumerate(confirmed)], back_label=f"{get_icon('back')} {t('plugin_native.back')}")
        if key is None or not key.isdecimal() or not 0 < int(key) <= len(confirmed):
            return
        item = confirmed[int(key) - 1]
        completed = item["phase"] == "removed"
        result_key = "completed_removed" if completed else "completed"
        if confirm(breadcrumb, t("confirm_ui.profile_record" if completed else "confirm_ui.profile"), info_lines=[
                t("confirm_ui.profile_record_detail" if completed else "confirm_ui.profile_detail"),
                t("plugin_native.profile_recovery.details", profile=item["profile"],
                  transaction=item["transaction"], digest=item["record_sha256"])]):
            clear_screen()
            print_header(breadcrumb)
            with waiting(t("confirm_ui.profile")):
                recovery.recover(item["transaction"], item["record_sha256"])
            print_success(t("plugin_native.profile_recovery." + result_key))
            pause()
    except (KeyboardInterrupt, UserCancel):
        return
    except (OSError, ValueError, RuntimeError):
        print_error(t("plugin_native.lifecycle.profile_recovery_failed"))
        pause()
