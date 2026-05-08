"""MySQL 配方业务专属常量，与 core 完全隔离"""

# ─── 版本 API ─────────────────────────────────────────────────────────────────
MYSQL_VERSIONS_API_URL   = "https://endoflife.date/api/mysql.json"

# ─── 版本列表硬编码 fallback（网络不通时使用） ───────────────────────────────
MYSQL_VERSIONS_FALLBACK  = ["9.7.0", "9.0.1", "8.4.9", "8.0.46"]

# ─── Linux tarball 命名规则（静态映射，无需网络探针） ─────────────────────────
# minimal tarball 包含 lib/private/ bundled 私有库（protobuf/ssl 等），无需系统依赖
# 5.x 无 minimal，使用完整包（需系统 glibc2.12+）
#
# major >= 9   → glibc2.28-minimal.tar.xz（9.7.0 已验证）
# major == 8, minor >= 4 → glibc2.28-minimal.tar.xz（8.4.9 已验证）
# major == 8, minor < 4  → glibc2.17-minimal.tar.xz（8.0-8.3 已验证）
# major < 8   → glibc2.12 完整包 .tar.gz（5.x 无 minimal）
MYSQL_LINUX_GLIBC_OLD    = "glibc2.12"         # 5.x 完整包
MYSQL_LINUX_GLIBC_17     = "glibc2.17"         # 8.0-8.3 minimal
MYSQL_LINUX_GLIBC_NEW    = "glibc2.28"         # 8.4+ / 9.x minimal
MYSQL_LINUX_EXT_OLD      = ".tar.gz"           # 5.x 完整包
MYSQL_LINUX_EXT_NEW      = ".tar.xz"           # 8.x / 9.x minimal
MYSQL_LINUX_SUFFIX_MINIMAL = "-minimal"        # minimal tarball 后缀
MYSQL_LINUX_SUFFIX_FULL    = ""               # 5.x 完整包无后缀

# ─── 不支持 MySQL 5.x 的新系统 codename（官方 APT repo 404，tarball 也无意义） ─
# Debian 12+（bookworm/trixie）和 Ubuntu 22.04+（jammy/noble/oracular）
MYSQL_NO_5X_CODENAMES = {
    "bookworm", "trixie", "forky",           # Debian 12 / 13 / 14
    "jammy", "kinetic", "lunar", "mantic",   # Ubuntu 22.04 / 22.10 / 23.04 / 23.10
    "noble", "oracular", "plucky",           # Ubuntu 24.04 / 24.10 / 25.04
}

# ─── 下载 URL 模板（按优先级排列；最后一条作 fallback 兜底） ─────────────────
# {version}      : 完整版本号，如 9.7.0
# {major_minor}  : 主.次版本，如 9.7（由 common.py 动态计算）
# {arch}         : linux 架构，如 x86_64/aarch64
#
# cdn.mysql.com/Downloads/MySQL-{major_minor}/ 覆盖全版本（含当前最新）
# archives 路径只含历史版本，新版上线约 1 个月后才会同步，故作 fallback
MYSQL_DL_LINUX_URLS = [
    "https://cdn.mysql.com/Downloads/MySQL-{major_minor}/mysql-{version}-linux-glibc2.28-{arch}.tar.xz",
    "https://dev.mysql.com/get/Downloads/MySQL-{major_minor}/mysql-{version}-linux-glibc2.28-{arch}.tar.xz",
    "https://downloads.mysql.com/archives/get/p/23/file/mysql-{version}-linux-glibc2.28-{arch}.tar.xz",
]

# Windows：固定 winx64，无架构变量
MYSQL_DL_WINDOWS_URLS = [
    "https://cdn.mysql.com/Downloads/MySQL-{major_minor}/mysql-{version}-winx64.zip",
    "https://dev.mysql.com/get/Downloads/MySQL-{major_minor}/mysql-{version}-winx64.zip",
    "https://downloads.mysql.com/archives/get/p/23/file/mysql-{version}-winx64.zip",
]

