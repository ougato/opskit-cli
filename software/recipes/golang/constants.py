"""Golang 配方业务专属常量，与 core 完全隔离"""

# ─── Golang 版本 API ──────────────────────────────────────────────────────────
GOLANG_VERSIONS_API    = "https://go.dev/dl/?mode=json&include=all"
GOLANG_EOL_API         = "https://endoflife.date/api/go.json"

# ─── 下载 URL 列表（完整 URL，按优先级排列，国内镜像优先）──────────────────────
# {version}: 版本号，如 1.23.4  {arch}: 架构，如 amd64/arm64
GOLANG_DL_LINUX_URLS = [
    "https://golang.google.cn/dl/go{version}.linux-{arch}.tar.gz",
    "https://mirror.ghproxy.com/https://go.dev/dl/go{version}.linux-{arch}.tar.gz",
    "https://go.dev/dl/go{version}.linux-{arch}.tar.gz",
]

GOLANG_DL_DARWIN_URLS = [
    "https://golang.google.cn/dl/go{version}.darwin-{arch}.tar.gz",
    "https://mirror.ghproxy.com/https://go.dev/dl/go{version}.darwin-{arch}.tar.gz",
    "https://go.dev/dl/go{version}.darwin-{arch}.tar.gz",
]

GOLANG_DL_WINDOWS_URLS = [
    "https://golang.google.cn/dl/go{version}.windows-{arch}.zip",
    "https://mirror.ghproxy.com/https://go.dev/dl/go{version}.windows-{arch}.zip",
    "https://go.dev/dl/go{version}.windows-{arch}.zip",
]

# ─── 安装目录（系统级，需 root/管理员） ────────────────────────────────────────
GOLANG_INSTALL_DIR_LINUX   = "/usr/local/go"
GOLANG_INSTALL_DIR_DARWIN  = "/usr/local/go"
GOLANG_INSTALL_DIR_WINDOWS = "go"   # 相对 %LOCALAPPDATA% 的子目录

# ─── opskit 私有安装目录（无 root 时 fallback） ───────────────────────────────
GOLANG_PRIVATE_SUBDIR  = ".opskit/go"

# ─── Windows 固定 bin 目录：硬链接放这里，一次性加入 PATH，切换立即生效 ────────
GOLANG_WIN_BIN_SUBDIR  = ".opskit/bin"

# ─── GOPATH / GOROOT 注入标记 ─────────────────────────────────────────────────
GOPATH_MARKER_BEGIN    = "# opskit-go-path-begin"
GOPATH_MARKER_END      = "# opskit-go-path-end"

# ─── Windows PATH 注入标记 ────────────────────────────────────────────────────
WIN_GO_PS_MARKER       = "# opskit-go-path-begin"
WIN_GO_PS_END          = "# opskit-go-path-end"
WIN_GO_CMD_MARKER      = "@rem opskit-go-path-begin"
WIN_GO_CMD_END         = "@rem opskit-go-path-end"

# ─── cmd AutoRun bat 存放路径（相对 HOME） ────────────────────────────────────
CMD_AUTORUN_GO_SUBPATH = ".opskit/cmd_autorun_go.bat"

# ─── 快照 ─────────────────────────────────────────────────────────────────────
SNAPSHOT_SUBDIR        = ".opskit/snapshots"
SNAPSHOT_GOLANG_FILE   = "golang.json"

# ─── 超时 ─────────────────────────────────────────────────────────────────────
GOLANG_DOWNLOAD_TIMEOUT = 600
GOLANG_INSTALL_TIMEOUT  = 120

# ─── 版本列表硬编码 fallback（网络不通时使用） ───────────────────────────────
GOLANG_VERSIONS_FALLBACK = [
    "1.26.2", "1.26.1", "1.25.8", "1.24.4", "1.23.8", "1.22.12",
]

# ─── /etc/profile.d go 文件名 ─────────────────────────────────────────────────
PROFILE_D_GO_FILE      = "/etc/profile.d/opskit-golang.sh"

# ─── Windows .cmd shim 模板 ───────────────────────────────────────────────────
SHIM_GO_CMD_TEMPLATE = (
    "@echo off\r\n"
    "rem opskit golang shim - auto version routing\r\n"
    r'set "_SNAP=%USERPROFILE%\.opskit\snapshots\golang.json"' + "\r\n"
    r'if not exist "%_SNAP%" goto :fallback' + "\r\n"
    r'for /f "usebackq tokens=1,* delims=:" %%A in (`findstr "go_bin_dir" "%_SNAP%"`) do (' + "\r\n"
    r'    set "_RAW=%%B"' + "\r\n"
    ")\r\n"
    r'if not defined _RAW goto :fallback' + "\r\n"
    r'set "_BIN=%_RAW: =%"' + "\r\n"
    r'set "_BIN=%_BIN:,=%"' + "\r\n"
    r'set _BIN=%_BIN:"=%' + "\r\n"
    r'if exist "%_BIN%\go.exe" (' + "\r\n"
    r'    "%_BIN%\go.exe" %*' + "\r\n"
    "    exit /b %ERRORLEVEL%\r\n"
    ")\r\n"
    ":fallback\r\n"
    "where go >nul 2>&1\r\n"
    "if %ERRORLEVEL% equ 0 ( go %* ) else ( echo Go not found & exit /b 1 )\r\n"
)

# ─── Linux/macOS sh shim 模板 ─────────────────────────────────────────────────
SHIM_GO_SH_TEMPLATE = """\
#!/bin/sh
# opskit golang shim — 自动感知版本切换
_snap="$HOME/.opskit/snapshots/golang.json"
if [ -f "$_snap" ]; then
    _bin=$(sed -n 's/.*"go_bin_dir"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p' "$_snap" | head -1)
    if [ -x "$_bin/go" ]; then
        exec "$_bin/go" "$@"
    fi
fi
exec {fallback} "$@"
"""
