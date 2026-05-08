"""MongoDB 配方业务专属常量，与 core 完全隔离"""

# ─── 版本 API ─────────────────────────────────────────────────────────────────
MONGO_VERSIONS_API_URL   = "https://endoflife.date/api/mongodb.json"

# ─── 下载 URL 列表（完整 URL，按优先级排列；最后一条作为 fallback 兜底）────────
# {version}: 版本号，如 8.2.6  {arch}: 架构，如 x86_64/aarch64
#
# Linux 说明：fastdl 的 Linux 包必须带发行版标识，裸路径返回 403。
#   ubuntu2204: 主源，实测全版本 x86_64/aarch64 均 200，兼容所有主流发行版
#   amazon2023: fallback，实测全版本均 200
# macOS 说明：fastdl 给 macOS 的路径没有发行版标识，直连就是 200
# Windows 说明：fastdl Windows 直连 200，不需要发行版标识
MONGO_DL_LINUX_URLS = [
    "https://fastdl.mongodb.org/linux/mongodb-linux-{arch}-ubuntu2204-{version}.tgz",
    "https://fastdl.mongodb.org/linux/mongodb-linux-{arch}-amazon2023-{version}.tgz",
]

MONGO_DL_DARWIN_URLS = [
    "https://fastdl.mongodb.org/osx/mongodb-macos-{arch}-{version}.tgz",
]

MONGO_DL_WINDOWS_URLS = [
    "https://fastdl.mongodb.org/windows/mongodb-windows-x86_64-{version}.zip",
]

# ─── opskit 私有安装目录（相对 HOME） ─────────────────────────────────────────
MONGO_PRIVATE_SUBDIR     = ".opskit/mongodb"

# ─── Windows 固定 bin 目录：junction active 指向激活版本 ──────────────────────
MONGO_WIN_BIN_SUBDIR     = ".opskit/bin"

# ─── 快照 ─────────────────────────────────────────────────────────────────────
SNAPSHOT_SUBDIR          = ".opskit/snapshots"
SNAPSHOT_MONGODB_FILE    = "mongodb.json"

# ─── 超时 ─────────────────────────────────────────────────────────────────────
MONGO_DOWNLOAD_TIMEOUT   = 600
MONGO_INSTALL_TIMEOUT    = 120

# ─── 版本列表硬编码 fallback（网络不通时使用） ───────────────────────────────
MONGO_VERSIONS_FALLBACK  = ["8.0.4", "7.0.14", "6.0.19", "5.0.30"]

# ─── detect 命令 ──────────────────────────────────────────────────────────────
MONGO_DETECT_CMD         = "mongod"

# ─── verify 失败信息 ──────────────────────────────────────────────────────────
MONGO_VERIFY_ERR         = "mongod --version check failed after install"

# ─── /etc/profile.d mongod 文件名 ─────────────────────────────────────────────
PROFILE_D_MONGO_FILE     = "/etc/profile.d/opskit-mongodb.sh"

# ─── shell rc PATH 注入标记 ───────────────────────────────────────────────────
MONGO_PATH_MARKER_BEGIN  = "# opskit-mongodb-path-begin"
MONGO_PATH_MARKER_END    = "# opskit-mongodb-path-end"

# ─── Windows PATH 注入标记 ────────────────────────────────────────────────────
WIN_MONGO_PS_MARKER      = "# opskit-mongodb-path-begin"
WIN_MONGO_PS_END         = "# opskit-mongodb-path-end"
WIN_MONGO_CMD_MARKER     = "@rem opskit-mongodb-path-begin"
WIN_MONGO_CMD_END        = "@rem opskit-mongodb-path-end"

# ─── cmd AutoRun bat 存放路径（相对 HOME） ────────────────────────────────────
CMD_AUTORUN_MONGO_SUBPATH = ".opskit/cmd_autorun_mongo.bat"

# ─── Windows .cmd shim 模板 ───────────────────────────────────────────────────
SHIM_MONGO_CMD_TEMPLATE = (
    "@echo off\r\n"
    "rem opskit mongodb shim - auto version routing\r\n"
    r'set "_SNAP=%USERPROFILE%\.opskit\snapshots\mongodb.json"' + "\r\n"
    r'if not exist "%_SNAP%" goto :fallback' + "\r\n"
    r'for /f "usebackq tokens=1,* delims=:" %%A in (`findstr "mongod_bin_dir" "%_SNAP%"`) do (' + "\r\n"
    r'    set "_RAW=%%B"' + "\r\n"
    ")\r\n"
    r'if not defined _RAW goto :fallback' + "\r\n"
    r'set "_BIN=%_RAW: =%"' + "\r\n"
    r'set "_BIN=%_BIN:,=%"' + "\r\n"
    r'set _BIN=%_BIN:"=%' + "\r\n"
    r'if exist "%_BIN%\mongod.exe" (' + "\r\n"
    r'    "%_BIN%\mongod.exe" %*' + "\r\n"
    "    exit /b %ERRORLEVEL%\r\n"
    ")\r\n"
    ":fallback\r\n"
    "where mongod >nul 2>&1\r\n"
    "if %ERRORLEVEL% equ 0 ( mongod %* ) else ( echo mongod not found & exit /b 1 )\r\n"
)

# ─── Linux/macOS sh shim 模板 ─────────────────────────────────────────────────
SHIM_MONGO_SH_TEMPLATE = """\
#!/bin/sh
# opskit mongodb shim — 自动感知版本切换
_snap="$HOME/.opskit/snapshots/mongodb.json"
if [ -f "$_snap" ]; then
    _bin=$(sed -n 's/.*"mongod_bin_dir"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p' "$_snap" | head -1)
    if [ -x "$_bin/mongod" ]; then
        exec "$_bin/mongod" "$@"
    fi
fi
exec {fallback} "$@"
"""
