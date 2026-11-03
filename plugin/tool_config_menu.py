"""Host-owned Creator configuration, separate from plugin execution grants."""
from core import plugin_tool_config as tools
from core.i18n import t
from core.prompt import select, text_input, confirm, pause, UserCancel
from core.theme import print_info, print_error, print_success


def entry():
    prefix = "plugin_native.tool_config."
    breadcrumb = ["OpsKit", t("menu.plugin"), t(prefix + "menu")]
    try:
        values = tools.creators()
        for name, path in sorted(values.items()):
            print_info(t(prefix + "details", name=name, path=path))
        key = select(breadcrumb, t(prefix + "select"), [
            {"key": "1", "label": t(prefix + "configure")},
            {"key": "2", "label": t(prefix + "remove")},
        ])
        if key not in {"1", "2"}:
            return
        if key == "1":
            name = text_input(breadcrumb, t(prefix + "name"))
            path = text_input(breadcrumb, t(prefix + "path"))
            if confirm(breadcrumb, t(prefix + "confirm"), info_lines=[
                    t(prefix + "details", name=name, path=path)]):
                tools.configure_creator(name, path)
                print_success(t(prefix + "saved"))
                pause()
        else:
            if not values:
                print_info(t(prefix + "remove"))
                pause()
                return
            names = sorted(values)
            selected = select(breadcrumb, t(prefix + "remove"), [
                {"key": str(index + 1), "label": name} for index, name in enumerate(names)])
            if selected is None or not selected.isdecimal() or not 0 < int(selected) <= len(names):
                return
            name = names[int(selected) - 1]
            if confirm(breadcrumb, t(prefix + "confirm_remove"), info_lines=[
                    t(prefix + "details", name=name, path=values[name])]):
                tools.remove_creator(name)
                print_success(t(prefix + "removed"))
                pause()
    except (KeyboardInterrupt, UserCancel):
        return
    except (OSError, ValueError, RuntimeError):
        print_error(t("plugin_native.lifecycle.tool_config_failed"))
        pause()
