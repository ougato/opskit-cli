"""Java JDK 配方业务专属常量，与 core 完全隔离"""

# ─── Adoptium Temurin 版本 API ────────────────────────────────────────────────
# 获取可用 LTS 版本列表
JAVA_RELEASES_API      = "https://api.adoptium.net/v3/info/available_releases"
# 按 major 版本获取最新包（含下载 URL）
# 占位符：{major} {os} {arch}
JAVA_ASSETS_API        = "https://api.adoptium.net/v3/assets/latest/{major}/hotspot?os={os}&architecture={arch}&image_type=jdk&jvm_impl=hotspot&vendor=eclipse"

# ─── 下载 URL 镜像策略 ────────────────────────────────────────────────────────
# Adoptium 下载链接来自 GitHub Releases，使用 ghproxy 加速 + 官方直连兜底
# {url}: 完整 GitHub 原始 URL
GHPROXY_PREFIX         = "https://mirror.ghproxy.com/"

# 清华大学 Adoptium 镜像（国内首选，直接文件下载，无需 API）
# 路径格式：/Adoptium/{major}/jdk/{arch}/{os}/{filename}
# arch 映射：x64 / aarch64 / arm
# os 映射：linux / windows / mac
TUNA_ADOPTIUM_BASE     = "https://mirrors.tuna.tsinghua.edu.cn/Adoptium"

# 中科大 Adoptium 镜像（备选）
USTCM_ADOPTIUM_BASE    = "https://mirrors.ustc.edu.cn/adoptium"

# 所有国内下载镜像（按优先级排列），filename 中 + 已替换为 _ )
# URL 格式: {base}/{major}/jdk/{arch}/{os}/{filename}
JAVA_CN_MIRRORS = [
    TUNA_ADOPTIUM_BASE,
    USTCM_ADOPTIUM_BASE,
]

# ─── opskit 私有安装目录（相对 HOME） ─────────────────────────────────────────
JAVA_PRIVATE_SUBDIR    = ".opskit/java"

# ─── Windows 固定 bin 目录：.cmd wrapper，一次性加入 PATH，切换立即生效 ────────
JAVA_WIN_BIN_SUBDIR    = ".opskit/bin"

# ─── Linux/macOS shim PATH 标记 ───────────────────────────────────────────────
JAVA_PATH_MARKER_BEGIN = "# opskit-java-path-begin"
JAVA_PATH_MARKER_END   = "# opskit-java-path-end"

# ─── Windows PATH 注入标记 ────────────────────────────────────────────────────
WIN_JAVA_PS_MARKER     = "# opskit-java-path-begin"
WIN_JAVA_PS_END        = "# opskit-java-path-end"
WIN_JAVA_CMD_MARKER    = "@rem opskit-java-path-begin"
WIN_JAVA_CMD_END       = "@rem opskit-java-path-end"

# ─── cmd AutoRun bat 存放路径（相对 HOME） ────────────────────────────────────
CMD_AUTORUN_JAVA_SUBPATH = ".opskit/cmd_autorun_java.bat"

# ─── 快照 ─────────────────────────────────────────────────────────────────────
SNAPSHOT_SUBDIR        = ".opskit/snapshots"
SNAPSHOT_JAVA_FILE     = "java.json"

# ─── 超时 ─────────────────────────────────────────────────────────────────────
JAVA_DOWNLOAD_TIMEOUT  = 600
JAVA_INSTALL_TIMEOUT   = 120

# ─── /etc/profile.d java 文件名 ───────────────────────────────────────────────
PROFILE_D_JAVA_FILE    = "/etc/profile.d/opskit-java.sh"

# ─── shim 命令列表（java / javac / jar / javadoc / keytool）────────────────────
JAVA_SHIM_CMDS         = ("java", "javac", "jar", "javadoc", "keytool")

# ─── 版本列表硬编码 fallback（网络不通时使用，仅 LTS）────────────────────────
JAVA_VERSIONS_FALLBACK = [
    "21.0.11+10", "17.0.19+10", "11.0.27+6", "8.0.452+9",
]

# ─── Windows .cmd shim 模板（读快照路由到正确版本 bin） ───────────────────────
# {cmd}: 命令名，如 java / javac
SHIM_JAVA_CMD_TEMPLATE = (
    "@echo off\r\n"
    "rem opskit java shim - auto version routing\r\n"
    r'set "_SNAP=%USERPROFILE%\.opskit\snapshots\java.json"' + "\r\n"
    r'if not exist "%_SNAP%" goto :fallback' + "\r\n"
    r'for /f "usebackq tokens=1,* delims=:" %%A in (`findstr "java_bin_dir" "%_SNAP%"`) do (' + "\r\n"
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
    "if %ERRORLEVEL% equ 0 ( {cmd} %* ) else ( echo Java not found & exit /b 1 )\r\n"
)

# ─── Linux/macOS sh shim 模板 ─────────────────────────────────────────────────
# {cmd}: 命令名  {fallback}: fallback 路径
SHIM_JAVA_SH_TEMPLATE = """\
#!/bin/sh
# opskit java shim — 自动感知版本切换，无需重启终端
_snap="$HOME/.opskit/snapshots/java.json"
if [ -f "$_snap" ]; then
    _bin=$(sed -n 's/.*"java_bin_dir"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p' "$_snap" | head -1)
    if [ -x "$_bin/{cmd}" ]; then
        exec "$_bin/{cmd}" "$@"
    fi
fi
exec {fallback} "$@"
"""
