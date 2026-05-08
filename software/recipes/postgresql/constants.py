"""PostgreSQL 配方业务专属常量（二进制下载安装，对齐 MongoDB/Go 架构）"""

# ─── 版本 ─────────────────────────────────────────────────────────────────────

PGSQL_VERSIONS_FALLBACK  = ["18.3", "17.9", "16.13", "15.17", "14.22", "13.23", "12.20"]
PGSQL_VERSIONS_API_URL   = "https://endoflife.date/api/postgresql.json"

# ─── 安装目录 ─────────────────────────────────────────────────────────────────

PGSQL_PRIVATE_SUBDIR     = ".opskit/postgresql"      # 根目录：~/.opskit/postgresql/
SNAPSHOT_SUBDIR          = ".opskit/snapshots"        # 快照目录
SNAPSHOT_PGSQL_FILE      = "postgresql.json"          # 快照文件名

# ─── PATH 注入标记（Shell rc / Windows 注册表）────────────────────────────────

PGSQL_PATH_MARKER_BEGIN  = "# >>> opskit postgresql begin >>>"
PGSQL_PATH_MARKER_END    = "# <<< opskit postgresql end <<<"
WIN_PGSQL_PS_MARKER      = "# >>> opskit postgresql begin >>>"
WIN_PGSQL_PS_END         = "# <<< opskit postgresql end <<<"
WIN_PGSQL_CMD_MARKER     = "REM >>> opskit postgresql begin >>>"
WIN_PGSQL_CMD_END        = "REM <<< opskit postgresql end <<<"
CMD_AUTORUN_PGSQL_SUBPATH = ".opskit/postgresql/pgsql_autorun.bat"
PROFILE_D_PGSQL_FILE     = "/etc/profile.d/opskit-postgresql.sh"

# ─── shim 模板 ────────────────────────────────────────────────────────────────

SHIM_PGSQL_SH_TEMPLATE = (
    "#!/bin/sh\n"
    "PGSQL_ACTIVE=\"$HOME/.opskit/postgresql/active\"\n"
    "if [ -d \"$PGSQL_ACTIVE/bin\" ]; then\n"
    "  if [ -d \"$PGSQL_ACTIVE/lib\" ]; then\n"
    "    export LD_LIBRARY_PATH=\"$PGSQL_ACTIVE/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}\"\n"
    "  fi\n"
    "  exec \"$PGSQL_ACTIVE/bin/$(basename \"$0\")\" \"$@\"\n"
    "fi\n"
    "exec \"{fallback}\" \"$@\"\n"
)

SHIM_PGSQL_CMD_TEMPLATE = (
    "@echo off\r\n"
    "set \"PGSQL_ACTIVE=%USERPROFILE%\\.opskit\\postgresql\\active\"\r\n"
    "if exist \"%PGSQL_ACTIVE%\\bin\\psql.exe\" (\r\n"
    "  \"%PGSQL_ACTIVE%\\bin\\%~n0.exe\" %*\r\n"
    ") else (\r\n"
    "  echo PostgreSQL not found. Please reinstall via OpsKit.\r\n"
    ")\r\n"
)

# ─── Windows 子目录（fallback bin wrapper）────────────────────────────────────

PGSQL_WIN_BIN_SUBDIR     = ".opskit/postgresql/bin"   # fallback wrapper 目录

# ─── 下载 URL 模板（实测验证 2025-05，按赛马优先级排列）──────────────────────
#
# Windows：EDB 官方 CDN（build=1，实测 296MB，全版本 200），
#           theseus-rs ghfast 加速（47MB）作 fallback
# macOS：  EDB 官方 CDN（osx-binaries build=1，实测 319MB，x86_64），
#           theseus-rs ghfast 加速（9MB，支持 arm64）作 fallback
# Linux：  theseus-rs musl 静态链接（25MB，无系统依赖），ghfast 国内加速主源，
#           GitHub 直连作 fallback
#
# theseus-rs 版本号格式：{major}.{minor}.0（如 17.2 → 17.2.0）

PGSQL_DL_WINDOWS_URLS = [
    # EDB 官方 CDN，最快最稳（国内直连约 2-3s）
    "https://get.enterprisedb.com/postgresql/postgresql-{version}-1-windows-x64-binaries.zip",
    # theseus-rs ghfast 加速（较小，作 fallback）
    "https://ghfast.top/https://github.com/theseus-rs/postgresql-binaries/releases/download/{version}.0/postgresql-{version}.0-x86_64-pc-windows-msvc.zip",
]

PGSQL_DL_DARWIN_URLS = [
    # EDB 官方 CDN（仅 x86_64，arm64 403，但可在 arm64 Rosetta 下运行）
    "https://get.enterprisedb.com/postgresql/postgresql-{version}-1-osx-binaries.zip",
    # theseus-rs ghfast 加速（支持原生 arm64，作 fallback）
    "https://ghfast.top/https://github.com/theseus-rs/postgresql-binaries/releases/download/{version}.0/postgresql-{version}.0-{arch}-apple-darwin.tar.gz",
]

PGSQL_DL_LINUX_URLS = [
    # theseus-rs musl 静态链接，ghfast 国内加速（主源）
    "https://ghfast.top/https://github.com/theseus-rs/postgresql-binaries/releases/download/{version}.0/postgresql-{version}.0-{arch}-unknown-linux-musl.tar.gz",
    # GitHub 直连（fallback）
    "https://github.com/theseus-rs/postgresql-binaries/releases/download/{version}.0/postgresql-{version}.0-{arch}-unknown-linux-musl.tar.gz",
]

# ─── Linux PGDG deb 包下载 URL 模板（glibc 原生，适合 Debian/Ubuntu）─────────
#
# deb 包路径格式：pool/main/p/postgresql-{major}/postgresql[-client]-{major}_{version}-1.pgdg{codename}+1_{arch}.deb
# pgdg codename 映射：Debian 12(bookworm)=12, Debian 11(bullseye)=11,
#                    Ubuntu 22.04=22, Ubuntu 24.04=24
# 按赛马优先级排列：Aliyun（最快）→ PGDG 官方
#
# 下载策略：
#   1. 从 PGDG Packages.gz 动态查询实际包路径（保证版本准确）
#   2. server deb + client deb + libpq5 deb 三包合并解压到版本目录
#   3. bin 路径：usr/lib/postgresql/{major}/bin/ → 目标 bin/
#   4. lib 路径：usr/lib/x86_64-linux-gnu/ → 目标 lib/

PGSQL_PGDG_PACKAGES_URLS = [
    "https://mirrors.aliyun.com/postgresql/repos/apt/dists/{codename}-pgdg/main/binary-{arch}/Packages.gz",
    "https://apt.postgresql.org/pub/repos/apt/dists/{codename}-pgdg/main/binary-{arch}/Packages.gz",
]

PGSQL_PGDG_DEB_BASE_URLS = [
    "https://mirrors.aliyun.com/postgresql/repos/apt/{path}",
    "https://apt.postgresql.org/pub/repos/apt/{path}",
]

PGSQL_LINUX_DEB_ARCH_MAP = {
    "x86_64": "amd64",
    "aarch64": "arm64",
}

PGSQL_PGDG_SUPPORTED_MAJORS = [18, 17, 16, 15, 14, 13, 12]
