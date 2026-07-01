"""Oh My Zsh 安装、卸载、管理与 Powerlevel10k 自动配置。

设计要点：
- 仅支持 linux / macos（Windows 无 zsh）。
- 安装后关闭 Oh My Zsh 自动更新。
- 安装前询问是否安装 Powerlevel10k，选择后自动写入预置 .p10k.zsh，
  用户开箱即用，无需手动跑配置向导。
- 管理面板保留「自定义配置 Powerlevel10k」入口，供高级用户手动调整。
- 安装成功后自动把默认 Shell 切换为 zsh。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from rich.console import Console

from core.http import get_bytes
from core.i18n import t
from core.progress import MultiStepProgress
from core.prompt import UserCancel, clear_screen, confirm, pause, select
from core.theme import (
    get_color,
    get_icon,
    print_action_title,
    print_error,
    print_info,
    print_success,
    print_warning,
)
from software.base import InstallError, UninstallError
from software.recipes.ohmyzsh.constants import (
    CHSH_COMMAND,
    COMMAND_TIMEOUT_SECONDS,
    DEFAULT_THEME_NAME,
    DISABLE_UPDATE_LINES,
    DOWNLOAD_TIMEOUT_SECONDS,
    GIT_CLONE_TIMEOUT_SECONDS,
    GIT_COMMAND,
    GIT_PACKAGE,
    INSTALL_ERROR_TAIL_LINES,
    INSTALL_TIMEOUT_SECONDS,
    MANAGED_BLOCK_BEGIN,
    MANAGED_BLOCK_END,
    OMZ_INSTALL_SCRIPT_URL,
    P10K_CONFIG_MARKER,
    P10K_REPO_URL,
    P10K_THEME_NAME,
    SH_COMMAND,
    SUPPORTED_PLATFORMS,
    ZSH_COMMAND,
    ZSH_PACKAGE,
    omz_dir,
    p10k_config_path,
    p10k_theme_dir,
    zshrc_path,
)
from software.recipes.ohmyzsh.p10k_preset import P10K_PRESET

console = Console()

_BREADCRUMB_BASE = ["OpsKit", t("menu.software"), t("software.category.systools"), t("software.ohmyzsh")]


def _breadcrumb(action_key: str) -> list[str]:
    return ["OpsKit", t("menu.software"), t("software.category.systools"), t("software.ohmyzsh"), t(action_key)]


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def _ensure_supported() -> None:
    from core.platform import get_platform

    if get_platform().os_type not in SUPPORTED_PLATFORMS:
        raise InstallError(t("ohmyzsh.error.unsupported_os"))


def _run(command: list[str], check: bool = False, timeout: int = COMMAND_TIMEOUT_SECONDS,
         env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


# ─── 检测 ─────────────────────────────────────────────────────────────────────

def detect() -> str | None:
    """已安装则返回 Oh My Zsh 仓库短提交号，否则 None。"""
    d = omz_dir()
    if not d.exists():
        return None
    if command_exists(GIT_COMMAND):
        result = _run([GIT_COMMAND, "-C", str(d), "rev-parse", "--short", "HEAD"])
        rev = (result.stdout or "").strip()
        if rev:
            return rev
    return t("ohmyzsh.installed_mark")


def p10k_installed() -> bool:
    return p10k_theme_dir().exists()


# ─── 环境准备 ─────────────────────────────────────────────────────────────────

def _ensure_zsh_and_git() -> None:
    """确保 zsh、git 存在，缺失则用系统包管理器安装。"""
    from core.pkg_runner import get_runner

    missing = [
        pkg
        for cmd, pkg in ((ZSH_COMMAND, ZSH_PACKAGE), (GIT_COMMAND, GIT_PACKAGE))
        if not command_exists(cmd)
    ]
    if not missing:
        return
    runner = get_runner()
    try:
        runner.install(missing)
    except Exception as exc:
        raise InstallError(t("ohmyzsh.error.dep_failed", error=str(exc))) from exc
    if not command_exists(ZSH_COMMAND):
        raise InstallError(t("ohmyzsh.error.zsh_missing"))


# ─── 安装 Oh My Zsh ───────────────────────────────────────────────────────────

def _install_omz() -> None:
    """以非交互方式运行官方安装脚本（不改 shell、不进 zsh，全部交由本工具接管）。"""
    if omz_dir().exists():
        return
    content = get_bytes(OMZ_INSTALL_SCRIPT_URL, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    if content is None:
        raise InstallError(t("ohmyzsh.error.download_failed"))
    with tempfile.NamedTemporaryFile("wb", suffix=".sh", delete=False) as f:
        script_path = Path(f.name)
        f.write(content)
    try:
        script_path.chmod(0o700)
        env = {
            **os.environ,
            "RUNZSH": "no",   # 装完不要直接进入 zsh
            "CHSH": "no",     # 由本工具统一切换默认 shell
            "KEEP_ZSHRC": "no",
        }
        result = _run(
            [SH_COMMAND, str(script_path), "--unattended"],
            timeout=INSTALL_TIMEOUT_SECONDS,
            env=env,
        )
        if result.returncode != 0 or not omz_dir().exists():
            detail = (result.stderr or result.stdout or "").strip()
            tail = "\n".join(detail.splitlines()[-INSTALL_ERROR_TAIL_LINES:])
            raise InstallError(tail or t("ohmyzsh.error.install_failed"))
    finally:
        script_path.unlink(missing_ok=True)


# ─── .zshrc 托管块 ────────────────────────────────────────────────────────────

def _read_zshrc() -> str:
    path = zshrc_path()
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _strip_managed_block(text: str) -> str:
    """移除已有的托管块，保证幂等写入。"""
    begin = text.find(MANAGED_BLOCK_BEGIN)
    if begin == -1:
        return text
    end = text.find(MANAGED_BLOCK_END, begin)
    if end == -1:
        return text[:begin].rstrip() + "\n"
    end += len(MANAGED_BLOCK_END)
    return (text[:begin].rstrip() + "\n" + text[end:].lstrip("\n")).rstrip() + "\n"


def _set_theme(text: str, theme_name: str) -> str:
    """把 .zshrc 中的 ZSH_THEME 替换为目标主题（不存在则追加）。"""
    import re

    line = f'ZSH_THEME="{theme_name}"'
    if re.search(r"^\s*ZSH_THEME=.*$", text, flags=re.MULTILINE):
        return re.sub(r"^\s*ZSH_THEME=.*$", line, text, count=1, flags=re.MULTILINE)
    return text.rstrip() + "\n" + line + "\n"


def _apply_zshrc(with_p10k: bool) -> None:
    """写入托管块：关闭自动更新，按需启用 Powerlevel10k 并 source 预置配置。"""
    text = _read_zshrc()
    text = _strip_managed_block(text)
    text = _set_theme(text, P10K_THEME_NAME if with_p10k else DEFAULT_THEME_NAME)

    block_lines = [MANAGED_BLOCK_BEGIN, *DISABLE_UPDATE_LINES]
    if with_p10k:
        cfg = p10k_config_path()
        block_lines.append(f'[[ ! -f "{cfg}" ]] || source "{cfg}"  {P10K_CONFIG_MARKER}')
    block_lines.append(MANAGED_BLOCK_END)

    new_text = text.rstrip() + "\n\n" + "\n".join(block_lines) + "\n"
    zshrc_path().write_text(new_text, encoding="utf-8")


# ─── Powerlevel10k ────────────────────────────────────────────────────────────

def _install_p10k() -> None:
    """克隆 Powerlevel10k 主题（已存在则跳过）。"""
    dest = p10k_theme_dir()
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not command_exists(GIT_COMMAND):
        raise InstallError(t("ohmyzsh.error.git_missing"))
    result = _run(
        [GIT_COMMAND, "clone", "--depth", "1", P10K_REPO_URL, str(dest)],
        timeout=GIT_CLONE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0 or not dest.exists():
        detail = (result.stderr or result.stdout or "").strip()
        raise InstallError(detail or t("ohmyzsh.error.p10k_clone_failed"))


def _write_p10k_preset() -> None:
    """写入预置 .p10k.zsh，让用户无需跑向导即可直接使用。"""
    p10k_config_path().write_text(P10K_PRESET, encoding="utf-8")


# ─── 默认 Shell 切换 ──────────────────────────────────────────────────────────

def _switch_default_shell() -> bool:
    """把当前用户默认登录 Shell 切换为 zsh，成功返回 True。"""
    zsh_path = shutil.which(ZSH_COMMAND)
    if not zsh_path:
        return False
    current = os.environ.get("SHELL", "")
    if current.endswith("/zsh"):
        return True

    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    result = _run([CHSH_COMMAND, "-s", zsh_path, user] if user else [CHSH_COMMAND, "-s", zsh_path])
    if result.returncode == 0:
        return True

    # 普通用户无权限时尝试提权。
    try:
        from core.privilege import run_as_root

        cmd = [CHSH_COMMAND, "-s", zsh_path, user] if user else [CHSH_COMMAND, "-s", zsh_path]
        r2 = run_as_root(cmd, check=False, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=COMMAND_TIMEOUT_SECONDS)
        return r2.returncode == 0
    except Exception:
        return False


# ─── 对外主流程 ───────────────────────────────────────────────────────────────

def install_ohmyzsh() -> None:
    _ensure_supported()

    base = _BREADCRUMB_BASE
    # 安装前询问是否安装 Powerlevel10k。
    try:
        with_p10k = confirm(breadcrumb=base, prompt=t("ohmyzsh.confirm_p10k"), theme_key="software")
    except UserCancel:
        return

    breadcrumb = _breadcrumb("software.install")
    clear_screen()
    print_action_title(breadcrumb)

    descs = [
        t("ohmyzsh.step.check_env"),
        t("ohmyzsh.step.install_omz"),
        t("ohmyzsh.step.disable_update"),
    ]
    if with_p10k:
        descs.append(t("ohmyzsh.step.config_p10k"))
    descs.append(t("ohmyzsh.step.switch_shell"))

    shell_switched = False
    try:
        with MultiStepProgress(descs) as sp:
            sp.step(t("ohmyzsh.step.check_env"))
            _ensure_zsh_and_git()

            sp.step(t("ohmyzsh.step.install_omz"))
            _install_omz()

            sp.step(t("ohmyzsh.step.disable_update"))
            _apply_zshrc(with_p10k)

            if with_p10k:
                sp.step(t("ohmyzsh.step.config_p10k"))
                _install_p10k()
                _write_p10k_preset()

            sp.step(t("ohmyzsh.step.switch_shell"))
            shell_switched = _switch_default_shell()
            sp.complete()
    except InstallError:
        raise
    except Exception as exc:
        raise InstallError(str(exc)) from exc

    console.print()
    _render_done_panel(with_p10k=with_p10k, shell_switched=shell_switched)


def uninstall_ohmyzsh() -> None:
    base = _BREADCRUMB_BASE
    if not omz_dir().exists():
        clear_screen()
        print_action_title(base, trailing_blank=False)
        print_warning(t("software.not_installed_hint", name=t("software.ohmyzsh")))
        pause()
        return

    try:
        if not confirm(breadcrumb=base, prompt=t("uninstall.confirm", name=t("software.ohmyzsh")),
                       theme_key="software"):
            return
    except UserCancel:
        return

    breadcrumb = _breadcrumb("software.uninstall")
    clear_screen()
    print_action_title(breadcrumb)
    descs = [
        t("software.step.remove_files"),
        t("software.step.cleanup"),
    ]
    try:
        with MultiStepProgress(descs) as sp:
            sp.step(descs[0])
            shutil.rmtree(omz_dir(), ignore_errors=True)
            shutil.rmtree(p10k_theme_dir().parent, ignore_errors=True)

            sp.step(descs[1])
            _cleanup_zshrc()
            p10k_config_path().unlink(missing_ok=True)
            sp.complete()
    except Exception as exc:
        raise UninstallError(str(exc)) from exc
    print_success(t("ohmyzsh.output.uninstall_done"))
    pause()


def _cleanup_zshrc() -> None:
    """卸载时移除托管块，并把主题回落到默认，避免 zsh 报找不到 p10k。"""
    if not zshrc_path().exists():
        return
    text = _strip_managed_block(_read_zshrc())
    text = _set_theme(text, DEFAULT_THEME_NAME)
    zshrc_path().write_text(text, encoding="utf-8")


# ─── 结果面板 ─────────────────────────────────────────────────────────────────

def _render_done_panel(with_p10k: bool, shell_switched: bool) -> None:
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    success = get_color("success")
    value_style = get_color("text")

    rows = [t("ohmyzsh.output.omz_ready")]
    rows.append(
        t("ohmyzsh.output.p10k_ready") if with_p10k else t("ohmyzsh.output.p10k_skipped")
    )
    rows.append(
        t("ohmyzsh.output.shell_switched") if shell_switched else t("ohmyzsh.output.shell_manual")
    )

    tbl = Table.grid(padding=(0, 1))
    tbl.add_column(no_wrap=False)
    for row in rows:
        tbl.add_row(Text(row, style=value_style))
    console.print(Panel(
        tbl,
        title=f"[{success}]{t('ohmyzsh.output.install_done')}[/{success}]",
        border_style=success,
        padding=(1, 2),
        expand=False,
    ))
    pause()


# ─── 管理面板 ─────────────────────────────────────────────────────────────────

def manage_ohmyzsh() -> None:
    base = _breadcrumb("software.manage")
    if not omz_dir().exists():
        clear_screen()
        print_action_title(base, trailing_blank=False)
        print_warning(t("software.not_installed_hint", name=t("software.ohmyzsh")))
        pause()
        return

    while True:
        try:
            key = select(
                breadcrumb=base,
                subtitle=t("prompt.select"),
                choices=[
                    {"key": "1", "label": f"{get_icon('edit')} {t('ohmyzsh.manage.p10k_configure')}"},
                    {"key": "2", "label": f"{get_icon('save')} {t('ohmyzsh.manage.p10k_apply_preset')}"},
                    {"key": "3", "label": f"{get_icon('switch')} {t('ohmyzsh.manage.switch_shell')}"},
                ],
                theme_key="software",
                back_label=f"{get_icon('back')} {t('menu.back')}",
            )
        except UserCancel:
            return
        if key is None:
            return

        if key == "1":
            _manage_p10k_configure(base)
        elif key == "2":
            _manage_p10k_apply_preset(base)
        elif key == "3":
            _manage_switch_shell(base)


def _manage_p10k_configure(breadcrumb: list[str]) -> None:
    """手动运行 Powerlevel10k 配置向导（交互式）。"""
    clear_screen()
    print_action_title(breadcrumb, trailing_blank=False)
    if not p10k_installed():
        print_warning(t("ohmyzsh.error.p10k_not_installed"))
        pause()
        return
    if not command_exists(ZSH_COMMAND):
        print_warning(t("ohmyzsh.error.zsh_missing"))
        pause()
        return
    print_info(t("ohmyzsh.manage.p10k_configure_hint"))
    try:
        # p10k configure 是 zsh 函数，需交互式 zsh 环境运行。
        subprocess.run([ZSH_COMMAND, "-i", "-c", "p10k configure"], check=False)
    except Exception as exc:
        print_error(t("error.unknown", error=str(exc)))
    pause()


def _manage_p10k_apply_preset(breadcrumb: list[str]) -> None:
    """（重新）应用本工具的推荐 Powerlevel10k 配置。"""
    clear_screen()
    print_action_title(breadcrumb, trailing_blank=False)
    try:
        _install_p10k()
        _apply_zshrc(with_p10k=True)
        _write_p10k_preset()
    except Exception as exc:
        print_error(t("error.unknown", error=str(exc)))
        pause()
        return
    print_success(t("ohmyzsh.output.p10k_ready"))
    pause()


def _manage_switch_shell(breadcrumb: list[str]) -> None:
    clear_screen()
    print_action_title(breadcrumb, trailing_blank=False)
    if _switch_default_shell():
        print_success(t("ohmyzsh.output.shell_switched"))
    else:
        print_warning(t("ohmyzsh.output.shell_manual"))
    pause()
