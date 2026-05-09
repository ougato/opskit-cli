# CentOS 双 WireGuard 隧道冲突根因分析

## 环境

| 主机 | IP | 角色 |
|---|---|---|
| centos | icerror.top | WireGuard 客户端（同时运行两个 WG 接口） |
| hongkong | 104.219.214.78 | WG 服务端 A（10.0.0.0/24） |
| aliyun | 47.109.207.95 | WG 服务端 B（10.10.10.0/24） |

## 故障现象

在 centos 上同时启动 `SERVER-CENTOS`（连 hongkong）和 `wg0`（连 aliyun）后：

- `ping 10.10.10.1`（aliyun）→ ✅ 正常
- `ping 10.0.0.1`（hongkong）→ ❌ 100% 丢包

单独运行 `SERVER-CENTOS` 时一切正常，问题仅在启动 `wg0` 后出现。

## 根因：opskit 覆盖了已有 Xray 配置文件

### 原始状态（安装 wg0 之前）

```
centos 上的 Xray 配置（/usr/local/etc/xray/config.json）：
  inbound:  127.0.0.1:4000 (dokodemo-door, UDP)
  outbound: VLESS+REALITY → hongkong:3443

SERVER-CENTOS.conf:
  Endpoint = 127.0.0.1:4000  → 通过 Xray → hongkong
```

流量路径：
```
SERVER-CENTOS → UDP → 127.0.0.1:4000 → Xray → hongkong:3443 → hongkong WG(3002) → 10.0.0.1 ✅
```

### 安装 wg0 后（opskit 执行 `write_file(XRAY_CONFIG_FILE, xray_cfg)`）

opskit 的 `client.py` 第 147 行直接调用 `write_file()` **整体覆盖**了 Xray 配置文件：

```python
# client.py L139-147
xray_cfg = xray_client_config(
    sni=sni,
    server_port=server_port,
    uuid=uuid,
    local_port=local_port,   # 固定值 4000
    wg_port=wg_port,          # 固定值 3002
)
write_file(XRAY_CONFIG_FILE, xray_cfg)  # ← 整体覆盖！
```

覆盖后的 Xray 配置：
```
  inbound:  127.0.0.1:4000 (dokodemo-door, UDP)   ← 端口没变
  outbound: VLESS+WS+TLS → aliyun:443              ← 目的地变了！
```

### 覆盖后的流量路径

```
SERVER-CENTOS → UDP → 127.0.0.1:4000 → Xray → aliyun:443 → aliyun WG(3002)
                                                              ↓
                                        aliyun WG 不认识 SERVER-CENTOS 的公钥 → 丢弃 ❌

wg0 → UDP → 127.0.0.1:4000 → Xray → aliyun:443 → aliyun WG(3002) → 10.10.10.1 ✅
```

**本质**：两个 WG 接口共用同一个 Xray 隧道（127.0.0.1:4000），但隧道只连接 aliyun，不再连接 hongkong。

## 问题链

```
1. opskit 使用固定端口 4000（CLIENT_XRAY_LOCAL_PORT 常量）
2. opskit 不检测端口是否已被占用
3. opskit 不检测是否已有 Xray 配置文件
4. write_file() 直接覆盖，不合并
5. 覆盖后原有隧道（→ hongkong）被替换为新隧道（→ aliyun）
6. SERVER-CENTOS 的 endpoint 仍然指向 4000，但流量到了错误的目的地
```

## 修复方案

### 已实施的修复

构建双 inbound + 双 outbound + 路由规则的 Xray 配置：

```
inbound "wg-aliyun"    127.0.0.1:4000 → routing → outbound "proxy-aliyun"    → aliyun:443
inbound "wg-hongkong"  127.0.0.1:4001 → routing → outbound "proxy-hongkong"  → hongkong:3443
```

同时修改 `SERVER-CENTOS.conf` 的 Endpoint 为 `127.0.0.1:4001`。

### 修复后验证

```
ping 10.0.0.1  → 195ms, 3/3 成功, 0% 丢包 ✅（hongkong 国际线路延迟正常）
ping 10.10.10.1 → 9ms,  3/3 成功, 0% 丢包 ✅（aliyun 内网直连）
```

## 结论

| 项目 | 说明 |
|---|---|
| 根因 | opskit 整体覆盖 Xray 配置文件，未检测已有配置 |
| 触发条件 | 系统上已有非 opskit 管理的 WG + Xray 隧道 |
| 影响 | 原有隧道静默失效，用户无感知 |
| 修复 | 双 inbound/outbound + 路由规则分流 + 不同本地端口 |
| 预防（待实现） | opskit 安装前检测端口占用，自动分配可用端口 |
