"""Framework disk headroom configuration; no business outputs or history."""
from core.i18n import t
from core.prompt import text_input, confirm, pause, UserCancel, clear_screen, print_header
from core.sdk import waiting
from core.theme import print_info, print_error, print_success


def entry():
    from core.plugin_disk_policy import policy, configure
    prefix = "plugin_native.disk."
    breadcrumb = ["OpsKit", t("menu.plugin"), t("plugin.manage"), t("plugin_native.maintenance"), t(prefix + "menu")]
    try:
        clear_screen()
        print_header(breadcrumb)
        with waiting(t(prefix + "reading")):
            value = policy()
        print_info(t(prefix + "details", minimum=value["minimum_free_mib"]))
        minimum = int(text_input(breadcrumb, t(prefix + "minimum"), default=str(value["minimum_free_mib"])))
        if confirm(breadcrumb, t("confirm_ui.save"), info_lines=[t(prefix + "minimum"), str(minimum), t("confirm_ui.disk_detail")]):
            with waiting(t(prefix + "saving")):
                configure(minimum)
            print_success(t(prefix + "saved"))
            pause()
    except (KeyboardInterrupt, UserCancel):
        return
    except (OSError, ValueError, RuntimeError):
        print_error(t(prefix + "failed"))
        pause()
