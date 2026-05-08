"""Redis 配方业务专属常量（官方二进制直接下载，对齐 install-strategy.md）"""

# ─── 版本 ─────────────────────────────────────────────────────────────────────
REDIS_VERSIONS_FALLBACK = [
    "8.6.3", "8.4.3", "8.2.6", "8.0.6",
    "7.4.9", "7.2.14", "7.0.15",
    "6.2.22", "6.0.20",
]
REDIS_VERSIONS_API_URL  = "https://endoflife.date/api/redis.json"

# ─── 安装目录（私有，不需要 root）─────────────────────────────────────────────
REDIS_PRIVATE_SUBDIR    = ".opskit/redis"          # ~/.opskit/redis/
SNAPSHOT_SUBDIR         = ".opskit/snapshots"
SNAPSHOT_REDIS_FILE     = "redis.json"

# ─── Linux 下载：packages.redis.io deb 解析后提取二进制 ──────────────────────
# Packages.gz 查询 URL：先从阿里云 deb 镜像获取，fallback 到 packages.redis.io 官方
# {codename} = bookworm/bullseye/jammy/noble 等，{arch} = amd64/arm64
REDIS_DEB_PACKAGES_URLS = [
    "https://mirrors.aliyun.com/debian/dists/{codename}/main/binary-{arch}/Packages.gz",
    "https://packages.redis.io/deb/dists/{codename}/main/binary-{arch}/Packages.gz",
]
REDIS_DEB_BASE_URLS = [
    "https://mirrors.aliyun.com/debian",
    "https://packages.redis.io/deb",
]

# ─── Windows 下载：redis-windows/redis-windows GitHub Releases zip ──────────
# 支持 Redis 6.x / 7.x / 8.x，基于 MSYS2 构建，持续活跃维护
# URL 格式：Redis-{version}-Windows-x64-msys2.zip
REDIS_DL_WINDOWS_URLS = [
    "https://gh-proxy.com/https://github.com/redis-windows/redis-windows/releases/download/{version}/Redis-{version}-Windows-x64-msys2.zip",
    "https://ghfast.top/https://github.com/redis-windows/redis-windows/releases/download/{version}/Redis-{version}-Windows-x64-msys2.zip",
    "https://github.moeyy.xyz/https://github.com/redis-windows/redis-windows/releases/download/{version}/Redis-{version}-Windows-x64-msys2.zip",
    "https://github.com/redis-windows/redis-windows/releases/download/{version}/Redis-{version}-Windows-x64-msys2.zip",
]
# Windows fallback 版本（当用户选择的版本在 redis-windows 仓库不存在时使用）
REDIS_WINDOWS_FALLBACK_VERSION = "7.4.9"

# ─── macOS 下载：官方源码 tarball + make 编译 ─────────────────────────────────
REDIS_DL_MACOS_URLS = [
    "https://download.redis.io/releases/redis-{version}.tar.gz",
]

# ─── Linux 源码 tarball（macOS 同款，作为 deb 解析失败的 fallback）────────────
REDIS_DL_LINUX_SRC_URLS = [
    "https://download.redis.io/releases/redis-{version}.tar.gz",
]

# ─── shim 模板 ────────────────────────────────────────────────────────────────
# Linux / macOS sh shim，{binary} = redis-server / redis-cli，{fallback} = 系统路径
SHIM_REDIS_SH_TEMPLATE = """\
#!/bin/sh
# opskit redis shim — 自动感知版本切换
_snap="$HOME/.opskit/snapshots/redis.json"
if [ -f "$_snap" ]; then
    _bin=$(sed -n 's/.*"redis_bin_dir"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p' "$_snap" | head -1)
    if [ -x "$_bin/{binary}" ]; then
        exec "$_bin/{binary}" "$@"
    fi
fi
exec {fallback} "$@"
"""

# Windows cmd shim（通过 active junction 定位，{binary} = redis-server / redis-cli 等）
SHIM_REDIS_CMD_TEMPLATE = (
    "@echo off\r\n"
    "set _active=%USERPROFILE%\\.opskit\\redis\\active\\bin\\{binary}.exe\r\n"
    "if exist \"%_active%\" ( \"%_active%\" %* & exit /b %ERRORLEVEL% )\r\n"
    "echo {binary} not found, please install Redis via opskit 1>&2\r\n"
    "exit /b 1\r\n"
)

# ─── PATH 标记（shell rc 注入用）─────────────────────────────────────────────
REDIS_PATH_MARKER_BEGIN = "# >>> opskit redis >>>"
REDIS_PATH_MARKER_END   = "# <<< opskit redis <<<"
PROFILE_D_REDIS_FILE    = "/etc/profile.d/opskit-redis.sh"

# ─── Windows PATH 标记 ────────────────────────────────────────────────────────
WIN_REDIS_PS_MARKER          = "# >>> opskit redis >>>"
WIN_REDIS_PS_END             = "# <<< opskit redis <<<"
WIN_REDIS_CMD_MARKER         = "REM >>> opskit redis >>>"
WIN_REDIS_CMD_END            = "REM <<< opskit redis <<<"
CMD_AUTORUN_REDIS_SUBPATH    = ".opskit/redis/autorun_redis.bat"
