"""Redis Windows 平台驱动：tporadowski/redis zip 解压 + shim cmd + 注册表 PATH 注入"""
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
    WIN_REDIS_PS_MARKER,
    WIN_REDIS_PS_END,
    WIN_REDIS_CMD_MARKER,
    WIN_REDIS_CMD_END,
    CMD_AUTORUN_REDIS_SUBPATH,
    SHIM_REDIS_CMD_TEMPLATE,
)


class WindowsDriver(PlatformDriver):

    # ─── 二进制安装（zip 解压）────────────────────────────────────────────────

    def install_binary(self, version: str, src: Path) -> str:
        """
        将 redis-windows/redis-windows zip 解压到版本专属目录：~/.opskit/redis/redis{version}/
        返回 bin 目录路径字符串（exe 位于 zip 内顶层子目录下，rglob 自动定位）。
        """
        from .common import redis_version_dir, redis_bin_dir
        dest = redis_version_dir(version)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(str(src), "r") as zf:
                for member in zf.infolist():
                    name = member.filename
                    parts = name.split("/", 1)
                    rel = parts[1] if len(parts) > 1 and parts[1] else parts[0]
                    target = dest / rel.replace("/", os.sep)
                    if name.endswith("/"):
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src_f, open(target, "wb") as out:
                            shutil.copyfileobj(src_f, out)
        except Exception as e:
            raise InstallError(t("software.redis_error.win_extract_failed", version=version, error=e)) from e

        bin_d = redis_bin_dir(version)
        bin_d.mkdir(parents=True, exist_ok=True)
        redis_exe = bin_d / "redis-server.exe"
        if not redis_exe.exists():
            for candidate in dest.rglob("redis-server.exe"):
                src_dir = candidate.parent
                # 将 exe 同级目录下所有文件（含 msys-*.dll 运行时）复制到 bin/
                for f in src_dir.iterdir():
                    if f.is_file():
                        shutil.copy2(str(f), str(bin_d / f.name))
                break
        if not redis_exe.exists():
            raise InstallError(t("software.redis_error.win_bad_structure", version=version))
        return str(bin_d)

    # ─── shim ─────────────────────────────────────────────────────────────────

    def install_shim(self, fallback_bin: str) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        for binary in ("redis-server", "redis-cli", "redis-sentinel", "redis-benchmark"):
            content = SHIM_REDIS_CMD_TEMPLATE.format(binary=binary)
            (sdir / f"{binary}.cmd").write_text(content, encoding="utf-8")

    def remove_shim(self) -> None:
        from .common import shim_dir
        sdir = shim_dir()
        for name in ("redis-server.cmd", "redis-cli.cmd", "redis-sentinel.cmd", "redis-benchmark.cmd"):
            shim = sdir / name
            if shim.exists():
                shim.unlink(missing_ok=True)
        try:
            sdir.rmdir()
        except Exception:
            pass

    def shim_active(self) -> bool:
        from .common import shim_dir
        shims = str(shim_dir())
        return any(p == shims for p in os.environ.get("PATH", "").split(";"))

    # ─── version link（junction active）──────────────────────────────────────

    def _active_junction(self) -> Path:
        from .common import redis_versions_dir
        return redis_versions_dir() / "active"

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
            wbin = Path.home() / ".opskit" / "bin"
            wbin.mkdir(parents=True, exist_ok=True)
            bin_path = Path(bin_dir)
            for stem in ("redis-server", "redis-cli", "redis-sentinel", "redis-benchmark"):
                src = bin_path / f"{stem}.exe"
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
        self._remove_path(str(junction / "bin"))
        wbin = Path.home() / ".opskit" / "bin"
        for name in ("redis-server.cmd", "redis-cli.cmd", "redis-sentinel.cmd", "redis-benchmark.cmd"):
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
        from .common import load_snapshot, redis_bin_dir
        snap = load_snapshot()
        active = snap.get("active_version")
        if active:
            redis_exe = redis_bin_dir(active) / "redis-server.exe"
            if redis_exe.exists():
                return active
        redis_cmd = shutil.which("redis-server")
        if redis_cmd:
            try:
                r = subprocess.run(
                    [redis_cmd, "--version"], capture_output=True, text=True, timeout=5
                )
                line = r.stdout.strip()
                for part in line.split():
                    if part.startswith("v="):
                        return part[2:]
                    p = part.lstrip("v")
                    if p and p[0].isdigit():
                        return p.rstrip(",")
            except Exception:
                pass
        return None

    # ─── PATH 注入（四层，对齐 mysql/windows.py）─────────────────────────────

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
                winreg.SetValueEx(sys_key, "PATH", 0, sys_type, ";".join([path_to_add] + sys_parts))
            winreg.CloseKey(sys_key)
            ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 2, 5000, None)
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
                winreg.SetValueEx(key, "PATH", 0, reg_type, ";".join([path_to_add] + parts))
            winreg.CloseKey(key)
            ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 2, 5000, None)
        except Exception:
            pass
        ps_block = (
            f"\n{WIN_REDIS_PS_MARKER}\n"
            f"$env:PATH = '{path_to_add}' + ';' + $env:PATH\n"
            f"{WIN_REDIS_PS_END}\n"
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
                if WIN_REDIS_PS_MARKER not in text:
                    ps_profile.write_text(text + ps_block, encoding="utf-8")
            except Exception:
                pass
        bat_path = str(Path.home() / CMD_AUTORUN_REDIS_SUBPATH)
        bat_content = (
            f"@echo off\r\n"
            f"@{WIN_REDIS_CMD_MARKER}\r\n"
            f"@set PATH={path_to_add};%PATH%\r\n"
            f"@{WIN_REDIS_CMD_END}\r\n"
        )
        try:
            import winreg as _wr
            Path(bat_path).parent.mkdir(parents=True, exist_ok=True)
            Path(bat_path).write_text(bat_content, encoding="utf-8")
            cmd_key = _wr.CreateKeyEx(
                _wr.HKEY_CURRENT_USER, r"Software\Microsoft\Command Processor",
                0, _wr.KEY_SET_VALUE,
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
                    if line.strip() == WIN_REDIS_PS_MARKER:
                        skip = True
                    if not skip:
                        out.append(line)
                    if line.strip() == WIN_REDIS_PS_END:
                        skip = False
                ps_profile.write_text("".join(out), encoding="utf-8")
            except Exception:
                pass
        try:
            bat_path = str(Path.home() / CMD_AUTORUN_REDIS_SUBPATH)
            Path(bat_path).unlink(missing_ok=True)
            cmd_key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Command Processor",
                0, winreg.KEY_SET_VALUE,
            )
            winreg.DeleteValue(cmd_key, "AutoRun")
            winreg.CloseKey(cmd_key)
        except Exception:
            pass