# macOS：macos 版本号随 MySQL 版本变化，由 common.py 中函数动态计算
# {macos_ver}: 14 或 15（由 _mysql_macos_ver() 决定），{arch}: x86_64/arm64
MYSQL_DL_DARWIN_URLS = [
    "https://cdn.mysql.com/Downloads/MySQL-{major_minor}/mysql-{version}-macos{macos_ver}-{arch}.tar.gz",
    "https://dev.mysql.com/get/Downloads/MySQL-{major_minor}/mysql-{version}-macos{macos_ver}-{arch}.tar.gz",
    "https://downloads.mysql.com/archives/get/p/23/file/mysql-{version}-macos{macos_ver}-{arch}.tar.gz",
]

# ─── opskit 私有安装目录（相对 HOME） ─────────────────────────────────────────
MYSQL_PRIVATE_SUBDIR     = ".opskit/mysql"

# ─── 快照 ─────────────────────────────────────────────────────────────────────
SNAPSHOT_SUBDIR          = ".opskit/snapshots"
SNAPSHOT_MYSQL_FILE      = "mysql.json"

# ─── 超时 ─────────────────────────────────────────────────────────────────────
MYSQL_DOWNLOAD_TIMEOUT   = 600
MYSQL_INSTALL_TIMEOUT    = 120

# ─── detect 命令 ──────────────────────────────────────────────────────────────
MYSQL_DETECT_CMD         = "mysql"

# ─── shell rc PATH 注入标记 ───────────────────────────────────────────────────
MYSQL_PATH_MARKER_BEGIN  = "# opskit-mysql-path-begin"
MYSQL_PATH_MARKER_END    = "# opskit-mysql-path-end"

# ─── /etc/profile.d mysql 文件名 ──────────────────────────────────────────────
PROFILE_D_MYSQL_FILE     = "/etc/profile.d/opskit-mysql.sh"

# ─── Windows PATH 注入标记 ────────────────────────────────────────────────────
WIN_MYSQL_PS_MARKER      = "# opskit-mysql-path-begin"
WIN_MYSQL_PS_END         = "# opskit-mysql-path-end"
WIN_MYSQL_CMD_MARKER     = "@rem opskit-mysql-path-begin"
WIN_MYSQL_CMD_END        = "@rem opskit-mysql-path-end"

# ─── cmd AutoRun bat 存放路径（相对 HOME） ────────────────────────────────────
CMD_AUTORUN_MYSQL_SUBPATH = ".opskit/cmd_autorun_mysql.bat"

# ─── Windows .cmd shim 模板 ───────────────────────────────────────────────────
SHIM_MYSQL_CMD_TEMPLATE = (
    "@echo off\r\n"
    "rem opskit mysql shim - auto version routing\r\n"
    r'set "_SNAP=%USERPROFILE%\.opskit\snapshots\mysql.json"' + "\r\n"
    r'if not exist "%_SNAP%" goto :fallback' + "\r\n"
    r'for /f "usebackq tokens=1,* delims=:" %%A in (`findstr "mysql_bin_dir" "%_SNAP%"`) do (' + "\r\n"
    r'    set "_RAW=%%B"' + "\r\n"
    ")\r\n"
    r'if not defined _RAW goto :fallback' + "\r\n"
    r'set "_BIN=%_RAW: =%"' + "\r\n"
    r'set "_BIN=%_BIN:,=%"' + "\r\n"
    r'set _BIN=%_BIN:"=%' + "\r\n"
    r'if exist "%_BIN%\mysql.exe" (' + "\r\n"
    r'    "%_BIN%\mysql.exe" %*' + "\r\n"
    "    exit /b %ERRORLEVEL%\r\n"
    ")\r\n"
    ":fallback\r\n"
    "where mysql >nul 2>&1\r\n"
    "if %ERRORLEVEL% equ 0 ( mysql %* ) else ( echo mysql not found & exit /b 1 )\r\n"
)

# ─── Linux/macOS sh shim 模板 ─────────────────────────────────────────────────
SHIM_MYSQL_SH_TEMPLATE = """\
#!/bin/sh
# opskit mysql shim — 自动感知版本切换
_snap="$HOME/.opskit/snapshots/mysql.json"
if [ -f "$_snap" ]; then
    _bin=$(sed -n 's/.*"mysql_bin_dir"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p' "$_snap" | head -1)
    if [ -x "$_bin/{binary}" ]; then
        exec "$_bin/{binary}" "$@"
    fi
fi
exec {fallback} "$@"
"""

