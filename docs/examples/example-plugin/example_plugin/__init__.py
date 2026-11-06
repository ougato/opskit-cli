"""Minimal SDK 4 plugin with the same action for menu and CLI."""
from core.sdk import ModuleInfo, get_icon, print_error, print_success, register_locale, select, t

register_locale({
    "zh": {"example": {"title": "示例工具", "select": "请选择", "run": "执行任务",
                       "back": "返回", "done": "任务已完成", "usage": "用法：--hello",
                       "invalid": "参数无效，请使用 --help 查看用法"}},
    "en": {"example": {"title": "Example tool", "select": "Select", "run": "Run task",
                       "back": "Back", "done": "Task completed", "usage": "Usage: --hello",
                       "invalid": "Invalid arguments; use --help for usage"}},
})


def _run():
    print_success(t("example.done"))


def entry():
    while True:
        key = select(["OpsKit", t("example.title")], t("example.select"),
                     [{"key": "1", "label": f"{get_icon('devtools')} {t('example.run')}"}],
                     back_label=f"{get_icon('back')} {t('example.back')}")
        if key is None:
            return
        if key == "1":
            _run()


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
