# OpsKit 插件开发规范

OpsKit 支持外部插件：无需改动 OpsKit 源码、无需重新打包，把插件仓库 `git clone`
进插件目录（或用菜单「🧩 插件管理 → 安装插件」）即可扩展主菜单。

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

## 清单规范（plugin.yaml）

```yaml
name: myplugin           # 必填。唯一标识，^[a-z][a-z0-9_]*$，与内置模块 key 不得冲突
version: 1.0.0           # 必填。插件自身版本（semver）
api_version: 1           # 必填。依赖的 SDK API 大版本，必须等于当前 OpsKit 的 SDK_API_VERSION
kind: python             # 必填。python | exec
entry: myplugin_pkg      # 必填。python: 插件目录内的包名; exec: 相对可执行文件路径
order: 50                # 可选。主菜单排序权重（内置模块 1-40，建议 50+）
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
```

`exec` 的 `entry` 支持按平台映射：

```yaml
kind: exec
entry:
  linux: bin/run.sh
  darwin: bin/run.sh
  windows: bin/run.exe
```

校验失败（缺字段 / name 非法 / kind 非法 / api_version 不匹配 / entry 不存在）时插件被
跳过并写入日志（`logs/opskit.log`），不影响主程序与其他插件。

## 信任模型与安全边界

插件代码加载前必须经用户明确信任（同 Homebrew tap 模型）：

- 首次信任：菜单安装时展示名称/版本/形态/权限声明，用户确认后记录插件目录内容指纹
  （全部文件 sha256 汇总，跳过 .git / __pycache__）到 `<data_dir>/plugin_trust.yaml`；
  手动 `git clone` 的插件需在菜单「信任插件」确认后才会加载
- 变化重确认：插件内容一旦变化（如 `git pull` 更新）指纹失效，重新信任前不加载，
  防止「先发好版本、后续更新投毒」
- 来源警告：安装时 URL 主机不在配置 `plugin.trusted_sources` 白名单时强警告
- 防崩隔离：插件 import / register / 菜单入口的任何异常（含 `sys.exit()`）只写日志 +
  短提示，不终止主程序；entry 包名与已有模块（core / rich 等）重名时拒绝加载

⚠ 安全边界：`permissions` 只是声明式透明度机制，**不是沙箱**。python 插件与主程序
同进程、exec 插件继承当前用户权限，被信任的插件拥有与 OpsKit 同等的系统权限；
请只信任来源可靠的插件，并避免以 root 运行 OpsKit（除非确实需要）。

## 形态一：python 插件（进程内加载）

目录结构：

```
myplugin/
├── plugin.yaml
└── myplugin_pkg/            # entry 指向的包
    ├── __init__.py          # 必须暴露 register() -> ModuleInfo
    ├── menu.py              # UI 层（推荐三层结构，与内置模块一致）
    └── commands.py          # 逻辑层
```

`__init__.py`：

```python
from core.sdk import ModuleInfo


def register() -> ModuleInfo:
    from myplugin_pkg.menu import entry
    return ModuleInfo(
        key="myplugin",              # 会被清单 name 覆盖，保持一致即可
        description_key="plugin.myplugin.desc",
        order=50,
        entry=entry,
        platforms=["linux", "darwin", "windows"],
    )
```

`menu.py` 示例：

```python
from core.sdk import select, pause, print_success, t, get_icon, UserCancel


def entry() -> None:
    while True:
        try:
            key = select(
                breadcrumb=["OpsKit", "My Plugin"],
                subtitle=t("prompt.select"),
                choices=[{"key": "1", "label": "🚀 Deploy"}],
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            break
        if key is None:
            break
        if key == "1":
            print_success("deployed")
            pause()
```

**约束**：python 插件只允许 `from core.sdk import ...`，禁止 import core 其他内部模块
（内部模块随时重构，不保证兼容）。

## 形态二：exec 插件（子进程执行，语言无关）

适合 Go / Rust / Shell 编写的工具。菜单项被选中后 OpsKit 以继承 stdio 的子进程运行
`entry` 可执行文件（工作目录为插件根目录），退出后按任意键回到主菜单。

```
mytool/
├── plugin.yaml              # kind: exec, entry: bin/mytool
└── bin/
    └── mytool               # 可执行文件（chmod +x）
```

## SDK API（api_version = 1）

`core/sdk.py` 是插件唯一允许的依赖面，导出：

| 分类 | 导出 |
|---|---|
| 协议 | `ModuleInfo` |
| i18n | `t` / `current_lang` |
| 主题 | `get_color` / `get_icon` / `print_success` / `print_error` / `print_warning` / `print_info` |
| 交互 | `select` / `confirm` / `text_input` / `pause` / `clear_screen` / `UserCancel` / `console` |
| 执行 | `run` / `run_lines` / `which` / `cmd_ok` |
| 路径 | `data_dir` / `cache_dir` / `log_dir` / `plugins_dir` |
| 日志 | `get_logger` |

不兼容变更（删除导出 / 改签名语义）才递增 `SDK_API_VERSION`；新增导出不递增。

## 发布约定

- 仓库命名建议：`opskit-plugin-<name>`
- 安装：`git clone <repo> <plugins_dir>/<name>`，或菜单「插件管理 → 安装插件」输入 URL
- 更新：`git pull`，或菜单「插件管理 → 更新插件」
- 启用/禁用：写入 OpsKit 配置 `modules.<name>.enabled`（菜单可切换），重启生效
- 信任：安装/更新/手动 clone 后均需在菜单确认信任，否则插件不加载
