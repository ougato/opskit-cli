# 插件间服务（provides / uses）+ 多选控件

## 功能概述

- 平台新增插件间服务机制：插件在 plugin.yaml 声明 `provides: [<服务名>]` 对外提供能力，
  其他插件声明 `uses: [{service, source}]` 并通过 SDK `get_service()` /
  `open_service_menu()` 使用，无需依赖提供方包名；提供方未安装且给了 source 时
  引导用户确认安装并走标准信任流程。
- 平台新增多选交互控件 `multi_select`：↑↓ 移动光标、空格勾选、回车确认，
  默认全不选；0 返回 None，ESC 抛 UserCancel。
- SDK 新增 `ensure_python_package`：插件按需声明重依赖（如 boto3），已可导入
  静默返回 True，否则用应用 venv 的 pip 安装。

## 涉及文件

- `core/plugin.py`：PluginManifest 新增 `provides` / `uses` 字段与清单解析
- `core/plugin_services.py`：新建。服务发现 / 解析 / 缓存 / 引导安装 /
  `MenuService` Protocol
- `core/prompt.py`：新增 `_read_key_seq()`（方向键跨平台归一化）与 `multi_select()`
- `core/sdk.py`：导出 `multi_select` / `get_service` / `open_service_menu` /
  `ensure_python_package`
- `plugin/commands.py`：install / update / remove 后调用 `invalidate_service_cache()`
- `plugin/menu.py`：`_confirm_trust` 改公开 `confirm_trust`（引导安装复用）
- `core/locale/zh.yaml` / `en.yaml`：新增 `service.*` 文案
- `docs/plugin-spec.md`、`.windsurf/plans/001.md`：规范与架构文档同步
- `tests/test_plugin_services.py`：新建守卫测试

## 核心实现流程

1. `get_service(name)`：进程内缓存命中直接返回 → `list_manifests()` 找
   `provides` 含 name 的提供方 → CHECKSUMS 校验 + 信任校验 → import entry 包 →
   调用 `provide_service(name)` → 缓存并返回；任何失败返回 None（写日志）。
2. `open_service_menu(name, breadcrumb, context, source)`：get_service 失败且
   有 source 时确认安装（`plugin.commands.install` + `confirm_trust`，拒绝回滚）→
   `isinstance(svc, MenuService)` 校验后调用 `svc.open_menu(breadcrumb, context)`。
3. `multi_select`：raw 模式读键，Unix ESC 后 50ms 内跟 `[` 判为 ANSI 方向键，
   Windows 走 msvcrt `\x00`/`\xe0` 前缀；渲染沿用 Powerline 风格（reverse 高亮光标行）。

## 跨平台处理

- 方向键：Unix ANSI 序列 / Windows msvcrt 双路径
- 非 TTY（管道）下读键失败直接返回 None，避免死循环

## 已知限制

- 服务提供方仅限 python 插件（exec 插件无进程内 API）
- `service_api_version` 兼容性由调用方自行校验

## 测试验证

```bash
python3 -m pytest tests/test_plugin_services.py tests/test_plugin.py
python3 -m ruff check core/plugin_services.py core/prompt.py core/plugin.py core/sdk.py plugin/
```
