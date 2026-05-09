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

## 🖥️ CLI 命令行用法 / CLI Usage

OpsKit 支持两种使用方式：**交互式菜单**（默认）和 **CLI 命令行直达**。

> **开发模式**：将以下所有 `opskit` 替换为 `python main.py`，例如 `python main.py software install docker`

### 启动与全局选项

```bash
opskit                                # 启动交互式菜单（默认入口）
opskit --version                      # 显示版本号
opskit -V                             # 同上（短参数）
opskit --theme catppuccin             # 启动时指定主题
opskit --lang zh                      # 启动时指定语言（zh / en）
opskit --theme catppuccin --lang en   # 同时指定主题和语言
```

#### 非交互模式（`--yes` / `OPSKIT_YES`）

参考 `apt -y` / `rustup -y` / `terraform -auto-approve` / `NONINTERACTIVE=1 brew install` 等开源社区惯例，OpsKit 支持两种方式跳过所有确认弹窗和暂停等待：

| 方式 | 用法 | 说明 |
|---|---|---|
| CLI 标志 | `opskit --yes software install docker` | 跳过所有 confirm / pause 交互 |
| 短参数 | `opskit -y software install docker` | 同上 |
| 环境变量 | `OPSKIT_YES=1 opskit software install docker` | CI/CD 管道一次性禁用所有 prompt |

> `--yes` 仅跳过确认弹窗和 pause，不会替代必填的业务参数（如 `--token` / `--version` / `HOST`）。缺少必填参数时仍进入交互输入。

### 📦 软件管理 `opskit software <command>`

#### 查看类

| 命令 | 说明 |
|---|---|
| `opskit software list` | 显示所有可用软件及安装状态（表格形式，含版本、平台） |
| `opskit software search` | 搜索软件（交互式输入关键词，按名称匹配） |
| `opskit software installed` | 仅显示已安装的软件列表（支持翻页） |

#### 安装 `opskit software install [NAME] [OPTIONS]`

不带参数时进入交互式选择；带参数时直接安装指定软件。支持 `--token` 和 `--version` 选项跳过所有交互。

| 选项 | 说明 |
|---|---|
| `NAME` | 软件 key（可选，不填则进入交互式选择） |
| `--token, -t` | WireGuard 客户端连接令牌（跳过交互输入，直达安装） |
| `--version` | 指定安装版本号（跳过版本选择器，直达安装） |

```bash
# 交互式安装（弹出分类列表选择）
opskit software install

# ── 运维工具（DevOps）──────────────────────────────────────────────
opskit software install docker        # Docker 容器引擎（Linux）
opskit software install nginx         # Nginx Web 服务器（Linux）
opskit software install mysql         # MySQL 关系型数据库（Linux / macOS / Windows）
opskit software install redis         # Redis 内存数据库（Linux / macOS / Windows）
opskit software install postgresql    # PostgreSQL 关系型数据库（Linux / macOS / Windows）
opskit software install mongodb       # MongoDB 文档型数据库（Linux / macOS / Windows）
opskit software install wireguard     # WireGuard VPN（Linux）→ 进入子菜单选择服务端 / 客户端

# ── 开发工具（DevTools）────────────────────────────────────────────
opskit software install golang        # Go 编程语言（Linux / macOS / Windows）
opskit software install python        # Python 解释器（Linux / macOS / Windows）
opskit software install java          # Java JDK（Linux / macOS / Windows）
opskit software install nodejs        # Node.js 运行时（Linux / macOS / Windows）
```

##### 非交互式安装（脚本化 / 自动化）

```bash
# ── WireGuard 客户端：--token 直达安装，无需任何交互 ────────────────
opskit software install wg_client --token "eJy0VE1v2zAM..."
opskit software install wg_client -t "eJy0VE1v2zAM..."

# ── 多版本软件：--version 指定版本号，跳过版本选择器 ────────────────
opskit software install mysql --version 8.0.36
opskit software install redis --version 7.2.4
opskit software install postgresql --version 16.2
opskit software install mongodb --version 7.0.8
opskit software install golang --version 1.22.2
opskit software install python --version 3.12.3
opskit software install java --version 21.0.3
opskit software install nodejs --version 20.12.2
```

> **多版本软件**（mysql / redis / postgresql / mongodb / golang / python / java / nodejs）不带 `--version` 时会弹出版本选择器，支持翻页浏览。

#### 卸载 `opskit software uninstall [NAME]`

不带参数时进入交互式选择；带参数时直接卸载指定软件。

