# UI Panel 通用规范

本规则适用于 CLI 中所有操作页面（安装/卸载/诊断）的信息面板展示。

## 强制规则

1. **所有操作页面必须在进度条或 Panel 前调用 `print_action_title`**
2. **`print_action_title` 上方 1 个空行，下方 1 个空行**（函数内部已包含，调用方不额外打印）
3. **Panel 后不加 `_con.print()`**，`pause()` 自带 `\n` 前缀，已提供 1 个空行
4. **成功消息前必须有 1 个空行**：使用 `console.print()` 先打空行，再调 `print_success()`，禁止用 `f"\n{msg}"` 方式（Rich markup 会把 `\n` 截断）

## Panel 颜色三层规范

```python
_LABEL = "#7f849c"        # 标签列：柔和灰，可读不抢眼
_VALUE = "bold #cdd6f4"   # 值列：亮白加粗，一眼抓住重点
_SEC   = "bold #89b4fa"   # 分区标题（── 服务状态 ──）：蓝色加粗
```

### 用法

```python
def _lbl(s: str) -> Text:
    return Text(s, style=_LABEL)

def _val(s: str) -> Text:
    return Text(s, style=_VALUE)

def _sec(s: str) -> Text:
    return Text(f"── {s} ──", style=_SEC)
```

### 列定义（禁止使用 style="dim" 列）

```python
tbl = Table.grid(padding=(0, 2))
tbl.add_column(no_wrap=True)   # 标签列，不加 style="dim"
tbl.add_column(no_wrap=False)  # 值列
```

## Panel 边框颜色

| 用途 | 边框色 | 标题样式 |
|---|---|---|
| 安装成功 | `#a6e3a1`（绿色） | `bold #a6e3a1` |
| 诊断信息 | `#89b4fa`（蓝色） | `bold` 白色 |
| 错误/警告 | `#f38ba8`（红色） | `bold #f38ba8` |

## 服务状态圆点

```python
def _dot(ok: bool) -> Text:
    return Text("● active", style="#a6e3a1") if ok else Text("● inactive", style="#f38ba8")
```

## 字符串截断

```python
def _trunc(s: str, n: int = 42) -> str:
    return s if len(s) <= n else s[:n] + "..."
```

## 完整页面布局模板

```
（空行）← print_action_title 头部空行
 OpsKit  模块  子模块  操作   ← Powerline 标题
（空行）← print_action_title 尾部空行
╭─────────────── 标题 ───────────────╮
│                                    │
│  ── 分区标题 ──                    │
│  标签    值                        │
│                                    │
╰────────────────────────────────────╯
（空行）← pause() 自带 \n
按任意键返回菜单...
```

## 新增操作页面检查清单

- [ ] `print_action_title([...])` 在进度条/Panel 前调用
- [ ] `Table.grid` 列定义不含 `style="dim"`
- [ ] 标签用 `_lbl()`，值用 `_val()`，分区用 `_sec()`
- [ ] Panel 后**不加** `_con.print()`（由 `pause()` 提供空行）
- [ ] 成功消息：`_con2.print(); print_success(msg)` 两行，不用 `f"\n{msg}"`
- [ ] i18n key 命名：`wireguard.xxx_diagnose.*`，与模块前缀保持一致
