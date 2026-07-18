"""安装配方抽象基类"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class InstallStep:
    description_key: str   # i18n key，如 'software.step.download'
    weight: int = 1        # 占总进度的权重


class Recipe(ABC):
    key: str                # 唯一标识，如 'docker' / 'nginx'
                            # 显示名由 get_icon(key) + t(f'software.{key}') 动态组合
    category: str = "devops"      # 分类：'devtools' | 'devops'
    description: str = ""         # 一句话描述，供搜索匹配
    platforms: list[str]    # 支持平台
    # 依赖声明：str 表示"存在即可"，dict 支持版本约束
    # 示例：[{"key": "python", "min": "3.10"}] 或 ["python"]
    dependencies: list = []  # list[str | dict[str, str]]

    # ── 能力声明（子类可覆盖）──
    has_upgrade: bool = True       # 是否支持升级（False 时菜单中灰显）
    has_diagnose: bool = False     # 是否支持诊断
    has_submenu: bool = False      # 是否有子菜单（如 WireGuard → 公网/内网）
    has_wizard: bool = False       # 是否使用自定义安装向导
    has_manage: bool = False       # 是否支持管理（如 WireGuard peer 管理）
    has_version_picker: bool = False  # 安装时直接展示版本列表（subtitle 显示已安装版本，选中即装，无二次确认）
    has_install_version_selection: bool = True  # 普通安装时是否需要展示版本选择
    confirm_before_install: bool = True  # 未安装时是否需要二次确认安装
    confirm_before_uninstall: bool = True  # 卸载前是否需要二次确认
    has_switch: bool = False       # 是否支持版本切换（多版本共存，切换激活版本）
    hidden: bool = False           # 是否在分类/搜索列表中隐藏（仅通过父 recipe submenu 访问）
    requires_root: bool = False    # install/uninstall/upgrade 是否需要 root（系统级软件置 True，用户态软件保持 False）

    @abstractmethod
    def detect(self) -> str | None:
        """检测是否已安装，返回版本号字符串或 None"""

    @abstractmethod
    def versions(self) -> list[str]:
        """
        可安装的版本列表，双源策略：

        1. 调用在线 API 获取最新版本列表（带超时 TIMEOUT_VERSION_FETCH）
        2. API 失败 → 回退到内置硬编码列表（常见 LTS 版本）
        3. 返回列表按版本降序排列，第一个为推荐版本
        """

    @abstractmethod
    def install(self, version: str) -> None:
        """
        执行安装，抛出异常表示失败。

        实现要求：
        - 使用 core/progress.py 的 MultiStepProgress 显示进度
        - 安装失败时抛出 InstallError
        - 安装前调用 check_disk_space()
        """

    @abstractmethod
    def uninstall(self, version: str | None = None) -> None:
        """执行卸载，抛出异常表示失败。

        version 为 None 表示卸载全部；单版本类配方可忽略该参数。
        """

    def activate(self) -> None:
        """把已安装软件的可执行目录注入当前进程 PATH（未安装时无副作用）。

        默认无操作。多版本共存类配方（golang / nodejs 等私有目录安装）
        应覆盖此方法，保证本进程内的子进程能直接调用到该软件。
        """
        pass

    def system_version(self) -> str | None:
        """
        检测系统中该软件当前版本（供依赖链调用）。

        默认复用 detect()。子类可覆盖以提供更精确的版本检测。
        返回格式：'3.11.2' / '24.0.7' 等，None 表示未安装。
        """
        return self.detect()

    def min_version(self) -> str | None:
        """
        本 Recipe 对外声明的最低可用版本（供调用方判断是否满足需求）。

        None 表示无版本要求。格式：'3.10' / '24.0' 等。
        """
        return None

    def diagnose(self) -> None:
        """运行诊断检查（has_diagnose=True 时子类实现）"""
        pass

    def manage(self) -> None:
        """运行管理界面（has_manage=True 时子类实现）"""
        pass

    def submenu_items(self) -> list[dict]:
        """返回子菜单项列表（has_submenu=True 时子类实现）

        返回格式：[{"key": "wg_server", "label_key": "software.wg_server"}]
        """
        return []

    def upgrade(self, version: str) -> None:
        """
        升级到指定版本（默认实现：卸载 + 安装）。

        子类可覆盖此方法提供更高效的原地升级。
        """
        self.uninstall()
        self.install(version)

    def steps(self, action: str = "install") -> list[InstallStep]:
        """
        返回安装/卸载步骤列表（用于进度条）。

        默认返回通用步骤，子类可覆盖以提供精确步骤。
        action: 'install' / 'uninstall' / 'upgrade'
        """
        if action == "uninstall":
            return [
                InstallStep("software.step.stop_service"),
                InstallStep("software.step.remove_files"),
                InstallStep("software.step.cleanup"),
            ]
        return [
            InstallStep("software.step.check"),
            InstallStep("software.step.download"),
            InstallStep("software.step.install"),
            InstallStep("software.step.verify"),
        ]


class InstallError(Exception):
    """安装失败异常"""
    pass


class UninstallError(Exception):
    """卸载失败异常"""
    pass
