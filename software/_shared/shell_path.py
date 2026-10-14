"""Linux/macOS shell PATH 注入与系统 symlink 公共逻辑。

各 recipe 的 ``LinuxDriver`` 在 ``install_shim`` / ``remove_shim`` /
``shim_active`` / ``apply_version_link`` / ``restore_original`` 中存在近乎一致
的样板：

- 把 shim 目录注入当前进程 ``PATH``（让 opskit 内部子进程立即可用）；
- 在 ``~/.bashrc`` / ``~/.zshrc`` / ``~/.profile`` / ``~/.bash_profile`` 之间用
  marker 块写入或移除 ``export PATH``；
- root 下写入 / 删除 ``/etc/profile.d`` 文件；
- root 下在 ``/usr/local/bin`` 创建 / 删除指向受管目录的 symlink。

差异仅为 marker 字符串、shim 名集合、profile.d 路径、受管根目录，收敛为函数参数。
"""
from __future__ import annotations

import os
import shlex
import stat
import tempfile
import uuid
from collections.abc import Iterable
from pathlib import Path

from software._shared.mutation import system_mutation

_RC_FILES = (".bashrc", ".zshrc", ".profile", ".bash_profile")
SYSTEM_BIN_DIR = "/usr/local/bin"


def _profile_text(path):
    for node in (path, *path.parents):
        if node.is_symlink() or (hasattr(node, "is_junction") and node.is_junction()):
            raise ValueError("shell profile path must not be a link")
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024 * 1024:
        raise ValueError("shell profile must be a bounded regular file")
    with path.open("rb") as source:
        opened = os.fstat(source.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("shell profile identity changed")
        body = source.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024:
        raise ValueError("shell profile exceeds limit")
    return body.decode("utf-8")


def _export_path(directory):
    # Shell quoting cannot escape PATH's colon delimiter inside an entry.
    if not isinstance(directory, str) or not os.path.isabs(directory) or any(c in directory for c in "\x00\r\n"):
        raise ValueError("invalid shim directory")
    if ":" in os.path.splitdrive(directory)[1]:
        raise ValueError("invalid shim directory")
    return 'export PATH=' + shlex.quote(directory) + ':"$PATH"\n'


def _validate_markers(begin, end):
    if any(not isinstance(value, str) or not value.startswith("#") or any(c in value for c in "\x00\r\n")
           for value in (begin, end)) or begin == end:
        raise ValueError("invalid shell profile markers")


def _without_block(text, begin, end):
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == begin]
    ends = [i for i, line in enumerate(lines) if line.strip() == end]
    if not starts and not ends:
        return text
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError("ambiguous shell profile block")
    return "".join(lines[:starts[0]] + lines[ends[0] + 1:])


def _owned_profile(text, begin, end):
    lines = text.splitlines()
    if len(lines) != 3 or lines[0] != begin or lines[2] != end:
        return False
    try:
        words = shlex.split(lines[1])
        if len(words) != 2 or words[0] != "export" or not words[1].startswith("PATH=") or not words[1].endswith(":$PATH"):
            return False
        return lines[1] + "\n" == _export_path(words[1][5:-6])
    except ValueError:
        return False


