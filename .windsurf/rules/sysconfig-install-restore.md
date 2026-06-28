# 系统配置安装快照与智能还原规则

## 核心原则

安装软件时，OpsKit 修改的所有系统配置必须通过 `core.sysconfig.SysConfigManager` 进行管理，确保卸载时能智能还原，让用户感觉"从未安装过"。

## SysConfigManager 接口

```python
from core.sysconfig import SysConfigManager

# 安装时（在任何系统修改之前调用）
SysConfigManager.save(
    recipe_key="wg_server",
    sysparams={"net.ipv4.ip_forward": "1"},   # key: 参数名, value: opskit 要写入的值
    pre_install={"nginx_installed_by_opskit": True},  # 其他安装前状态
)

# 卸载时（在删除 opskit 文件之后调用）
pre = SysConfigManager.restore("wg_server")
# restore() 自动处理 sysctl 参数的智能还原
# 返回 pre_install dict 供调用方处理其他还原（如软链接等）

# 卸载全部完成后
SysConfigManager.remove("wg_server")
```

## 智能还原规则

- **当前值 == opskit 安装时写入的值** → 安全还原为安装前原值
- **当前值 != opskit 安装时写入的值** → 用户手动改过，不干预，保留用户设置
- **多软件共用同一参数**（如 docker 和 wg_server 都需要 `ip_forward=1`）→ 引用计数，只有最后一个软件卸载时才还原
- **快照文件损坏/不存在** → 保守策略：只删 opskit 自己的文件，不改系统参数

## 快照存储位置

- 运行时文件：`get_data_dir() / "data" / "install_snapshots.json"`
- 打包模式：`/var/lib/opskit/data/install_snapshots.json`
- 开发模式：`<项目根>/data/install_snapshots.json`（已在 .gitignore 排除）

## 快照数据格式

```json
{
  "wg_server": {
    "status": "installed",
    "installed_at": "2026-05-03T18:00:00",
    "sysparams": {
      "net.ipv4.ip_forward": {
        "original": "0",
        "opskit_value": "1"
      }
    },
    "pre_install": {
      "nginx_installed_by_opskit": true
    }
  }
}
```

## 什么时候需要接入 SysConfigManager

- 安装时需要修改 **sysctl 内核参数**（如 `ip_forward`、`somaxconn` 等）→ 必须用 `save()`
- 安装时需要记录安装前状态（如某文件是否存在）→ 用 `pre_install` 字段
- 安装时只写 **opskit 独占文件**（如 `/etc/nginx/conf.d/vless-ws.conf`）→ 不需要，直接写即可

## OpsKit 独占文件原则

- opskit 只创建带自己命名的独立文件，绝不修改系统原有文件
- systemd 用 drop-in 目录（`wg-quick@wg0.service.d/`），不改系统 unit 文件
- nginx 用 `conf.d/` 独立文件，不改用户已有 nginx 配置
- sysctl 用独立的 `/etc/sysctl.d/99-{name}.conf`，不改 `/etc/sysctl.conf`

## 当前已接入的 Recipe

| Recipe | sysparams | pre_install 字段 |
|---|---|---|
| `wg_server` | `net.ipv4.ip_forward` | `nginx_installed_by_opskit` |
| `wg_client` | 无 | 无（客户端不修改系统参数） |
| `xui_server` | `net.core.default_qdisc`、`net.ipv4.tcp_congestion_control` | 无 |

## 禁止事项

- 禁止在卸载时硬编码 `sysctl -w param=0`，必须通过 `SysConfigManager.restore()` 处理
- 禁止直接修改 `/etc/sysctl.conf`、`/etc/environment` 等系统共享文件
- 禁止在 Recipe 基类（`software/base.py`）中耦合 SysConfigManager
