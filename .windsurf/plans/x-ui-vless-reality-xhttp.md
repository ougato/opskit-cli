# x-ui Automation Plan: VLESS-Reality-XHTTP and Trojan

## 1. Goal

Add an `x-ui` automation entry under OpsKit `Software Management -> DevOps Tools`.
The feature should follow the existing WireGuard recipe pattern and provide a safe, repeatable installer/manager for x-ui or 3x-ui.

Target capabilities:

- Install, uninstall, diagnose, and manage x-ui/3x-ui.
- Create a VLESS + REALITY + XHTTP inbound automatically.
- Create an optional Trojan inbound as a fallback node.
- Print copy-ready VLESS and Trojan share links.
- Store generated state in a root-only state file.
- Reuse the WireGuard module's recipe, state, diagnose, manage, and token/share-link ideas where appropriate.

Scope note: this feature is for user-owned servers and network tunnel automation. The CLI should remind users to comply with local laws and provider policies.

## 2. Existing project references

Relevant files:

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

WireGuard already demonstrates the desired high-level pattern:

- Parent recipe with `has_submenu=True`.
- Hidden child recipes for real operations.
- Wizard install flow with `has_wizard=True`.
- Diagnose action with `has_diagnose=True`.
- Manage action with `has_manage=True`.
- State file for generated runtime details.
- Share/token generation for client import.

## 3. Proposed module layout

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

Recommended tests:

```text
tests/test_xui_recipe.py
tests/test_xui_links.py
tests/test_xui_templates.py
tests/test_xui_state_redaction.py
```

## 4. Recipe design

### 4.1 Parent recipe

```python
@register
class XuiRecipe(Recipe):
    key: ClassVar[str] = "xui"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "x-ui / 3x-ui panel"
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

### 4.2 Hidden server recipe

```python
@register
class XuiServerRecipe(Recipe):
    key: ClassVar[str] = "xui_server"
    category: ClassVar[str] = "devops"
    description: ClassVar[str] = "x-ui VLESS REALITY XHTTP / Trojan server"
    platforms: ClassVar[list[str]] = ["linux"]
    dependencies: ClassVar[list] = [{"key": "python", "min": "3.10"}]

    has_upgrade: ClassVar[bool] = False
    has_diagnose: ClassVar[bool] = True
    has_manage: ClassVar[bool] = True
    has_submenu: ClassVar[bool] = False
    has_wizard: ClassVar[bool] = True
    hidden: ClassVar[bool] = True

    def detect(self) -> str | None:
        # Check systemctl status and x-ui binary/version.
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

## 5. Wizard flow

Prompt for or generate:

1. Panel port: default `54321`.
2. Node port: default `443`; if occupied, suggest `8443`.
3. Panel username: user input or generated.
4. Panel password: generated strong password; print once and store root-only.
5. Public host/IP: auto-detect with manual override.
6. REALITY SNI/serverName: default `www.cloudflare.com`, editable.
7. REALITY dest: default `{sni}:443`.
8. REALITY shortId: generated 8-byte hex.
9. VLESS UUID: generated UUID.
10. XHTTP path: default `/xhttp-{short}`.
11. Trojan password: generated strong password.
12. Trojan port: default `8443`.
13. Optional firewall allow rules.
14. Optional BBR enablement.

## 6. x-ui installation strategy

Initial implementation can call the 3x-ui installer, wrapped in `xui/utils.py`:

```bash
bash <(curl -Ls https://raw.githubusercontent.com/MHSanaei/3x-ui/master/install.sh)
```

Implementation guidance:

- Check `curl`, `systemctl`, `sqlite3`, and network access first.
- Prefer pinned release URLs if the upstream project exposes stable releases.
- Log the installer URL and version.
- Avoid logging generated secrets.
- Start with `systemctl enable --now x-ui`.

## 7. VLESS-Reality-XHTTP inbound template

Add `xui/templates.py` with a helper similar to WireGuard's Xray templates.

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

