"""RedisRecipe 主类：纯调度，零平台 if，多版本快照管理，对齐 MySQL/MongoDB 模式"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import ClassVar

from software.base import InstallError, InstallStep, Recipe
from software.registry import register
from . import common
from .driver import get_driver


@register
class RedisRecipe(Recipe):
    key: ClassVar[str] = "redis"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "Redis 内存数据库"
    platforms: ClassVar[list[str]] = ["linux", "darwin", "windows"]
    dependencies: ClassVar[list[str]] = []
    has_version_picker: ClassVar[bool] = True
    has_switch: ClassVar[bool] = True

    # ─── detect / versions ────────────────────────────────────────────────────

    def detect(self) -> str | None:
        return get_driver().detect_active()

    def installed_versions(self) -> list[str]:
        return list(common.load_snapshot().get("versions", {}).keys())

    def _active_version(self) -> str | None:
        return common.load_snapshot().get("active_version")

    def versions(self) -> list[str]:
        return common.version_list()

    # ─── steps ────────────────────────────────────────────────────────────────

    def steps(self, action: str = "install") -> list[InstallStep]:
        if action == "uninstall":
            return [
                InstallStep("software.step.stop_service"),
                InstallStep("software.step.remove_files"),
                InstallStep("software.step.cleanup"),
            ]
        return [
            InstallStep("software.step.check"),
            InstallStep("software.step.download"),
            InstallStep("software.step.install"),
            InstallStep("software.step.verify"),
        ]

    # ─── install ──────────────────────────────────────────────────────────────

    def install(self, version: str) -> None:
        import sys
        from core.platform import get_platform
        from core.progress import MultiStepProgress
        from core.i18n import t

        info = get_platform()
        driver = get_driver()

        descs = [
            t("software.step.check"),
            t("software.step.download"),
            t("software.step.install"),
            t("software.step.verify"),
        ]
        with MultiStepProgress(descs) as sp:
            sp.step(descs[0])
            if info.os_type not in self.platforms:
                raise InstallError(t(
                    "software.redis_error.platform_not_supported",
                    platform=info.os_type,
                ))

            platform_data = driver.snapshot_pre_install()

            sp.step(descs[1])
            with tempfile.TemporaryDirectory(
                prefix="opskit-redis-", ignore_cleanup_errors=True
            ) as tmpdir:
                if sys.platform == "win32":
                    src = common.download_redis_windows(version, Path(tmpdir) / "redis")
                elif sys.platform == "darwin":
                    src = common.download_redis_macos(version, Path(tmpdir) / "redis")
                else:
                    src = common.download_redis_linux(version, Path(tmpdir) / "redis")

                sp.step(descs[2])
                try:
                    bin_dir = driver.install_binary(version, src)
                except InstallError:
                    raise
                except Exception as e:
                    raise InstallError(t(
                        "software.redis_error.extract_failed",
                        version=version, error=str(e),
                    )) from e

            snap = common.load_snapshot()
            versions = snap.get("versions", {})
            versions[version] = {"redis_bin_dir": bin_dir, **platform_data}
            snap["versions"] = versions
            snap["active_version"] = version
            snap["redis_bin_dir"] = bin_dir
            common.save_snapshot(snap)

            fallback = shutil.which("redis-server") or "redis-server"
            driver.install_shim(fallback)
            driver.apply_version_link(bin_dir)

            sp.step(descs[3])
            if not self.detect():
                raise InstallError(t("software.redis_error.verify_failed"))
            sp.complete()

    # ─── switch ───────────────────────────────────────────────────────────────

    def switch(self, version: str) -> None:
        from core.i18n import t

        snap = common.load_snapshot()
        versions = snap.get("versions", {})
        if version not in versions:
            raise InstallError(t(
                "software.redis_error.not_installed",
                version=version,
            ))
        bin_dir = versions[version]["redis_bin_dir"]
        snap["active_version"] = version
        snap["redis_bin_dir"] = bin_dir
        common.save_snapshot(snap)
        get_driver().apply_version_link(bin_dir)

    # ─── uninstall ────────────────────────────────────────────────────────────

    def uninstall(self, version: str | None = None) -> None:
        from core.progress import MultiStepProgress
        from core.i18n import t

        driver = get_driver()
        snap = common.load_snapshot()
        versions = snap.get("versions", {})

        descs = [
            t("software.step.stop_service"),
            t("software.step.remove_files"),
            t("software.step.cleanup"),
        ]
        with MultiStepProgress(descs) as sp:
            sp.step(descs[0])

            if version is not None:
                # 卸载指定版本
                target_versions = [version] if version in versions else []
            else:
                # 卸载全部
                target_versions = list(versions.keys())

            sp.step(descs[1])
            for ver in target_versions:
                ver_dir = common.redis_version_dir(ver)
                try:
                    if ver_dir.exists():
                        shutil.rmtree(str(ver_dir), ignore_errors=True)
                except Exception:
                    pass
                versions.pop(ver, None)

            sp.step(descs[2])
            if not versions:
                driver.remove_shim()
                driver.restore_original()
                common.delete_snapshot()
                try:
                    redis_root = common.redis_versions_dir()
                    if redis_root.exists():
                        shutil.rmtree(str(redis_root), ignore_errors=True)
                except Exception:
                    pass
            else:
                # 切换激活版本到剩余最新
                new_active = next(iter(versions))
                snap["active_version"] = new_active
                snap["redis_bin_dir"] = versions[new_active]["redis_bin_dir"]
                snap["versions"] = versions
                common.save_snapshot(snap)
                driver.apply_version_link(versions[new_active]["redis_bin_dir"])
            sp.complete()
