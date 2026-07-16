# OpsKit 插件开发规范

OpsKit 支持外部插件：无需改动 OpsKit 源码、无需重新打包，把插件仓库 `git clone`
进插件目录（或用菜单「🧩 插件工具 → 插件管理 → 安装插件」）即可扩展「插件工具」菜单，
安装 / 更新 / 卸载热插拔立即生效，无需重启。

本文是插件开发的唯一权威规范。按「10 分钟上手」一节即可跑通第一个插件，
其余章节按需查阅。

## 目录

1. [10 分钟上手](#10-分钟上手)
2. [插件目录](#插件目录)
3. [清单规范（plugin.yaml）](#清单规范pluginyaml)
4. [形态一：python 插件](#形态一python-插件进程内加载)
5. [形态二：exec 插件](#形态二exec-插件子进程执行语言无关)
6. [SDK API](#sdk-apiapi_version--1)
7. [信任模型与安全边界](#信任模型与安全边界)
8. [开发调试与排错](#开发调试与排错)
9. [必须遵守的约束（红线）](#必须遵守的约束红线)
10. [发布约定](#发布约定)
11. [发布前自查清单](#发布前自查清单)

## 10 分钟上手

以开发模式（源码运行 OpsKit）为例，创建一个最小 python 插件：

```bash
# 1. 在插件目录下建插件
mkdir -p plugins/hello/hello_pkg

# 2. 写清单
cat > plugins/hello/plugin.yaml <<'EOF'
name: hello
version: 0.1.0
api_version: 1
kind: python
entry: hello_pkg
icon: "👋"
label:
  zh: 你好插件
  en: Hello Plugin
EOF

# 3. 写入口包
cat > plugins/hello/hello_pkg/__init__.py <<'EOF'
from core.sdk import ModuleInfo


def register() -> ModuleInfo:
    from hello_pkg.menu import entry
    return ModuleInfo(
        key="hello",
        description_key="hello.desc",
        order=50,
        entry=entry,
        platforms=["linux", "darwin", "windows"],
    )
EOF

cat > plugins/hello/hello_pkg/menu.py <<'EOF'
from core.sdk import pause, print_success, register_locale, t

register_locale({
    "zh": {"hello": {"desc": "示例插件", "done": "你好，OpsKit！"}},
    "en": {"hello": {"desc": "Demo plugin", "done": "Hello, OpsKit!"}},
})


def entry() -> None:
    print_success(t("hello.done"))
    pause()
EOF

# 4. 运行 OpsKit，在「插件工具 → 插件管理 → 更新插件」中选中 hello 确认信任，
#    立即出现在「插件工具」菜单（无需重启）
python3 main.py
```

要点：**新插件必须先经用户确认信任，否则不会加载**；手动 clone 的插件在
「更新插件」中选中即弹信任确认（见
[信任模型](#信任模型与安全边界)）。

## 插件目录

| 场景 | 路径 |
|---|---|
| 环境变量覆盖 | `OPSKIT_PLUGINS_DIR` |
| 开发模式（源码运行） | `<项目根>/plugins/` |
| 打包模式 Linux root | `/var/lib/opskit/plugins/` |
| 打包模式 Linux 非 root | `~/.local/share/opskit/plugins/` |
| 打包模式 macOS | `~/Library/Application Support/opskit/plugins/` |
| 打包模式 Windows | `%LOCALAPPDATA%\opskit\plugins\` |

每个插件是插件目录下的**一个子目录**，根部必须有 `plugin.yaml` 清单。
以 `.` 或 `_` 开头的目录被忽略。

## 清单规范（plugin.yaml）

```yaml
name: myplugin           # 必填。唯一标识，^[a-z][a-z0-9_]*$，与内置模块 key 不得冲突
version: 1.0.0           # 必填。插件自身版本，必须是合法 semver（x.y.z，允许 -/+ 后缀），非法拒绝加载
api_version: 1           # 必填。依赖的 SDK API 大版本，必须等于当前 OpsKit 的 SDK_API_VERSION
kind: python             # 必填。python | exec
entry: myplugin_pkg      # 必填。python: 插件目录内的包名; exec: 相对可执行文件路径
order: 50                # 可选。插件工具菜单排序权重（建议 50+）
platforms: [linux, darwin, windows]   # 可选。默认三平台
icon: "🚀"               # 可选。菜单图标（emoji）
label:                   # 可选。菜单显示名（按语言，缺省回退 en → 任意）
  zh: 我的插件
  en: My Plugin
description:
  zh: 插件描述
  en: Plugin description
permissions:             # 可选。权限声明（声明式，不强制），信任确认时展示给用户
  - network              # 建议值：network / filesystem / exec / root
  - exec
group: insight-flow      # 可选。分组 id（^[a-z][a-z0-9-]*$），同 group 的插件在
                         # 「插件工具」中聚合为一个入口，进入后再选具体插件；
                         # 插件管理（更新 / 卸载）的选择列表采用同样的分组结构，
                         # 组内按插件显示名（label）列出
group_icon: "📊"        # 可选。分组入口图标
group_label:             # 可选。分组入口显示名（按语言）
  zh: Insight Flow
  en: Insight Flow
```

`exec` 的 `entry` 支持按平台映射：

```yaml
kind: exec
entry:
  linux: bin/run.sh
  darwin: bin/run.sh
  windows: bin/run.exe
```

校验失败（缺字段 / name 非法 / version 非 semver / kind 非法 / api_version 不匹配 /
entry 不存在）时插件被跳过并写入日志（`logs/opskit.log`），不影响主程序与其他插件。

## 形态一：python 插件（进程内加载）

目录结构（推荐三层结构，与内置模块一致）：

```
myplugin/
├── plugin.yaml
└── myplugin_pkg/            # entry 指向的包
    ├── __init__.py          # 必须暴露 register() -> ModuleInfo
    ├── menu.py              # UI 层：菜单渲染与交互
    ├── commands.py          # 逻辑层：业务实现（可被单测）
    └── constants.py         # 常量层：URL / 路径 / 超时等
```

`__init__.py`：

```python
from core.sdk import ModuleInfo


def register() -> ModuleInfo:
    from myplugin_pkg.menu import entry
    return ModuleInfo(
        key="myplugin",              # 会被清单 name 覆盖，保持一致即可
        description_key="myplugin.desc",
        order=50,
        entry=entry,
        platforms=["linux", "darwin", "windows"],
    )
```

`menu.py` 示例（含子菜单循环 + 自带文案）：

```python
from core.sdk import (
    UserCancel,
    get_icon,
    pause,
    print_success,
    register_locale,
    select,
    t,
)

register_locale({
    "zh": {"myplugin": {"desc": "我的插件", "deploy": "部署", "done": "部署完成"}},
    "en": {"myplugin": {"desc": "My plugin", "deploy": "Deploy", "done": "Deployed"}},
})


def entry() -> None:
    while True:
        try:
            key = select(
                breadcrumb=["OpsKit", t("myplugin.desc")],
                subtitle=t("prompt.select"),
                choices=[{"key": "1", "label": f"🚀 {t('myplugin.deploy')}"}],
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            break
        if key is None:
            break
        if key == "1":
            print_success(t("myplugin.done"))
            pause()
```

**entry 包名规范**：

- 必须匹配 `^[a-z][a-z0-9_]*$`；
- **必须带插件特征后缀**（如 `myplugin_pkg`），禁止使用 `core` / `rich` / `yaml` /
  `utils` 等通用名——与主程序或其依赖重名的包会被拒绝加载；
- 两个插件的 entry 包名也不得相同（后加载者被拒绝）。

**文案（i18n）**：所有用户可见文字用 `t()` 输出，插件自己的文案在包 import 时通过
`register_locale()` 注册，key 用 `<name>.` 前缀命名空间（如 `myplugin.deploy`），
不得占用主程序已有 key（冲突时插件 key 被忽略）。

**第三方依赖**：python 插件运行在 OpsKit 进程内，**不能** `pip install` 额外依赖
（打包后的单文件程序没有 pip 环境）。只能使用标准库 + SDK 导出。需要重依赖的场景
请改用 exec 形态自带运行时。

## 形态二：exec 插件（子进程执行，语言无关）

适合 Go / Rust / Shell 编写的工具，或需要第三方依赖的场景。菜单项被选中后 OpsKit
以继承 stdio 的子进程运行 `entry` 可执行文件（工作目录为插件根目录），退出后按
任意键回到主菜单。

```
mytool/
├── plugin.yaml              # kind: exec, entry: bin/mytool
└── bin/
    └── mytool               # 可执行文件（chmod +x；Windows 用 .exe/.bat）
```

约定：

- `entry` 必须位于插件目录内（相对路径逃逸会被拒绝）；
- 子进程直接使用用户的终端（stdio 继承），可以自由做交互式 UI；
- 退出码不影响 OpsKit，启动失败只显示短错误并回菜单；
- 菜单显示名 / 图标 / 描述全部来自清单的 `label` / `icon` / `description`。

## SDK API（api_version = 1）

`core/sdk.py` 是 python 插件唯一允许的依赖面，导出：

| 分类 | 导出 |
|---|---|
| 协议 | `ModuleInfo` |
| i18n | `t` / `current_lang` / `register_locale` |
| 主题 | `get_color` / `get_icon` / `print_success` / `print_error` / `print_warning` / `print_info` |
| 交互 | `select` / `multi_select` / `paged_select` / `confirm` / `text_input` / `pause` / `clear_screen` / `print_header` / `UserCancel` / `console`（`text_input` 支持 `info_lines` 插入 ├─ 信息行；`multi_select` ↑↓ 移动、空格勾选、回车确认，默认全不选；`paged_select` 分页单选，每页最多 9 项、n/p 翻页） |
| 执行 | `run` / `run_lines` / `which` / `cmd_ok` |
| 路径 | `data_dir` / `cache_dir` / `log_dir` / `plugins_dir` / `plugin_data_dir` |
| YAML | `load_yaml` / `save_yaml`（插件配置 / 构建记录读写，禁止直接 import yaml） |
| 日志 | `get_logger` |
| 软件安装 | `ensure_software(key)`（复用平台软件配方检测 + 按需安装推荐版本，如 golang / docker / nodejs；仅限注册表内配方，插件禁止自行实现安装器） |
| Python 依赖 | `ensure_python_package(package, import_name="")`（已可导入静默返回 True，否则用应用 venv 的 pip 安装，失败返回 False；供插件按需声明重依赖，如 boto3） |
| 插件间服务 | `get_service(name)` / `open_service_menu(name, breadcrumb, context, source)`（见下「插件间服务」章节） |

不兼容变更（删除导出 / 改签名语义）才递增 `SDK_API_VERSION`；新增导出不递增。
`api_version` 不匹配的插件会被静默跳过（写日志），OpsKit 升级大版本后插件需适配
并更新清单。

## 插件间服务（provides / uses）

插件可以向其他插件提供可复用能力（如通用云存储），调用方无需依赖提供方包名。

**提供方**（仅限 python 插件）：

1. 清单声明 `provides: [storage]`
2. entry 包模块级暴露 `provide_service(name: str) -> object | None`
3. 服务对象携带 `service_api_version: int`；带交互界面的服务实现
   `open_menu(breadcrumb: list[str], context: dict) -> None`

**调用方**：

```yaml
uses:
  - service: storage
    source: git@example.com:org/storage-hub.git   # 提供方建议安装来源（可选）
```

```python
from core.sdk import get_service, open_service_menu

# 菜单项一行接入：未安装时按 source 自动 clone 安装（仅信任确认一次交互）
open_service_menu(
    "storage",
    breadcrumb=[...],
    context={"caller": "myplugin"},
    source="git@example.com:org/storage-hub.git",
)

# 程序化使用：提供方未安装 / 未信任时返回 None
svc = get_service("storage")
```

安全模型与插件加载一致：提供方必须已通过信任确认与 CHECKSUMS 完整性校验才会
被加载；未安装时直接自动 clone，安装后展示概要并等待用户确认信任，拒绝则回滚删除。插件
安装 / 更新 / 卸载后服务缓存自动失效。服务 API 变更时递增 `service_api_version`，
调用方自行校验兼容性。

## 信任模型与安全边界

插件代码加载前必须经用户明确信任（同 Homebrew tap 模型）：

- 首次信任：菜单安装时展示名称/版本/形态/权限声明，用户确认后记录插件目录内容指纹
  （全部文件 sha256 汇总，跳过 .git / __pycache__）到 `<data_dir>/plugin_trust.yaml`；
  手动 `git clone` 的插件在「更新插件」中选中即弹信任确认，确认后才会加载
- 平台更新自动继承：已信任插件经「插件管理 → 更新插件」拉取的新内容自动继承信任，
  首次确认后更新不再重复询问；例外：版本回退（防降级攻击）需用户显式确认，
  内容与 CHECKSUMS.yaml 不符直接拒绝加载
- 变化重确认：插件内容被平台更新流程之外的途径改动（如手动 `git pull` / 改文件）
  时指纹失效，重新信任前不加载，防止「先发好版本、后续更新投毒」
- 产物指纹清单：插件根目录存在 `CHECKSUMS.yaml`（文件级 sha256 清单，由
  `opskit plugin fingerprint` 生成）时，平台在每次加载前校验目录实际内容与清单
  一致，不一致即拒绝加载并告警（可能被篡改）；无清单的存量插件回落 TOFU 模型
- 来源警告：安装时 URL 主机不在配置 `plugin.trusted_sources` 白名单时显示警告，
  真正的安全闸门是 clone 后的信任确认
- 防崩隔离：插件 import / register / 菜单入口的任何异常（含 `sys.exit()`）只写日志 +
  短提示，不终止主程序；entry 包名与已有模块（core / rich 等）重名时拒绝加载

⚠ 安全边界：`permissions` 只是声明式透明度机制，**不是沙箱**。python 插件与主程序
同进程、exec 插件继承当前用户权限，被信任的插件拥有与 OpsKit 同等的系统权限；
请只信任来源可靠的插件，并避免以 root 运行 OpsKit（除非确实需要）。

## 开发调试与排错

开发工作流：

1. 源码运行 OpsKit：`python3 main.py`，插件放 `<项目根>/plugins/<name>/`
   （或 `export OPSKIT_PLUGINS_DIR=/path/to/dev-plugins` 指向任意目录）；
2. 每次改动插件文件后内容指纹变化，需重新信任——进「插件工具 → 插件管理 → 更新插件」
   选中该插件重新确认，确认后立即生效；开发目录若存在 CHECKSUMS.yaml，改动后需
   重新运行 `opskit plugin fingerprint`（或临时删除该文件），否则校验不符拒绝加载；
3. 插件被跳过 / 不显示时，先看日志：`logs/opskit.log`（grep 插件名）。

常见问题速查：

| 现象 | 原因 | 处理 |
|---|---|---|
| 插件工具不出现插件 | 未信任 / 内容变化后未重新信任 | 插件管理 → 更新插件 → 选中确认信任 |
| 日志 `manifest missing fields` | plugin.yaml 缺必填字段 | 补齐 name/version/api_version/kind/entry |
| 日志 `invalid version` | version 非 semver | 改为 x.y.z 格式 |
| 日志 `integrity check failed` | 内容与 CHECKSUMS.yaml 不符 | 重新生成清单；若非自己改动警惕篡改 |
| 日志 `api_version ... incompatible` | 与当前 SDK 大版本不符 | 适配后更新清单 api_version |
| 日志 `entry ... shadows an existing module` | entry 包名与已有模块重名 | 改带后缀的包名（如 `xxx_pkg`） |
| 日志 `entry package has no register()` | `__init__.py` 未暴露 register | 按模板补 register() |
| 日志 `exec entry not found` | entry 路径不存在 / 未随仓库提交 | 检查相对路径与可执行位 |
| 菜单显示原始 key（如 `myplugin.desc`） | 文案未注册 | 包 import 时调用 `register_locale()` |
| 运行中报「插件 X 异常退出」 | entry 抛异常 / sys.exit() | 看日志定位；主程序不受影响 |

## 必须遵守的约束（红线）

- 只准 `from core.sdk import ...`，**禁止** import core 其他内部模块
  （内部模块随时重构，不保证兼容）；
- 禁止修改 `sys.path`、`sys.modules`、全局钩子等进程级状态；
- 插件的配置、缓存、构建产物一律落盘 `plugin_data_dir(<命名空间>)`
  （`<data-dir>/plugin-data/<命名空间>/`），**禁止写入插件目录自身**（会破坏
  信任指纹导致下次加载失效），也禁止污染其他任意路径；命名空间由开发者声明，
  多个插件传同一命名空间即共享目录；
- 界面一律用 SDK 交互组件（select / confirm / text_input / pause / print_*），
  禁止自造菜单渲染与交互流程；每个执行阶段必须 `clear_screen()` +
  `print_header(面包屑)` 保持标题常驻，与主程序风格一致；
- 菜单列表每页最多展示 9 项：选项可能超过 9 个的列表必须用 `paged_select`
  分页（n 下一页 / p 上一页，翻页文案缺省用平台通用「上一页 / 下一页」），
  禁止一页堆超过 9 个序号选项；
- 禁止调用 `sys.exit()` / `os._exit()`（会被守卫拦截，但属于违规行为）；
- 用户可见文字一律走 `t()` + `register_locale()`，不硬编码单一语言；
- 长时间操作要有进度提示，错误信息保持简短（详细内容写日志 `get_logger()`）；
- 环境依赖安装一律走 `ensure_software(key)` 复用平台软件配方（安装进度与反馈
  同软件菜单一致），插件禁止自行下载 / 脚本安装系统软件；
- 文件读写使用 `data_dir()` / `cache_dir()` 下的自有子目录，不污染其他路径；
- `permissions` 如实声明（network / filesystem / exec / root），获取用户信任。

## 发布约定

- 仓库命名建议：`opskit-plugin-<name>`
- 安装：菜单「插件工具 → 插件管理 → 安装插件」输入 URL，或 `git clone <repo> <plugins_dir>/<name>`
- 更新：菜单「插件管理 → 更新插件」，已信任插件自动继承信任并热重载；手动 `git pull`
  属平台外改动，需重新信任
- 卸载：菜单「插件管理 → 卸载插件」，删除目录 + 移除信任记录，列表即刻消失
- 信任：安装/手动 clone 后需在菜单确认信任，否则插件不加载
- 版本：清单 `version` 是唯一权威版本来源，semver，每次发布必须递增（版本回退会触发
  用户降级确认）；建议 git tag 与 version 对应（`v1.0.5`），并在仓库 README 标注兼容的
  `api_version`
- 产物指纹：发布前在插件目录运行 `opskit plugin fingerprint <目录>` 生成
  `CHECKSUMS.yaml` 并随仓库提交；`opskit plugin fingerprint <目录> --check` 可本地验证；
  平台加载前校验不符即拒绝加载，防发布后篡改
- 运行时数据禁止写入插件目录（会破坏指纹与 CHECKSUMS 校验），一律落盘
  `plugin_data_dir(<命名空间>)`

## 发布前自查清单

- [ ] `plugin.yaml` 五个必填字段齐全，name 合法且不与内置模块冲突
- [ ] `api_version` 与目标 OpsKit 的 SDK 版本一致
- [ ] python 插件：entry 包名带特征后缀；只 import `core.sdk`；无第三方依赖
- [ ] exec 插件：可执行文件在插件目录内且有可执行位；`platforms` 与提供的产物一致
- [ ] `label` / `description` 提供 zh + en；自有文案通过 `register_locale()` 注册
- [ ] `permissions` 如实声明
- [ ] `version` 已递增，并运行 `opskit plugin fingerprint` 重新生成 CHECKSUMS.yaml
- [ ] 在干净环境 clone → 信任 → 验证「插件工具」菜单立即出现、功能可用
- [ ] 菜单入口内抛异常只显示短错误、能正常返回主菜单（不会杀死 OpsKit）
