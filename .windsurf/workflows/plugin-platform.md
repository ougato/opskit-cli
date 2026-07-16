# 外部插件平台

## 功能概述

OpsKit 开放外部插件扩展：用户把插件仓库 git clone 进插件目录（或用「插件工具 → 插件管理」安装），
信任确认后立即出现在「插件工具」菜单（热插拔，无需重启）。支持两种插件形态：

- **python**：进程内加载，实现 `register() -> ModuleInfo`，只准依赖 `core/sdk.py` 稳定 API
- **exec**：子进程执行任意可执行程序（Go / Rust / Shell），语言无关

## 涉及文件

| 文件 | 职责 |
|---|---|
| `core/sdk.py` | 插件 SDK 稳定 API 层（SDK_API_VERSION = 1，含 print_header / plugin_data_dir） |
| `core/plugin.py` | 清单解析校验 + python/exec 插件加载 + 故障隔离 |
| `core/paths.py` | `plugins_dir()` + `plugin_data_dir(ns)`（插件数据唯一落盘点 `<data-dir>/plugin-data/<ns>/`） |
| `core/constants.py` | `DIR_PLUGINS` / `FILE_PLUGIN_MANIFEST` / `PLUGIN_API_VERSION` |
| `core/module.py` | `ModuleInfo` 新增 `label` / `icon` 可选字段 |
| `core/loader.py` | 只发现内置模块；`builtin_module_keys()` 供插件 key 冲突检查 |
| `main.py` | 主菜单渲染支持 label / icon 覆盖 |
| `plugin/` | 插件工具模块（插件管理：安装 / 更新 / 卸载 + 已信任插件入口） |
| `docs/plugin-spec.md` | 插件开发规范（对外文档） |
| `tests/test_plugin.py` | 自动化测试 |

## 核心实现流程

1. 主菜单 `discover_modules()` 只加载内置模块；外部插件由「插件工具」菜单每轮循环
   调用 `plugin.commands.loaded_plugins()` 实时扫描加载（热插拔，打包模式同样生效）
2. 扫描 `plugins_dir()` 一级子目录，读 `plugin.yaml`：
   校验必填字段 → name 格式 `^[a-z][a-z0-9_]*$` → kind ∈ {python, exec}
   → `api_version == PLUGIN_API_VERSION` → 与内置模块 key 不冲突
3. python：`sys.path` 注入插件目录 → import entry 包 → `register()` → 用清单的
   name/order/platforms/label/icon 覆盖
4. exec：解析 entry 路径（支持按平台映射，路径逃逸拒绝）→ 生成继承 stdio 的
   子进程入口，退出后 `pause()` 回主菜单
5. 任一步失败只写 `logs/opskit.log` 并跳过该插件，不影响其他模块
6. 热插拔：`load_plugin` / `unload_plugin`（清 sys.modules + sys.path），
   安装 / 更新 / 卸载立即生效；`modules.<key>.enabled` 仅作配置项过滤，无菜单
7. 分组：清单可选 `group` / `group_icon` / `group_label`，同 group 插件在插件工具
   聚合为一个入口（图标/名取组内首个声明者），进入后再选具体插件

## 跨平台处理

- 插件目录按 `data_dir()` 派生：Linux root `/var/lib/opskit/plugins`、
  非 root `~/.local/share/opskit/plugins`、Windows `%LOCALAPPDATA%\opskit\plugins`、
  macOS `~/Library/Application Support/opskit/plugins`
- exec entry 支持 `{linux: ..., darwin: ..., windows: ...}` 平台映射
- 清单 `platforms` 字段复用 loader 现有平台过滤

## 已知限制

- python 插件与主程序同进程，恶意/劣质代码无沙箱隔离（信任模型同 Homebrew tap）
- 更新热重载在插件未运行时才安全；正在运行的插件菜单退出后新代码才生效
- exec 插件为整程序接管终端模式，暂无 JSON 协议双向通信
- 插件自带 i18n 文案暂不合并进主程序 locale，需用清单 label/description 或自行处理

## 测试验证

```bash
.venv/bin/python -m pytest tests/test_plugin.py -q   # 24 用例
.venv/bin/python -m pytest tests/ -q                 # 全量 527 passed
```

手工验证：`OPSKIT_PLUGINS_DIR=/tmp/opk-plugins python3 main.py`，
主菜单常驻「🧩 插件工具」，进入后首项为「⚙️ 插件管理」，已信任插件
（如「🚀 Hello Plugin」）列在其后，安装 / 更新 / 卸载无需重启即刻生效。
