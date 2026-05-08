# Python 开发规则

## 版本要求

- 最低 Python 3.10+（使用 `match` 语句、类型联合 `X | Y`）
- 依赖通过 `requirements.txt` 管理，固定版本号

## 代码风格

- 缩进：4 个空格，禁止 Tab
- 行长度：最大 100 字符
- 字符串：优先单引号，f-string 拼接变量
- 类型注解：所有函数参数和返回值必须标注类型，`from __future__ import annotations` 按需引入
- 导入顺序：标准库 → 第三方 → 本地模块，各组之间空一行

## 命名规范

- 模块/包：`snake_case`（小写下划线）
- 类：`PascalCase`
- 函数/变量：`snake_case`
- 常量：`UPPER_SNAKE_CASE`
- 私有成员：`_single_leading_underscore`

## 错误处理

- 禁止裸 `except:`，必须指定异常类型 `except SomeError as e:`
- 用户可见的错误必须通过 Rich 的 `console.print("[red]错误：...[/red]")` 显示，不直接 `raise` 到终端
- 子进程执行失败时打印错误并返回非零退出码，不吞掉异常

## 函数设计

- 每个函数只做一件事，超过 40 行考虑拆分
- 纯命令执行函数（`commands.py`）不直接调用菜单 UI，UI 层调用命令层，单向依赖
- 耗时操作必须通过 `runner.py` 封装，支持实时输出

## 注释规范

- 枚举常量注释写在**每行上方**，禁止行尾注释
- 函数 docstring 格式：

```python
def build_docker(image: str, platform: str, tag: str) -> bool:
    """
    构建 Docker 镜像。

    :param image: 镜像名（含 namespace）
    :param platform: 目标平台，如 linux/amd64
    :param tag: 镜像 tag
    :return: 成功返回 True，失败返回 False
    """
```

## 禁止事项

- 禁止 `os.system()`，统一使用 `subprocess.run()` 或 `runner.py` 封装
- 禁止硬编码 Registry、镜像名、API 地址等配置，必须从 `core/config.py` 读取
- 禁止在 `commands.py` 中直接调用 InquirerPy 或 Rich 的交互组件
- 禁止 `print()`，统一使用 `console.print()`（Rich Console）
