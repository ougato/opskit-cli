"""PostgreSQL Windows 平台驱动：zip 解压、shim cmd、注册表 PATH 注入（对齐 mongodb/windows.py）"""
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
    PGSQL_WIN_BIN_SUBDIR,
    WIN_PGSQL_PS_MARKER,
    WIN_PGSQL_PS_END,
    WIN_PGSQL_CMD_MARKER,
    WIN_PGSQL_CMD_END,
    CMD_AUTORUN_PGSQL_SUBPATH,
    SHIM_PGSQL_CMD_TEMPLATE,
)


class WindowsDriver(PlatformDriver):

    # ─── tarball（zip）安装 ───────────────────────────────────────────────────

    def install_tarball(self, version: str, tarball: Path) -> str:
        """
        将 zip 解压到版本专属目录：~/.opskit/postgresql/postgresql{version}/
        返回 bin 目录路径字符串。
        """
        from .common import pgsql_version_dir, pgsql_bin_dir
        dest = pgsql_version_dir(version)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(str(tarball), "r") as zf:
                for member in zf.infolist():
                    name = member.filename
                    # EDB zip 顶层目录名如 pgsql/，theseus-rs 为 postgresql-17.2.0-x86_64.../
                    parts = name.split("/", 1)
                    if len(parts) < 2 or not parts[1]:
                        continue
                    rel = parts[1]
                    target = dest / rel.replace("/", os.sep)
                    if name.endswith("/"):
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(target, "wb") as out:
                            shutil.copyfileobj(src, out)
        except Exception as e:
            raise InstallError(t("software.postgresql_error.win_extract_failed", version=version, error=e)) from e

        bin_dir = pgsql_bin_dir(version)
        if not (bin_dir / "psql.exe").exists():
            raise InstallError(t("software.postgresql_error.win_bad_structure", version=version))
        return str(bin_dir)

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        for name in ("psql.cmd", "pg_ctl.cmd", "pg_dump.cmd", "pg_restore.cmd"):
            (sdir / name).write_text(SHIM_PGSQL_CMD_TEMPLATE, encoding="utf-8")

    def remove_shim(self) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        for name in ("psql.cmd", "pg_ctl.cmd", "pg_dump.cmd", "pg_restore.cmd"):
            shim = sdir / name
            if shim.exists():
                shim.unlink(missing_ok=True)
        try:
            sdir.rmdir()
        except Exception:
            pass

    # ─── version link（junction active）──────────────────────────────────────

    def _active_junction(self) -> Path:
        from .common import active_link
        return active_link()

    def _set_junction(self, target_dir: str) -> bool:
        junction = self._active_junction()
        junction.parent.mkdir(parents=True, exist_ok=True)
        try:
            if os.path.lexists(str(junction)):
                subprocess.run(["cmd", "/c", "rmdir", str(junction)], capture_output=True)
            r = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), target_dir],
                capture_output=True,
            )
            return r.returncode == 0
        except Exception:
            return False

    def apply_version_link(self, bin_dir: str) -> None:
        version_dir = str(Path(bin_dir).parent)
        junction_ok = self._set_junction(version_dir)

        if junction_ok:
            active_bin = str(self._active_junction() / "bin")
            self._inject_path(active_bin)
        else:
            wbin = Path.home() / PGSQL_WIN_BIN_SUBDIR
            wbin.mkdir(parents=True, exist_ok=True)
            for exe_name in ("psql.exe", "pg_ctl.exe", "pg_dump.exe", "pg_restore.exe"):
                src = Path(bin_dir) / exe_name
                stem = exe_name.replace(".exe", "")
                if not src.exists():
                    continue
                dest = wbin / f"{stem}.cmd"
                try:
                    dest.write_text(f"@echo off\r\n\"{src}\" %*\r\n", encoding="utf-8")
                except Exception:
                    pass
            self._inject_path(str(wbin))

    def restore_original(self) -> None:
        junction = self._active_junction()
        try:
            if os.path.lexists(str(junction)):
                subprocess.run(["cmd", "/c", "rmdir", str(junction)], capture_output=True)
        except Exception:
            pass
        active_bin = str(junction / "bin")
        self._remove_path(active_bin)
        wbin = Path.home() / PGSQL_WIN_BIN_SUBDIR
        for name in ("psql.cmd", "pg_ctl.cmd", "pg_dump.cmd", "pg_restore.cmd"):
            try:
                (wbin / name).unlink(missing_ok=True)
            except Exception:
                pass
        try:
            wbin.rmdir()
        except Exception:
            pass
        self._remove_path(str(wbin))

    # ─── detect ───────────────────────────────────────────────────────────────

    def detect(self) -> str | None:
        import subprocess as _sp
        from .common import load_snapshot, pgsql_bin_dir
        snap = load_snapshot()
        active = snap.get("active_version")
        if active:
            psql_exe = pgsql_bin_dir(active) / "psql.exe"
            if psql_exe.exists():
                try:
                    r = _sp.run([str(psql_exe), "--version"], capture_output=True, text=True, timeout=5)
                    if r.returncode == 0:
                        for part in r.stdout.strip().split():
                            if part and part[0].isdigit():
                                return part.rstrip(",")
                except Exception:
                    pass
                return active
        psql_cmd = shutil.which("psql")
        if psql_cmd:
            try:
                r = _sp.run([psql_cmd, "--version"], capture_output=True, text=True, timeout=5)
                for part in r.stdout.strip().split():
                    if part and part[0].isdigit():
                        return part.rstrip(",")
            except Exception:
                pass
        return None

    # ─── PATH 注入（四层，对齐 mongodb/windows.py）───────────────────────────

    def _inject_path(self, path_to_add: str) -> None:
        import winreg
        import ctypes

        try:
            sys_key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                0, winreg.KEY_READ | winreg.KEY_SET_VALUE,
            )
            sys_val, sys_type = winreg.QueryValueEx(sys_key, "PATH")
            sys_parts = [p for p in sys_val.split(";") if p]
            if path_to_add not in sys_parts:
                winreg.SetValueEx(sys_key, "PATH", 0, sys_type,
                                  ";".join([path_to_add] + sys_parts))
            winreg.CloseKey(sys_key)
            ctypes.windll.user32.SendMessageTimeoutW(
                0xFFFF, 0x001A, 0, "Environment", 2, 5000, None
            )
        except Exception:
            pass

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
            if path_to_add not in parts:
                winreg.SetValueEx(key, "PATH", 0, reg_type,
                                  ";".join([path_to_add] + parts))
            winreg.CloseKey(key)
            ctypes.windll.user32.SendMessageTimeoutW(
                0xFFFF, 0x001A, 0, "Environment", 2, 5000, None
            )
        except Exception:
            pass

        ps_block = (
            f"\n{WIN_PGSQL_PS_MARKER}\n"
            f"$env:PATH = '{path_to_add}' + ';' + $env:PATH\n"
            f"{WIN_PGSQL_PS_END}\n"
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
                if WIN_PGSQL_PS_MARKER not in text:
                    ps_profile.write_text(text + ps_block, encoding="utf-8")
            except Exception:
                pass

        bat_path = str(Path.home() / CMD_AUTORUN_PGSQL_SUBPATH)
        bat_content = (
            f"@echo off\r\n"
            f"@{WIN_PGSQL_CMD_MARKER}\r\n"
            f"@set PATH={path_to_add};%PATH%\r\n"
            f"@{WIN_PGSQL_CMD_END}\r\n"
        )
        try:
            import winreg as _wr
            Path(bat_path).parent.mkdir(parents=True, exist_ok=True)
            Path(bat_path).write_text(bat_content, encoding="utf-8")
            cmd_key = _wr.CreateKeyEx(
                _wr.HKEY_CURRENT_USER,
                r"Software\Microsoft\Command Processor", 0,
                _wr.KEY_SET_VALUE,
            )
            _wr.SetValueEx(cmd_key, "AutoRun", 0, _wr.REG_SZ, bat_path)
            _wr.CloseKey(cmd_key)
        except Exception:
            pass

        cur_path = os.environ.get("PATH", "")
        if path_to_add not in cur_path.split(";"):
            os.environ["PATH"] = path_to_add + ";" + cur_path

    def _remove_path(self, path_to_remove: str) -> None:
        import winreg

        try:
            sys_key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                0, winreg.KEY_READ | winreg.KEY_SET_VALUE,
            )
            sys_val, sys_type = winreg.QueryValueEx(sys_key, "PATH")
            sys_parts = [p for p in sys_val.split(";") if p and p != path_to_remove]
            winreg.SetValueEx(sys_key, "PATH", 0, sys_type, ";".join(sys_parts))
            winreg.CloseKey(sys_key)
        except Exception:
            pass

        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Environment", 0,
                winreg.KEY_READ | winreg.KEY_SET_VALUE,
            )
            cur, reg_type = winreg.QueryValueEx(key, "PATH")
            parts = [p for p in cur.split(";") if p and p != path_to_remove]
            winreg.SetValueEx(key, "PATH", 0, reg_type, ";".join(parts))
            winreg.CloseKey(key)
        except Exception:
            pass

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
                    if line.strip() == WIN_PGSQL_PS_MARKER:
                        skip = True
                    if not skip:
                        out.append(line)
                    if line.strip() == WIN_PGSQL_PS_END:
                        skip = False
                ps_profile.write_text("".join(out), encoding="utf-8")
            except Exception:
                pass

        try:
            bat_path = str(Path.home() / CMD_AUTORUN_PGSQL_SUBPATH)
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
