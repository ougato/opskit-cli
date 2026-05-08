# 路径管理与包管理器规范

## 核心原则

**零硬编码**：所有系统路径、包管理器命令，必须通过统一抽象层获取，禁止在业务代码中直接写死字符串。

---

## 路径管理规则

### 强制要求

所有系统路径必须通过 `core/paths.py` 提供的函数获取，**禁止**在任何其他 `.py` 文件中直接写路径字符串。

### 可用路径函数

| 函数 | 返回值类型 | 说明 |
|---|---|---|
| `data_dir()` | `Path` | OpsKit 运行数据目录（随平台/用户/环境变量自动切换） |
| `config_dir()` | `Path` | 配置目录（`data_dir() / "config"`） |
| `cache_dir()` | `Path` | 缓存目录 |
| `log_dir()` | `Path` | 日志目录 |
| `xray_binary()` | `Path` | xray 可执行文件路径 |
| `xray_config_dir()` | `Path` | xray 配置目录 |
| `xray_config_file()` | `Path` | xray 配置文件路径 |
| `xray_data_dir()` | `Path` | xray geo 数据目录 |
| `xray_log_dir()` | `Path` | xray 日志目录 |
| `wg_config_dir()` | `Path` | WireGuard 配置目录 |
| `wg_config_file()` | `Path` | WireGuard 配置文件路径 |
| `nginx_webroot()` | `Path` | nginx 静态文件根目录（自动区分 Debian / CentOS / Alpine） |
| `nginx_conf_dir()` | `Path` | nginx 配置目录 |

### 正确用法

```python
# ✅ 正确
from core.paths import xray_log_dir, nginx_webroot

log = xray_log_dir()
access_log = str(log / "access.log")
webroot = str(nginx_webroot())
```

### 禁止写法

```python
# ❌ 禁止：直接写死路径字符串
log_path = "/var/log/xray/access.log"
webroot = "/usr/share/nginx/html"
binary = "/usr/local/bin/xray"
```

### 新增路径时的规则

1. 在 `core/paths.py` 中新增函数，返回 `Path` 对象
2. 函数内用 `if/elif` 处理不同发行版/平台差异（参考 `nginx_webroot()` 的实现）
3. 同步更新 `wireguard/constants.py`（如有对应常量）
4. 同步更新 `.windsurf/plans/001.md` 对应章节

---

## 包管理器规则

### 强制要求

所有软件包安装/卸载操作必须通过 `core/pkg_runner.py` 的 `get_runner()` 获取运行器后调用，**禁止**直接调用 `subprocess.run(["apt-get", ...])` 等命令。

**例外**：`core/pkg_runner.py` 内部实现、`core/venv_bootstrap.py` bootstrap 阶段（pkg_runner 尚未可用）可直接调用。

### 可用方法

```python
from core.pkg_runner import get_runner

runner = get_runner()          # 自动检测当前系统包管理器，返回对应 Runner 实例（带缓存）

runner.update_index()          # 更新软件源索引（apt update / yum makecache 等）
runner.install(["pkg1"])       # 安装包
runner.remove(["pkg1"])        # 卸载包
runner.install_python3("3.11") # 安装指定版本 Python，返回可执行路径或 None
runner.install_venv_pkg(python_bin)  # 安装 python3.X-venv（Debian 特有）
runner.install_extras(["repo"]) # 安装额外源（EPEL 等，仅 yum/dnf 支持）
```

### 正确用法

```python
# ✅ 正确
from core.pkg_runner import get_runner

runner = get_runner()
runner.update_index()
runner.install(["nginx", "curl"])
```

### 禁止写法

```python
# ❌ 禁止：硬编码包管理器命令
subprocess.run(["apt-get", "install", "-y", "nginx"], ...)
subprocess.run(["yum", "install", "-y", "nginx"], ...)

# ❌ 禁止：cmd_map 分发模式
pm = info.pkg_manager
cmd_map = {
    "apt": ["apt-get", "install", "-y", "nginx"],
    "yum": ["yum", "install", "-y", "nginx"],
}
subprocess.run(cmd_map[pm], ...)
```

### 新增发行版支持时的规则

1. 在 `core/pkg_runner.py` 中新增继承 `PkgRunner` 的子类
2. 在 `_RUNNER_MAP` 字典中注册映射关系
3. 实现 `update_index()`、`install()`、`remove()` 三个必须方法
4. 如有特殊包名差异，重写 `install_python3()` 方法
5. 调用方代码**零改动**

---

## 平台判断规则

需要根据平台差异执行不同逻辑时，使用 `core.platform.get_platform()` 获取平台信息，
**禁止**使用 `sys.platform`、`platform.system()` 或 `shutil.which("apt-get")` 在业务代码中做平台判断。

```python
# ✅ 正确
from core.platform import get_platform

info = get_platform()
if info.os_type == "linux":
    ...
if info.pkg_manager == "apt":
    ...

# ❌ 禁止：业务代码中直接判断
import sys
if sys.platform == "linux":
    ...
if shutil.which("apt-get"):
    ...
```

**例外**：`core/paths.py` 内部判断路径、`core/pkg_runner.py` 内部实现可使用 `sys.platform`。

---

## URL / 链接管理规则

### 核心原则

