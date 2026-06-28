# x-ui 自动化方案：VLESS-Reality-XHTTP 与 Trojan

## 1. 目标

在 OpsKit 的「软件管理 -> 运维工具」分类中新增 `x-ui` 自动化能力。该能力参考现有 WireGuard Recipe 的父子菜单、安装向导、诊断、管理和状态保存模式，为用户自有服务器提供 x-ui/3x-ui 的安装与节点配置自动化。

目标能力：

- 一键安装、卸载、诊断、管理 x-ui/3x-ui。
- 自动创建 VLESS + REALITY + XHTTP 入站。
- 可选创建 Trojan 入站，作为备用节点。
- 输出可复制的 VLESS 和 Trojan 分享链接。
- 将生成的节点信息保存到 root-only 状态文件。
- 复用 WireGuard 模块中的 Recipe、state、diagnose、manage、token/share-link 等设计思路。

范围说明：该功能用于用户自有服务器上的网络代理/隧道配置自动化。CLI 应提示用户遵守当地法律法规和服务商条款。

## 2. 项目现状参考

相关文件：

```text
software/menu.py
software/registry.py
software/base.py
software/recipes/*/recipe.py
software/recipes/wireguard/recipe.py
wireguard/server.py
wireguard/client.py
wireguard/templates.py
wireguard/token.py
wireguard/constants.py
.windsurf/architecture/recipe-system.md
.windsurf/architecture/wireguard-ws-tls.md
```

WireGuard 已经提供了本功能需要参考的整体模式：

- 父 Recipe 使用 `has_submenu=True`，作为菜单容器。
- 隐藏子 Recipe 承载真实安装、诊断、管理逻辑。
- 安装向导使用 `has_wizard=True`。
- 诊断入口使用 `has_diagnose=True`。
- 管理入口使用 `has_manage=True`。
- 通过 state 文件保存运行时生成的配置。
- 通过 token/share-link 形式输出客户端可导入的信息。

## 3. 建议模块结构

新增目录：

```text
xui/
  __init__.py
  constants.py
  templates.py
  server.py
  links.py
  utils.py

software/recipes/xui/
  __init__.py
  recipe.py
```

建议新增测试：

```text
tests/test_xui_recipe.py
tests/test_xui_links.py
tests/test_xui_templates.py
tests/test_xui_state_redaction.py
```

## 4. Recipe 设计

### 4.1 父级 Recipe：XuiRecipe

父级 Recipe 只负责在「运维工具」分类中展示 `x-ui` 菜单，并进入子菜单。

```python
@register
class XuiRecipe(Recipe):
    key: ClassVar[str] = "xui"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "x-ui / 3x-ui 面板"
    platforms: ClassVar[list[str]] = ["linux"]

    has_upgrade: ClassVar[bool] = False
    has_diagnose: ClassVar[bool] = False
    has_submenu: ClassVar[bool] = True
    has_wizard: ClassVar[bool] = False

    def detect(self) -> str | None:
        return None

    def versions(self) -> list[str]:
        return ["latest"]

    def install(self, version: str) -> None:
        pass

    def uninstall(self) -> None:
        pass

    def submenu_items(self) -> list[dict]:
        return [
            {"key": "xui_server", "label_key": "software.xui_server"},
        ]
```

### 4.2 隐藏子项：XuiServerRecipe

`xui_server` 作为真正执行安装、卸载、诊断、管理的隐藏子项。

```python
@register
class XuiServerRecipe(Recipe):
    key: ClassVar[str] = "xui_server"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "x-ui VLESS REALITY XHTTP / Trojan 服务端"
    platforms: ClassVar[list[str]] = ["linux"]
    dependencies: ClassVar[list] = [{"key": "python", "min": "3.10"}]

    has_upgrade: ClassVar[bool] = False
    has_diagnose: ClassVar[bool] = True
    has_manage: ClassVar[bool] = True
    has_submenu: ClassVar[bool] = False
    has_wizard: ClassVar[bool] = True
    hidden: ClassVar[bool] = True

    def detect(self) -> str | None:
        # 检查 systemctl 状态和 x-ui 版本。
        ...

    def versions(self) -> list[str]:
        return ["latest"]

    def steps(self, action: str = "install") -> list[InstallStep]:
        if action == "uninstall":
            return [
                InstallStep("software.step.stop_service"),
                InstallStep("software.step.remove_files"),
                InstallStep("software.step.cleanup"),
            ]
        return [
            InstallStep("xui.step.check_os"),
            InstallStep("xui.step.install_deps"),
            InstallStep("xui.step.install_xui"),
            InstallStep("xui.step.generate_credentials"),
            InstallStep("xui.step.configure_panel"),
            InstallStep("xui.step.create_vless_xhttp"),
            InstallStep("xui.step.create_trojan"),
            InstallStep("xui.step.start_service"),
            InstallStep("xui.step.verify"),
            InstallStep("xui.step.print_links"),
        ]

    def install(self, version: str) -> None:
        from xui.server import install_server
        install_server()

    def uninstall(self) -> None:
        from xui.server import uninstall_server
        uninstall_server()

    def diagnose(self) -> None:
        from xui.server import diagnose_server
        diagnose_server()

    def manage(self) -> None:
        from xui.server import manage_nodes
        manage_nodes()
```

