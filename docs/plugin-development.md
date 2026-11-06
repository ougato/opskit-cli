# OpsKit 插件开发指南（SDK 4）

本文面向插件开发者；宿主能力的完整限制与安全协议见[插件规范](plugin-spec.md)。本指南中的代码片段说明接口，不需要安装示例插件或添加框架菜单。

## 开发约定

- 插件是独立仓库，代码仅依赖公开的 `core.sdk`，不导入其他 `core.*` 内部模块。SDK 版本、服务业务版本和 RPC 传输版本彼此独立。
- `plugin.yaml` 的 `api_version` 必须是整数 `4`，不能是字符串、浮点数或布尔值。原 SDK 3 插件可在管理列表中查看、更新和卸载，但不能执行或提供服务；升级后重新审核、授信并准备匹配当前 SDK 的运行时。
- 所有面向用户的菜单、提示、结果、错误和帮助提供中文与英文，语言键和插值参数一致。中文固定菜单操作名使用四个汉字，“返回”使用两个汉字；动态游戏名、路径等不是固定操作名。图标通过 `get_icon(token)` 获取，并在目标终端检查占位和对齐，不使用 `🗮`。
- 交互由框架统一呈现；插件只请求操作及目标，不能自行选择、记录或绕过框架的授权选项。一次授权、永久授权和返回均由框架管理；取消不留下永久拒绝记录。`--yes`、业务确认参数和非交互模式均不能代替能力授权。
- 插件代码在 Windows AppContainer/Job Object 中运行；Linux/macOS 后端当前尚未实现，明确拒绝执行。沙盒内进程与请求执行的宿主工具不同：经授权的宿主工具按当前用户权限运行，可能执行项目脚本，应在业务确认中准确说明影响。
- 不把凭据放进代码、日志、可共享配置或构建产物；不因更新、迁移或清理而覆盖用户原始数据。用户取消、撤权、校验失败、运行失败都应有清晰结果。

## 清单与入口

```yaml
name: example
version: 1.0.0
api_version: 4
kind: python
entry: example_plugin
platforms: [windows]
permissions: []
label:
  zh: 示例工具
  en: Example tool
```

`name` 以小写字母开头，之后仅含小写字母、数字和下划线。`entry` 是插件自身 Python 包名；平台不会在宿主导入插件。菜单调用 worker 中的 `register().entry()`；命令行调用 `cli(argv)`，返回 0..255 的整数或 `None`。`opskit plugin run example -- ...` 的 `--` 后参数属于插件，插件自行解析和提供本地化帮助。

```python
from core.sdk import ModuleInfo, get_icon, register_locale, select, t

register_locale({
    "zh": {"example": {"title": "示例工具", "select": "请选择", "run": "执行任务", "back": "返回"}},
    "en": {"example": {"title": "Example tool", "select": "Select", "run": "Run task", "back": "Back"}},
})

def entry():
    while True:
        key = select(["OpsKit", t("example.title")], t("example.select"),
                     [{"key": "1", "label": get_icon("devtools") + " " + t("example.run")}],
                     back_label=get_icon("back") + " " + t("example.back"))
        if key is None:
            return
        if key == "1":
            return  # 调用插件自己的业务逻辑

def register():
    return ModuleInfo(key="example", description_key="example.title", order=50, entry=entry)
```

菜单和 `cli` 可共用同一业务函数，但非交互命令必须提供所需参数，不能打开菜单后猜测用户选择。业务危险操作需要独立、明确的业务确认；宿主授权则由框架另行处理。

## 交互接口

以下均从 `core.sdk` 导入。选择菜单只接收可序列化数据，不支持向宿主传入回调；`paged_select` 的回调在插件进程本地执行。

```text
select(breadcrumb, subtitle, choices, theme_key='root', show_shortcuts=None,
       back_label='', description='') -> str | None
select_input(breadcrumb, subtitle, choices, back_label='', description='', default='') -> str | None
multi_select(breadcrumb, subtitle, options, theme_key='root', back_label='', all_index=None) -> list[int] | None
paged_select(*, breadcrumb, items, choice_of, subtitle_of, back_label,
             nav_labels=None, theme_key='root', page_size=9, on_select=None) -> item | None
confirm(breadcrumb, prompt, theme_key='root', info_lines=None, require_explicit=False) -> bool
text_input(breadcrumb, prompt, default='', hint='', theme_key='root', info_lines=None, secret=False) -> str
pause(msg=''); clear_screen(); print_header(breadcrumb)
```

`select` 为单键选择，`choices` 是 `{'key': '1', 'label': '...'}` 列表；`select_input` 读取整行，空行使用 `default`。两者输入 0 或按 Esc 返回 `None`。多选使用方向键、空格和回车，返回已勾选的零起始下标；0/Esc 返回 `None`，只有空选项列表才无须交互直接返回 `[]`。有选项时，即使启用 `--yes` 也必须由用户选择；非交互模式不能以空选择冒充用户确认。

`select` 对超过 9 项且从 1 起连续编号的选项自动分页，返回的仍是原始编号。需要保留对象、使用自定义按键或翻页后继续浏览时使用 `paged_select`。分页 `page_size` 为 1..9；`choice_of(item, index_on_page)` 可省略 `key` 自动编号，也可提供不重复的单字符键，`0`、`p`、`n` 留给返回和翻页。`subtitle_of(page, total_pages)` 的页码从 0 开始。未设置 `on_select` 时返回所选对象；设置时调用 `on_select(item)` 并继续浏览。0/Esc 返回 `None`。

