# OpsKit 插件开发指南（SDK 4）

本文面向插件开发者；宿主能力的完整限制与安全协议见[插件规范](plugin-spec.md)。可复制[完整示例](examples/example-plugin/)作为独立插件仓库的起点，不需要修改框架菜单。示例菜单和 `opskit plugin run example -- --hello` 共用业务函数；`--help` 显示用法。安装前须将示例提交到自己的 Git 仓库，再使用框架安装、审核和信任流程。

项目启动、主菜单和内置功能的调用关系见[项目架构与功能流程](项目架构与功能流程.md)；可抽取能力及清理建议见[框架规划与清理清单](框架规划与清理清单.md)。规划中的能力不代表当前 SDK 已经提供。

## 从零接入插件

1. **创建独立仓库**：复制[示例清单](examples/example-plugin/plugin.yaml)和[示例入口](examples/example-plugin/example_plugin/__init__.py)，将名称、版本、Python 包名和中英文显示名称改为自己的内容。外部插件只导入 `core.sdk` 的公开接口。
2. **声明入口与平台**：在 `plugin.yaml` 设置 SDK 版本、入口包和支持平台；按实际需要声明权限。插件可声明分组，框架负责在“插件工具”中发现、分组和显示入口。清单字段及校验以[插件规范](plugin-spec.md)为准。
3. **组织业务**：实现 `register()` 返回带 `entry` 的 `ModuleInfo`；需要命令行时实现 `cli(argv)`。菜单与命令行调用同一业务函数，CLI 所需参数显式提供，错误返回非零状态。示例中的 `_run()` 同时被菜单和 `cli()` 调用。
4. **实现交互和文案**：用 `register_locale()` 注册插件命名空间的中英文文案；菜单调用 SDK 的选择、输入和确认接口，使用主题图标、面包屑和信息行。按本页[交互接口](#交互接口)处理返回、取消、分页和等待，不自行绘制授权界面。
5. **接入资源**：插件配置和记录放在自身 `plugin_data_dir()`；宿主目录、文件、工具、网络或产物通过相应 SDK 请求。先确定所需访问范围，再处理授权拒绝、取消、撤权和执行失败；业务确认与宿主授权分别处理。跨插件复用须声明服务契约并由操作者授权。
6. **安装与运行**：提交到自己的 Git 仓库后，从“插件工具 → 插件管理”安装、审核并信任；Windows x64 安装链路会自动准备基础运行环境。命令行安装保留独立授信步骤。插件的菜单入口在“插件工具”下，命令行通过 `opskit plugin run <插件名> -- <插件参数>` 调用。详见[采集、审核与更新](plugin-spec.md#采集审核与更新)。
7. **验证与发布**：先运行插件自己的业务测试，再按本页[发布前检查](#发布前检查)覆盖菜单、CLI、权限和实际运行环境。额外 Python 依赖需要审核过的离线输入；不能假定插件安装时会执行仓库安装脚本或联网安装依赖。

## 菜单与能力归属

以下是当前实现，外部插件只能直接依赖 `core.sdk` 导出；SDK 的具体签名和取消语义见本页[交互接口](#交互接口)和 [SDK 导出目录](#sdk-导出目录)。

| 需求 | 当前由谁负责 | 插件应如何使用 |
|---|---|---|
| 主菜单、插件工具入口、分组、安装更新与权限管理 | 框架 | 在清单声明插件和可选分组；经框架安装与审核，不修改主菜单或自行实现安装管理。 |
| 选择、分页、输入、确认、等待、标题和反馈 | 框架提供交互和主题，插件提供业务内容 | 从 SDK 选择对应交互接口，传入本地化文案、上下文和必要信息行。 |
| 插件业务菜单、参数、处理逻辑和结果判断 | 插件 | 使用 SDK 交互组织自身流程；菜单和 CLI 共用业务逻辑，并自行解释业务工具的结果。 |
| 文案、图标和语言切换 | 框架提供语言与主题机制，插件维护自己的文案 | 用 `register_locale`、`t` 和主题 token；清单图标的特殊规则见下文。 |
| 状态、日志和进程 | SDK 提供隔离状态、日志与沙盒进程接口 | 状态使用自身身份命名空间；沙盒进程不等于宿主进程，宿主工具使用受授权接口。 |
| 宿主目录、文件、工具、产物和网络 | 框架 broker 处理请求和授权 | 调用 SDK 的相应宿主能力；目录读取、写入与执行的授权范围各不相同，不能以业务确认代替。 |
| 跨插件共享服务或菜单 | 提供方定义服务；框架检查声明与授权 | 调用方声明所需服务、版本和方法，经操作者批准后使用 SDK 代理；不能直接导入其他插件的对象。 |
| 软件管理、监控、网络等内置模块 | 框架内部实现 | 这些模块不是外部插件可直接调用的 SDK；需要新能力时先评估并设计公开接口。 |

例如，选择 Creator 可执行文件时，可用现有 SDK 查找和检查文件，再由插件维护具体选择流程与配置；执行构建可请求宿主工具执行，但 Creator 参数、成功判定和平台产物规则仍属于 Cocos 业务。框架不会因为展示相似的菜单而授予宿主权限。

## 开发约定

- 插件是独立仓库，代码仅依赖公开的 `core.sdk`，不导入其他 `core.*` 内部模块。SDK 版本、服务业务版本和 RPC 传输版本彼此独立。
- `plugin.yaml` 的 `api_version` 必须是整数 `4`，不能是字符串、浮点数或布尔值。旧版插件不进入管理列表、不能执行或提供服务；升级后重新安装、审核、授信并准备匹配当前 SDK 的运行时。旧目录和数据不会自动删除。
- 所有面向用户的菜单、提示、结果、错误和帮助提供中文与英文，语言键和插值参数一致。中文固定菜单操作名使用四个汉字，“返回”使用两个汉字；动态游戏名、路径等不是固定操作名。图标通过 `get_icon(token)` 获取，并在目标终端检查占位和对齐，不使用 `🗮`。
- 交互由框架统一呈现；插件只请求操作及目标，不能自行选择、记录或绕过框架的授权选项。一次授权、永久授权和返回均由框架管理；取消不留下永久拒绝记录。`--yes`、业务确认参数和非交互模式均不能代替能力授权。
- 插件代码在 Windows AppContainer/Job Object 中运行；Linux/macOS 后端当前尚未实现，明确拒绝执行。沙盒内进程与请求执行的宿主工具不同：经授权的宿主工具按当前用户权限运行，可能执行项目脚本，应在业务确认中准确说明影响。
- 不把凭据放进代码、日志、可共享配置或构建产物；不因更新、迁移或清理而覆盖用户原始数据。用户取消、撤权、校验失败、运行失败都应有清晰结果。
- `permissions` 必须是权限名数组；不存在该字段表示空数组。非数组、重复项或不支持的权限会使清单无效，不会静默改成未声明。
- 清单中的 `label`、`group_label` 每项最多 256 个字符，语言键最多 32 个字符，每组最多 16 项；`icon` 和 `group_icon` 最多 64 个字符。它们不能包含终端控制字符或换行；插件自身生成的菜单文案同样不得注入终端控制字符。
- 菜单信任和更新已有来源时的内网连接确认必须由用户明确选择，不能由全局 `--yes` 代答。安装菜单中用户亲自输入仓库地址即允许本次连接其指定主机和端口；非交互安装只采集并准备插件，私网须显式指定 `--allow-private`，授信使用独立的指纹命令。信任界面显示可用的来源、分支和内容指纹，确认期间内容变化会拒绝授信。

## 插件配置开发规范

插件自行定义业务配置的字段、文件格式、作用域和覆盖规则。统一约定配置从哪里读取、怎样校验，以及读取外部配置时需要什么权限。

| 配置来源 | 读取方式 | 适用场景 |
|---|---|---|
| 插件自带默认值 | Python 常量或插件包内的只读配置 | 开箱默认值和内置模板 |
| 插件自己的持久配置 | `plugin_data_dir("<清单中的 name>")`。JSON 可用 Python 标准库，YAML 可用 SDK 的 `load_yaml` 和 `save_yaml` | 插件级设置、项目级覆盖、工具路径 |
| 用户选定的宿主项目 | 通过 `host_path_info` 和 `read_host_file` 访问。保存已读取的文件可用 `compare_write_host_file` 检查原始摘要后写入 | 仓库内构建配置、渠道配置、团队共用文件 |
| 本次调用参数 | 由菜单输入或解析插件 CLI 参数，再交给同一配置解析函数 | 本次构建版本、平台或临时覆盖 |
| 其他插件提供的配置 | 通过声明版本和方法的 SDK 服务读取 | 需要跨插件共享的业务数据 |

开发者应在插件 README 中提供配置表，逐项列出字段、类型、默认值、是否必填、配置来源、覆盖优先级和修改入口。提供一个最小示例。对有多层配置的插件，推荐顺序为「本次显式参数 → 项目配置 → 插件配置 → 内置默认值」，从左到右取第一个已设置的值。如果某字段由项目决定，例如 Cocos 的渠道编码，应在文档注明其唯一来源。

- 在首次访问外部资源前校验配置。区分字段缺失、显式空值、格式错误和版本不兼容。SDK 的 `load_yaml` 在文件缺失、解析失败或顶层不是映射时均返回空字典。需要区分这些情况的插件应使用可明确报告解析错误的方式
- 需要长期保存的配置应有明确的格式版本或兼容规则。迁移应校验新结果并保留可恢复的旧配置。配置文件中的路径只表示目标，实际访问仍由宿主 SDK 校验和授权
- 凭据单独管理。配置示例只能包含占位值或凭据引用，导出、日志和菜单摘要不得输出真实密钥

以 Cocos 为例，游戏级输出目录和保留数量覆盖插件级默认值，重置游戏设置后重新继承默认值。客户端版本与渠道由选中分支的 `env/environment.json` 提供，构建版本可通过菜单或命令行指定。插件 README 应明确每一项的实际生效来源。

上传目录时，使用 `list_host_files(directory)` 取得已授权目录中的相对路径，再用 `artifact_info(directory, relative)` 获取单个文件的 SHA-256。多个文件可用 `artifact_batch_info(directory, relatives)` 按 `HOST_ARTIFACT_BATCH_LIMIT` 分批读取元数据，结果按输入顺序返回。将摘要随相对路径和签名地址传给 `upload_files`，单文件也可使用 `upload_file`。宿主会重新核对摘要并统一显示进度，插件无需逐文件打印。插件应使用所选目录的相对路径生成对象键。七牛配置可使用 `kodo-stat` 描述符校验同一对象的大小和服务端 QETag，宿主仍先核对本地 SHA-256。校验不请求产物正文。

```python
from core.sdk import upload_files

receipts = upload_files(directory, [[relative, put_url, sha256, headers, get_url]],
                        breadcrumb=["OpsKit", "My Plugin", "Upload"])
```
首次归档前，列表可用 `list_host_files(directory, missing_ok=True)` 将尚未创建的目录显示为空。

在同一已授权目录读取多个小文件时，可使用 `read_host_files(directory, relatives)`。每批最多 64 个文件，每个最多 1 MiB，合计最多 8 MiB。例如 `manifest, config = read_host_files(project, ["package.json", "env/environment.json"])`。兼容旧框架的插件可在 SDK 未提供此接口时逐个读取。

SDK 提供 `HOST_FILE_LIST_SUBDIRECTORIES` 时，可用 `list_host_files(project, subdirectories=["assets", "settings"], missing_ok=True)` 枚举指定子树。结果包含子目录前缀，同一个项目目录授权覆盖整批枚举。

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

清单中的 `icon` 和 `group_icon` 当前是直接显示的图标字符，不是主题 token；省略时由框架使用默认图标。菜单代码使用 `get_icon(token)`，不要把 token 名称直接写入这两个清单字段。确需自定义清单图标时，应在目标终端确认宽度和字体占位，避免使用 `🗮`。

```python
from core.sdk import ModuleInfo, get_icon, register_locale, select, t

register_locale({
    "zh": {"example": {"title": "示例工具", "run": "执行任务", "back": "返回", "info": "操作：运行示例任务"}},
    "en": {"example": {"title": "Example tool", "run": "Run task", "back": "Back", "info": "Operation: Run the example task"}},
})

def entry():
    while True:
        key = select(["OpsKit", t("example.title")], "",
                     [{"key": "1", "label": get_icon("devtools") + " " + t("example.run")}],
                     back_label=get_icon("back") + " " + t("example.back"),
                     info_lines=[t("example.info")])
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
       back_label='', description='', info_lines=None) -> str | None
select_input(breadcrumb, subtitle, choices, back_label='', description='', default='', info_lines=None) -> str | None
multi_select(breadcrumb, subtitle, options, theme_key='root', back_label='', all_index=None) -> list[int] | None
paged_select(*, breadcrumb, items, choice_of, subtitle_of, back_label,
             nav_labels=None, theme_key='root', page_size=9, on_select=None, initial_page=0) -> item | None
confirm(breadcrumb, prompt, theme_key='root', info_lines=None, require_explicit=False) -> bool
text_input(breadcrumb, prompt, default='', hint='', theme_key='root', info_lines=None, secret=False,
           *, initial='') -> str
pause(msg=''); clear_screen(); print_header(breadcrumb)
waiting(message='') -> context manager
```

`select` 为单键选择，按数字立即返回，无需回车；`choices` 是 `{'key': '1', 'label': '...'}` 列表。`select_input` 用于确需输入完整文本的菜单，输入后按回车，空行使用 `default`；普通选项菜单不要使用它。两者输入 0 或按 Esc 返回 `None`。多选使用方向键、空格和回车，返回已勾选的零起始下标；0/Esc 返回 `None`，只有空选项列表才无须交互直接返回 `[]`。有选项时，即使启用 `--yes` 也必须由用户选择；非交互模式不能以空选择冒充用户确认。

为每个业务菜单、选择、输入和确认传入当前面包屑，保留实际入口、已选对象和当前功能；独立步骤可以继续追加步骤名称。例如游戏构建中输入 Git 路径使用“OpsKit → Cocos 工具 → 游戏名 → 项目构建”，字段名称交给 `text_input` 的 `prompt`。复用工具选择等流程时显式传递入口上下文，没有选中对象时不要虚构对象。宿主授权标题仍由框架管理。

多选菜单每页最多显示 9 项，按 `n` 或 `p` 翻页时保留已勾选的项目。将 `subtitle` 传为空字符串可隐藏标题下的提示行。Windows 控制台中，方向键和空格每次按下只生效一次，松开后可以再次按下。

`text_input` 的 `initial` 将单行文本填入可编辑输入缓冲区，光标位于末尾。左右方向键移动光标，Backspace 删除左侧字符，Delete 删除光标处字符。清空后回车使用 `default`，默认为空字符串。可清空的配置只传 `initial`。

`atomic_write_host_file(directory, relative, lock_name, expected_sha256, content)` 在目录读写授权下原子替换不超过 1 MiB 的普通文件。`relative` 为单个文件名，`lock_name` 为协作写入者共用的点号前缀锁目录。`expected_sha256=None` 表示要求目标不存在；返回 `{"written": false}` 时重新读取并合并后重试。

Windows 上可用 `host_artifacts("publish_tree", {"source": source, "destination": destination, "files": files})` 发布已校验的文件树。源路径和目标路径使用绝对路径，目标父目录须已存在。`files` 的键为目标相对路径，值包含源相对路径 `source` 和 `sha256`。宿主在目标卷边复制边核对 SHA-256 和写入长度，发布前检查暂存文件的清单、身份、大小和修改时间，再原子发布整个目录。需要独立回读验证内容的调用方应另行校验。目标已存在时，只有全部文件和摘要一致才返回成功。

支持 `HOST_ARTIFACT_REMOVE_FILE` 的 Windows 宿主提供 `host_artifacts("remove_verified_file", {"directory": directory, "file": filename, "sha256": digest})`。该接口要求目录读写授权，只删除摘要匹配且不超过 1 MiB 的普通文件。`file` 必须是单个文件名。

SDK 提供 `HOST_ARTIFACT_PUBLISH_NESTED` 时，目标可位于源根目录下。参与复制的源文件必须在目标目录之外，源根目录和目标目录也必须不同。

发布树中的生成文件可将 `source` 换成 Base64 `content`，并提供相同的 SHA-256 字段。所有内联文件合计最多 4 MiB 加 1 KiB。宿主将生成文件与源文件一起验摘要并原子发布。

`HOST_ARTIFACT_PUBLISH_PARENTS` 可用时，传入已存在的绝对路径 `create_parents_from`，宿主可在同一次发布调用里创建目标父目录。目标必须位于该目录下，每层创建仍检查目录写入授权。

`HOST_ARTIFACT_COMMIT_HASHES` 可用时，给产物 `commit` 传入 `include_file_hashes: true`，可在目录读取授权下取得校验时计算的完整 `file_hashes`。超过 128 个文件或路径超出限制时省略该字段，调用方可继续按批读取摘要。发布时仍会校验复制内容。

`host_artifacts("zip_entry", {"directory": directory, "file": file, "member": member})` 在目录读取授权下返回指定 ZIP 成员的 Base64 内容。成员必须唯一，最大 1 MiB；该操作不解压到宿主磁盘。

`host_artifacts("sign_update_manifest", {"directory": directory, "file": file, "manifest": manifest, "public_key": public_key})` 在目录读取授权下为 v1 更新清单签名。传入 Base64 清单和包内公钥的 Base64 SPKI DER 编码。宿主确认 P-256 私钥与公钥对应，并使用固定签名域和 SHA-256 生成 Base64 DER 签名。提供已有 `signature` 时，宿主验签后返回原签名。私钥保留在宿主。

`host_artifacts("file_header", {"directory": directory, "file": file})` 在目录读取授权下返回文件前 12 字节的十六进制编码和同一读取流的完整 SHA-256，用于将格式检查与归档摘要绑定。

`host_artifacts("read_verified_file", {"directory": directory, "file": file, "sha256": digest, "limit": limit})` 在目录读取授权下读取已知 SHA-256 的文件，并返回 Base64 内容。调用者指定上限，最多 8 MiB。摘要不符或超出上限时读取失败。

选择菜单、确认框和文本输入均可通过 `info_lines` 传入信息行列表，框架统一绘制 `├─`，逐行显示在选项或输入位置之前；数字菜单自动分页时每页保留这些信息。信息内容由调用方本地化，建议每个元素为一行；已有多行文本也会拆为独立行。`select`、`select_input` 和 `text_input` 的信息按纯文本显示，适合含方括号的路径；`confirm` 保留 Rich markup 语义，动态内容应转义。已有 `description` 参数也使用同一行样式，新调用优先使用 `info_lines`。

信息行只是通用展示模板，不是授权接口。插件调用 `host_path_info`、`read_host_file`、`host_execute` 等 SDK 能力时，框架独立生成授权窗口、校验目标并处理本次、永久和返回选项；插件自己的菜单或业务确认不能授予宿主权限。目录授权提示仅显示权限、资源和授权选项，多个权限用 ` | ` 分隔。单独申请执行宿主工具时仍会说明工具以当前用户权限运行。插件指纹仍用于授权绑定，可在权限管理中查看。

目录读取授权包含该目录下的子目录；同一插件在已授权目录内读取时无需再次授权。授权按路径组件判断，同名前缀的兄弟目录不包含在内；读取授权不包含写入或工具执行。构建等需要写入项目的流程，可在入口调用 `host_path_info(project, True, write=True)` 显式申请该目录及子目录的读写授权；后续受控读写复用此授权。确需在项目中运行工具的流程可调用 `host_path_info(project, True, write=True, execute=True)`，一次申请读写及执行权限；工具可访问项目外文件，旧读写授权不会自动升级。链接及越界检查仍然有效。

需要等待时使用 `with waiting():`，框架在下一空行显示与插件克隆相同的彩色点阵，不重复打印等待或取消说明。可传入已本地化的纯文本作为简短状态；空文本只显示动画。授权或菜单出现时自动暂停，结束、异常或取消时自动清除；非交互和重定向输出不显示动画。此接口只负责显示，不能替代宿主任务的取消、期限和授权检查，也不能保证插件本地代码支持 Esc 取消。

```python
from core.sdk import waiting

def synchronize(task):
    with waiting():
        return task()
```

例如，在插件的菜单或命令行入口中检查已有项目及其构建目录时，可直接调用 SDK，不需要插件维护授权缓存：

```python
from core.sdk import host_path_info

def inspect_build_directory(project, build_directory):
    host_path_info(project, True)
    return host_path_info(build_directory, True)
```

当 `build_directory` 是 `project` 下的普通子目录时，首次授权项目目录后，第二次读取会复用该授权。两个路径都由调用方提供，目录必须存在。

`host_execute` 默认按具体命令授权；显式项目读写及执行授权也覆盖在其目录树中启动的工具。`output_header` 可传入面包屑列表，让交互终端在授权结束、工具输出开始前清屏并显示标题，非交互模式不会清屏。确需反复调用同一工具时，可在 options 中设置 `authorization_scope: "tool"`，请求按插件指纹、工具路径和摘要授权，允许该插件在任意工作目录使用任意参数运行该工具；宿主会明确显示这一范围。工具范围不支持 `output_parent`、`config_base64` 或 `tool_dependencies`。插件指纹变化需重新授权；按工具授权时工具内容变化也需重新授权。项目读写及执行授权不绑定单个工具，旧的命令授权不会自动扩大。

工具构建或安装需要进度动画时，先检查 SDK 是否提供 `HOST_TOOL_PROGRESS`，再向 `host_execute` 传入[进度描述](plugin-spec.md#宿主能力与多语言)。插件提供本地化阶段名称及第三方日志的字面标记，框架负责在宿主终端渲染。百分比来自工具输出，只有起止信号的阶段显示等待动画。插件代码自身的阶段可用 `progress_step(message, failed_label)` 显示同款动画。旧 SDK 可省略此选项以兼容原始输出流。下载等能准确计数的插件阶段先检查 `HOST_COUNTED_PROGRESS`，再使用 `with progress_step(label, failed_label, total=len(files)) as update:`。每项实际成功落盘后调用 `update(done)`，失败时保留未完成状态。

若工具日志需要等业务结果确认后再显示，可检查 SDK 是否支持 `finish_host_tool_output` 并启用 `defer_output`。工具退出码、产物校验和归档等所需步骤均通过后，调用 `finish_host_tool_output(True)`。任一步骤失败则调用 `finish_host_tool_output(False)`，宿主回放当前尚未处理的日志。

扩展交互时优先为现有菜单类型增加可序列化信息行，不为每个业务定义一套绘制函数；如需新的交互类型，须同时设计宿主实现、SDK 签名、沙盒转发、取消和非交互语义，并补充中英文及终端测试。授权窗口与业务确认保持独立，插件不得传入或控制框架授权结果。

`select` 对超过 9 项且从 1 起连续编号的选项自动分页，返回的仍是原始编号。需要保留对象、使用自定义按键或翻页后继续浏览时使用 `paged_select`。默认按 `p` / `n` 单键翻页，使用框架“上一页”“下一页”文案；`select` 的快捷键占用翻页键时会自动选择其他按键。分页 `page_size` 为 1..9；`choice_of(item, index_on_page)` 可省略 `key` 自动编号，也可提供不重复的单字符键，`0`、`p`、`n` 留给返回和翻页。`subtitle_of(page, total_pages)` 和 `initial_page` 的页码从 0 开始；后者可定位上次选择所在页，不会自动确认。未设置 `on_select` 时返回所选对象；设置时调用 `on_select(item)` 并继续浏览。0/Esc 返回 `None`。

`confirm` 的 0/Esc 返回 `False`；普通确认可受 `--yes` 影响，必须由用户明确选择时传 `require_explicit=True`。`text_input` 的 Esc 抛出 `UserCancel`，调用方在适当的菜单边界处理；`default` 是空行时返回的值，`hint` 只改变展示。需要保持已有值时应传入 `default`，重置应提供明确操作。Ctrl+C 退出交互；不能把取消当成确认。

例如修改已有路径时，插件可以在自身菜单边界处理取消，让空输入保留原值；以下文案键须先由插件注册：

```python
from core.sdk import UserCancel, confirm, t, text_input

def edit_path(breadcrumb, current):
    try:
        value = text_input(breadcrumb, t("myplugin.path_prompt"), default=current,
                           hint=t("myplugin.path_hint"))
    except UserCancel:
        return None
    if value != current and not confirm(breadcrumb, t("myplugin.path_confirm"),
                                        require_explicit=True):
        return None
    return value
```

长列表可用 `paged_select` 将原始对象映射成页面选项；插件只在本地执行 `choice_of`、`subtitle_of` 和可选的 `on_select`，不将 Python 函数传给宿主。需要自由输入名称和数字时用 `select_input`，在业务代码中定义并测试数字优先级和 `0` 返回语义。选择宿主目录或工具后，直接通过相应 SDK 能力请求目标，授权选项由框架显示。

`console` 是 Rich Console，适合必要的只读输出；普通消息优先使用下列主题接口。菜单由 SDK 呈现，插件避免重复自行绘制编号或清屏。

## SDK 导出目录

下表列出全部公开导出；需要精确参数和默认值时以 `core/sdk.py` 和本节签名为准。除宿主能力函数外，返回值均在插件进程内使用。

| 类别 | 公开接口 | 用途和边界 |
|---|---|---|
| 版本和入口 | `SDK_API_VERSION`, `ModuleInfo` | 当前为 4；`ModuleInfo(key, description_key, order, entry, platforms=..., enabled=True, label=None, icon=None)`。入口显示元数据以清单为准。 |
| 文案 | `t(key, **kwargs)`, `current_lang()`, `register_locale(catalog)` | 注册 `{语言: 嵌套字典}`，使用插件自己的命名空间；不覆盖平台文案。 |
| 主题 | `get_color(token)`, `get_icon(token)`, `print_success(msg, elapsed=None)`, `print_error(msg)`, `print_warning(msg)`, `print_info(msg, *, show_icon=True)` | 复用平台主题和状态输出。 |
| 菜单 | `select`, `select_input`, `multi_select`, `paged_select`, `confirm`, `text_input`, `pause`, `clear_screen`, `print_header`, `waiting`, `UserCancel`, `console` | 签名、取消及非交互语义见上节。 |
| 跨插件服务 | `get_service(name)`, `open_service_menu(name, breadcrumb, context=None)` | 前者返回远程代理，实际调用时核验；后者调用已授权服务的 `open_menu`。缺少提供方、契约或授权时调用失败，不自动安装。 |
| 状态和路径 | `plugin_data_dir(namespace)`, `data_dir()`, `cache_dir()`, `log_dir()`, `plugins_dir()` | 使用自身 `plugin_data_dir` 保存持久业务状态；名称必须与清单中的插件身份完全一致。其他路径是本次隔离工作区内路径或只读插件快照，不代表宿主路径或持久授权。 |
| 状态格式 | `load_yaml(path)`, `save_yaml(path, data)`, `get_logger(name='opskit')` | 在自身可访问的隔离路径内读写；注意不要记录敏感信息。 |
| 沙盒进程 | `run(cmd, *, cwd=None, env=None, timeout=None, capture=False, check=True, output_limit=1048576)`, `run_lines(cmd, *, cwd=None, env=None, timeout=None)`, `which(name)`, `cmd_ok(cmd, timeout=5)` | 只在沙盒权限及进程限制内工作；不代表能运行宿主 Git 或 Creator。 |
| 依赖 | `ensure_python_package(package, import_name='')` | 仅检查已准备运行时中的依赖，不安装任何软件或 Python 包。 |
| 宿主文件 | `read_host_file(directory, relative)`, `compare_write_host_file(directory, relative, expected_sha256, content)`, `create_host_file(directory, relative, content)`, `host_file_status(directory, relative)`, `artifact_info(directory, relative)` | 通过宿主授权及有界校验；写入须处理失败与中断。 |
| 宿主工具 | `host_execute(executable, arguments, directory, options=None)`, `host_path_info(path, directory=False, write=False, execute=False)`, `host_find_executable(name, preferred="")`, `host_file_version(path)`, `host_asar_directories(directory, relative, member)` | 按已获准的读取目录树、工具和执行范围访问。`host_asar_directories` 只列举 ASAR 目录。`host_find_executable` 查找普通绝对路径，返回前仍需目录读取授权；找不到时返回空字符串，不执行程序。 |
| 产物和网络 | `host_artifacts(action, parameters)`, `https_request(url, method='GET', headers=None, body=b'')`, `upload_file(directory, relative, url, sha256, headers=None)` | 使用框架的产物凭据、受限 HTTPS 和内容摘要绑定上传。HTTPS 响应提供 `etag`，正文上限 1 MiB。 |

插件自身状态按身份和服务调用方隔离，成功调用只有通过最终的期限、授权和状态检查后才提交。临时工作区并非永久存储；不得推断其他插件的状态目录。不要使用已删除的 `ensure_software`、`software_bin` 或旧 `host_archive_directories`。

## 服务契约

提供方必须为每个 `provides` 项声明 `service_contracts`。调用方在 `uses` 中写明 `service`、`provider`、业务 `api_version` 和 `methods`。在需要服务的菜单入口调用 `request_plugin_install(provider)`，只传 `uses` 中已在宿主登记的提供方 ID。返回 `True` 时可以立即调用 `get_service`。用户取消或拒绝时返回 `False`，此时退出当前操作。需要确认时应在交互终端运行，候选不符合要求或流程无法完成时抛出 `PermissionError`。

确认范围包含指定指纹的授信和声明的方法调用权限。提供方运行时使用具体目录、网络地址等资源时，仍按对应能力授权检查。方法与结果必须是可序列化数据。可运行的接入示例见 [示例插件](examples/example-plugin)，从「云端配置」触发。

```yaml
uses:
  - service: storage
    provider: storage_plugin
    api_version: 1
    methods: [read, open_menu]
```

操作者先分别审核调用方和提供方及其指纹，再使用 `opskit plugin authorize-service` 授权精确的调用边。插件更新、指纹变化或撤权后不能沿用旧授权；每次调用都重新验证。`get_service` 不表示提供方已经可用，访问方法或 `service_api_version` 时才会进行实际 RPC 和校验。

### 跨插件菜单导航

需要让调用方打开某个菜单时，提供方可以在既有服务的 `open_menu` 中公开 `action: navigate` 和稳定的 `destination` 标识。提供方在服务文档列出允许的目标、参数和返回值，自己渲染菜单、解释选项并保存配置。未知目标应明确拒绝。目标标识取自公开契约，独立于界面语言、菜单序号、模块名和函数名。

```python
if request_plugin_install("storage_hub"):
    get_service("storage").open_menu(breadcrumb, {
        "action": "navigate",
        "destination": "configuration",
        "configure_upload_root": True,
    })
```

这是一次交互式服务调用。菜单结束后控制权回到调用方，保存与否按提供方契约读取。需要使用选择结果时直接调用服务代理。提供方沿用宿主绑定的调用方身份及隔离状态，调用方传递业务选项即可。宿主继续处理安装、授信和资源授权。

当业务操作允许在缺少配置时打开设置，提供方可以提供显式选项，例如 `select_profile` 的 `configure_if_missing`。它复用同一个设置菜单，返回后重新读取配置。没有可用配置或用户取消时结束当前业务操作。非交互调用使用显式配置和业务参数。

## 发布前检查

1. 校验清单、菜单四字规则、主题图标宽度，以及中英文的键和占位符。
2. 测试返回、Esc、Ctrl+C、空输入、分页超过九项、非交互与 `--yes`，覆盖失败、撤权及数据保留。
3. 审核依赖和插件来源，准备匹配当前 SDK 摘要的隔离运行时；内容变化需重新审核授信。只生成 `CHECKSUMS.yaml` 不等于签名或授权。
4. 分别测试源码、实际 Windows 原生沙盒和发布包；宿主工具、网络服务、目标平台及另一台机器都需要各自的真实验收，不能以模拟测试代替。

在框架源码检出中运行 `python -m pytest -q tests/test_plugin_developer_example.py tests/test_plugin_sdk4.py tests/test_plugin_framework_menu.py`，可检查示例的中英文菜单、命令行和框架交互契约。这不是第三方插件的完整自动审核：插件维护者还需针对自己的动态菜单、实际图标、授权目标和业务命令执行上述检查，并在实际终端验证图标占位。

确认界面应使用简短的操作标题，将对象、路径和必要的操作后果放入 `info_lines`；删除、覆盖、同步或授权等操作必须保留决定所需的范围与风险信息。确认项和取消行为由框架统一呈现，不要将长问题或动态对象拼入面包屑标题。

文本输入由宿主统一渲染。默认值和输入提示直接显示在输入行，无需添加括号。可编辑的建议值使用 `initial`，空行回退值使用 `default`，敏感默认值由宿主遮挡。


### 输入界面的 ESC 返回

`text_input` 在 ESC 时抛出 `UserCancel`。单步输入由直接打开它的菜单捕获，再重绘菜单。菜单中的 ESC 与「返回」都只离开本层。多步输入用 `input_steps(*steps)`，每个回调接收该字段上一次确认的值，首次进入接收 `None`。后续字段按 ESC 回到前一字段，首字段按 ESC 将 `UserCancel` 交还直接调用方。普通字段用 `initial` 提供可编辑的上一次值，敏感字段不预填明文。Ctrl+C 沿用框架的全局退出行为。完整表单提交前的草稿由调用方管理，已经逐项保存的配置在返回时保留。
