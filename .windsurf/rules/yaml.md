# YAML 字符串引号规则

## 强制规则

所有 `core/locale/*.yaml` 和 `core/mirrors/sources.yaml` 以及 `core/themes/*.yaml` 文件中，**所有字符串值（value）必须使用双引号包裹**。

## 原因

YAML 1.1（PyYAML 默认）将以下裸词解析为非字符串类型，导致运行时 bug：

| 裸词 | 被解析为 |
|---|---|
| `yes` / `no` | `True` / `False`（布尔） |
| `on` / `off` | `True` / `False`（布尔） |
| `true` / `false` | `True` / `False`（布尔） |
| `null` / `~` | `None` |
| 纯数字 `123` | `int` |
| 小数 `1.5` | `float` |

## 规则细节

1. **所有 value 加双引号**，包括中文、英文、数字、空字符串
2. **key 本身不需要加引号**，除非 key 本身是 YAML 保留字（如 `yes`/`no`）
3. **key 是保留字时也必须加双引号**（如 `"yes":`、`"no":`）
4. 多行字符串使用 `|` 或 `>` 块标量，无需双引号
5. 已有双引号的不得重复添加

## 示例

```yaml
# 错误
prompt:
  yes: 确认
  no: 取消
  select: 请选择

# 正确
prompt:
  "yes": "确认"
  "no": "取消"
  select: "请选择"
```

## 检查方式

用以下脚本检测 locale yaml 中是否存在未加引号的裸字符串 value：

```bash
python3 scripts/fix_yaml_quotes.py --check core/locale/zh.yaml core/locale/en.yaml
```

## 执行修复

```bash
python3 scripts/fix_yaml_quotes.py core/locale/zh.yaml core/locale/en.yaml
```