`confirm` 的 0/Esc 返回 `False`；普通确认可受 `--yes` 影响，必须由用户明确选择时传 `require_explicit=True`。`text_input` 的 Esc 抛出 `UserCancel`，调用方在适当的菜单边界处理；`default` 是空行时返回的值，`hint` 只改变展示。需要保持已有值时应传入 `default`，重置应提供明确操作。Ctrl+C 退出交互；不能把取消当成确认。

`console` 是 Rich Console，适合必要的只读输出；普通消息优先使用下列主题接口。菜单由 SDK 呈现，插件避免重复自行绘制编号或清屏。

## SDK 导出目录

下表列出全部公开导出；需要精确参数和默认值时以 `core/sdk.py` 和本节签名为准。除宿主能力函数外，返回值均在插件进程内使用。

| 类别 | 公开接口 | 用途和边界 |
|---|---|---|
| 版本和入口 | `SDK_API_VERSION`, `ModuleInfo` | 当前为 4；`ModuleInfo(key, description_key, order, entry, platforms=..., enabled=True, label=None, icon=None)`。入口显示元数据以清单为准。 |
| 文案 | `t(key, **kwargs)`, `current_lang()`, `register_locale(catalog)` | 注册 `{语言: 嵌套字典}`，使用插件自己的命名空间；不覆盖平台文案。 |
| 主题 | `get_color(token)`, `get_icon(token)`, `print_success(msg, elapsed=None)`, `print_error(msg)`, `print_warning(msg)`, `print_info(msg, *, show_icon=True)` | 复用平台主题和状态输出。 |
| 菜单 | `select`, `select_input`, `multi_select`, `paged_select`, `confirm`, `text_input`, `pause`, `clear_screen`, `print_header`, `UserCancel`, `console` | 签名、取消及非交互语义见上节。 |
| 跨插件服务 | `get_service(name)`, `open_service_menu(name, breadcrumb, context=None)` | 前者返回远程代理，实际调用时核验；后者调用已授权服务的 `open_menu`。缺少提供方、契约或授权时调用失败，不自动安装。 |
| 状态和路径 | `plugin_data_dir(namespace)`, `data_dir()`, `cache_dir()`, `log_dir()`, `plugins_dir()` | 使用自身 `plugin_data_dir` 保存持久业务状态；名称须为自身身份或其连字符别名。其他路径是本次隔离工作区内路径或只读插件快照，不代表宿主路径或持久授权。 |
| 状态格式 | `load_yaml(path)`, `save_yaml(path, data)`, `get_logger(name='opskit')` | 在自身可访问的隔离路径内读写；注意不要记录敏感信息。 |
| 沙盒进程 | `run(cmd, *, cwd=None, env=None, timeout=None, capture=False, check=True, output_limit=1048576)`, `run_lines(cmd, *, cwd=None, env=None, timeout=None)`, `which(name)`, `cmd_ok(cmd, timeout=5)` | 只在沙盒权限及进程限制内工作；不代表能运行宿主 Git 或 Creator。 |
| 依赖 | `ensure_python_package(package, import_name='')` | 仅检查已准备运行时中的依赖，不安装任何软件或 Python 包。 |
| 宿主文件 | `read_host_file(directory, relative)`, `compare_write_host_file(directory, relative, expected_sha256, content)`, `create_host_file(directory, relative, content)`, `host_file_status(directory, relative)`, `artifact_info(directory, relative)` | 通过宿主授权及有界校验；写入须处理失败与中断。 |
| 宿主工具 | `host_execute(executable, arguments, directory, options=None)`, `host_path_info(path, directory=False)`, `host_file_version(path)`, `host_asar_directories(directory, relative, member)` | 只访问精确获准的目录、工具及参数。`host_asar_directories` 只列举 ASAR 目录。 |
| 产物和网络 | `host_artifacts(action, parameters)`, `https_request(url, method='GET', headers=None, body=b'')`, `upload_file(directory, relative, url, sha256, headers=None)` | 使用框架的产物凭据、受限 HTTPS 和内容摘要绑定上传；详细约束见插件规范。 |

插件自身状态按身份和服务调用方隔离，成功调用只有通过最终的期限、授权和状态检查后才提交。临时工作区并非永久存储；不得推断其他插件的状态目录。不要使用已删除的 `ensure_software`、`software_bin` 或旧 `host_archive_directories`。

## 服务契约

提供方必须为每个 `provides` 项声明 `service_contracts`；调用方在 `uses` 中写明 `service`、`provider`、业务 `api_version` 和 `methods`。SDK 4 不接受旧的 `source` 字段或不完整声明，也不会按服务名自动安装仓库。方法与结果必须是可序列化数据，不能传函数或共享 Python 对象。

```yaml
uses:
  - service: storage
    provider: storage_plugin
    api_version: 1
    methods: [read, open_menu]
```

操作者先分别审核调用方和提供方及其指纹，再使用 `opskit plugin authorize-service` 授权精确的调用边。插件更新、指纹变化或撤权后不能沿用旧授权；每次调用都重新验证。`get_service` 不表示提供方已经可用，访问方法或 `service_api_version` 时才会进行实际 RPC 和校验。

## 发布前检查

1. 校验清单、菜单四字规则、主题图标宽度，以及中英文的键和占位符。
2. 测试返回、Esc、Ctrl+C、空输入、分页超过九项、非交互与 `--yes`，覆盖失败、撤权及数据保留。
3. 审核依赖和插件来源，准备匹配当前 SDK 摘要的隔离运行时；内容变化需重新审核授信。只生成 `CHECKSUMS.yaml` 不等于签名或授权。
4. 分别测试源码、实际 Windows 原生沙盒和发布包；宿主工具、网络服务、目标平台及另一台机器都需要各自的真实验收，不能以模拟测试代替。
