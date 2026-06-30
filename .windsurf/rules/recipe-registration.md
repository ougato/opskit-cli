# 软件注册规范（新增软件必须进 CLI）

很多用户通过**无交互命令行**使用本工具（`opskit software list` / `install <name>`），
因此每个新增软件**必须**自动出现在 CLI 与交互菜单里，不能只加目录就完事。

## 强制要求

新增一个软件 `<name>` 时，必须同时满足：

1. **目录结构**：`software/recipes/<name>/recipe.py` 中定义 `XxxRecipe(Recipe)` 子类，并加 `@register` 装饰器。
2. **包导出**：`software/recipes/<name>/__init__.py` 必须 `from .recipe import XxxRecipe`。
   - 注册表靠导入 recipe 包触发 `@register`；漏了这一行，`software list` / 菜单都看不到该软件（最常见的坑）。
3. **唯一 key**：`key: ClassVar[str] = "<name>"`，全局唯一。
4. **平台声明**：`platforms` 包含目标 OS（`linux` / `darwin` / `windows`），否则在该平台被 CLI/菜单过滤掉。
5. **菜单分类**：`category` 必须是 `"devtools"` 或 `"devops"`（见 `software/menu.py` `entry()`），否则交互菜单里看不到（仅 CLI/搜索可见）。
6. **子项隐藏**：仅通过父 recipe 子菜单访问的子项（如 `wg_server`/`wg_client`）设 `hidden = True`。

## 守卫测试

`tests/test_recipe_registration.py` 会扫描 `software/recipes/` 自动校验上述 1/2/5：

- `test_every_recipe_package_is_registered`：带 `@register` 的包必须被注册表发现。
- `test_registered_recipes_have_menu_category`：非隐藏 recipe 的 `category` 必须在菜单分类内。

新增软件后跑 `pytest tests/test_recipe_registration.py`，红了按提示补齐即可。