所有对外网络地址（API URL、下载地址、镜像源、文档链接等）必须集中定义在 `core/constants.py`，**禁止**在任何其他 `.py` 文件中直接写死 URL 字符串。

### 已定义的 URL 常量（`core/constants.py`）

| 常量名 | 说明 |
|---|---|
| `GITHUB_BASE` | `https://github.com` |
| `GITHUB_API_BASE` | `https://api.github.com` |
| `GITHUB_RAW_BASE` | `https://raw.githubusercontent.com` |
| `GITHUB_API_RELEASES` | GitHub Releases 最新版本 API 模板（含 `{repo}` 占位符） |
| `GITHUB_API_RELEASES_LIST` | GitHub Releases 列表 API 模板（含 `{repo}` 占位符） |
| `GITHUB_API_TAGS` | GitHub Tags 列表 API 模板（含 `{repo}` 占位符） |
| `GHPROXY_BASE` | `https://mirror.ghproxy.com`（GitHub 代理加速） |
| `XRAY_REPO` | `XTLS/Xray-core`（仓库路径，不含域名） |
| `XRAY_DOC_URL` | Xray 文档 URL（systemd 单元 Documentation= 字段） |
| `XRAY_DOWNLOAD_ZIP` | Xray 下载 ZIP 文件名 |
| `XRAY_API_LATEST` | Xray-core 最新版本 API URL |
| `XRAY_API_LATEST_GHPROXY` | Xray-core 最新版本 API 的 ghproxy 镜像 |
| `ACME_INSTALL_MIRRORS` | acme.sh 安装源列表（按优先级） |
| `PUBLIC_IP_APIS` | 公网 IP 探测 API 列表（按优先级） |
| `REGION_DETECT_APIS` | 地区探测 API 列表（含提取函数） |
| `NGINX_GITHUB_API` | nginx 版本列表 API URL |
| `DOCKER_GITHUB_API` | Docker 版本列表 API URL |
| `PYTHON_EOL_API` | Python 版本/EOL API URL |
| `ENDOFLIFE_API` | endoflife.date 通用 API 模板（含 `{product}` 占位符） |
| `GET_PIP_URL` | get-pip.py bootstrap URL |
| `BOOTSTRAP_URLS` | OpsKit 自更新 bootstrap JSON 地址列表 |

### 正确用法

```python
# ✅ 正确：从 constants 导入，不写死 URL
from core.constants import NGINX_GITHUB_API, TIMEOUT_VERSION_FETCH
import httpx
resp = httpx.get(NGINX_GITHUB_API, timeout=TIMEOUT_VERSION_FETCH)

# ✅ 正确：模板类 URL 用 .format() 填充
from core.constants import GITHUB_API_RELEASES_LIST
url = GITHUB_API_RELEASES_LIST.format(repo="nginx/nginx")

# ✅ 正确：组合 URL 用常量拼接
from core.constants import GHPROXY_BASE, GITHUB_RAW_BASE
mirror_url = f"{GHPROXY_BASE}/https://github.com/foo/bar/releases/v1/file.zip"
```

### 禁止写法

```python
# ❌ 禁止：直接写死 URL 字符串
resp = httpx.get("https://api.github.com/repos/nginx/nginx/tags?per_page=10")
url = "https://bootstrap.pypa.io/get-pip.py"
mirrors = ["https://gitee.com/neilpang/acme.sh/raw/master/acme.sh"]
```

### 新增 URL 时的规则

1. 在 `core/constants.py` 对应分类区块下新增常量
2. 命名遵循 `UPPER_SNAKE_CASE`，按用途分组（`XRAY_*`、`ACME_*`、`PUBLIC_IP_*` 等）
3. 带参数的 URL 使用 Python `str.format()` 占位符（`{repo}`、`{product}` 等）
4. 多个备用 URL 定义为 `list[str]`，调用方按序尝试
5. 同步更新本规范文档中的常量表

### 例外（不需修改）

- 单元测试中的 mock 占位 URL（`https://example.com`、`https://fast-mirror` 等）
- `core/constants.py` 本身的 URL 定义

---

## 检查清单（Code Review 必查）

- [ ] 代码中无裸路径字符串（`/var/log/`、`/usr/local/`、`C:\Users\` 等）
- [ ] 代码中无直接调用 `apt-get`、`yum`、`dnf`、`apk`、`brew`、`choco`、`winget` 的 `subprocess`
- [ ] 代码中无 `cmd_map = {"apt": [...], "yum": [...]}` 分发模式
- [ ] 代码中无裸 URL 字符串（`https://api.github.com/...`、`https://bootstrap.pypa.io/...` 等）
- [ ] 新增路径已在 `core/paths.py` 注册
- [ ] 新增发行版已在 `core/pkg_runner.py` 注册
- [ ] 新增 URL 已在 `core/constants.py` 注册

---

## 相关文件

| 文件 | 职责 |
|---|---|
| `core/constants.py` | 所有 URL、超时、重试等全局常量的唯一来源 |
| `core/paths.py` | 所有系统路径的唯一来源（Single Source of Truth） |
| `core/pkg_runner.py` | 包管理器策略模式，屏蔽发行版差异 |
| `core/platform.py` | 平台探测（OS 类型、包管理器、架构等） |
| `wireguard/constants.py` | 引用 `core/paths` 提供的路径常量（向后兼容） |
