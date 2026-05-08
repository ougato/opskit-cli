# Recipe 依赖管理规范

## 强制要求

每个新增 Recipe 必须：

1. **覆盖 `system_version()`**：返回系统中当前安装的版本号（如 `"3.11.2"`），`None` 表示未安装
2. **声明 `dependencies`**：明确列出依赖的其他 Recipe，使用结构化格式
3. **禁止在 recipe 内部直接 `apt/yum install` 其他软件**：依赖必须声明在 `dependencies` 字段，由 `resolver.py` 统一管理

## dependencies 格式

```python
# str 格式：存在即可，不限版本
dependencies: ClassVar[list] = ["python"]

# dict 格式：指定最低版本
dependencies: ClassVar[list] = [{"key": "python", "min": "3.10"}]

# 混合格式
dependencies: ClassVar[list] = [
    {"key": "python", "min": "3.10"},
    "docker",
]
```

## 版本比较规则

- 格式：`"major.minor"` 或 `"major.minor.patch"`
- 比较方式：转为 `tuple[int, ...]` 后按位比较
- 示例：`"3.9.0" < "3.10"` → `True`（因为 `(3,9) < (3,10)`）

## 边界场景处理

| 场景 | 行为 |
|---|---|
| 依赖缺失 | 静默安装（进度条中展示） |
| 版本满足 | 跳过，不动系统 |
| 版本低于要求 | 交互提示 + 用户确认 + 隔离安装 |
| 用户拒绝升级 | `InstallError` 终止 |
| 依赖未注册 | `InstallError("依赖 'xxx' 未在注册表中找到")` |
| 循环依赖 | `InstallError("检测到循环依赖")` |
| 依赖链过深（>5） | `InstallError("依赖链过深")` |

## Python 特殊说明

- Debian/Ubuntu 系统 Python 的 `ensurepip` 被禁用
- Python Recipe 安装时自动附带安装 `python3.X-venv`
- `system_version()` 按 `3.13 → 3.12 → 3.11 → 3.10 → python3` 顺序检测
- venv 创建失败时自动降级为 `--without-pip` + `get-pip.py` bootstrap

## 相关文件

| 文件 | 职责 |
|---|---|
| `software/base.py` | `system_version()` / `min_version()` 基类定义 |
| `software/resolver.py` | 依赖解析器（循环检测 + 递归 + 版本比较） |
| `software/menu.py` | `_do_install` / `_do_upgrade` 前调用 `resolve_deps()` |
| `core/venv_bootstrap.py` | 首次运行自动创建 `.venv` |
| `core/constants.py` | `PYTHON_MIN_VERSION = "3.10"` / `MAX_DEP_DEPTH = 5` |
