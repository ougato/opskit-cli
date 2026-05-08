"""Python 配方业务专属常量，与 core 完全隔离"""

# ─── Python 版本 API ──────────────────────────────────────────────────────────
PYTHON_EOL_API         = "https://endoflife.date/api/python.json"

# ─── pip bootstrap ───────────────────────────────────────────────────────────
GET_PIP_URL            = "https://bootstrap.pypa.io/get-pip.py"

# ─── uv 工具链（Linux/macOS 安装脚本） ───────────────────────────────────────
UV_INSTALL_SH          = "https://astral.sh/uv/install.sh"
UV_INSTALL_SH_GHPROXY  = "https://mirror.ghproxy.com/https://astral.sh/uv/install.sh"

# ─── uv 路径（相对 HOME） ─────────────────────────────────────────────────────
UV_BIN_SUBDIR          = ".opskit/bin"
UV_PYTHON_SUBDIR       = ".opskit/python"
UV_SHIM_SUBDIR         = ".opskit/shims"

# ─── uv 超时 ─────────────────────────────────────────────────────────────────
UV_INSTALL_TIMEOUT     = 120
UV_PYTHON_TIMEOUT      = 1800

# ─── Python 快照 ─────────────────────────────────────────────────────────────
SNAPSHOT_SUBDIR        = ".opskit/snapshots"
SNAPSHOT_PYTHON_FILE   = "python.json"

# ─── symlink PATH 注入标记 ────────────────────────────────────────────────────
SYMLINK_MARKER_BEGIN   = "# opskit-python-path-begin"
SYMLINK_MARKER_END     = "# opskit-python-path-end"

# ─── Python 源码编译 ──────────────────────────────────────────────────────────
PYTHON_SRC_URL         = "https://www.python.org/ftp/python/{full_ver}/Python-{full_ver}.tar.xz"
PYTHON_SRC_NPROC_MAX   = 8
PYTHON_BUILD_TIMEOUT   = 1800

# ─── opskit 要求的最低 Python 版本 ───────────────────────────────────────────
PYTHON_MIN_VERSION     = "3.10"

# ─── uv Windows 安装源 ────────────────────────────────────────────────────────
UV_INSTALL_PS1          = "https://astral.sh/uv/install.ps1"
UV_INSTALL_PS1_GHPROXY  = "https://mirror.ghproxy.com/https://astral.sh/uv/install.ps1"
UV_WIN_ZIP_URL          = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
UV_WIN_ZIP_GHPROXY      = "https://mirror.ghproxy.com/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"

# ─── cmd_autorun bat 存放路径（相对 HOME） ────────────────────────────────────
CMD_AUTORUN_SUBPATH     = ".opskit/cmd_autorun.bat"

# ─── Windows PATH 注入标记（PowerShell Profile / cmd bat 边界） ───────────────
WIN_SHIM_PS_MARKER      = "# opskit-shims-path-begin"
WIN_SHIM_PS_END         = "# opskit-shims-path-end"
WIN_SHIM_CMD_MARKER     = "@rem opskit-shims-path-begin"
WIN_SHIM_CMD_END        = "@rem opskit-shims-path-end"

# ─── Linux/macOS shim PATH 标记 ───────────────────────────────────────────────
SHIM_MARKER_BEGIN       = "# opskit-shims-path-begin"
SHIM_MARKER_END         = "# opskit-shims-path-end"

# ─── 版本列表硬编码 fallback（网络不通时使用） ───────────────────────────────
PYTHON_VERSIONS_FALLBACK = [
    "3.14.4", "3.13.13", "3.12.13", "3.11.15", "3.10.20", "3.9.25",
]

# ─── /etc/profile.d shim 文件名 ───────────────────────────────────────────────
PROFILE_D_SHIM_FILE     = "/etc/profile.d/opskit-python.sh"

# ─── Windows .cmd shim 模板 ───────────────────────────────────────────────────
SHIM_CMD_TEMPLATE = (
    "@echo off\r\n"
    "rem opskit python shim - auto version routing, no restart needed\r\n"
    r'set "_SNAP=%USERPROFILE%\.opskit\snapshots\python.json"' + "\r\n"
    r'if not exist "%_SNAP%" goto :fallback' + "\r\n"
    r'for /f "usebackq tokens=1,* delims=:" %%A in (`findstr "uv_python_path" "%_SNAP%"`) do (' + "\r\n"
    r'    set "_RAW=%%B"' + "\r\n"
    ")\r\n"
    r'if not defined _RAW goto :fallback' + "\r\n"
    r'set "_BIN=%_RAW: =%"' + "\r\n"
    r'set "_BIN=%_BIN:,=%"' + "\r\n"
    r'set _BIN=%_BIN:"=%' + "\r\n"
    r'if exist "%_BIN%" (' + "\r\n"
    r'    "%_BIN%" %*' + "\r\n"
    "    exit /b %ERRORLEVEL%\r\n"
    ")\r\n"
    ":fallback\r\n"
    "where python >nul 2>&1\r\n"
    "if %ERRORLEVEL% equ 0 ( python %* ) else ( echo Python not found & exit /b 1 )\r\n"
)

# ─── Linux/macOS sh shim 模板 ─────────────────────────────────────────────────
SHIM_SH_TEMPLATE = """\
#!/bin/sh
# opskit python shim — 自动感知版本切换，无需 hash -r
_snap="$HOME/.opskit/snapshots/python.json"
if [ -f "$_snap" ]; then
    _bin=$(sed -n 's/.*"uv_python_path"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p' "$_snap" | head -1)
    if [ -x "$_bin" ]; then
        exec "$_bin" "$@"
    fi
fi
exec {fallback} "$@"
"""
