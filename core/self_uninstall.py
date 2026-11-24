"""Uninstall the active standalone OpsKit installation."""
from contextlib import nullcontext
import errno
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass

from core import paths, updater, update_target_state
from core.directory_install import managed_install
from core.i18n import t
from core.resource_lock import ResourceLock


_PATH_MARKER = ".opskit-user-path"
_DATA_ENTRIES = (
    "config", "plugins", "plugin-data", "cache", "logs", "data", "backups",
    "native-plugin-runtimes", "native-fetch-tools", "native-fetch-staging",
    "plugin-runtimes", "plugin-artifact-locks", "plugin-capabilities.yaml",
    "plugin_trust.yaml", ".plugin-trust.lock", ".config.lock",
)


@dataclass(frozen=True)
class Plan:
    executable: Path
    executable_identity: tuple[int, int, int, int]
    mode: str
    data: tuple[Path, ...]
    profiles: tuple[Path, ...]
    path_marker: Path | None
    retained_runtime: Path | None = None


def _ordinary(path):
    """Reject links, reparse points and special files at owned path boundaries."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(t("self_uninstall.unsafe"))
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            return
        if (getattr(info, "st_file_attributes", 0) & 0x400
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                or (stat.S_ISREG(info.st_mode) and (part != path or info.st_nlink != 1))):
            raise ValueError(t("self_uninstall.unsafe"))
    return info


def _windows_install_dir():
    from software._shared.windows_account import local_app_data
    return local_app_data() / "opskit"


def _profile_replacement(profile, bin_dir):
    # The POSIX installer owns only its adjacent comment and exact export.
    lines = profile.read_bytes().splitlines(keepends=True)
    marker = b"# OpsKit"
    export = os.fsencode(f'export PATH="{bin_dir}:$PATH"')
    for index in range(len(lines) - 1):
        if (lines[index].rstrip(b"\r\n") == marker
                and lines[index + 1].rstrip(b"\r\n") == export):
            start = index - 1 if index and lines[index - 1] in {b"\n", b"\r\n"} else index
            return b"".join(lines[:start] + lines[index + 2:])
    return None


def plan(mode):
    if not paths._is_frozen():
        raise ValueError(t("self_uninstall.external"))
    managed = managed_install()
    target = managed.entry if managed else paths.installed_executable()
    if sys.platform == "win32":
        allowed = (_windows_install_dir() / "opskit.exe",)
    else:
        allowed = (Path.home() / ".local/bin/opskit", Path("/usr/local/bin/opskit"))
    info = _ordinary(target) if target in allowed else None
    if info is None or not stat.S_ISREG(info.st_mode):
        raise ValueError(t("self_uninstall.external"))
    if target == Path("/usr/local/bin/opskit") and not paths._is_root():
        raise ValueError(t("self_uninstall.system"))

    data = []
    if mode == "full":
        if any(os.environ.get(key) for key in ("OPSKIT_DATA_DIR", "OPSKIT_PLUGINS_DIR")):
            raise ValueError(t("self_uninstall.override"))
        root = target.parent if sys.platform == "win32" else paths.data_dir()
        locations = [root / name for name in _DATA_ENTRIES]
        if sys.platform != "win32":
            locations.extend((paths.cache_dir(), paths.log_dir()))
        for path in dict.fromkeys(locations):
            if _ordinary(path):
                data.append(path)

    profiles = []
    marker = None
    if sys.platform == "win32":
        candidate = target.parent / _PATH_MARKER
        if _ordinary(candidate):
            if candidate.read_bytes() != b"1\r\n":
                raise ValueError(t("self_uninstall.unsafe"))
            marker = candidate
    else:
        for name in (".bashrc", ".zshrc"):
            profile = Path.home() / name
            if profile.is_symlink():
                continue
            if _ordinary(profile) and _profile_replacement(profile, target.parent) is not None:
                profiles.append(profile)
    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    retained_runtime = managed.root if managed else None
    return Plan(target, identity, mode, tuple(data), tuple(profiles), marker, retained_runtime)


def _remove(path):
    info = _ordinary(path)
    if info is None:
        return
    if stat.S_ISDIR(info.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def _edit_profile(profile, directory):
    info = _ordinary(profile)
    if info is None:
        return
    updated = _profile_replacement(profile, directory)
    if updated is None:
        return
    fd, name = tempfile.mkstemp(prefix=".opskit-profile-", dir=profile.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(updated)
        temp.chmod(stat.S_IMODE(info.st_mode))
        current = profile.lstat()
        if not os.path.samestat(info, current) or info.st_mtime_ns != current.st_mtime_ns:
            raise ValueError(t("self_uninstall.unsafe"))
        temp.replace(profile)
    finally:
        temp.unlink(missing_ok=True)


def _remove_empty(path):
    try:
        path.rmdir()
    except OSError as error:
        if error.errno not in {errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST}:
            raise


def execute(request):
    target = request.executable
    managed = managed_install()
    managed_lock = managed.lock.hold() if managed else nullcontext()
    target_lock_path = updater._update_target_lock_path(target)
    target_lock = (ResourceLock(lambda: target_lock_path, label="update executable")
                   if managed else updater._update_target_resource)
    with managed_lock:
        with target_lock.hold():
            if request != plan(request.mode):
                raise ValueError(t("self_uninstall.changed"))
            state = update_target_state.read(target)
            if state and state["phase"] == "prepared":
                raise ValueError(t("self_uninstall.update_pending"))
            if managed:
                managed.state()
            for path in request.data:
                _remove(path)
            for profile in request.profiles:
                _edit_profile(profile, target.parent)
            state_path = update_target_state.path(target)
            if sys.platform == "win32":
                from core.file_digest import file_digest
                from core.self_uninstall_windows import schedule
                return schedule(request, file_digest(target), state_path, managed=managed)
            target.unlink()
            state_path.unlink(missing_ok=True)
        # Once the executable is gone, the updater no longer has a target.
        if not target.exists():
            target_lock_path.unlink(missing_ok=True)
            _remove_empty(target_lock_path.parent)
        if request.mode == "full":
            _remove_empty(paths.data_dir())
    return None


def run(*, purge, dry_run):
    from core.cli import emit, fail, non_interactive, output_mode
    from core.prompt import confirm, is_auto_yes, select
    from core.theme import get_icon, print_info

    mode = "full" if purge else "program"
    title = ["OpsKit", t("self_uninstall.title")]
    if not dry_run and not is_auto_yes():
        if non_interactive.get() or not sys.stdin.isatty():
            fail(t("self_uninstall.yes_required"), 2)
        if not purge:
            # Validate the execution origin before offering any destructive choices.
            plan("program")
            choice = select(title, "", [
                {"key": "1", "label": get_icon("software") + " " + t("self_uninstall.program")},
                {"key": "2", "label": get_icon("uninstall_full") + " " + t("self_uninstall.full")},
            ], back_label=t("menu.back"))
            if choice not in {"1", "2"}:
                return
            mode = "full" if choice == "2" else "program"
    request = plan(mode)
    path_changes = [str(path) for path in request.profiles]
    if request.path_marker:
        path_changes.append(str(request.executable.parent))
    detail = [str(request.executable), *map(str, request.data), *path_changes]
    description = t("self_uninstall.keep_software" if mode == "full" else "self_uninstall.keep_data")
    result = {
        "mode": mode, "executable": str(request.executable),
        "data": list(map(str, request.data)), "path_changes": path_changes,
    }
    detail.append(description)
    notice = None
    if request.retained_runtime:
        notice = t("self_uninstall.retained_runtime", path=request.retained_runtime)
        result["retained_runtime"] = str(request.retained_runtime)
        detail.append(notice)
    if dry_run:
        if output_mode.get() == "json":
            emit({**result, "status": "preview"})
        else:
            for line in detail:
                print_info(line)
        return
    if not is_auto_yes() and not confirm(title, t("self_uninstall." + mode), info_lines=detail):
        return
    failure = execute(request)
    if output_mode.get() == "json":
        emit({**result, "status": "scheduled" if failure else "removed",
              "failure_log": str(failure) if failure else None})
    elif failure:
        print_info(t("self_uninstall.scheduled", path=failure))
    else:
        print_info(t("self_uninstall.removed"))
    if notice and output_mode.get() != "json":
        print_info(notice)
