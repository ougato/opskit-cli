"""Trusted permission inspection and revocation in the existing plugin menu."""
from core.i18n import t
from core.plugin_capabilities import CapabilityAuthority, CapabilityDenied, _key, capability_info_lines
from core.prompt import select, confirm, pause, UserCancel, clear_screen, print_header
from core.sdk import waiting
from core.theme import print_info, print_error, print_success, get_icon
from rich.markup import escape


def entry():
    authority = CapabilityAuthority()
    breadcrumb = ["OpsKit", t("menu.plugin"), t("plugin.manage"), t("plugin_native.permissions")]
    try:
        while True:
            clear_screen()
            print_header(breadcrumb)
            with waiting(t("plugin_native.permissions_help")):
                grants = authority.list_grants()
            if not grants:
                print_info(t("plugin_native.no_grants"))
                pause()
                return
            choices = [{"key": str(index + 1), "label": get_icon("plugin_permissions") + " " + escape(request.plugin) + " · " +
                        t("plugin_native.operations." + request.operation)}
                       for index, request in enumerate(grants)]
            key = select(breadcrumb, t("plugin_native.select_grant"), choices, back_label=f"{get_icon('back')} {t('plugin_native.back')}")
            if key is None:
                return
            if not key.isdecimal() or not 0 < int(key) <= len(grants):
                continue
            request = grants[int(key) - 1]
            details = [escape(line) for line in capability_info_lines(request, include_fingerprint=True)]
            details.append("ID: " + _key(request))
            if confirm(breadcrumb, t("confirm_ui.revoke"),
                       info_lines=details):
                clear_screen()
                print_header(breadcrumb)
                with waiting(t("confirm_ui.revoke")):
                    authority.revoke(request)
                print_success(t("plugin_native.revoked"))
                pause()
    except (KeyboardInterrupt, UserCancel):
        return
    except CapabilityDenied as error:
        print_error(str(error))
        pause()
    finally:
        authority.close()
