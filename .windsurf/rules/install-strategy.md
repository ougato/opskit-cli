# OpsKit Recipe 安装策略规则

> 本文件是新增/修改任何 recipe 的**唯一操作手册**。
> 读完此文件即可独立完成一个跨平台、国内外兼容、赛马下载的软件 recipe。

---

## 一、核心原则（不可违反）

**严格禁止**使用系统包管理器（apt / yum / brew / winget / choco）作为主要安装方式。

必须采用**官方预编译二进制包直接下载解压**方式：
- 零系统依赖，任意 Linux 发行版均可用
- 不需要 root 权限（安装到 `~/.opskit/`）
- 多版本共存，可任意切换
- 完全可预测，不受系统环境污染

### 安装方式分级

| 级别 | 方式 | 适用场景 |
|---|---|---|
| Level 1 ✅ | 官方预编译二进制 tarball/zip 下载解压 | 所有新增软件必须优先采用 |
| Level 2 ✅ | 专用工具链管理器（如 `uv`） | 仅 Python |
| Level 3 ⚠️ | 源码编译 | 仅 Python 的最终 fallback |
| ❌ 禁止 | apt/yum/brew/winget/choco | 任何情况下均不得作为主路径 |

---

## 二、新增 Recipe 前必须做的事

### 步骤 1：全网搜索最佳下载源

搜索关键词示例：`{软件名} official binary download URL format linux windows macos x86_64 aarch64`

要求找到：
- 官方或权威第三方提供的预编译包（不是源码）
- 支持 Linux x86_64 / aarch64、Windows x64、macOS x86_64 / arm64
- URL 格式规律，可用版本号参数化

### 步骤 2：实测验证所有 URL（禁止跳过）

写临时脚本用 `httpx.head()` 验证，**必须全部通过**才能写入代码：

```python
import httpx, time
for ver, arch, url in test_cases:
    r = httpx.head(url, timeout=8, follow_redirects=True)
    cl = int(r.headers.get('content-length', 0))
    print(f'{r.status_code} {cl//1024//1024}MB  {ver} {arch}')
    # 要求：status==200，size>0，所有版本×架构全过
```

验证矩阵（**每个 URL 模板都必须覆盖**）：
- 最新 3 个主版本（如 17.x / 16.x / 15.x）
- 所有支持架构（x86_64 / aarch64）
- 所有支持平台（Linux / Windows / macOS）

---

## 三、下载 URL 策略

### 赛马 + fallback 模式（必须实现）

```python
# constants.py 中按优先级从高到低排列：
MYSOFT_DL_LINUX_URLS = [
    "https://国内镜像/{arch}/{version}/file.tar.gz",   # 主源（国内快）
    "https://ghfast.top/https://github.com/...",        # GitHub 加速镜像
    "https://github.com/官方/releases/download/...",    # 最终 fallback
]

# common.py 中：最后一条做 fallback，其余赛马
if len(urls) > 1:
    race_urls = urls[:-1]
    fallback  = urls[-1]
else:
    race_urls = urls
    fallback  = None

mirror.download(direct_urls=race_urls, fallback_url=fallback, ...)
```

### URL 来源优先级

| 优先级 | 来源 | 代表 |
|---|---|---|
| 1 | 国内专用 CDN/镜像 | 清华 TUNA、阿里云、npmmirror |
| 2 | GitHub 加速（ghfast.top） | `https://ghfast.top/https://github.com/...` |
| 3 | 官方非 GitHub CDN | `fastdl.mongodb.org`、`get.enterprisedb.com` |
| 4 | GitHub Releases 直连 | 最终 fallback，国内可能慢 |

### 注意事项

- `mirror.ghproxy.com` 存在 SSL EOF 问题，**不用**，改用 `ghfast.top`
- EDB（enterprisedb.com）Linux 包 **全部 403**，只有 Windows/macOS 可用
- MongoDB Linux 必须带发行版标识（`ubuntu2204`/`amazon2023`），裸路径 403
- 所有镜像站都可能挂，所以 fallback 必须是官方直连
- **theseus-rs PostgreSQL binaries 是 musl 动态链接，不是真正的静态链接**，需要 `/lib/ld-musl-x86_64.so.1`，在标准 Debian/Ubuntu（glibc）上**无法运行**，不得用于 Linux

---

## 四、各软件已验证的最佳下载源（实测 2025-05）

