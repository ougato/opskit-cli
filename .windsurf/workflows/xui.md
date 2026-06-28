# x-ui 自动化模块实现流程

## 功能概述

在软件管理的运维工具分类中新增 x-ui 自动化入口，采用父菜单 `xui` 与隐藏子项 `xui_server`。服务端向导支持安装 x-ui / 3x-ui、生成 VLESS REALITY XHTTP 入站、可选生成 Trojan 入站、输出分享链接，并将状态保存为 0600 权限的脱敏可诊断文件。

## 涉及文件

- `xui/constants.py`
- `xui/links.py`
- `xui/templates.py`
- `xui/utils.py`
- `xui/server.py`
- `software/recipes/xui/recipe.py`
- `core/locale/zh.yaml`
- `core/locale/en.yaml`
- `core/themes/catppuccin.yaml`
- `pyproject.toml`
- `build.py`
- `tests/test_xui_recipe.py`
- `tests/test_xui_links.py`
- `tests/test_xui_templates.py`

## 核心实现流程

1. `XuiRecipe` 作为父级菜单，只负责展示子菜单。
2. `XuiServerRecipe` 作为隐藏子项，承载安装、卸载、诊断、管理入口。
3. 安装向导读取面板端口、用户、密码、公网主机、VLESS 端口、SNI、XHTTP 路径与 Trojan 选项。
4. `xui/templates.py` 生成 VLESS REALITY XHTTP 与 Trojan 入站模板。
5. `xui/links.py` 生成客户端可导入的分享链接。
6. `xui/utils.py` 通过面板 API 添加入站；失败时写入待导入 JSON 文件。
7. 状态文件写入 `/etc/x-ui/opskit-state.json`，权限固定为 0600，诊断时脱敏敏感字段。

## 跨平台处理

当前 x-ui 服务端自动化仅支持 Linux。Recipe 的 `platforms` 限定为 `linux`，安装入口也会检查 `sys.platform`。

## 已知限制

- x-ui 面板 API 依赖本机面板已可访问；若 API 添加失败，会保存待导入入站配置。
- Trojan 证书自动签发不在本阶段实现范围内。
- 安装脚本使用上游 3x-ui 安装脚本，具体交互行为取决于上游脚本版本。

## 测试验证方法

```bash
PYTHONPATH=/root/opskit-cli uv run --no-project --with pytest --with rich --with pyyaml --with psutil --with httpx pytest tests/test_xui_recipe.py tests/test_xui_links.py tests/test_xui_templates.py -q
```
