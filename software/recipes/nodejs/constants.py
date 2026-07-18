"""NodeJS 配方业务专属常量，与 core 完全隔离"""

# ─── NodeJS 版本 API ──────────────────────────────────────────────────────────
NODEJS_VERSIONS_API    = "https://nodejs.org/dist/index.json"
NODEJS_EOL_API         = "https://endoflife.date/api/nodejs.json"

# ─── 下载 URL 列表（完整 URL，按优先级排列，国内镜像优先）──────────────────────
# {version}: 版本号，如 22.11.0  {arch}: 架构，如 x64/arm64
NODEJS_DL_LINUX_URLS = [
    "https://npmmirror.com/mirrors/node/v{version}/node-v{version}-linux-{arch}.tar.xz",
    "https://mirrors.aliyun.com/nodejs-release/v{version}/node-v{version}-linux-{arch}.tar.xz",
    "https://nodejs.org/dist/v{version}/node-v{version}-linux-{arch}.tar.xz",
]

NODEJS_DL_DARWIN_URLS = [
    "https://npmmirror.com/mirrors/node/v{version}/node-v{version}-darwin-{arch}.tar.gz",
    "https://mirrors.aliyun.com/nodejs-release/v{version}/node-v{version}-darwin-{arch}.tar.gz",
    "https://nodejs.org/dist/v{version}/node-v{version}-darwin-{arch}.tar.gz",
]

NODEJS_DL_WINDOWS_URLS = [
    "https://npmmirror.com/mirrors/node/v{version}/node-v{version}-win-{arch}.zip",
    "https://mirrors.aliyun.com/nodejs-release/v{version}/node-v{version}-win-{arch}.zip",
    "https://nodejs.org/dist/v{version}/node-v{version}-win-{arch}.zip",
]

# ─── 老 glibc 兼容（CentOS 7 等，glibc < 2.28 跑不了官方 Node 18+ 包）─────────
# unofficial-builds 提供 glibc-2.17 兼容包（仅 linux x64，最高到 22.x 主版本）
NODEJS_GLIBC_MIN = (2, 28)
NODEJS_GLIBC217_MAX_MAJOR = 22
NODEJS_DL_LINUX_GLIBC217_URLS = [
    "https://npmmirror.com/mirrors/node-unofficial-builds/v{version}/node-v{version}-linux-{arch}-glibc-217.tar.xz",
    "https://unofficial-builds.nodejs.org/download/release/v{version}/node-v{version}-linux-{arch}-glibc-217.tar.xz",
]

# ─── opskit 私有安装目录（无 root 时 fallback） ───────────────────────────────
NODEJS_PRIVATE_SUBDIR  = ".opskit/nodejs"

# ─── Windows 固定 bin 目录：.cmd wrapper，一次性加入 PATH，切换立即生效 ────────
NODEJS_WIN_BIN_SUBDIR  = ".opskit/bin"

# ─── Linux/macOS shim PATH 标记 ───────────────────────────────────────────────
NODE_PATH_MARKER_BEGIN = "# opskit-node-path-begin"
NODE_PATH_MARKER_END   = "# opskit-node-path-end"

# ─── Windows PATH 注入标记 ────────────────────────────────────────────────────
WIN_NODE_PS_MARKER     = "# opskit-node-path-begin"
WIN_NODE_PS_END        = "# opskit-node-path-end"
WIN_NODE_CMD_MARKER    = "@rem opskit-node-path-begin"
WIN_NODE_CMD_END       = "@rem opskit-node-path-end"

# ─── cmd AutoRun bat 存放路径（相对 HOME） ────────────────────────────────────
CMD_AUTORUN_NODE_SUBPATH = ".opskit/cmd_autorun_node.bat"

# ─── 快照 ─────────────────────────────────────────────────────────────────────
SNAPSHOT_SUBDIR        = ".opskit/snapshots"
SNAPSHOT_NODEJS_FILE   = "nodejs.json"

# ─── 超时 ─────────────────────────────────────────────────────────────────────
NODEJS_DOWNLOAD_TIMEOUT = 600
NODEJS_INSTALL_TIMEOUT  = 120

# ─── 版本列表硬编码 fallback（网络不通时使用） ───────────────────────────────
NODEJS_VERSIONS_FALLBACK = [
    "22.15.0", "22.14.0", "20.19.2", "20.18.3", "18.20.8",
]

# ─── /etc/profile.d node 文件名 ───────────────────────────────────────────────
PROFILE_D_NODE_FILE    = "/etc/profile.d/opskit-nodejs.sh"

# ─── Windows .cmd shim 模板（读快照路由到正确版本 bin） ───────────────────────
# {cmd}: 命令名，如 node / npm / npx
SHIM_NODE_CMD_TEMPLATE = (
    "@echo off\r\n"
    "rem opskit nodejs shim - auto version routing\r\n"
    r'set "_SNAP=%USERPROFILE%\.opskit\snapshots\nodejs.json"' + "\r\n"
    r'if not exist "%_SNAP%" goto :fallback' + "\r\n"
    r'for /f "usebackq tokens=1,* delims=:" %%A in (`findstr "node_bin_dir" "%_SNAP%"`) do (' + "\r\n"
    r'    set "_RAW=%%B"' + "\r\n"
    ")\r\n"
    r'if not defined _RAW goto :fallback' + "\r\n"
    r'set "_BIN=%_RAW: =%"' + "\r\n"
    r'set "_BIN=%_BIN:,=%"' + "\r\n"
    r'set _BIN=%_BIN:"=%' + "\r\n"
    r'if exist "%_BIN%\{cmd}.exe" (' + "\r\n"
    r'    "%_BIN%\{cmd}.exe" %*' + "\r\n"
    "    exit /b %ERRORLEVEL%\r\n"
    ")\r\n"
    ":fallback\r\n"
    "where {cmd} >nul 2>&1\r\n"
    "if %ERRORLEVEL% equ 0 ( {cmd} %* ) else ( echo Node.js not found & exit /b 1 )\r\n"
)

# ─── Linux/macOS sh shim 模板 ─────────────────────────────────────────────────
# {cmd}: 命令名，如 node / npm / npx  {fallback}: fallback 路径
SHIM_NODE_SH_TEMPLATE = """\
#!/bin/sh
# opskit nodejs shim — 自动感知版本切换，无需重启终端
_snap="$HOME/.opskit/snapshots/nodejs.json"
if [ -f "$_snap" ]; then
    _bin=$(sed -n 's/.*"node_bin_dir"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p' "$_snap" | head -1)
    if [ -x "$_bin/{cmd}" ]; then
        exec "$_bin/{cmd}" "$@"
    fi
fi
exec {fallback} "$@"
"""