```bash
# 交互式卸载（仅列出已安装的软件）
opskit software uninstall

# 直接卸载指定软件
opskit software uninstall docker      # 卸载 Docker
opskit software uninstall nginx       # 卸载 Nginx
opskit software uninstall mysql       # 卸载 MySQL（多版本时弹出版本选择 / 全部卸载）
opskit software uninstall redis       # 卸载 Redis（同上）
opskit software uninstall postgresql  # 卸载 PostgreSQL（同上）
opskit software uninstall mongodb     # 卸载 MongoDB（同上）
opskit software uninstall golang      # 卸载 Go（同上）
opskit software uninstall python      # 卸载 Python（同上）
opskit software uninstall java        # 卸载 Java（同上）
opskit software uninstall nodejs      # 卸载 Node.js（同上）
opskit software uninstall wireguard   # 卸载 WireGuard（进入子菜单选择服务端 / 客户端）
opskit software uninstall wg_server   # 直接卸载 WireGuard 服务端
opskit software uninstall wg_client   # 直接卸载 WireGuard 客户端（所有隧道）
```

#### 升级 `opskit software upgrade [NAME]`

不带参数时进入交互式选择；带参数时直接升级指定软件。

```bash
# 交互式升级（仅列出已安装且有新版本的软件）
opskit software upgrade

# 直接升级指定软件（自动检测当前版本，仅显示更新版本）
opskit software upgrade docker        # 升级 Docker
opskit software upgrade nginx         # 升级 Nginx
opskit software upgrade mysql         # 升级 MySQL
opskit software upgrade redis         # 升级 Redis
opskit software upgrade postgresql    # 升级 PostgreSQL
opskit software upgrade mongodb       # 升级 MongoDB
opskit software upgrade golang        # 升级 Go
opskit software upgrade python        # 升级 Python
opskit software upgrade java          # 升级 Java
opskit software upgrade nodejs        # 升级 Node.js
```

> **注意**：WireGuard 不支持升级（`has_upgrade=False`），升级菜单中会置灰显示。

#### 版本切换 `opskit software install <NAME>`

多版本软件安装后，可通过操作菜单中的「切换」选项切换活跃版本（仅限本地已安装版本）：

> 支持版本切换的软件：mysql / redis / postgresql / mongodb / golang / python / java / nodejs

#### 诊断 `opskit software diagnose <NAME>`

对指定软件运行诊断检查（服务状态、连接信息、连通性等）。

```bash
opskit software diagnose wg_server    # WireGuard 服务端诊断
                                      #   → 检查 wg-quick@wg0 / Xray 服务状态
                                      #   → 显示 WG 公钥 / UDP 端口 / Xray 端口 / VPN 网关
                                      #   → 列出所有客户端 Peer 及握手状态
                                      #   → 显示客户端凭证（Xray 公钥 / UUID / shortId）

opskit software diagnose wg_client    # WireGuard 客户端诊断
                                      #   → 遍历所有已安装隧道
                                      #   → 检查每条隧道的 wg-quick / xray 服务状态
                                      #   → 显示客户端 IP / 服务端 IP / 域名 / Xray 本地端口
                                      #   → ping 网关连通性测试
```

> **仅 WireGuard 支持诊断**，其他软件的诊断菜单项不可用。

#### 管理 `opskit software manage <NAME>`

对指定软件进入管理界面。

```bash
opskit software manage wg_server      # WireGuard 服务端管理
                                      #   → 添加客户端（自动分配 IP + 生成连接令牌）
                                      #   → 客户端列表（查看所有已注册客户端及握手信息）
                                      #   → 修改客户端名称
                                      #   → 删除客户端（移除 WG Peer + 清理状态）

opskit software manage wg_client      # WireGuard 客户端管理
                                      #   → 查看信息（所有隧道的连接参数详情）
                                      #   → 更新令牌（重新解析令牌并重启服务）
                                      #   → 移除隧道（选择并删除单条隧道）
```

> **仅 WireGuard 支持管理**，其他软件的管理菜单项不可用。

#### 软件能力矩阵

| 软件 key | 分类 | 平台 | 安装 | 卸载 | 升级 | 多版本切换 | 诊断 | 管理 |
|---|---|---|---|---|---|---|---|---|
| `docker` | DevOps | Linux | ✅ | ✅ | ✅ | — | — | — |
| `nginx` | DevOps | Linux | ✅ | ✅ | ✅ | — | — | — |
| `mysql` | DevOps | Linux / macOS / Win | ✅ | ✅ | ✅ | ✅ | — | — |
| `redis` | DevOps | Linux / macOS / Win | ✅ | ✅ | ✅ | ✅ | — | — |
| `postgresql` | DevOps | Linux / macOS / Win | ✅ | ✅ | ✅ | ✅ | — | — |
| `mongodb` | DevOps | Linux / macOS / Win | ✅ | ✅ | ✅ | ✅ | — | — |
| `wireguard` | DevOps | Linux | ✅ 子菜单 | ✅ | — | — | — | — |
| `wg_server` | DevOps | Linux | ✅ 向导 | ✅ | — | — | ✅ | ✅ |
| `wg_client` | DevOps | Linux | ✅ 向导 | ✅ | — | — | ✅ | ✅ |
| `golang` | DevTools | Linux / macOS / Win | ✅ | ✅ | ✅ | ✅ | — | — |
| `python` | DevTools | Linux / macOS / Win | ✅ | ✅ | ✅ | ✅ | — | — |
| `java` | DevTools | Linux / macOS / Win | ✅ | ✅ | ✅ | ✅ | — | — |
| `nodejs` | DevTools | Linux / macOS / Win | ✅ | ✅ | ✅ | ✅ | — | — |

