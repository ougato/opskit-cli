# OpsKit — 极简运维面板

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)](https://github.com/ougato/opskit-cli)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Download](https://img.shields.io/badge/download-file.icerror.top-blue)](https://file.icerror.top/d/mirror/soft/)

**OpsKit** is a lightweight, single-file server operations panel with a beautiful TUI (Terminal UI).
No web UI, no agent, no daemon. Just run it and go.

---

**OpsKit** 是一个极简的单文件服务器运维面板，使用终端 TUI 交互。
无需 Web UI、无需 Agent、无需守护进程，开箱即用。

---

## ✨ 功能特性 / Features

| 模块 | 功能 | Platforms |
|---|---|---|
| 📦 **软件管理** | Docker / Nginx / MySQL / Redis / PostgreSQL / MongoDB / Go / Python / Java / Node.js / WireGuard 一键安装 / 卸载 / 升级，多版本共存切换，搜索 + 分类浏览 | Linux, macOS, Windows |
| 📊 **系统监控** | CPU / 内存 / 磁盘 / 网络实时面板，进程列表（Top 15），实时刷新 | All |
| 🌐 **网络工具** | Ping / Traceroute / DNS 正反解析 / 端口扫描 / 下载测速 / 公网 IP | All |

---

## 🚀 快速开始 / Quick Start

### 方式一：一键安装（推荐）

```bash
curl -fsSL https://file.icerror.top/d/install/opskit.sh | bash
```

```powershell
irm https://file.icerror.top/d/install/opskit.ps1 | iex
```

```cmd
curl -fsSL https://file.icerror.top/d/mirror/soft/windows/opskit-windows-x64.exe -o opskit.exe
opskit.exe
```

> **注意**：CMD 方式仅下载到当前目录，不配置 PATH，不支持热更新。完整安装请使用 PowerShell 命令。

安装路径：

| 平台 | 路径 |
|---|---|
| Linux / macOS（root） | `/usr/local/bin/opskit` |
| Linux / macOS（非 root） | `~/.local/bin/opskit` |
| Windows | `%LOCALAPPDATA%\opskit\opskit.exe` |

---

### 方式二：直接运行（开发模式）

```bash
git clone https://github.com/ougato/opskit-cli.git
cd opskit-cli

# 创建虚拟环境并安装依赖（推荐）
make setup
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux / macOS

python main.py
```

### 方式三：下载预编译单文件

从 [file.icerror.top](https://file.icerror.top/d/mirror/soft/) 下载对应平台的可执行文件：

| 平台 | 架构 | 直链 |
|---|---|---|
| Linux x64 | x86_64 | `https://file.icerror.top/d/mirror/soft/linux/opskit-linux-x64` |
| Linux arm64 | aarch64 | `https://file.icerror.top/d/mirror/soft/linux/opskit-linux-arm64` |
| Windows x64 | x86_64 | `https://file.icerror.top/d/mirror/soft/windows/opskit-windows-x64.exe` |
| macOS arm64 | Apple Silicon | `https://file.icerror.top/d/mirror/soft/macos/opskit-darwin-arm64` |

```bash
# Linux x64
curl -fsSL https://file.icerror.top/d/mirror/soft/linux/opskit-linux-x64 -o opskit
chmod +x opskit && ./opskit

# Linux arm64
curl -fsSL https://file.icerror.top/d/mirror/soft/linux/opskit-linux-arm64 -o opskit
chmod +x opskit && ./opskit

# macOS
curl -fsSL https://file.icerror.top/d/mirror/soft/macos/opskit-darwin-arm64 -o opskit
chmod +x opskit && ./opskit

# Windows PowerShell
Invoke-WebRequest https://file.icerror.top/d/mirror/soft/windows/opskit-windows-x64.exe -OutFile opskit.exe
.\opskit.exe
```

---

## 📦 安装依赖 / Dependencies

```
Python >= 3.10
rich >= 13.7           # TUI 渲染
typer >= 0.12          # CLI 框架
pyyaml >= 6.0          # 配置与主题
psutil >= 5.9          # 系统监控
httpx >= 0.27          # HTTP 客户端（更新 / 测速）
platformdirs >= 4.0    # 跨平台数据目录（XDG / Win / macOS 规范）
sentry-sdk >= 2.0,<3   # 错误上报（遥测，可关闭）
```

---

## 🏗️ 构建单文件 / Build

```bash
# 完整流程（测试 → 编译 → manifest）
python build.py all

# 仅编译（auto: Nuitka → PyInstaller 回退）
python build.py build

# 指定后端
python build.py build --backend pyinstaller

# 其他命令
python build.py test     # 仅运行测试
python build.py clean    # 清理 _build/ + dist/
python build.py info     # 查看版本 / 平台 / 可用后端

# 输出位置
dist/opskit-{os}-{arch}[.exe]
dist/opskit-{os}-{arch}[.exe].sha256
dist/manifest.json       # 产物描述（供外部工具读取）
```

或使用 `make`（Linux / macOS，Windows 需先安装 make）：

```bash
make setup  # 创建 .venv 并安装依赖（首次使用）
make all    # 完整流程
make build  # 仅编译
make test   # 仅测试
make clean  # 清理
```

> **Windows 用户注意**：PowerShell 没有内置 `make`，直接使用 `python build.py` 替代：
>
> ```powershell
> # Windows — 直接调用 build.py（无需 make）
> .venv\Scripts\python build.py build   # 仅编译
> .venv\Scripts\python build.py all     # 完整流程
> .venv\Scripts\python build.py test    # 仅测试
> .venv\Scripts\python build.py clean   # 清理
> ```
>
> 如需使用 `make`，可通过 Chocolatey 安装：`choco install make`

**构建依赖：**

```bash
pip install nuitka zstandard   # 首选（C 编译，防逆向强）
# 或
pip install pyinstaller        # 备选（快速打包）

# UPX 压缩（推荐，减小体积）
# Linux:   sudo apt install upx-ucl
# macOS:   brew install upx
# Windows: choco install upx
```

---

## ⚙️ 配置文件 / Configuration

配置文件位于：

| 系统 | 路径 |
|---|---|
| Linux（root 打包） | `/var/lib/opskit/config/common.yaml` |
| Linux（非 root 打包） | `~/.local/share/opskit/config/common.yaml` |
| Linux（开发模式） | 项目根目录 `config/common.yaml` |
| macOS | `~/Library/Application Support/opskit/config/common.yaml` |
| Windows | `%LOCALAPPDATA%\opskit\config\common.yaml`（打包）/ 项目根目录（开发） |

**主要配置项：**

```yaml
language: auto        # zh / en / auto
theme: catppuccin     # 主题名称
update:
  enabled: true       # 启用自动更新
  auto_apply: true    # 自动应用更新
log:
  level: WARNING      # DEBUG / INFO / WARNING / ERROR
telemetry:
  enabled: true       # 错误上报（关闭：false）
```

---

## 🎨 主题 / Themes

内置 **Catppuccin Mocha** 主题，可在设置中切换。

新增主题：在 `core/themes/` 目录下创建 YAML 文件，参考 `catppuccin.yaml` 格式，无需修改任何 `.py` 文件。

---

## 🔄 自动更新 / Auto Update

- 启动时后台检测新版本（不阻塞主流程）
- 退出时如有更新，提示热替换
- 更新只覆盖可执行文件，用户配置数据不受影响
- Windows 通过 PowerShell 脚本延迟替换方案实现热更新

---

## 🧪 测试 / Testing

```bash
# 运行全部测试
python -m pytest tests/ -v --tb=short

# 核心模块单元测试
python -m pytest tests/test_software.py tests/test_config.py tests/test_i18n.py \
  tests/test_theme.py tests/test_loader.py tests/test_menu_imports.py \
  tests/test_monitor.py tests/test_network.py tests/test_mirror.py \
  tests/test_paths.py tests/test_version.py tests/test_auto_update.py -v --tb=short

# 期望全部通过（无 FAILED）
```

---

## 📁 项目结构 / Project Structure

```
opskit-cli/
├── main.py                   # 主入口 + 主菜单
├── build.py                  # 构建脚本（子命令：test/build/clean/all/info）
├── Makefile                  # 标准化入口
├── .github/workflows/release.yml  # GitHub Actions：4 平台构建 + 自动上传 FileBrowser
├── requirements.txt
├── pyproject.toml
├── core/                     # 框架核心层（不可作为插件模块）
│   ├── constants.py          # 全局常量（零硬编码）
│   ├── config.py             # 配置读写 + 路径解析
│   ├── paths.py              # 跨平台数据目录（XDG / Win / macOS 规范）
│   ├── theme.py              # 主题引擎
│   ├── i18n.py               # 国际化（zh / en / auto）
│   ├── module.py             # ModuleInfo 数据类
│   ├── loader.py             # 插件自动发现（dev/frozen 双模式）
│   ├── prompt.py             # TUI 交互原语（select / text_input / confirm）
│   ├── platform.py           # 跨平台探测
│   ├── runner.py             # 子进程执行 + 实时输出
│   ├── pkg_runner.py         # 跨平台包管理器策略模式
│   ├── progress.py           # Spinner / 多步进度条
│   ├── logger.py             # 日志系统（轮转）
│   ├── cleanup.py            # 信号处理 + 退出清理
│   ├── mirror.py             # 智能镜像源管理
│   ├── updater.py            # 自动更新（热替换）
│   ├── version.py            # 版本管理
│   ├── version_cache.py      # 版本列表后台缓存
│   ├── sysconfig.py          # 安装快照与还原管理
│   ├── http.py               # HTTP 工具
│   ├── utils.py              # 通用工具函数
│   ├── venv_bootstrap.py     # venv 自举（开发模式）
│   ├── themes/               # YAML 主题文件（catppuccin.yaml）
│   ├── locale/               # 语言包（zh.yaml / en.yaml）
│   └── mirrors/              # 镜像源配置
├── software/                 # 📦 软件管理模块
│   ├── __init__.py           # register() 注册入口
│   ├── base.py               # Recipe 抽象基类
│   ├── registry.py           # @register 装饰器 + 全局配方表
│   ├── resolver.py           # 依赖解析
│   ├── menu.py               # 菜单（搜索 / 分类 / 已装软件）
│   └── recipes/              # 安装配方（每个软件一个目录）
│       ├── docker/           # Docker（Linux）
│       ├── nginx/            # Nginx（Linux）
│       ├── golang/           # Go tarball 多版本（Linux / macOS / Windows）
│       ├── python/           # Python 多版本 via uv（Linux / macOS / Windows）
│       ├── java/             # Java JDK 多版本（Linux / macOS / Windows）
│       ├── nodejs/           # Node.js 多版本（Linux / macOS / Windows）
│       ├── mysql/            # MySQL 多版本（Linux / macOS / Windows）
│       ├── redis/            # Redis 多版本（Linux / macOS / Windows）
│       ├── postgresql/       # PostgreSQL 多版本（Linux / macOS / Windows）
│       ├── mongodb/          # MongoDB 多版本（Linux / macOS / Windows）
│       └── wireguard/        # WireGuard VPN（Linux，含服务端 / 客户端子菜单）
├── monitor/                  # 📊 系统监控模块
│   ├── __init__.py
│   ├── commands.py           # 数据采集（CPU / 内存 / 磁盘 / 网络 / 进程）
│   └── menu.py               # 仪表盘 + 各细分页面（实时刷新）
├── network/                  # 🌐 网络工具模块
│   ├── __init__.py
│   ├── commands.py           # Ping / Traceroute / DNS / 端口扫描 / 测速
│   └── menu.py               # 网络工具菜单
├── wireguard/                # WireGuard 核心实现（被 software/recipes/wireguard 调用）
│   ├── server.py             # 公网服务端安装 / 卸载 / 诊断 / 管理
│   ├── client.py             # 私网客户端安装 / 卸载 / 诊断 / 管理
│   ├── templates.py          # 配置模板
│   ├── utils.py              # 工具函数
│   ├── constants.py          # WireGuard 常量
│   └── token.py              # 连接令牌（加密 / 解密）
└── tests/                    # 单元测试 + 集成测试
```

---

## 🛡️ 安全说明 / Security

- 需要 root / Administrator 权限执行系统级操作（安装软件、防火墙管理等）
- 自动提权：Linux/macOS 使用 `sudo`，Windows 使用 `ShellExecute runas`
- 更新包通过 SHA256 校验，不执行未校验的文件

---

## 📄 许可证 / License

MIT License — 详见 [LICENSE](LICENSE)

---

## 🤝 贡献 / Contributing

1. Fork 本仓库
2. 新建功能分支：`git checkout -b feature/xxx`
3. 提交代码：`git commit -m 'feat: add xxx'`
4. 推送分支：`git push origin feature/xxx`
5. 发起 Pull Request

新增软件安装配方：参考 `software/recipes/docker/recipe.py`，实现 `Recipe` 抽象类，使用 `@register` 装饰器注册即可自动发现。

新增插件模块：在项目根目录新建包（含 `__init__.py`），实现 `register() -> ModuleInfo` 函数，`loader.py` 启动时自动扫描发现，无需修改任何现有代码。