## 5. 安装向导设计

安装向导需要收集或自动生成以下配置：

1. 面板端口：默认 `54321`。
2. 节点端口：默认 `443`；如被占用，建议切换到 `8443`。
3. 面板用户名：用户输入或自动生成。
4. 面板密码：自动生成强密码，只在安装完成时显示一次，并写入 root-only state。
5. 公网主机/IP：自动检测，允许用户手动覆盖。
6. REALITY SNI/serverName：默认 `www.cloudflare.com`，允许自定义。
7. REALITY dest：默认 `{sni}:443`。
8. REALITY shortId：自动生成 8 字节 hex。
9. VLESS UUID：自动生成 UUID。
10. XHTTP path：默认 `/xhttp-{short}`。
11. Trojan password：自动生成强密码。
12. Trojan 端口：默认 `8443`。
13. 是否放行防火墙端口。
14. 是否开启 BBR。

建议安装步骤：

```text
xui.step.check_os
xui.step.install_deps
xui.step.install_xui
xui.step.generate_credentials
xui.step.configure_panel
xui.step.create_vless_xhttp
xui.step.create_trojan
xui.step.start_service
xui.step.verify
xui.step.print_links
```

## 6. x-ui 安装策略

MVP 可以使用 3x-ui 官方安装脚本，但需要封装在 `xui/utils.py` 中，避免把脚本调用散落在业务逻辑里。

上游安装命令：

```bash
bash <(curl -Ls https://raw.githubusercontent.com/MHSanaei/3x-ui/master/install.sh)
```

实现建议：

- 先检查 `curl`、`systemctl`、`sqlite3` 和网络可用性。
- 如果上游提供稳定 release，优先使用固定版本或可追踪版本 URL。
- 日志中记录安装来源 URL 和版本。
- 不在日志中打印生成的密码、private key、Trojan password。
- 安装后执行 `systemctl enable --now x-ui`。

## 7. VLESS-Reality-XHTTP 入站模板

新增 `xui/templates.py`，参考 WireGuard 中的 Xray 配置模板。

```python
def vless_reality_xhttp_inbound(
    port: int,
    uuid: str,
    private_key: str,
    short_id: str,
    sni: str,
    dest: str,
    path: str,
) -> dict:
    return {
        "remark": "opskit-vless-reality-xhttp",
        "protocol": "vless",
        "port": port,
        "settings": {
            "clients": [{"id": uuid, "email": "opskit-vless"}],
            "decryption": "none",
        },
        "streamSettings": {
            "network": "xhttp",
            "security": "reality",
            "realitySettings": {
                "show": False,
                "dest": dest,
                "xver": 0,
                "serverNames": [sni],
                "privateKey": private_key,
                "shortIds": [short_id],
            },
            "xhttpSettings": {
                "path": path,
                "mode": "auto",
            },
        },
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
    }
```

实现阶段需要确认当前 3x-ui API 对 `xhttpSettings` 字段名、入站结构和 Xray core 版本的兼容性。

## 8. Trojan 入站模板

Trojan 建议作为可选能力，因为它通常需要 TLS 证书处理。

```python
def trojan_inbound(port: int, password: str, sni: str) -> dict:
    return {
        "remark": "opskit-trojan",
        "protocol": "trojan",
        "port": port,
        "settings": {
            "clients": [{"password": password, "email": "opskit-trojan"}],
        },
        "streamSettings": {
            "network": "tcp",
            "security": "tls",
            "tlsSettings": {"serverName": sni},
        },
        "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
    }
```

MVP 建议：

- 默认启用 VLESS REALITY XHTTP。
- Trojan 仅在用户提供域名/证书，或确认启用 ACME 时创建。
- ACME 自动化可以放到后续阶段。

