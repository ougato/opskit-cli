"""Bilingual capacity configuration and explicit receipt-bound build cleanup."""
from core.i18n import t
from core.prompt import select, text_input, confirm, pause, UserCancel
from core.theme import print_info, print_error, print_success


def entry():
    from core.plugin_disk_policy import policy, configure
    from core.plugin_build_history import inspect, clean, project_lock
    from core.plugin_host_tools import _ordinary
    prefix = "plugin_native.disk."
    breadcrumb = ["OpsKit", t("menu.plugin"), t(prefix + "menu")]
    try:
        value = policy()
        print_info(t(prefix + "details", minimum=value["minimum_free_mib"],
            directory=value["creator_output_directory"] or "project/build", keep=value["keep_builds"]))
        key = select(breadcrumb, t(prefix + "menu"), [
            {"key": "1", "label": t(prefix + "configure")},
            {"key": "2", "label": t(prefix + "history")},
        ])
        if key == "1":
            minimum = int(text_input(breadcrumb, t(prefix + "minimum"), default=str(value["minimum_free_mib"])))
            directory = text_input(breadcrumb, t(prefix + "directory"))
            keep = int(text_input(breadcrumb, t(prefix + "keep"), default=str(value["keep_builds"])))
            if confirm(breadcrumb, t(prefix + "confirm")):
                configure(minimum, directory, keep)
                print_success(t(prefix + "saved"))
                pause()
        elif key == "2":
            project = _ordinary(text_input(breadcrumb, t(prefix + "project")), directory=True)
            with project_lock(project):
                rows = inspect(project)
            for row in rows:
                print_info(t(prefix + "build_details", directory=row["directory"], size=row["bytes"],
                    status=t(prefix + ("candidate" if row["cleanup_candidate"] else "protected"))))
            candidates = [row for row in rows if row["cleanup_candidate"]]
            if not candidates:
                pause()
                return
            selected = select(breadcrumb, t(prefix + "history"), [
                {"key": str(index + 1), "label": row["directory"]} for index, row in enumerate(candidates)])
            if selected is None or not selected.isdecimal() or not 0 < int(selected) <= len(candidates):
                return
            row = candidates[int(selected) - 1]
            if confirm(breadcrumb, t(prefix + "cleanup_confirm"), info_lines=[row["directory"], row["record_sha256"]]):
                clean(project, row["id"], row["record_sha256"])
                print_success(t(prefix + "removed"))
                pause()
    except (KeyboardInterrupt, UserCancel):
        return
    except (OSError, ValueError, RuntimeError):
        print_error(t(prefix + "failed"))
        pause()
