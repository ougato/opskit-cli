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
from collections.abc import Iterable
from pathlib import Path

_RC_FILES = (".bashrc", ".zshrc", ".profile", ".bash_profile")
SYSTEM_BIN_DIR = "/usr/local/bin"


def _is_root() -> bool:
    return hasattr(os, "getuid") and os.getuid() == 0


def prepend_process_path(directory: str) -> None:
    """将目录加到当前进程 ``PATH`` 最前（已存在则不重复）。"""
    cur_path = os.environ.get("PATH", "")
    if directory not in cur_path.split(":"):
        os.environ["PATH"] = directory + ":" + cur_path


def process_path_contains(directory: str) -> bool:
    """当前进程 ``PATH`` 是否包含该目录。"""
    return any(p == directory for p in os.environ.get("PATH", "").split(":"))


def inject_rc_path(
    shims_path: str,
    marker_begin: str,
    marker_end: str,
    profile_d_file: str | None = None,
) -> None:
    """在各 shell rc 文件中写入 marker 包裹的 ``export PATH`` 块（幂等）。

    root 且给定 ``profile_d_file`` 时，额外写入 ``/etc/profile.d`` 文件。
    """
    block = (
        f"\n{marker_begin}\n"
        f'export PATH="{shims_path}:$PATH"\n'
        f"{marker_end}\n"
    )
    for name in _RC_FILES:
        rc = Path.home() / name
        if not rc.exists():
            continue
        text = rc.read_text(encoding="utf-8")
        if marker_begin not in text:
            rc.write_text(text + block, encoding="utf-8")

    if profile_d_file:
        try:
            if _is_root():
                pd = Path(profile_d_file)
                pd.write_text(f'export PATH="{shims_path}:$PATH"\n', encoding="utf-8")
                pd.chmod(0o644)
        except Exception:
            pass


def remove_rc_path(
    marker_begin: str,
    marker_end: str,
    profile_d_file: str | None = None,
) -> None:
    """移除各 shell rc 文件中 marker 包裹的块，并删除 ``/etc/profile.d`` 文件。"""
    for name in _RC_FILES:
        rc = Path.home() / name
        if not rc.exists():
            continue
        try:
            lines = rc.read_text(encoding="utf-8").splitlines(keepends=True)
            out: list[str] = []
            skip = False
            for line in lines:
                if line.strip() == marker_begin:
                    skip = True
                if not skip:
                    out.append(line)
                if line.strip() == marker_end:
                    skip = False
            rc.write_text("".join(out), encoding="utf-8")
        except Exception:
            pass

    if profile_d_file:
        try:
            pd = Path(profile_d_file)
            if pd.exists():
                pd.unlink()
        except Exception:
            pass


def link_into_system_bin(bin_dir: str, names: Iterable[str]) -> None:
    """root 下在 ``/usr/local/bin`` 创建 / 更新指向 ``bin_dir/<name>`` 的 symlink。"""
    if not _is_root():
        return
    bin_path = Path(bin_dir)
    for name in names:
        src = bin_path / name
        if not src.exists():
            continue
        dest = Path(SYSTEM_BIN_DIR) / name
        try:
            if dest.is_symlink() or dest.exists():
                dest.unlink()
            dest.symlink_to(src)
        except Exception:
            pass


def unlink_system_bin(names: Iterable[str], managed_root: Path | str) -> None:
    """root 下删除 ``/usr/local/bin`` 中指向受管目录的 symlink。"""
    if not _is_root():
        return
    marker = str(managed_root)
    for name in names:
        dest = Path(SYSTEM_BIN_DIR) / name
        try:
            if dest.is_symlink():
                if marker in str(dest.resolve()):
                    dest.unlink()
        except Exception:
            pass