### Go（golang）
- **Linux/macOS**：`https://mirrors.aliyun.com/golang/go{version}.linux-{arch}.tar.gz`
- **Windows**：阿里云镜像
- **fallback**：`golang.google.cn` / `go.dev`

### Node.js（nodejs）
- **全平台**：`https://npmmirror.com/mirrors/node/v{version}/node-v{version}-linux-x64.tar.xz`
- **fallback**：`nodejs.org` 官方直连

### Java（JDK / Adoptium Temurin）
- **全平台主源**：清华 TUNA `https://mirrors.tuna.tsinghua.edu.cn/Adoptium/{major}/jdk/{arch}/{os}/...`
- **fallback**：`ghfast.top` 加速 GitHub Adoptium Releases → GitHub 直连

### MongoDB
- **Linux**：`https://fastdl.mongodb.org/linux/mongodb-linux-{arch}-ubuntu2204-{version}.tgz`（主）
- **Linux fallback**：同路径 `amazon2023` 替换 `ubuntu2204`
- **Windows/macOS**：`https://fastdl.mongodb.org/windows/mongodb-windows-x86_64-{version}.zip`
- **注意**：Linux 裸路径（无发行版标识）返回 403，必须带 `ubuntu2204`

### PostgreSQL

**⚠️ 重要：theseus-rs 二进制在 Debian/Ubuntu（glibc）上无法运行（见下方第十三节）**

- **Windows 主源**：`https://get.enterprisedb.com/postgresql/postgresql-{version}-1-windows-x64-binaries.zip`（EDB，296MB）
- **Windows fallback**：`ghfast.top` + theseus-rs MSVC zip
- **macOS 主源**：EDB zip + theseus-rs `{arch}-apple-darwin.tar.gz` fallback
- **Linux 主源（glibc 原生）**：PGDG apt deb 直接下载解压（Aliyun 主 + PGDG 官方 fallback）
  - 下载 `postgresql-{major}` + `postgresql-client-{major}` + `libpq5` 三个 deb
  - 用 `ar x` + `lzma` 解压，提取 `usr/lib/postgresql/{major}/bin/` 和 `usr/lib/x86_64-linux-gnu/*.so`
  - 解压后创建 `libpq.so.5` → `libpq.so.5.x` symlink
  - shim 注入 `LD_LIBRARY_PATH` 指向版本目录 `lib/`
- **Packages.gz 查询 URL**：
  - Aliyun：`https://mirrors.aliyun.com/postgresql/repos/apt/dists/{codename}-pgdg/main/binary-{arch}/Packages.gz`
  - PGDG 官方：`https://apt.postgresql.org/pub/repos/apt/dists/{codename}-pgdg/main/binary-{arch}/Packages.gz`
  - `{codename}` = 发行版代号名（bookworm/bullseye/jammy/noble），**不是数字**
- **支持版本**：PG 12~18（PGDG 全支持），theseus-rs 在 Linux 上**禁止使用**
- **deb arch 映射**：`x86_64 → amd64`，`aarch64 → arm64`

---

## 五、Recipe 文件结构规范

每个 recipe 必须包含以下文件，职责严格分离：

```
software/recipes/{name}/
  __init__.py      # 只暴露 XxxRecipe，一行代码
  constants.py     # 所有常量：URL模板、路径、版本列表
  common.py        # 跨平台共用：架构映射、路径工具、快照管理、tarball下载
  driver.py        # PlatformDriver ABC + get_driver() 工厂函数
  linux.py         # LinuxDriver：detect + install/uninstall 逻辑
  windows.py       # WindowsDriver：detect + install/uninstall 逻辑
  darwin.py        # DarwinDriver：detect + install/uninstall 逻辑
  recipe.py        # XxxRecipe 主类：纯调度，零平台 if，只调 driver/common
```

### 各文件职责边界

- **`constants.py`**：只放常量，零逻辑，零导入（除类型）
- **`common.py`**：跨平台工具函数，不含平台 if（除架构映射）
- **`driver.py`**：ABC 定义 + 工厂函数，不含业务逻辑
- **`linux/windows/darwin.py`**：平台特有逻辑，不跨平台
- **`recipe.py`**：零平台 if，统一调 `get_driver()` 和 `common.*`

---

## 六、架构映射规范

