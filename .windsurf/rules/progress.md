# 进度条规则（硬性要求）

## 强制规则

所有多步骤操作（安装、卸载、备份等）必须使用 `core/progress.py` 的 `MultiStepProgress`，禁止使用其他进度条样式。

## 视觉规范

进度条字符：`█`（已完成）、`░`（未完成），宽度固定 20 格。

```
检测操作系统...          ████████████████████  100%  0.21s
安装 WireGuard...        ████████████████████  100%  3.14s
安装 xray...             ░░░░██░░░░░░░░░░░░░░        8.32s   ← 进行中（脉冲动画）
生成密钥对...            ████████░░░░░░░░░░░░   40%  2.07s   ← 失败时整行红色
```

进行中状态使用脉冲动画（`████` 高亮块在 `░` 区间来回移动），不显示百分比，只显示 elapsed。

## 描述文本对齐

**必须传入所有步骤的翻译后描述列表**，`MultiStepProgress` 自动计算最大显示宽度对齐，支持中英文混排（CJK 字符占 2 列宽）：

```python
steps = ["wireguard.step.check_os", "wireguard.step.install_wg", ...]
descs = [t(s) for s in steps]          # 先翻译，自动适配当前语言
with MultiStepProgress(descs) as sp:   # 传入列表，预计算对齐宽度
    sp.step(descs[0])
    ...
```

换语言（中/英/日）后无需修改任何代码，自动对齐。

## 操作页面标题（强制）

**所有安装/卸载/诊断操作页面，进度条前必须先显示标题**，使用 `print_action_title` 函数（Powerline 面包屑风格，与菜单标题完全一致）：

```python
from core.theme import print_action_title

print_action_title(["OpsKit", t("menu.software"), "WireGuard", t("software.install")])
with MultiStepProgress(descs) as sp:
    ...
```

`print_action_title` 封装在 `core/theme.py`，内部复用 `core/prompt._render_header`，改一处全局生效。

## 三种状态颜色

| 状态 | Token | 默认色 | 触发条件 |
|---|---|---|---|
| 进行中 | `progress.bar_active` | `#cdd6f4`（白/亮色） | `sp.step(desc)` 调用时，脉冲动画 |
| 完成 | `progress.bar_complete` | `#a6e3a1`（绿色） | 下一步 `sp.step()` 被调用，或 `with` 正常退出 |
| 失败 | 硬编码 `_RED` | `#f38ba8`（红色） | `with` 块内抛出异常，`__exit__` 自动标红，显示 50% |

**进行中必须用 `bar_active`（白色），不能用 `bar_complete`（绿色）**，否则进行中和完成视觉上无法区分。

## 时间格式

- **统一使用秒，保留两位小数**：`0.21s`、`3.14s`、`12.30s`
- 禁止使用 `0:00:08` 分秒格式
- 进行中和完成后均使用相同格式

## 完整用法模板

```python
from core.theme import print_action_title
from core.progress import MultiStepProgress
from core.i18n import t

step_keys = [
    "wireguard.step.check_os",
    "wireguard.step.install_wg",
    "wireguard.step.gen_keys",
]
descs = [t(k) for k in step_keys]

print_action_title(["OpsKit", t("menu.software"), "WireGuard", t("software.install")])
with MultiStepProgress(descs) as sp:
    sp.step(descs[0])
    check_os()

    sp.step(descs[1])
    install_wireguard()

    sp.step(descs[2])
    generate_keys()
# with 块正常退出时，最后一步自动标绿
```

## 不受此规则约束的组件

| 组件 | 用途 | 理由 |
|---|---|---|
| `spinner` | 短暂等待（检测软件、DNS 查询等） | 无步骤数，完成后自动消失 |
| `DownloadProgress` | 文件下载（显示速度+大小） | 需要专属的下载速度列 |

## 禁止事项

- 禁止使用旧 `StepProgress` 单行模式（已内部转发给 `MultiStepProgress`）
- 禁止在 `MultiStepProgress` 外使用 `TimeElapsedColumn`（会产生 `0:00:xx` 格式）
- 禁止在步骤行尾添加 ✅ ❌ 等图标（颜色已足够表达状态）
- 禁止在多步骤进度条中途 `clear_screen()`（会破坏 Live 渲染）
- 禁止不传 `descriptions` 列表（会导致对齐宽度按 `_MIN_DESC_WIDTH=16` 计算，可能错位）
- 禁止在 `MultiStepProgress` 内部嵌套 `DownloadProgress` 或其他 `Live` 组件（会冲突）
- 禁止在进度条前缺少 `print_action_title`（用户不知道当前在做什么）

## 操作完成后

所有步骤完成后，`with` 块退出，再：
1. `print_success(f"\n{msg}")` — 成功消息前加 `\n` 空行与进度条分离
2. 如有详细信息，用 `Panel` + `Table.grid` 展示（参考安装成功面板）
3. `pause()`（按任意键返回）