Implementation caveat: verify the exact 3x-ui API payload shape and Xray core support for `xhttpSettings` before coding the final API call.

## 8. Trojan inbound template

Trojan should be optional in MVP because it normally needs TLS certificate handling.

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

MVP recommendation:

- Enable VLESS REALITY XHTTP by default.
- Add Trojan only when the user provides a domain/certificate or confirms ACME setup.
- Add ACME automation in a later phase.

## 9. Share link helpers

Add `xui/links.py`.

### 9.1 VLESS link

```text
vless://{uuid}@{host}:{port}?type=xhttp&security=reality&pbk={public_key}&fp=chrome&sni={sni}&sid={short_id}&path={urlencoded_path}&mode=auto#opskit-vless-xhttp
```

Required fields:

- `uuid`
- `host`
- `port`
- `public_key`
- `sni`
- `short_id`
- `path`

### 9.2 Trojan link

```text
trojan://{password}@{host}:{port}?security=tls&sni={sni}&type=tcp#opskit-trojan
```

## 10. State file

Recommended path:

```text
~/.opskit/state/xui/server.json
```

Permissions: `0600`.

Example shape:

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

Never print the panel password, Trojan password, REALITY private key, or raw state JSON in diagnose output.

## 11. Manage menu

`manage_nodes()` should offer:

1. Show node summary.
2. Reprint VLESS/Trojan share links.
3. Add VLESS client.
4. Add Trojan client.
5. Rotate UUID/shortId.
6. Restart x-ui.
7. Show x-ui logs.
8. Export redacted state.

## 12. Diagnose checks

`diagnose_server()` should check:

- OS support.
- `systemctl is-active x-ui`.
- Panel port listener.
- VLESS/Trojan port listeners.
- Firewall rules: ufw/firewalld/iptables.
- Xray core version and XHTTP support.
- State file exists and is `0600`.
- Share-link fields are complete.

## 13. i18n keys

Add keys to `core/locale/zh.yaml` and `core/locale/en.yaml`:

```yaml
software.xui: "x-ui"
software.xui_server: "x-ui server"
xui.step.check_os: "Check OS"
xui.step.install_deps: "Install dependencies"
xui.step.install_xui: "Install x-ui"
xui.step.generate_credentials: "Generate credentials"
xui.step.configure_panel: "Configure panel"
xui.step.create_vless_xhttp: "Create VLESS REALITY XHTTP inbound"
xui.step.create_trojan: "Create Trojan inbound"
xui.step.start_service: "Start service"
xui.step.verify: "Verify installation"
xui.step.print_links: "Print share links"
```

## 14. Test plan

- `test_xui_recipe_registered`: recipe registry contains `xui` and `xui_server`.
- `test_xui_parent_submenu`: parent recipe returns `xui_server` submenu item.
- `test_vless_xhttp_link_generation`: VLESS URL has required query parameters and URL-encoded path.
- `test_trojan_link_generation`: Trojan URL has required query parameters.
- `test_xui_templates_shape`: inbound JSON contains required protocol, stream, and client fields.
- `test_xui_state_redaction`: redaction hides password/privateKey fields.

## 15. Implementation phases

### Phase 1: plan and skeleton

- Commit this plan.
- Add recipe package skeleton.
- Add i18n keys.
- Add placeholder install/manage/diagnose methods.

### Phase 2: link and template helpers

- Implement `xui/templates.py`.
- Implement `xui/links.py`.
- Add unit tests.

### Phase 3: installer wizard

- Install 3x-ui.
- Generate REALITY keys, UUID, shortId, and Trojan password.
- Write state file.
- Print VLESS link.

### Phase 4: x-ui API integration

- Research current 3x-ui API.
- Create inbound through API.
- Add diagnose and manage operations.

### Phase 5: certificate and hardening

- Add optional ACME for Trojan TLS.
- Add firewall prompts.
- Add panel exposure warnings and access restrictions.