## 9. 分享链接生成

新增 `xui/links.py`。

### 9.1 VLESS 链接

格式：

```text
vless://{uuid}@{host}:{port}?type=xhttp&security=reality&pbk={public_key}&fp=chrome&sni={sni}&sid={short_id}&path={urlencoded_path}&mode=auto#opskit-vless-xhttp
```

必需字段：

- `uuid`
- `host`
- `port`
- `public_key`
- `sni`
- `short_id`
- `path`

### 9.2 Trojan 链接

格式：

```text
trojan://{password}@{host}:{port}?security=tls&sni={sni}&type=tcp#opskit-trojan
```

## 10. 状态文件设计

推荐路径：

```text
~/.opskit/state/xui/server.json
```

权限：`0600`。

示例结构：

```json
{
  "status": "installed",
  "panel_port": 54321,
  "vless": {
    "host": "1.2.3.4",
    "port": 443,
    "uuid": "...",
    "public_key": "...",
    "short_id": "...",
    "sni": "www.cloudflare.com",
    "path": "/xhttp-abcd",
    "link": "vless://..."
  },
  "trojan": {
    "host": "1.2.3.4",
    "port": 8443,
    "password": "...",
    "sni": "example.com",
    "link": "trojan://..."
  }
}
```

诊断输出中不得打印面板密码、Trojan password、REALITY private key 或原始 state JSON。

## 11. 管理菜单设计

`manage_nodes()` 建议提供：

1. 查看节点摘要。
2. 重新打印 VLESS/Trojan 分享链接。
3. 新增 VLESS 用户。
4. 新增 Trojan 用户。
5. 轮换 UUID/shortId。
6. 重启 x-ui。
7. 查看 x-ui 日志。
8. 导出脱敏后的 state。

## 12. 诊断逻辑

`diagnose_server()` 应检查：

- 操作系统是否支持。
- `systemctl is-active x-ui`。
- 面板端口是否监听。
- VLESS/Trojan 端口是否监听。
- 防火墙规则：ufw/firewalld/iptables。
- Xray core 版本是否支持 XHTTP。
- state 文件是否存在且权限为 `0600`。
- 分享链接字段是否完整。

## 13. i18n 文案

新增到 `core/locale/zh.yaml` 和 `core/locale/en.yaml`：

```yaml
software.xui: "x-ui"
software.xui_server: "x-ui 服务端"
xui.step.check_os: "检查操作系统"
xui.step.install_deps: "安装依赖"
xui.step.install_xui: "安装 x-ui"
xui.step.generate_credentials: "生成凭据"
xui.step.configure_panel: "配置面板"
xui.step.create_vless_xhttp: "创建 VLESS REALITY XHTTP 入站"
xui.step.create_trojan: "创建 Trojan 入站"
xui.step.start_service: "启动服务"
xui.step.verify: "验证安装"
xui.step.print_links: "输出分享链接"
```

英文文件中保留对应英文翻译即可。

## 14. 测试计划

- `test_xui_recipe_registered`：注册表包含 `xui` 和 `xui_server`。
- `test_xui_parent_submenu`：父 Recipe 返回 `xui_server` 子菜单项。
- `test_vless_xhttp_link_generation`：VLESS URL 包含必要 query 参数，并正确 URL encode path。
- `test_trojan_link_generation`：Trojan URL 包含必要 query 参数。
- `test_xui_templates_shape`：入站 JSON 包含必要协议、stream、client 字段。
- `test_xui_state_redaction`：脱敏逻辑隐藏 password/privateKey 字段。

## 15. 实施阶段

### Phase 1：计划与骨架

- 提交本文档。
- 新增 Recipe package 骨架。
- 新增 i18n keys。
- `install/manage/diagnose` 先提供占位提示。

### Phase 2：链接与模板工具

- 实现 `xui/templates.py`。
- 实现 `xui/links.py`。
- 增加单元测试。

### Phase 3：安装向导

- 安装 3x-ui。
- 生成 REALITY keys、UUID、shortId、Trojan password。
- 写入 state 文件。
- 输出 VLESS 分享链接。

### Phase 4：x-ui API 集成

- 调研当前 3x-ui API。
- 通过 API 创建 inbound。
- 增加诊断和管理操作。

### Phase 5：证书与安全加固

- 增加 Trojan TLS 的可选 ACME 自动化。
- 增加防火墙确认提示。
- 增加面板公网暴露风险提示和访问限制。
