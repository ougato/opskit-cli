"""Oh My Zsh recipe 常量：安装源、路径、命令、超时。"""
from __future__ import annotations

from pathlib import Path
from typing import Final

# ─── 命令 ─────────────────────────────────────────────────────────────────────
ZSH_COMMAND: Final[str] = "zsh"
GIT_COMMAND: Final[str] = "git"
SH_COMMAND: Final[str] = "sh"
CHSH_COMMAND: Final[str] = "chsh"

ZSH_PACKAGE: Final[str] = "zsh"
GIT_PACKAGE: Final[str] = "git"

# ─── 安装源 ───────────────────────────────────────────────────────────────────
OMZ_INSTALL_SCRIPT_URL: Final[str] = (
    "https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh"
)
P10K_REPO_URL: Final[str] = "https://github.com/romkatv/powerlevel10k.git"
ZSH_AUTOSUGGESTIONS_REPO_URL: Final[str] = (
    "https://github.com/zsh-users/zsh-autosuggestions.git"
)
ZSH_SYNTAX_HIGHLIGHTING_REPO_URL: Final[str] = (
    "https://github.com/zsh-users/zsh-syntax-highlighting.git"
)

# Powerlevel10k 推荐字体 MesloLGS NF（rainbow 圆角分隔符/图标需要 Nerd Font 才能正常渲染）。
FONT_BASE_URL: Final[str] = (
    "https://github.com/romkatv/powerlevel10k-media/raw/master/"
)
FONT_FILES: Final[tuple[str, ...]] = (
    "MesloLGS NF Regular.ttf",
    "MesloLGS NF Bold.ttf",
    "MesloLGS NF Italic.ttf",
    "MesloLGS NF Bold Italic.ttf",
)
FC_CACHE_COMMAND: Final[str] = "fc-cache"

# ─── 路径 ─────────────────────────────────────────────────────────────────────
def home_dir() -> Path:
    return Path.home()


def omz_dir() -> Path:
    return home_dir() / ".oh-my-zsh"


def omz_custom_dir() -> Path:
    return omz_dir() / "custom"


def p10k_theme_dir() -> Path:
    return omz_custom_dir() / "themes" / "powerlevel10k"


def omz_plugin_dir(name: str) -> Path:
    return omz_custom_dir() / "plugins" / name


def font_dir() -> Path:
    """用户字体目录：macOS 用 ~/Library/Fonts，其余（Linux）用 ~/.local/share/fonts。"""
    import sys

    if sys.platform == "darwin":
        return home_dir() / "Library" / "Fonts"
    return home_dir() / ".local" / "share" / "fonts"


def zshrc_path() -> Path:
    return home_dir() / ".zshrc"


def p10k_config_path() -> Path:
    return home_dir() / ".p10k.zsh"


# ─── 主题与配置标记 ───────────────────────────────────────────────────────────
P10K_THEME_NAME: Final[str] = "powerlevel10k/powerlevel10k"
DEFAULT_THEME_NAME: Final[str] = "robbyrussell"

# 默认启用的 Oh My Zsh 插件（与用户 Debian 配置一致：git + 自动补全 + 语法高亮）。
DEFAULT_PLUGINS: Final[tuple[str, ...]] = ("git",)
# 需额外从 GitHub 克隆的插件：(插件名, 仓库地址)。
EXTRA_PLUGINS: Final[tuple[tuple[str, str], ...]] = (
    ("zsh-autosuggestions", ZSH_AUTOSUGGESTIONS_REPO_URL),
    ("zsh-syntax-highlighting", ZSH_SYNTAX_HIGHLIGHTING_REPO_URL),
)
FULL_PLUGINS: Final[tuple[str, ...]] = DEFAULT_PLUGINS + tuple(
    name for name, _ in EXTRA_PLUGINS
)

# .zshrc 中由本工具托管的配置块标记，便于幂等写入与卸载清理。
MANAGED_BLOCK_BEGIN: Final[str] = "# >>> opskit oh-my-zsh >>>"
MANAGED_BLOCK_END: Final[str] = "# <<< opskit oh-my-zsh <<<"
# source .p10k.zsh 行的行尾标记（幂等识别用）。
P10K_CONFIG_MARKER: Final[str] = "# opskit-p10k"

# 关闭 Oh My Zsh 自动更新（新版 zstyle 语法 + 旧版环境变量双保险）。
DISABLE_UPDATE_LINES: Final[tuple[str, ...]] = (
    "zstyle ':omz:update' mode disabled",
    "DISABLE_AUTO_UPDATE=\"true\"",
)

# ─── 超时（秒）────────────────────────────────────────────────────────────────
DOWNLOAD_TIMEOUT_SECONDS: Final[int] = 60
INSTALL_TIMEOUT_SECONDS: Final[int] = 600
GIT_CLONE_TIMEOUT_SECONDS: Final[int] = 300
COMMAND_TIMEOUT_SECONDS: Final[int] = 60

# 安装报错时保留的日志尾行数。
INSTALL_ERROR_TAIL_LINES: Final[int] = 20

# 支持的平台（Windows 无 zsh，不提供）。
SUPPORTED_PLATFORMS: Final[tuple[str, ...]] = ("linux", "darwin")
