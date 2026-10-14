"""ClickHouse Linux driver."""
from __future__ import annotations

from pathlib import Path

from software._shared import shell_path
from .common import clickhouse_version_dir, detect_clickhouse_version, extract_clickhouse_tarball, shim_dir
from .constants import (
    CLICKHOUSE_BINARIES,
    CLICKHOUSE_PATH_MARKER_BEGIN,
    CLICKHOUSE_PATH_MARKER_END,
    PROFILE_D_CLICKHOUSE_FILE,
    SHIM_CLICKHOUSE_SH_TEMPLATE,
)
from .driver import PlatformDriver


class LinuxDriver(PlatformDriver):
    def install_tarball(self, version: str, tarball: Path) -> str:
        from software._shared.archive_install import staged_directory
        from software.base import InstallError
        dest = clickhouse_version_dir(version)
        try:
            with staged_directory(dest, prefix=".opskit-clickhouse-") as staging:
                bin_dir = extract_clickhouse_tarball(tarball, staging, version)
                _ensure_aliases(bin_dir, fallback_binary=dest / "bin/clickhouse")
        except FileExistsError as error:
            raise InstallError("ClickHouse version directory already exists; refusing to overwrite") from error
        except InstallError:
            raise
        except Exception as error:
            raise InstallError("ClickHouse installation publication failed") from error
        return str(dest / "bin")

    def install_shim(self, fallback_bin: str) -> None:
        sdir = shim_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        for name in CLICKHOUSE_BINARIES:
            shim = sdir / name
            shim.write_text(
                SHIM_CLICKHOUSE_SH_TEMPLATE.format(binary=name, fallback=fallback_bin),
                encoding="utf-8",
            )
            shim.chmod(0o755)
        shell_path.prepend_process_path(str(sdir))
        shell_path.inject_rc_path(
            str(sdir),
            CLICKHOUSE_PATH_MARKER_BEGIN,
            CLICKHOUSE_PATH_MARKER_END,
            PROFILE_D_CLICKHOUSE_FILE,
        )

    def remove_shim(self) -> None:
        sdir = shim_dir()
        for name in CLICKHOUSE_BINARIES:
            (sdir / name).unlink(missing_ok=True)
        try:
            sdir.rmdir()
        except Exception:
            pass
        shell_path.remove_rc_path(
            CLICKHOUSE_PATH_MARKER_BEGIN,
            CLICKHOUSE_PATH_MARKER_END,
            PROFILE_D_CLICKHOUSE_FILE,
        )

    def shim_active(self) -> bool:
        return shell_path.process_path_contains(str(shim_dir()))

    def apply_version_link(self, bin_dir: str) -> None:
        from .common import clickhouse_versions_dir
        shell_path.link_into_system_bin(bin_dir, CLICKHOUSE_BINARIES, managed_root=clickhouse_versions_dir())
        shell_path.prepend_process_path(str(shim_dir()))

    def restore_original(self) -> None:
        from .common import clickhouse_versions_dir
        shell_path.unlink_system_bin(CLICKHOUSE_BINARIES, clickhouse_versions_dir())

    def detect_active(self) -> str | None:
        return detect_clickhouse_version()

    def snapshot_pre_install(self) -> dict:
        return {}


def _ensure_aliases(bin_dir: Path, *, fallback_binary: Path | None = None) -> None:
    clickhouse = fallback_binary or bin_dir / "clickhouse"
    subcommands = {
        "clickhouse-server": "server",
        "clickhouse-client": "client",
        "clickhouse-local": "local",
        "clickhouse-benchmark": "benchmark",
    }
    for name in CLICKHOUSE_BINARIES:
        target = bin_dir / name
        if target.exists():
            continue
        try:
            target.symlink_to("clickhouse")
        except Exception:
            subcommand = subcommands.get(name, "")
            args = f" {subcommand}" if subcommand else ""
            target.write_text(f'#!/bin/sh\nexec "{clickhouse}"{args} "$@"\n', encoding="utf-8")
            target.chmod(0o755)
