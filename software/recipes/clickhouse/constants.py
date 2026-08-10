"""ClickHouse recipe constants."""

CLICKHOUSE_VERSIONS_FALLBACK = [
    "26.7.3.19",
    "26.6.2.160",
    "26.3.17.110",
    "25.8.11.66",
    "25.3.7.194",
]
CLICKHOUSE_RELEASES_API_URL = "https://api.github.com/repos/ClickHouse/ClickHouse/releases"

CLICKHOUSE_PRIVATE_SUBDIR = ".opskit/clickhouse"
SNAPSHOT_SUBDIR = ".opskit/snapshots"
SNAPSHOT_CLICKHOUSE_FILE = "clickhouse.json"

CLICKHOUSE_DL_URLS = [
    "https://github.com/ClickHouse/ClickHouse/releases/download/v{version}-stable/clickhouse-common-static-{version}-{arch}.tgz",
    "https://github.com/ClickHouse/ClickHouse/releases/download/v{version}-lts/clickhouse-common-static-{version}-{arch}.tgz",
]

CLICKHOUSE_BINARIES = (
    "clickhouse",
    "clickhouse-server",
    "clickhouse-client",
    "clickhouse-local",
    "clickhouse-benchmark",
)

SHIM_CLICKHOUSE_SH_TEMPLATE = """\
#!/bin/sh
# opskit clickhouse shim - follows the active version
_snap="$HOME/.opskit/snapshots/clickhouse.json"
if [ -f "$_snap" ]; then
    _bin=$(sed -n 's/.*"clickhouse_bin_dir"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p' "$_snap" | head -1)
    if [ -x "$_bin/{binary}" ]; then
        exec "$_bin/{binary}" "$@"
    fi
fi
exec {fallback} "$@"
"""

CLICKHOUSE_PATH_MARKER_BEGIN = "# >>> opskit clickhouse >>>"
CLICKHOUSE_PATH_MARKER_END = "# <<< opskit clickhouse <<<"
PROFILE_D_CLICKHOUSE_FILE = "/etc/profile.d/opskit-clickhouse.sh"
