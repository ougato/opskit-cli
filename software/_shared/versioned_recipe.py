"""版本化 tarball recipe 模板基类 — 统一 install / uninstall / switch / upgrade 调度。

golang / nodejs / java / mongodb / mysql 五个 recipe 的安装主流程此前是
「复制粘贴」级别的同构代码（各约 150 行 install + uninstall + switch），仅在
i18n 命名空间、目录前缀、tarball 文件名、shim 命令等少数差异点不同。

本基类用模板方法（Template Method）固化「检测 → 下载 → 解压安装 → 写快照 →
shim/软链 → 校验」的不变骨架，把差异点收敛为子类的钩子（路径/下载/快照委托）与
类属性（命名空间/前缀/文件名模板），使每个 recipe 仅保留差异化逻辑。

对 Recipe 对外接口零改动：install/uninstall/switch/upgrade/steps/detect/
installed_versions 签名与可见行为与原各 recipe 完全一致。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
from abc import abstractmethod
from pathlib import Path
from typing import ClassVar

from core.i18n import t
from software.base import InstallError, InstallStep, Recipe

# 进度条占位推进上限：下载阶段无法获知真实百分比，用 ticker 线程推到此值后等真实完成
_TICKER_MAX_PCT = 90

# 验证阶段实际运行二进制的超时秒数
_VERIFY_RUN_TIMEOUT = 10


class VersionedTarballRecipe(Recipe):
    """多版本 tarball 软件的安装模板。

    子类必须提供的类属性：
        _error_ns:        i18n 错误命名空间，如 ``"golang_error"``
        _shim_cmd:        系统 shim 兜底命令名，如 ``"go"``
        _bin_dir_snap_key: 快照中记录 bin 目录的字段名，如 ``"go_bin_dir"``
        _tmpdir_prefix:   下载临时目录前缀，如 ``"opskit-go-"``
        _dir_prefix:      版本安装目录名前缀，如 ``"go"`` / ``"jdk"``
        _tarball_stem:    tarball 文件名模板（不含扩展名），如 ``"go{version}"``

    子类必须实现的钩子：
        _get_driver / _versions_dir / _version_dir / _bin_dir /
        _download / _load_snapshot / _save_snapshot / _delete_snapshot /
        versions
    """

    _error_ns: ClassVar[str]
    _shim_cmd: ClassVar[str]
    _bin_dir_snap_key: ClassVar[str]
    _tmpdir_prefix: ClassVar[str]
    _dir_prefix: ClassVar[str]
    _tarball_stem: ClassVar[str]

    # ── 子类必须实现的钩子 ───────────────────────────────────────────────────
    @abstractmethod
    def _get_driver(self):
        """返回该 recipe 的 PlatformDriver 实例（各 recipe 的 get_driver()）。"""

    @abstractmethod
    def _versions_dir(self) -> Path:
        """所有版本的根目录，如 ~/.opskit/go/"""

    @abstractmethod
    def _version_dir(self, version: str) -> Path:
        """指定版本的安装目录。"""

    @abstractmethod
    def _bin_dir(self, version: str) -> Path:
        """指定版本的 bin 目录。"""

    @abstractmethod
    def _download(self, version: str, dest: Path):
        """下载 tarball 到 dest（各 recipe 的 download_*_tarball）。"""

    @abstractmethod
    def _load_snapshot(self) -> dict:
        ...

    @abstractmethod
    def _save_snapshot(self, data: dict) -> None:
        ...

    @abstractmethod
    def _delete_snapshot(self) -> None:
        ...

    # ── 可覆盖的差异点（带默认实现）─────────────────────────────────────────
    def _tarball_ext(self) -> str:
        """tarball 扩展名。默认 Windows .zip / 其余 .tar.gz；子类按需覆盖。"""
        import sys
        return ".zip" if sys.platform == "win32" else ".tar.gz"

    def _decode_version(self, raw: str) -> str:
        """将目录名去前缀后的残段解码为版本号。默认原样返回。"""
        return raw

    def _sort_key(self, version: str) -> list[int]:
        """版本排序键。默认按 ``.`` 分段取数字。"""
        return [int(x) for x in version.split(".") if x.isdigit()]

    def _switch_bin_dir(self, version: str) -> str:
        """switch 时写入快照的 bin 目录。默认 ``_bin_dir(version)``。"""
        return str(self._bin_dir(version))

    # ── 模板方法实现（不变骨架）─────────────────────────────────────────────
    def detect(self) -> str | None:
        return self._get_driver().detect_active()

    def activate(self) -> None:
        snap = self._load_snapshot()
        bin_dir = snap.get(self._bin_dir_snap_key)
        active = snap.get("active_version")
        if not bin_dir and active:
            bin_dir = str(self._bin_dir(active))
        if not bin_dir or not Path(bin_dir).is_dir():
            return
        try:
            self._get_driver().apply_version_link(bin_dir)
        except Exception:
            pass

    def _active_version(self) -> str | None:
        return self._load_snapshot().get("active_version")

    def installed_versions(self) -> list[str]:
        base = self._versions_dir()
        if not base.exists():
            return []
        versions: list[str] = []
        prefix_len = len(self._dir_prefix)
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if not name.startswith(self._dir_prefix):
                continue
            ver = self._decode_version(name[prefix_len:])
            if ver and ver[0].isdigit():
                versions.append(ver)
        versions.sort(key=self._sort_key, reverse=True)
        return versions

    def steps(self, action: str = "install") -> list[InstallStep]:
        if action == "uninstall":
            return [
                InstallStep("software.step.remove_files"),
                InstallStep("software.step.cleanup"),
            ]
        return [
            InstallStep("software.step.check"),
            InstallStep("software.step.download"),
            InstallStep("software.step.install"),
            InstallStep("software.step.verify"),
        ]

    def install(self, version: str) -> None:
        from core.platform import get_platform
        from core.progress import MultiStepProgress

        info = get_platform()
        driver = self._get_driver()

        descs = [
            t("software.step.check"),
            t("software.step.download"),
            t("software.step.install"),
            t("software.step.verify"),
        ]
        with MultiStepProgress(descs) as sp:
            sp.step(t("software.step.check"))
            if info.os_type not in self.platforms:
                raise InstallError(
                    t(f"software.{self._error_ns}.platform_not_supported", platform=info.os_type)
                )

            sp.step(t("software.step.download"))
            with tempfile.TemporaryDirectory(prefix=self._tmpdir_prefix, ignore_cleanup_errors=True) as tmpdir:
                tarball = Path(tmpdir) / f"{self._tarball_stem.format(version=version)}{self._tarball_ext()}"

                _stop_pct = threading.Event()

                def _ticker():
                    pct = 1
                    while not _stop_pct.wait(1.0):
                        if pct < _TICKER_MAX_PCT:
                            pct += 1
                        sp.set_step_pct(pct)

                t_pct = threading.Thread(target=_ticker, daemon=True)
                t_pct.start()
                try:
                    dl_result = self._download(version, tarball)
                finally:
                    _stop_pct.set()
                    t_pct.join(timeout=2)

                # _download 可能返回 Path（tarball）或 list[Path]（APT deb 包列表）
                install_arg = dl_result if dl_result is not None else tarball

                sp.step(t("software.step.install"))
                pre = driver.snapshot_pre_install()
                snap = self._load_snapshot()
                if not snap:
                    snap = {
                        **pre,
                        "installed_versions": [],
                        "active_version": None,
                    }

                bin_dir = driver.install_tarball(version, install_arg)

            installed = snap.get("installed_versions", [])
            if version not in installed:
                installed.append(version)
            snap["installed_versions"] = installed
            snap["active_version"] = version
            snap[self._bin_dir_snap_key] = bin_dir
            self._save_snapshot(snap)

            sp.step(t("software.step.verify"))
            fallback = shutil.which(self._shim_cmd) or self._shim_cmd
            try:
                driver.install_shim(fallback)
            except Exception:
                pass

            try:
                driver.apply_version_link(bin_dir)
            except Exception:
                pass

            self._verify_runnable(bin_dir)
            if not self.detect():
                raise InstallError(t(f"software.{self._error_ns}.verify_failed"))
            sp.complete()

    def _verify_runnable(self, bin_dir: str) -> None:
        """真正运行一次主命令 --version，捕获缺共享库等只有执行才暴露的问题"""
        exe = Path(bin_dir) / (self._shim_cmd + (".exe" if sys.platform == "win32" else ""))
        if not exe.exists():
            return
        try:
            r = subprocess.run(
                [str(exe), "--version"],
                capture_output=True, text=True, timeout=_VERIFY_RUN_TIMEOUT,
            )
        except Exception as e:
            raise InstallError(t(f"software.{self._error_ns}.verify_failed") + f"（{e}）") from e
        if r.returncode != 0:
            lines = [ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()]
            detail = f"（{lines[-1]}）" if lines else ""
            raise InstallError(t(f"software.{self._error_ns}.verify_failed") + detail)

    def upgrade(self, version: str) -> None:
        """升级：直接安装新版本（保留已有版本，不卸载）"""
        self.install(version)

    def switch(self, version: str) -> None:
        installed = self.installed_versions()
        if version not in installed:
            raise InstallError(t(f"software.{self._error_ns}.not_installed", version=version))

        bin_dir = self._switch_bin_dir(version)
        snap = self._load_snapshot()
        snap["active_version"] = version
        snap[self._bin_dir_snap_key] = bin_dir
        self._save_snapshot(snap)

        try:
            self._get_driver().apply_version_link(bin_dir)
        except Exception:
            pass

    def uninstall(self, version: str | None = None) -> None:
        from core.progress import MultiStepProgress

        driver = self._get_driver()
        snap = self._load_snapshot()
        active = snap.get("active_version")

        def _remove_version_dir(ver: str) -> None:
            d = self._version_dir(ver)
            if d.exists():
                shutil.rmtree(str(d), ignore_errors=True)

        descs = [
            t("software.step.remove_files"),
            t("software.step.cleanup"),
        ]
        with MultiStepProgress(descs) as sp:
            sp.step(t("software.step.remove_files"))
            installed = self.installed_versions()

            if version is None:
                for v in installed:
                    _remove_version_dir(v)
                driver.remove_shim()
                driver.restore_original()
                self._delete_snapshot()
            else:
                _remove_version_dir(version)
                remaining = [v for v in installed if v != version]

                if not remaining:
                    # 最后一个版本被卸载：全部清理
                    driver.remove_shim()
                    driver.restore_original()
                    self._delete_snapshot()
                elif version == active:
                    # 卸载激活版，remaining 非空：切换到第一个可用剩余版本
                    switched = False
                    for fallback_ver in remaining:
                        try:
                            self.switch(fallback_ver)
                            switched = True
                            break
                        except Exception:
                            continue
                    if not switched:
                        driver.remove_shim()
                        driver.restore_original()
                        self._delete_snapshot()
                    else:
                        new_snap = self._load_snapshot()
                        new_snap["installed_versions"] = remaining
                        self._save_snapshot(new_snap)
                else:
                    # 卸载非激活版，remaining 非空：仅更新快照
                    snap["installed_versions"] = remaining
                    self._save_snapshot(snap)

            sp.step(t("software.step.cleanup"))
            sp.complete()
