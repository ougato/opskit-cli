"""Shared uv bootstrap from fixed archives, never downloaded shell scripts."""
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import zipfile

from core.archive import extract_tar, extract_zip
from core.archive_reader import open_tar, open_zip
from core.deadline import Deadline
from software.base import InstallError
from .uv_release import UV_RELEASE, UV_ASSET_SHA256


def asset_name(info):
    arch = {"x86_64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64",
            "x86": "i686", "armv7": "armv7", "armv6": "arm"}.get(info.arch)
    if info.os_type == "windows" and arch in {"x86_64", "aarch64", "i686"}:
        name = f"uv-{arch}-pc-windows-msvc.zip"
    elif info.os_type == "darwin" and arch in {"x86_64", "aarch64"}:
        name = f"uv-{arch}-apple-darwin.tar.gz"
    elif info.os_type == "linux" and arch:
        suffix = "musleabihf" if arch in {"arm", "armv7"} else "musl"
        name = f"uv-{arch}-unknown-linux-{suffix}.tar.gz"
    else:
        raise InstallError("uv archive is unavailable for this platform/architecture")
    if name not in UV_ASSET_SHA256:
        raise InstallError("uv archive has no reviewed integrity pin")
    return name


def ensure_uv_archive():
    from core.platform import get_platform
    from core.process import capture
    from .common import uv_bin_path
    from .constants import UV_INSTALL_TIMEOUT
    destination = uv_bin_path()
    for node in destination.parents:
        try:
            info = node.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_reparse_tag", 0):
            raise InstallError("Private uv directory must not be a link")
    if os.path.lexists(destination):
        info = destination.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or getattr(info, "st_reparse_tag", 0) or not os.access(destination, os.X_OK)):
            raise InstallError("Unsafe or unusable private uv path; refusing to overwrite")
        return str(destination)
    platform_info = get_platform()
    name = asset_name(platform_info)
    windows = platform_info.os_type == "windows"
    curl = (shutil.which("curl.exe") if windows else None) or shutil.which("curl")
    if not curl:
        existing = shutil.which("uv")
        if existing:
            return existing
        raise InstallError("Safe uv archive bootstrap requires curl or an existing uv")
    deadline = Deadline(UV_INSTALL_TIMEOUT, "uv bootstrap")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=".opskit-uv-", dir=destination.parent) as work:
            archive_path = Path(work) / ("uv.zip" if windows else "uv.tar.gz")
            budget = deadline.remaining()
            result = capture(
                [curl, "--fail", "--location", "--silent", "--show-error",
                 "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "10",
                 "--max-time", str(budget), "--max-filesize", str(64 * 1024**2),
                 "--output", str(archive_path),
                 f"https://github.com/astral-sh/uv/releases/download/{UV_RELEASE}/{name}"],
                timeout=budget, max_bytes=16384, stdin=subprocess.DEVNULL,
            )
            deadline.remaining()
            if result.returncode or archive_path.stat().st_size > 64 * 1024**2:
                raise InstallError("uv archive download failed")
            digest = hashlib.sha256()
            with archive_path.open("rb") as source:
                for chunk in iter(lambda: source.read(65536), b""):
                    deadline.remaining()
                    digest.update(chunk)
            if digest.hexdigest() != UV_ASSET_SHA256[name]:
                raise InstallError("uv archive integrity check failed")
            payload = Path(work) / "payload"
            if windows:
                with open_zip(archive_path) as archive:
                    if len(archive.infolist()) > 32 or sum(member.file_size for member in archive.infolist()) > 128 * 1024**2:
                        raise InstallError("uv archive exceeds extraction limits")
                    extract_zip(archive, payload)
                candidates = list(payload.rglob("uv.exe"))
                if len(candidates) != 1:
                    raise InstallError("uv archive is missing its unique executable")
                binary = candidates[0]
            else:
                with open_tar(archive_path, max_files=32, max_payload_bytes=128 * 1024**2) as archive:
                    extract_tar(archive, payload, strip_components=1)
                binary = payload / "uv"
            deadline.remaining()
            if not binary.is_file() or binary.is_symlink():
                raise InstallError("uv archive is missing its regular executable")
            if windows:
                # Windows rename cannot overwrite a competing destination.
                if os.path.lexists(destination):
                    raise InstallError("Private uv path appeared during installation; refusing to overwrite")
                binary.rename(destination)
            else:
                binary.chmod(0o755)
                # Exclusive same-filesystem creation; cleanup drops the temp link.
                os.link(binary, destination, follow_symlinks=False)
        return str(destination)
    except InstallError:
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        raise InstallError("uv archive bootstrap failed; existing files preserved") from error
