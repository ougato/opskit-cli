"""Windows 平台驱动：zip 解压、shim cmd、注册表 PATH 注入"""
from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from software.base import InstallError
from core.i18n import t
from .driver import PlatformDriver
from .constants import (
    GOLANG_INSTALL_DIR_WINDOWS,
    WIN_GO_PS_MARKER,
    WIN_GO_PS_END,
    WIN_GO_CMD_MARKER,
    WIN_GO_CMD_END,
    CMD_AUTORUN_GO_SUBPATH,
    SHIM_GO_CMD_TEMPLATE,
    GOLANG_WIN_BIN_SUBDIR,
)


class WindowsDriver(PlatformDriver):

    # ─── tarball（zip）安装 ───────────────────────────────────────────────────

    def install_tarball(self, version: str, tarball: Path) -> str:
        """
        将 zip 解压到版本专属目录：~/.opskit/go/go{version}/
        返回 bin 目录路径字符串。
        """
        from .common import go_version_dir, go_bin_dir
        dest = go_version_dir(version)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(str(tarball), "r") as zf:
                for member in zf.infolist():
                    name = member.filename
                    if not name.startswith("go/"):
                        continue
                    rel = name[3:]
                    if not rel:
                        continue
                    target = dest / rel.replace("/", os.sep)
                    if name.endswith("/"):
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(target, "wb") as out:
                            shutil.copyfileobj(src, out)
        except Exception as e:
            raise InstallError(t("software.golang_error.extract_failed", version=version, error=e)) from e

        bin_dir = go_bin_dir(version)
        if not (bin_dir / "go.exe").exists():
            raise InstallError(t("software.golang_error.bad_structure", version=version, file="bin/go.exe"))
        return str(bin_dir)

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        for name in ("go.cmd", "gofmt.cmd"):
            (sdir / name).write_text(SHIM_GO_CMD_TEMPLATE, encoding="utf-8")
        # PATH 注入由 apply_version_link 的 junction 方案统一处理，
        # 此处只写快照读取类 shim，不向注册表重复注入 PATH

    def remove_shim(self) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        for name in ("go.cmd", "gofmt.cmd"):
            shim = sdir / name
            if shim.exists():
                shim.unlink(missing_ok=True)
        try:
            sdir.rmdir()
        except Exception:
            pass
        # shim 目录已不在 PATH 里，无需清除

    def shim_active(self) -> bool:
        from .common import shim_dir
        shims = str(shim_dir())
        return any(p == shims for p in os.environ.get("PATH", "").split(";"))

    # ─── version link ────────────────────────────────────────────────────────
    # 方案：directory junction ~\.opskit\go\active 始终指向激活版本目录。
    # PATH 注入 active\bin（一次性写入注册表），切换时只改 junction 目标。
    # 已打开的 PowerShell 无需重启，go version 立即无感知生效。

    def _win_bin_dir(self) -> Path:
        return Path.home() / GOLANG_WIN_BIN_SUBDIR

    def _active_junction(self) -> Path:
        from .common import go_versions_dir
        return go_versions_dir() / "active"

    def _set_junction(self, target_dir: str) -> bool:
        """创建或更新 directory junction，返回是否成功"""
        junction = self._active_junction()
        junction.parent.mkdir(parents=True, exist_ok=True)
        try:
            # os.path.lexists 检测 junction 自身是否存在（不解析目标，
            # 处理目标目录已删除但 junction 文件本身仍存在的情况）
            if os.path.lexists(str(junction)):
                subprocess.run(
                    ["cmd", "/c", "rmdir", str(junction)],
                    capture_output=True,
                )
            r = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), target_dir],
                capture_output=True,
            )
            return r.returncode == 0
        except Exception:
            return False

    def apply_version_link(self, bin_dir: str) -> None:
        """
        切换时更新 junction ~/.opskit/go/active -> 激活版本目录。
        PATH 里固定写 active/bin，junction 切换后已开的 PowerShell 立即感知，
        无需任何用户操作，完全无感知。
        """
        # bin_dir 格式：~/.opskit/go/go1.X.Y/bin，取上一级即版本目录
        version_dir = str(Path(bin_dir).parent)
        junction_ok = self._set_junction(version_dir)

        if junction_ok:
            # 把 junction/bin 注入注册表 PATH（一次性，永久有效）
            active_bin = str(self._active_junction() / "bin")
            self._inject_path(active_bin)
        else:
            # junction 失败（极少见，非管理员且策略禁止），fallback 到 .cmd wrapper
            wbin = self._win_bin_dir()
            wbin.mkdir(parents=True, exist_ok=True)
            bin_path = Path(bin_dir)
            for stem, exe in (("go", "go.exe"), ("gofmt", "gofmt.exe")):
                src = bin_path / exe
                if not src.exists():
                    continue
                dest = wbin / f"{stem}.cmd"
                try:
                    dest.write_text(
                        f"@echo off\r\n\"{src}\" %*\r\n",
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            self._inject_path(str(wbin))

    def restore_original(self) -> None:
        # 删除 junction（用 lexists 检测自身存在，不解析目标）
        junction = self._active_junction()
        try:
            if os.path.lexists(str(junction)):
                subprocess.run(
                    ["cmd", "/c", "rmdir", str(junction)],
                    capture_output=True,
                )
        except Exception:
            pass
        # 清理 active\bin 的注册表 PATH 注入
        active_bin = str(junction / "bin")
        self._remove_path(active_bin)
        # 清理 fallback .cmd wrapper
        wbin = self._win_bin_dir()
        for name in ("go.cmd", "gofmt.cmd"):
            try:
                (wbin / name).unlink(missing_ok=True)
            except Exception:
                pass
        try:
            wbin.rmdir()
        except Exception:
            pass
        self._remove_path(str(wbin))

    def snapshot_pre_install(self) -> dict:
        return {}

    # ─── detect ───────────────────────────────────────────────────────────────

    def detect_active(self) -> str | None:
        from .common import load_snapshot, go_bin_dir
        snap = load_snapshot()
        active = snap.get("active_version")
        if active:
            go_exe = go_bin_dir(active) / "go.exe"
            if go_exe.exists():
                return active
        go_cmd = shutil.which("go")
        if go_cmd:
            try:
                r = subprocess.run([go_cmd, "version"], capture_output=True, text=True, timeout=5)
                line = r.stdout.strip()
                if line.startswith("go version go"):
                    parts = line.split()
                    if len(parts) >= 3:
                        return parts[2][2:]
            except Exception:
                pass
        return None

    # ─── PATH 注入（四层）────────────────────────────────────────────────────

    def _inject_path(self, shims_path: str) -> None:
        import winreg
        import ctypes

        # 0. HKLM 系统级 PATH（管理员权限）
        try:
            sys_key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                0, winreg.KEY_READ | winreg.KEY_SET_VALUE,
            )
            sys_val, sys_type = winreg.QueryValueEx(sys_key, "PATH")
            sys_parts = [p for p in sys_val.split(";") if p]
            if shims_path not in sys_parts:
                winreg.SetValueEx(sys_key, "PATH", 0, sys_type,
                                  ";".join([shims_path] + sys_parts))
            winreg.CloseKey(sys_key)
            ctypes.windll.user32.SendMessageTimeoutW(
                0xFFFF, 0x001A, 0, "Environment", 2, 5000, None
            )
        except Exception:
            pass

        # 1. HKCU 用户级 PATH
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Environment", 0,
                winreg.KEY_READ | winreg.KEY_SET_VALUE,
            )
            try:
                cur, reg_type = winreg.QueryValueEx(key, "PATH")
            except FileNotFoundError:
                cur, reg_type = "", winreg.REG_EXPAND_SZ
            parts = [p for p in cur.split(";") if p]
            if shims_path not in parts:
                winreg.SetValueEx(key, "PATH", 0, reg_type,
                                  ";".join([shims_path] + parts))
            winreg.CloseKey(key)
            ctypes.windll.user32.SendMessageTimeoutW(
                0xFFFF, 0x001A, 0, "Environment", 2, 5000, None
            )
        except Exception:
            pass

        # 2. PowerShell Profile
        ps_block = (
            f"\n{WIN_GO_PS_MARKER}\n"
            f"$env:PATH = '{shims_path}' + ';' + $env:PATH\n"
            f"{WIN_GO_PS_END}\n"
        )
        docs = Path.home() / "Documents"
        for ps_profile in (
            docs / "PowerShell" / "profile.ps1",
            docs / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1",
            docs / "WindowsPowerShell" / "profile.ps1",
        ):
            try:
                ps_profile.parent.mkdir(parents=True, exist_ok=True)
                text = ps_profile.read_text(encoding="utf-8") if ps_profile.exists() else ""
                if WIN_GO_PS_MARKER not in text:
                    ps_profile.write_text(text + ps_block, encoding="utf-8")
            except Exception:
                pass

        # 3. cmd AutoRun
        bat_path = str(Path.home() / CMD_AUTORUN_GO_SUBPATH)
        bat_content = (
            f"@echo off\r\n"
            f"@{WIN_GO_CMD_MARKER}\r\n"
            f"@set PATH={shims_path};%PATH%\r\n"
            f"@{WIN_GO_CMD_END}\r\n"
        )
        try:
            Path(bat_path).parent.mkdir(parents=True, exist_ok=True)
            Path(bat_path).write_text(bat_content, encoding="utf-8")
            cmd_key = winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Command Processor", 0,
                winreg.KEY_SET_VALUE,
            )
            winreg.SetValueEx(cmd_key, "AutoRun", 0, winreg.REG_SZ, bat_path)
            winreg.CloseKey(cmd_key)
        except Exception:
            pass

        # 4. 当前进程 PATH
        cur_path = os.environ.get("PATH", "")
        if shims_path not in cur_path.split(";"):
            os.environ["PATH"] = shims_path + ";" + cur_path

    def _remove_path(self, shims_path: str) -> None:
        import winreg

        # 0. HKLM
        try:
            sys_key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                0, winreg.KEY_READ | winreg.KEY_SET_VALUE,
            )
            sys_val, sys_type = winreg.QueryValueEx(sys_key, "PATH")
            sys_parts = [p for p in sys_val.split(";") if p and p != shims_path]
            winreg.SetValueEx(sys_key, "PATH", 0, sys_type, ";".join(sys_parts))
            winreg.CloseKey(sys_key)
        except Exception:
            pass

        # 1. HKCU
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Environment", 0,
                winreg.KEY_READ | winreg.KEY_SET_VALUE,
            )
            cur, reg_type = winreg.QueryValueEx(key, "PATH")
            parts = [p for p in cur.split(";") if p and p != shims_path]
            winreg.SetValueEx(key, "PATH", 0, reg_type, ";".join(parts))
            winreg.CloseKey(key)
        except Exception:
            pass

        # 2. PowerShell Profile
        docs = Path.home() / "Documents"
        for ps_profile in (
            docs / "PowerShell" / "profile.ps1",
            docs / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1",
            docs / "WindowsPowerShell" / "profile.ps1",
        ):
            if not ps_profile.exists():
                continue
            try:
                lines = ps_profile.read_text(encoding="utf-8").splitlines(keepends=True)
                out, skip = [], False
                for line in lines:
                    if line.strip() == WIN_GO_PS_MARKER:
                        skip = True
                    if not skip:
                        out.append(line)
                    if line.strip() == WIN_GO_PS_END:
                        skip = False
                ps_profile.write_text("".join(out), encoding="utf-8")
            except Exception:
                pass

        # 3. cmd AutoRun
        try:
            bat_path = str(Path.home() / CMD_AUTORUN_GO_SUBPATH)
            Path(bat_path).unlink(missing_ok=True)
            cmd_key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Command Processor", 0,
                winreg.KEY_SET_VALUE,
            )
            winreg.DeleteValue(cmd_key, "AutoRun")
            winreg.CloseKey(cmd_key)
        except Exception:
            pass