### 📊 系统监控 `opskit monitor <command>`

| 命令 | 说明 |
|---|---|
| `opskit monitor dashboard` | 实时概览仪表盘（CPU / 内存 / 磁盘 / 网络 / 负载，自动刷新） |
| `opskit monitor cpu` | CPU 详情（总使用率 + 每核使用率，实时刷新） |
| `opskit monitor memory` | 内存详情（物理内存 + Swap 使用率，实时刷新） |
| `opskit monitor disk` | 磁盘详情（所有分区挂载点、总量、已用、可用、使用率） |
| `opskit monitor network` | 网络详情（所有网卡实时上传 / 下载速率 + 总流量，实时刷新） |
| `opskit monitor processes` | 进程列表（按 CPU 使用率排序，Top 15，实时刷新） |

```bash
# 示例
opskit monitor dashboard              # 启动实时概览仪表盘
opskit monitor cpu                    # 查看 CPU 每核使用率
opskit monitor memory                 # 查看内存 + Swap 使用情况
opskit monitor disk                   # 查看磁盘分区使用情况
opskit monitor network                # 查看网络接口实时流量
opskit monitor processes              # 查看进程列表（Top 15）
```

### 🌐 网络工具 `opskit network <command> [HOST]`

所有需要目标主机的命令均支持直传 `HOST` 参数跳过交互输入。

| 命令 | 参数 | 说明 |
|---|---|---|
| `opskit network ping [HOST]` | 可选 | Ping 测试（显示延迟 / 丢包率） |
| `opskit network traceroute [HOST]` | 可选 | 路由追踪（逐跳显示路径） |
| `opskit network dns [HOST]` | 可选 | DNS 查询（域名→正查，IP→反查，自动判断） |
| `opskit network port-scan [HOST]` | 可选 | 端口扫描（扫描常用端口状态） |
| `opskit network speed-test` | 无 | 下载测速（天然非交互） |
| `opskit network public-ip` | 无 | 公网 IP 查询（天然非交互） |

```bash
# ── 交互式（不带参数，弹出输入框）────────────────────────────────────
opskit network ping                   # 交互式 Ping 测试
opskit network traceroute             # 交互式路由追踪
opskit network dns                    # 交互式 DNS 正向 / 反向查询
opskit network port-scan              # 交互式端口扫描

# ── 非交互式（直传 HOST，跳过输入框）──────────────────────────────────
opskit network ping 8.8.8.8           # 直接 Ping 8.8.8.8
opskit network ping google.com        # 直接 Ping google.com
opskit network traceroute 1.1.1.1     # 直接追踪到 1.1.1.1
opskit network dns google.com         # 正向查询（域名 → IP）
opskit network dns 8.8.8.8            # 反向查询（IP → 域名，自动识别）
opskit network port-scan 192.168.1.1  # 扫描 192.168.1.1 的常用端口

# ── 天然非交互（无需任何参数）─────────────────────────────────────────
opskit network speed-test             # 下载测速
opskit network public-ip              # 查询公网 IP
```

### 查看帮助

```bash
opskit --help                         # 查看所有可用命令
opskit software --help                # 查看软件管理所有子命令
opskit monitor --help                 # 查看系统监控所有子命令
opskit network --help                 # 查看网络工具所有子命令
opskit software install --help        # 查看 install 命令参数说明
```

### 🤖 非交互式快速参考（CI/CD / 脚本化）

以下示例展示完全无交互的 CLI 用法，适用于 CI/CD 管道、自动化脚本、Ansible playbook 等场景。

```bash
# ── 全局 --yes 跳过所有确认弹窗 ──────────────────────────────────────
opskit -y software install docker
opskit --yes software install nginx
OPSKIT_YES=1 opskit software install docker     # 环境变量方式（Linux/macOS）
$env:OPSKIT_YES="1"; opskit software install docker  # PowerShell

# ── 软件安装（指定版本，零交互）──────────────────────────────────────
opskit -y software install mysql --version 8.0.36
opskit -y software install python --version 3.12.3
opskit -y software install nodejs --version 20.12.2

# ── WireGuard 客户端（令牌直装，零交互）──────────────────────────────
opskit -y software install wg_client --token "eJy0VE1v2zAM..."

# ── 软件卸载（跳过确认）─────────────────────────────────────────────
opskit -y software uninstall docker
opskit -y software uninstall wg_client

# ── 网络工具（直传 HOST，零交互）─────────────────────────────────────
opskit network ping 8.8.8.8
opskit network dns google.com
opskit network port-scan 192.168.1.1
opskit network speed-test
opskit network public-ip

# ── 系统监控（天然非交互）────────────────────────────────────────────
opskit monitor dashboard
opskit monitor cpu
opskit monitor disk
```

---

## �� 安装依赖 / Dependencies

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
