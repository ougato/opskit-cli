"""Windows 平台驱动：.cmd shim、HKLM/HKCU PATH 注入、PS Profile、cmd AutoRun"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from software.base import InstallError
from .driver import PlatformDriver
from .constants import (
    UV_INSTALL_PS1,
    UV_INSTALL_PS1_GHPROXY,
    UV_WIN_ZIP_URL,
    UV_WIN_ZIP_GHPROXY,
    CMD_AUTORUN_SUBPATH,
    WIN_SHIM_PS_MARKER,
    WIN_SHIM_PS_END,
    WIN_SHIM_CMD_MARKER,
    WIN_SHIM_CMD_END,
    SHIM_CMD_TEMPLATE,
)


class WindowsDriver(PlatformDriver):

    # ─── uv ──────────────────────────────────────────────────────────────────

    def ensure_uv(self) -> str:
        from .constants import UV_INSTALL_TIMEOUT
        from .common import uv_bin_path

        # 优先用 OpsKit 自管的私有 uv（版本新、元数据最新）；系统 PATH 上可能存在
        # 陈旧的 uv，其内置 python-build-standalone 清单缺新版本，会导致
        # "No download found"，故仅作为最后兜底。
        private_uv = uv_bin_path()
        if private_uv.exists() and os.access(str(private_uv), os.X_OK):
            return str(private_uv)

        private_uv.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["UV_INSTALL_DIR"] = str(private_uv.parent)

        ps_cmd = (
            shutil.which("powershell.exe") or shutil.which("powershell")
            or shutil.which("pwsh.exe") or shutil.which("pwsh")
        )
        if ps_cmd:
            for ps1_url in (UV_INSTALL_PS1_GHPROXY, UV_INSTALL_PS1):
                try:
                    subprocess.run(
                        [ps_cmd, "-ExecutionPolicy", "Bypass", "-Command",
                         f"irm '{ps1_url}' | iex"],
                        env=env, check=True, timeout=UV_INSTALL_TIMEOUT,
                        capture_output=True,
                    )
                    if private_uv.exists():
                        return str(private_uv)
                except Exception:
                    continue

        curl_bin = shutil.which("curl.exe") or shutil.which("curl")
        if curl_bin:
            import tempfile, zipfile
            for dl_url in (UV_WIN_ZIP_GHPROXY, UV_WIN_ZIP_URL):
                try:
                    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as zf:
                        zip_path = zf.name
                    subprocess.run(
                        [curl_bin, "-L", "-o", zip_path, dl_url],
                        check=True, timeout=UV_INSTALL_TIMEOUT, capture_output=True,
                    )
                    with zipfile.ZipFile(zip_path) as z:
                        for member in z.namelist():
                            if member.endswith("uv.exe"):
                                z.extract(member, path=str(private_uv.parent))
                                extracted = private_uv.parent / member
                                if extracted != private_uv:
                                    extracted.rename(private_uv)
                                break
                    os.unlink(zip_path)
                    if private_uv.exists():
                        return str(private_uv)
                except Exception:
                    continue

        sys_uv = shutil.which("uv")
        if sys_uv:
            return sys_uv

        from core.i18n import t
        raise InstallError(t("software.python_error.uv_win_failed"))

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        for name in ("python.cmd", "python3.cmd"):
            (sdir / name).write_text(SHIM_CMD_TEMPLATE, encoding="utf-8")
        self._inject_path(str(sdir))

    def remove_shim(self) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        for name in ("python.cmd", "python3.cmd"):
            shim = sdir / name
            if shim.exists():
                shim.unlink(missing_ok=True)
        try:
            sdir.rmdir()
        except Exception:
            pass
        self._remove_path(str(sdir))

    def shim_active(self) -> bool:
        from .common import shim_dir
        shims = str(shim_dir())
        return any(p == shims for p in os.environ.get("PATH", "").split(";"))

    # ─── version link（Windows 无 symlink，shim 已路由，空实现）─────────────────

    def apply_version_link(self, new_bin: str) -> None:
        pass

    def restore_original(
        self,
        symlink_path: str,
        original_target: str | None,
        had_local_bin_path: bool,
    ) -> None:
        pass

    def snapshot_pre_install(self) -> dict:
        return {}

    # ─── detect ───────────────────────────────────────────────────────────────

    def detect_active(self) -> str | None:
        from .common import load_snapshot
        snap = load_snapshot()
        active = snap.get("active_version")
        if active:
            uv_bin = snap.get("uv_python_path", "")
            if uv_bin and Path(uv_bin).exists():
                return active
        py = shutil.which("python")
        if py:
            try:
                r = subprocess.run([py, "--version"], capture_output=True, text=True, timeout=5)
                line = r.stdout.strip() or r.stderr.strip()
                if "Python" in line:
                    return line.split()[-1]
            except Exception:
                pass
        return None

    # ─── PATH 注入（四层）────────────────────────────────────────────────────

    def _inject_path(self, shims_path: str) -> None:
        import winreg
        import ctypes

        # 0. HKLM 系统级 PATH（管理员权限，最彻底）
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
            f"\n{WIN_SHIM_PS_MARKER}\n"
            f"$env:PATH = '{shims_path}' + ';' + $env:PATH\n"
            f"{WIN_SHIM_PS_END}\n"
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
                if WIN_SHIM_PS_MARKER not in text:
                    ps_profile.write_text(text + ps_block, encoding="utf-8")
            except Exception:
                pass

        # 3. cmd AutoRun
        bat_path = str(Path.home() / CMD_AUTORUN_SUBPATH)
        bat_content = (
            f"@echo off\r\n"
            f"@{WIN_SHIM_CMD_MARKER}\r\n"
            f"@set PATH={shims_path};%PATH%\r\n"
            f"@{WIN_SHIM_CMD_END}\r\n"
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
                    if line.strip() == WIN_SHIM_PS_MARKER:
                        skip = True
                    if not skip:
                        out.append(line)
                    if line.strip() == WIN_SHIM_PS_END:
                        skip = False
                ps_profile.write_text("".join(out), encoding="utf-8")
            except Exception:
                pass

        # 3. cmd AutoRun
        try:
            bat_path = str(Path.home() / CMD_AUTORUN_SUBPATH)
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
