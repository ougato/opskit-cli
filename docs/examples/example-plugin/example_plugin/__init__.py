"""Minimal SDK 4 plugin with the same action for menu and CLI."""
from core.sdk import ModuleInfo, get_icon, paged_select, print_error, print_success, register_locale, select, t, waiting
from core.sdk import UserCancel, get_service, input_steps, list_host_files, request_plugin_install, text_input

register_locale({
    "zh": {"example": {"title": "示例工具", "run": "执行任务",
                       "back": "返回", "storage": "云端配置", "info": "操作：运行示例任务", "done": "任务已完成", "usage": "用法：--hello",
                       "invalid": "参数无效，请使用 --help 查看用法"}},
    "en": {"example": {"title": "Example tool", "run": "Run task",
                       "back": "Back", "storage": "Cloud configuration", "info": "Operation: Run the example task", "done": "Task completed", "usage": "Usage: --hello",
                       "invalid": "Invalid arguments; use --help for usage"}},
})


def _run():
    print_success(t("example.done"))


def files_for_upload(directory):
    """List relative object names in the archive, which may not exist yet."""
    return list_host_files(directory, missing_ok=True)


def open_storage_configuration(breadcrumb):
    """Open configuration from a menu that needs the declared storage service."""
    if not request_plugin_install("storage_hub"):
        return False
    return get_service("storage").open_menu(breadcrumb, {"configure": True})


def run_waiting(task):
    """Run a local callable while the host displays its waiting indicator."""
    with waiting():
        return task()


def choose_item(items, initial_page=0):
    """Select an item by a single key, starting at the requested page."""
    return paged_select(breadcrumb=["OpsKit", t("example.title")], items=items,
                        choice_of=lambda item, _: {"label": str(item)},
                        subtitle_of=lambda page, total: "",
                        back_label=f"{get_icon('back')} {t('example.back')}",
                        initial_page=initial_page)


def collect_fields(breadcrumb, first_label, second_label):
    """Call from a menu loop and redraw that menu when this returns None."""
    try:
        return input_steps(
            lambda previous: text_input(breadcrumb, first_label, initial=previous or ""),
            lambda previous: text_input(breadcrumb, second_label, initial=previous or ""),
        )
    except UserCancel:
        return None


def entry():
    breadcrumb = ["OpsKit", t("example.title")]
    while True:
        key = select(breadcrumb, "",
                     [{"key": "1", "label": f"{get_icon('devtools')} {t('example.run')}"},
                      {"key": "2", "label": t("example.storage")}],
                     back_label=f"{get_icon('back')} {t('example.back')}",
                     info_lines=[t("example.info")])
        if key is None:
            return
        if key == "1":
            _run()
        if key == "2":
            try:
                open_storage_configuration(breadcrumb)
            except PermissionError as error:
                print_error(str(error))


def register():
    return ModuleInfo(key="example", description_key="example.title", order=50, entry=entry)


def cli(argv):
    if argv == ["--hello"]:
        _run()
        return 0
    if argv == ["--help"]:
        print(t("example.usage"))
        return 0
    print_error(t("example.invalid"))
    return 2
