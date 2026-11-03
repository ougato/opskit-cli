"""Operator-reviewed collection of recorded, unused native cache objects."""
from core import native_catalog_cleanup as cleanup
from core.i18n import t
from core.prompt import select, confirm, pause, UserCancel
from core.theme import print_info, print_error, print_success


def entry():
    prefix = "plugin_native.cache_cleanup."
    breadcrumb = ["OpsKit", t("menu.plugin"), t(prefix + "menu")]
    try:
        kind = select(breadcrumb, t(prefix + "catalog"), [
            {"key": "1", "label": t(prefix + "runtime")},
            {"key": "2", "label": t(prefix + "fetch")},
            {"key": "3", "label": t(prefix + "prepare")},
        ])
        if kind not in {"1", "2", "3"}:
            return
        kind = {"1": "runtime", "2": "fetch", "3": "prepare"}[kind]
        records = cleanup.inspect(kind)
        candidates = [item for item in records if item["valid"] and not item["referenced"]]
        if any(not item["valid"] for item in records):
            print_info(t(prefix + "uncertain"))
        if not candidates:
            print_info(t(prefix + "empty"))
            pause()
            return
        key = select(breadcrumb, t(prefix + "select"), [
            {"key": str(index + 1), "label": item["object"]}
            for index, item in enumerate(candidates)])
        if key is None or not key.isdecimal() or not 0 < int(key) <= len(candidates):
            return
        item = candidates[int(key) - 1]
        if confirm(breadcrumb, t(prefix + "confirm"), info_lines=[
                t(prefix + "details", catalog=t(prefix + kind), object=item["object"],
                  digest=item["record_sha256"])]):
            cleanup.collect(kind, item["object"], item["record_sha256"])
            print_success(t(prefix + "completed"))
            pause()
    except (KeyboardInterrupt, UserCancel):
        return
    except (OSError, ValueError, RuntimeError):
        print_error(t("plugin_native.lifecycle.cache_cleanup_failed"))
        pause()