```python
# common.py 中标准写法
import platform

def _xxx_arch() -> str:
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):  return "x86_64"    # 或 "amd64"/"x64" 按目标格式
    if m in ("aarch64", "arm64"): return "aarch64"   # 或 "arm64"
    return "x86_64"                                    # 兜底
```

各软件架构字符串对照：

| 物理架构 | Go | Node.js | Java(Adoptium) | MongoDB | PostgreSQL(theseus) |
|---|---|---|---|---|---|
| x86_64 | `amd64` | `x64` | `x64` | `x86_64` | `x86_64` |
| aarch64 | `arm64` | `arm64` | `aarch64` | `aarch64` | `aarch64` |

---

## 七、安装目录规范

```
~/.opskit/
  {软件名}/
    {软件名}{version}/    # 解压目录，如 mongodb8.0.4 / postgresql17.2
    shims/               # shim 脚本（mongod、psql 等）
    active               # 符号链接/junction 指向当前激活版本
```

- 路径常量放 `constants.py`，如 `MONGO_PRIVATE_SUBDIR = ".opskit/mongodb"`
- 路径工具函数放 `common.py`，如 `mongo_versions_dir()` / `mongo_version_dir(version)`
- 快照文件：`~/.opskit/snapshots/{软件名}.json`，记录已安装版本和当前激活版本

---

## 八、安装流程规范

### 进度步骤（MultiStepProgress）

```python
# recipe.py install() 标准写法
descs = [
    t("software.step.check"),
    t("software.step.download"),
    t("software.step.install"),
    t("software.step.verify"),
]
with MultiStepProgress(descs) as sp:
    sp.step(t("software.step.check"))
    # 1. 检测平台合法性，检测是否已安装

    sp.step(t("software.step.download"))
    with tempfile.TemporaryDirectory(prefix="opskit-{name}-", ignore_cleanup_errors=True) as tmpdir:
        tarball = common.download_xxx_tarball(version, Path(tmpdir) / "file.tgz")
        # 2. 赛马下载

        sp.step(t("software.step.install"))
        driver.install(version, tarball)
        # 3. 解压 + active 链接 + shim + PATH

    sp.step(t("software.step.verify"))
    # 4. 运行 {bin} --version 验证
    sp.complete()
```

### TemporaryDirectory 必须加 `ignore_cleanup_errors=True`

防止 Windows WinError 32（文件被进程占用）导致临时目录清理失败崩溃。

---

## 九、版本列表查询规范

```python
# common.py 中标准三级降级
def version_list() -> list[str]:
    # Level 1: 官方 API（最准确）
    # Level 2: endoflife.date API（通用备用）
    # Level 3: 硬编码 fallback（离线兜底）
    try:
        resp = httpx.get(VERSIONS_API, timeout=TIMEOUT_VERSION_FETCH)
        if resp.status_code == 200:
            ...
    except Exception:
        pass
    return list(VERSIONS_FALLBACK)
```

版本 API 来源优先级：
1. 软件官方 API（如 `go.dev/dl/?mode=json`、`nodejs.org/dist/index.json`）
2. `https://endoflife.date/api/{软件名}.json`（通用）
3. 硬编码 fallback 列表（`constants.py` 中维护最近 5 个版本）

---

## 十、错误提示本地化规则

**所有用户可见字符串禁止硬编码**，必须用 `t()` 翻译：

```python
# 错误：
raise InstallError("PostgreSQL 安装失败：xxx")

# 正确：
raise InstallError(t("software.postgresql_error.install_fail", error=e))
```

- 每个 recipe 的错误 key 放在 `software.{recipe}_error.*` 下
- `zh.yaml` 和 `en.yaml` **必须同步新增**，缺一不可
- 格式：`InstallError(t("software.xxx_error.yyy", param=value))`

---

## 十一、Recipe 类规范

```python
@register
class XxxRecipe(Recipe):
    key:               ClassVar[str]       = "xxx"
    category:          ClassVar[str]       = "devops"     # 或 "devtools"
    description:       ClassVar[str]       = "软件描述"
    platforms:         ClassVar[list[str]] = ["linux", "darwin", "windows"]
    dependencies:      ClassVar[list[str]] = []
    has_version_picker: ClassVar[bool]     = True         # 版本列表超 9 项必须 True
    has_switch:        ClassVar[bool]      = True         # 支持多版本切换则 True

    def detect(self) -> str | None:
        return get_driver().detect()                      # 统一调 driver，不自己实现

    def installed_versions(self) -> list[str]:
        return list(common.load_snapshot().get("versions", {}).keys())

    def versions(self) -> list[str]:
        return common.version_list()

    def install(self, version: str) -> None: ...

    def uninstall(self, version: str | None = None) -> None: ...  # 必须有 version 参数
```

