"""Installation state shared by the stable launcher and installed OpsKit builds."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from uuid import uuid4

from core.directory_archive import unpack_package
from core.file_identity import same_file_identity
from core.paths import absolute_path
from core.resource_lock import ResourceBusyError, ResourceLock


ENTRY_ENV = "OPSKIT_DIRECTORY_ENTRY"
APP_NAME = "opskit.exe" if os.name == "nt" else "opskit"
PROBE_TIMEOUT = 20
_GENERATION = re.compile(r"g-[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class InstallError(ValueError):
    """A managed directory installation cannot be used or modified safely."""


@dataclass(frozen=True)
class Generation:
    name: str
    version: int
    tree_sha256: str


def _safe_path(path: Path, *, directory: bool) -> os.stat_result:
    info = absolute_path(path).lstat()
    valid_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not valid_type(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise InstallError("unsafe directory installation path")
    if not directory and info.st_nlink != 1:
        raise InstallError("unexpected hard link in directory installation")
    return info


def _trusted_path(path: Path, *, directory: bool, owner: int) -> None:
    info = _safe_path(path, directory=directory)
    if os.name != "nt" and (info.st_uid != owner or info.st_mode & 0o022):
        raise InstallError("directory installation is writable by another user")


def _state_generation(value) -> Generation | None:
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"name", "version", "tree_sha256"}
            or not isinstance(value["name"], str) or not _GENERATION.fullmatch(value["name"])
            or not isinstance(value["tree_sha256"], str) or not _DIGEST.fullmatch(value["tree_sha256"])
            or type(value["version"]) is not int or not 0 < value["version"] <= 999999999):
        raise InstallError("invalid directory installation generation")
    return Generation(**value)


@dataclass
class InstallState:
    active: Generation
    previous: Generation | None = None
    staged: Generation | None = None
    rejected: int | None = None

    @classmethod
    def read(cls, path: Path) -> InstallState:
        with path.open("rb") as source:
            raw = source.read(2049)
        if len(raw) > 2048:
            raise InstallError("directory installation state exceeds size limit")
        data = json.loads(raw)
        if (not isinstance(data, dict) or set(data) != {"schema", "active", "previous", "staged", "rejected"}
                or type(data["schema"]) is not int or data["schema"] != 1):
            raise InstallError("invalid directory installation state")
        active, previous, staged = (_state_generation(data[key]) for key in ("active", "previous", "staged"))
        rejected = data["rejected"]
        if (active is None or previous is not None and previous.version >= active.version
                or staged is not None and staged.version <= active.version
                or rejected is not None and (type(rejected) is not int or not 0 < rejected <= 999999999)
                or len({item.name for item in (active, previous, staged) if item})
                != sum(item is not None for item in (active, previous, staged))):
            raise InstallError("inconsistent directory installation state")
        return cls(active, previous, staged, rejected)

    def write(self, path: Path) -> None:
        from dataclasses import asdict

        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".state-")
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump({"schema": 1, **asdict(self)}, output, separators=(",", ":"))
                output.flush()
                if os.name != "nt":
                    os.fchmod(output.fileno(), 0o644)
                os.fsync(output.fileno())
            os.replace(temporary, path)
            _sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class Install:
    def __init__(self, entry: Path):
        self.entry = entry
        self.root = entry.with_name(f".{entry.name}.versions")
        self.state_path = self.root / "state.json"
        self.lock = ResourceLock(lambda: self.root / "locks" / "update.lock", label="directory update")

    def _owner(self) -> int:
        return _safe_path(self.root, directory=True).st_uid

    def state(self) -> InstallState:
        owner = self._owner()
        entry = _safe_path(self.entry, directory=False)
        if os.name != "nt" and (entry.st_uid != owner or entry.st_mode & 0o022):
            raise InstallError("OpsKit launcher and its installation have different owners")
        _trusted_path(self.entry.parent, directory=True, owner=owner)
        _trusted_path(self.root, directory=True, owner=owner)
        _trusted_path(self.state_path, directory=False, owner=owner)
        return InstallState.read(self.state_path)

    def tree(self, generation: Generation) -> Path:
        path = self.root / generation.name
        _trusted_path(path, directory=True, owner=self._owner())
        return path

    def executable(self, generation: Generation) -> Path:
        path = self.tree(generation) / APP_NAME
        _trusted_path(path, directory=False, owner=self._owner())
        return path

    def can_write(self) -> bool:
        return os.access(self.root, os.W_OK) and (os.name == "nt" or os.geteuid() == self._owner())

    def prepare(self, archive: Path, version: int, *, base: Path | None = None) -> Generation:
        name = "g-" + uuid4().hex
        path = self.root / name
        filesystem_path = absolute_path(path)
        filesystem_path.mkdir(mode=0o700)
        try:
            manifest = unpack_package(archive, path, version, base=base)
            generation = Generation(name, version, manifest.tree_sha256)
            self.executable(generation)
            if os.name != "nt":
                for directory, _subdirectories, _files in os.walk(path, topdown=False):
                    Path(directory).chmod(0o755)
                    _sync_directory(Path(directory))
            _sync_directory(path)
            _sync_directory(self.root)
        except BaseException:
            shutil.rmtree(filesystem_path)
            raise
        return generation

    def discard(self, generation: Generation) -> None:
        """Discard a candidate that has never been available to launcher sessions."""
        shutil.rmtree(absolute_path(self.tree(generation)))

    def env(self) -> dict[str, str]:
        env = {**os.environ, ENTRY_ENV: str(self.entry), "PYINSTALLER_RESET_ENVIRONMENT": "1"}
        if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
            if "LD_LIBRARY_PATH_ORIG" in env:
                env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
            else:
                env.pop("LD_LIBRARY_PATH", None)
        return env

    def healthy(self, generation: Generation) -> bool:
        try:
            reset_dll_directory()
            result = subprocess.run([str(self.executable(generation)), "--non-interactive", "--output", "json", "--version"],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    env=self.env(), timeout=PROBE_TIMEOUT, check=False)
            if result.returncode != 0:
                return False
            payload = json.loads(result.stdout)
            return isinstance(payload, dict) and payload.get("data") == {"version": generation.version}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False

    def revert(self, failed: Generation, good: Generation) -> None:
        """Persist a boot failure only if another updater has not changed the selection."""
        if not self.can_write():
            return
        try:
            with self.lock.hold():
                state = self.state()
                if state.active == failed and state.previous == good:
                    InstallState(good, staged=state.staged,
                                 rejected=max(state.rejected or 0, failed.version)).write(self.state_path)
        except (OSError, ResourceBusyError):
            # The previous generation can still run when another process is updating.
            pass


def managed_install() -> Install | None:
    """Identify a real frozen child by matching its executable to an installed generation."""
    value = os.environ.get(ENTRY_ENV)
    if not value or not getattr(sys, "frozen", False):
        return None
    entry = Path(value)
    if not entry.is_absolute() or ".." in entry.parts:
        return None
    install = Install(entry)
    runtime = Path(sys.executable).absolute()
    if (absolute_path(runtime.parent.parent) != absolute_path(install.root) or runtime.name != APP_NAME
            or not _GENERATION.fullmatch(runtime.parent.name)):
        return None
    owner = _safe_path(entry, directory=False).st_uid
    for path in (entry.parent, install.root, runtime.parent):
        _trusted_path(path, directory=True, owner=owner)
    _trusted_path(runtime, directory=False, owner=owner)
    return install


def reset_dll_directory() -> None:
    if os.name == "nt":
        import ctypes

        if not ctypes.windll.kernel32.SetDllDirectoryW(None):
            raise ctypes.WinError()


def bootstrap(source: Path, archive: Path, target: Path, version: int) -> None:
    """Install a verified seed from the platform installer without opening the menu."""
    if not all(path.is_absolute() and ".." not in path.parts for path in (source, target, archive)) or source == target:
        raise InstallError("invalid directory installer paths")
    install = Install(target)
    _trusted_path(target.parent, directory=True, owner=os.geteuid() if os.name != "nt" else 0)
    try:
        install.root.mkdir(mode=0o755)
    except FileExistsError:
        pass
    else:
        if os.name != "nt":
            install.root.chmod(0o755)
    _trusted_path(install.root, directory=True, owner=os.geteuid() if os.name != "nt" else 0)
    with install.lock.hold():
        try:
            target_before = _safe_path(target, directory=False)
        except FileNotFoundError:
            target_before = None

        try:
            previous_state = install.state()
        except FileNotFoundError:
            previous_state = None
        if previous_state and previous_state.active.version > version:
            raise InstallError("cannot install an older OpsKit version")
        generation = install.prepare(archive, version)
        staged_launcher = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            if not install.healthy(generation):
                raise InstallError("OpsKit did not pass the startup check")
            with source.open("rb") as original, staged_launcher.open("xb") as output:
                shutil.copyfileobj(original, output)
                output.flush()
                if os.name != "nt":
                    os.fchmod(output.fileno(), 0o755)
                os.fsync(output.fileno())
            # Keep the older slot on reinstall, even when repairing the same build.
            previous = None if previous_state is None else previous_state.active
            if previous and previous.version == version:
                previous = previous_state.previous
            try:
                target_after = _safe_path(target, directory=False)
            except FileNotFoundError:
                target_after = None
            if (target_before is None) != (target_after is None) or (
                    target_before is not None and not same_file_identity(target_before, target_after)):
                raise InstallError("OpsKit launcher changed during installation")
        except BaseException:
            install.discard(generation)
            staged_launcher.unlink(missing_ok=True)
            raise

        try:
            # The generation must survive any error after the pointer can be
            # visible, including failures synchronizing the parent directory.
            InstallState(generation, previous).write(install.state_path)
            try:
                os.replace(staged_launcher, target)
            except OSError:
                if previous_state is not None:
                    previous_state.write(install.state_path)
                else:
                    install.state_path.unlink()
                    _sync_directory(install.root)
                raise
            _sync_directory(target.parent)
        finally:
            staged_launcher.unlink(missing_ok=True)