def _publish_profile(path, text, *, private=False, new_file_mode=0o644, mode_override=None):
    if mode_override is not None and (type(mode_override) is not int or not 0 <= mode_override <= 0o777):
        raise ValueError("invalid file publication mode")
    mode = (mode_override if mode_override is not None else
            stat.S_IMODE(path.stat().st_mode) if path.exists() else new_file_mode)
    descriptor, name = tempfile.mkstemp(prefix=".opskit-profile-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            if private:
                from core.secrets import restrict_permissions
                restrict_permissions(temporary)
            else:
                temporary.chmod(mode)
                if os.name == "nt" and path.exists():
                    from core.windows_permissions import copy_dacl
                    copy_dacl(path, temporary)
            output.write(text.encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _apply_profiles(changes, *, publish=None):
    publish = _publish_profile if publish is None else publish
    attempted = []
    try:
        for path, previous, replacement in changes:
            if _profile_text(path) != previous:
                raise ValueError("shell profile changed before publication")
            attempted.append((path, previous, replacement))
            if replacement is None:
                path.unlink()
            else:
                publish(path, replacement)
    except BaseException:
        incomplete = False
        for path, previous, replacement in reversed(attempted):
            try:
                current = _profile_text(path)
                if current == previous:
                    continue
                if current != replacement:
                    raise ValueError("shell profile changed during rollback")
                if previous is None:
                    path.unlink()
                else:
                    publish(path, previous)
            except Exception:
                incomplete = True
        if incomplete:
            from software.base import InstallError
            raise InstallError("shell profile operation failed; rollback incomplete") from None
        raise


def _is_root() -> bool:
    return hasattr(os, "getuid") and os.getuid() == 0


def prepend_process_path(directory: str) -> None:
    """将目录移到当前进程 ``PATH`` 最前，移除同一路径的重复项。"""
    cur_path = os.environ.get("PATH", "")
    parts = [p for p in cur_path.split(os.pathsep) if p != directory]
    os.environ["PATH"] = os.pathsep.join([directory, *parts])


def process_path_contains(directory: str) -> bool:
    """当前进程 ``PATH`` 是否包含该目录。"""
    return any(p == directory for p in os.environ.get("PATH", "").split(os.pathsep))


@system_mutation
def inject_rc_path(shims_path: str, marker_begin: str, marker_end: str,
                   profile_d_file: str | None = None) -> None:
    """Publish owned PATH blocks; preserve administrator files and compensate failures."""
    _validate_markers(marker_begin, marker_end)
    export = _export_path(shims_path)
    block = marker_begin + "\n" + export + marker_end + "\n"
    changes = []
    for name in _RC_FILES:
        path = Path.home() / name
        previous = _profile_text(path)
        if previous is not None:
            retained = _without_block(previous, marker_begin, marker_end)
            replacement = retained + ("" if not retained or retained.endswith("\n") else "\n") + block
            if replacement != previous:
                changes.append((path, previous, replacement))
    if profile_d_file and _is_root():
        path = Path(profile_d_file)
        previous = _profile_text(path)
        legacy = 'export PATH="' + shims_path + ':$PATH"\n'
        safe_legacy = not any(c in shims_path for c in '$`"') and previous == legacy
        if previous is not None and not _owned_profile(previous, marker_begin, marker_end) and not safe_legacy:
            raise FileExistsError("refusing to overwrite an unmanaged system profile")
        if previous != block:
            changes.append((path, previous, block))
    _apply_profiles(changes)


@system_mutation
def remove_rc_path(marker_begin: str, marker_end: str,
                   profile_d_file: str | None = None) -> None:
    """Remove only owned blocks, propagating errors and restoring prior contents."""
    _validate_markers(marker_begin, marker_end)
    changes = []
    for name in _RC_FILES:
        path = Path.home() / name
        previous = _profile_text(path)
        if previous is not None:
            replacement = _without_block(previous, marker_begin, marker_end)
            if replacement != previous:
                changes.append((path, previous, replacement))
    if profile_d_file and _is_root():
        path = Path(profile_d_file)
        previous = _profile_text(path)
        if previous is not None:
            if not _owned_profile(previous, marker_begin, marker_end):
                raise FileExistsError("refusing to delete an unmanaged system profile")
            changes.append((path, previous, None))
    _apply_profiles(changes)


def _command_link(path):
    if path.is_symlink():
        return path.readlink()
    if path.exists():
        raise FileExistsError("refusing to overwrite an unmanaged command file")
    return None


def _publish_command_link(dest, target):
    temporary = dest.with_name(".opskit-link-" + uuid.uuid4().hex)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, dest)
    finally:
        temporary.unlink(missing_ok=True)


def _command_target(dest, target):
    # Windows readlink preserves the extended path prefix; normalize only the
    # comparison path, retaining the original target for exact restoration.
    value = str(target)
    if os.name == "nt" and value.startswith("\\\\?\\"):
        value = "\\\\" + value[8:] if value.startswith("\\\\?\\UNC\\") else value[4:]
    return (dest.parent / value).resolve()


def _apply_command_links(changes):
    attempted = []
    try:
        for dest, previous, replacement in changes:
            if _command_link(dest) != previous:
                raise ValueError("command link changed before publication")
            attempted.append((dest, previous, replacement))
            if replacement is None:
                dest.unlink()
            else:
                _publish_command_link(dest, replacement)
    except BaseException:
        incomplete = False
        for dest, previous, replacement in reversed(attempted):
            try:
                current = _command_link(dest)
                if current == previous:
                    continue
                if replacement is None:
                    if current is not None:
                        raise ValueError("command link changed during rollback")
                elif current is None or dest.resolve() != replacement.resolve():
                    raise ValueError("command link changed during rollback")
                if previous is None:
                    dest.unlink()
                else:
                    _publish_command_link(dest, previous)
            except Exception:
                incomplete = True
        if incomplete:
            from software.base import InstallError
            raise InstallError("command link operation failed; rollback incomplete") from None
        raise


@system_mutation
def link_into_system_bin(bin_dir: str, names: Iterable[str], *, managed_root: Path | str) -> None:
    """root 下在 ``/usr/local/bin`` 创建 / 更新指向 ``bin_dir/<name>`` 的 symlink。"""
    if not _is_root():
        return
    bin_path = Path(bin_dir)
    root = Path(managed_root).resolve()
    bin_path.resolve().relative_to(root)
    planned = []
    for name in names:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("invalid command link name")
        src = bin_path / name
        if not src.exists():
            continue
        src.resolve().relative_to(root)
        dest = Path(SYSTEM_BIN_DIR) / name
        previous = _command_link(dest)
        if previous is not None:
            try:
                _command_target(dest, previous).relative_to(root)
            except ValueError:
                raise FileExistsError("refusing to overwrite an unmanaged command link") from None
        planned.append((dest, previous, src))
    _apply_command_links(planned)


@system_mutation
def unlink_system_bin(names: Iterable[str], managed_root: Path | str) -> None:
    """root 下删除 ``/usr/local/bin`` 中指向受管目录的 symlink。"""
    if not _is_root():
        return
    root = Path(managed_root).resolve()
    planned = []
    for name in names:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("invalid command link name")
        dest = Path(SYSTEM_BIN_DIR) / name
        if dest.is_symlink():
            previous = dest.readlink()
            try:
                _command_target(dest, previous).relative_to(root)
            except ValueError:
                continue
            planned.append((dest, previous, None))
    _apply_command_links(planned)