---

## 十三、Linux 二进制包来源选型规则

### ⚠️ theseus-rs PostgreSQL binaries 陷阱（已踩坑，必读）

theseus-rs 的 `postgresql-{version}.0-x86_64-unknown-linux-musl.tar.gz` **并非真正静态链接**：

- `ldd` 显示 `interpreter /lib/ld-musl-x86_64.so.1`（musl 动态链接器）
- 依赖 `libpq.so.5` 和 `libc.musl-x86_64.so.1`
- 标准 Debian/Ubuntu 使用 glibc，**没有** `/lib/ld-musl-x86_64.so.1`
- 运行时报错：`No such file or directory`（interpreter 找不到）
- **结论：theseus-rs 二进制只能在 Alpine Linux（musl 环境）中使用，禁止用于 Debian/Ubuntu**

### Linux PostgreSQL 正确方案：PGDG deb 直接解压

```python
# 步骤：
# 1. 检测发行版代号（lsb_release -cs 或 /etc/os-release）
# 2. 从 PGDG Packages.gz 查询实际 deb 包路径（server + client + libpq5）
# 3. 下载三个 deb（Aliyun 主，PGDG 官方 fallback）
# 4. ar x pkg.deb → 提取 data.tar.xz → 解压目标文件
# 5. bin: usr/lib/postgresql/{major}/bin/ → 安装目录/bin/
# 6. lib: usr/lib/x86_64-linux-gnu/*.so → 安装目录/lib/
# 7. 为 .so.x.y 创建 .so.x 和 .so symlink
# 8. shim 注入 LD_LIBRARY_PATH=安装目录/lib
```

### Linux 二进制选型决策树

| 情况 | 方案 | 原因 |
|---|---|---|
| glibc 系（Debian/Ubuntu/CentOS/RHEL）| 官方 apt/yum 仓库 deb/rpm 直接下载解压 | 唯一可靠的 glibc 原生二进制 |
| musl 系（Alpine）| theseus-rs 或 static-musl 预编译包 | musl 动态链接匹配 |
| 通用静态链接（如 Go/Node.js）| 官方 tar.gz | 真正零依赖静态链接 |

**判断是否真正静态链接的方法**：
```bash
file ./binary          # 查看 interpreter
ldd  ./binary          # 查看动态依赖
# 真正静态：ldd 输出 "not a dynamic executable"
# musl 动态：interpreter=/lib/ld-musl-x86_64.so.1
```

---

## 十二、实施 Checklist（每次新增/修改必须过）

**验证阶段（写代码前）**
- [ ] 全网搜索过最佳下载源
- [ ] `httpx.head()` 实测验证：所有版本 × 所有架构 × 所有平台，status==200 且 size>0

**代码阶段**
- [ ] 文件结构完整：`__init__ / constants / common / driver / linux / windows / darwin / recipe`
- [ ] `constants.py`：URL 模板按优先级排列，国内镜像在前，GitHub 直连在后
- [ ] `common.py`：`download_xxx_tarball()` 实现赛马逻辑（`race_urls + fallback`）
- [ ] `common.py`：`version_list()` 三级降级，有硬编码 fallback
- [ ] 各 driver：`detect()` 已实现，recipe 统一调 `get_driver().detect()`
- [ ] `recipe.py`：零平台 if，所有错误走 `t()` i18n
- [ ] `recipe.py`：`has_switch`、`has_version_picker`、`uninstall(version=None)` 已正确声明
- [ ] `TemporaryDirectory(ignore_cleanup_errors=True)` 已加

**本地化阶段**
- [ ] `zh.yaml` 新增 `software.{name}_error.*` 所有 key
- [ ] `en.yaml` 同步新增对应英文 key

**验收阶段**
- [ ] `python -m py_compile` 所有文件语法无错
- [ ] 写临时验收脚本，实测下载链路（HEAD 探针 + 前 128KB GET）
- [ ] 提交前清理所有临时测试文件
