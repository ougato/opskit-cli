"""守卫测试：新增软件 recipe 必须注册进 CLI（自动发现 + 菜单分类）。

防止常见疏漏：新增 software/recipes/<name>/recipe.py 后忘记在
<name>/__init__.py 里 `from .recipe import XxxRecipe`，导致 @register 不执行，
software list / 交互菜单里都看不到该软件（很多用户走无交互 CLI，尤其要保证可见）。
"""
from __future__ import annotations

from pathlib import Path

from software.registry import all_recipes

# 与 software/menu.py entry() 暴露的分类保持一致
MENU_CATEGORIES = {"devtools", "devops", "systools"}

RECIPES_DIR = Path(__file__).resolve().parent.parent / "software" / "recipes"


def _recipe_packages() -> list[str]:
    """所有含 recipe.py 且声明了 @register 的 recipe 包目录名。"""
    pkgs: list[str] = []
    for d in sorted(RECIPES_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or d.name == "__pycache__":
            continue
        recipe_py = d / "recipe.py"
        if recipe_py.exists() and "@register" in recipe_py.read_text(encoding="utf-8"):
            pkgs.append(d.name)
    return pkgs


def _registered_packages() -> set[str]:
    """已被注册表自动发现的 recipe 所属包目录名。"""
    pkgs: set[str] = set()
    for cls in all_recipes():
        mod = cls.__module__
        if mod.startswith("software.recipes."):
            pkgs.add(mod.split(".")[2])  # software.recipes.<pkg>.recipe
    return pkgs


def test_every_recipe_package_is_registered() -> None:
    """每个 recipe 包都必须被自动发现注册，否则 CLI / 菜单看不到。"""
    missing = sorted(set(_recipe_packages()) - _registered_packages())
    assert not missing, (
        f"以下 recipe 未注册进 CLI：{missing}。"
        f" 请在 software/recipes/<name>/__init__.py 中 `from .recipe import XxxRecipe`，"
        f"并确认 recipe 类带 @register 装饰器。"
    )


def test_registered_recipes_have_menu_category() -> None:
    """非隐藏 recipe 的 category 必须是菜单可见分类，否则只在 CLI / 搜索可见。"""
    bad = sorted(
        cls.key
        for cls in all_recipes()
        if not getattr(cls, "hidden", False)
        and getattr(cls, "category", "devops") not in MENU_CATEGORIES
    )
    assert not bad, (
        f"以下软件的 category 不在菜单分类 {sorted(MENU_CATEGORIES)} 内，"
        f"交互菜单将看不到：{bad}"
    )


def test_software_list_shows_every_supported_recipe() -> None:
    """端到端守卫：`software list` 必须列出当前平台上所有非隐藏 recipe。

    这是用户实际使用的入口（很多人只跑无交互 CLI）。新增软件后若忘了注册 /
    平台声明写错，这里会直接把缺失的 key 报出来。
    """
    import main
    from core.platform import get_platform
    from core.prompt import console

    info = get_platform()
    expected = sorted(
        cls.key
        for cls in all_recipes()
        if not getattr(cls, "hidden", False)
        and info.os_type in getattr(cls, "platforms", [])
    )

    with console.capture() as cap:
        main._print_software_table()
    output = cap.get()

    missing = [key for key in expected if key not in output]
    assert not missing, (
        f"以下软件在 `software list` 里看不到（当前平台 {info.os_type}）：{missing}。"
        f" 新增 recipe 必须在 __init__.py 导出并注册，且 platforms 含当前平台。"
    )
