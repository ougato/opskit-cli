# WireGuard 双接口冲突诊断

## 现象

- `ping 10.10.10.1`（wg0）→ ✅ 正常 8ms
- `ping 10.0.0.1`（SERVER-CENTOS）→ ❌ 100% 丢包

## 根因

两个 WireGuard 接口的 peer endpoint 都是 `127.0.0.1:4000`（同一个 Xray 隧道）：

```
SERVER-CENTOS peer endpoint: 127.0.0.1:4000 → Xray → aliyun:443 → WG port 3002
wg0           peer endpoint: 127.0.0.1:4000 → Xray → aliyun:443 → WG port 3002
```

**Xray dokodemo-door 只有一条隧道**，固定将 UDP 转发到远端 `127.0.0.1:3002`。远端 aliyun 的 WireGuard 监听 3002 端口，只配置了 wg0 对应的 peer（10.10.10.0/24），不认识 SERVER-CENTOS 的公钥，所以丢弃。

## 解决方案

取决于 SERVER-CENTOS 连的是哪个远端服务器：

**情况 A：SERVER-CENTOS 也连 aliyun**
→ 在 aliyun 的 WireGuard wg0 里添加 SERVER-CENTOS 的 peer，允许 10.0.0.0/24。两个接口可以共享同一条 Xray 隧道。

**情况 B：SERVER-CENTOS 连的是另一台服务器（非 aliyun）**
→ 需要第二条 Xray 隧道（不同本地端口），例如：
- Xray inbound 2: `127.0.0.1:4001` → 远端服务器 B
- SERVER-CENTOS peer endpoint 改为 `127.0.0.1:4001`

**情况 C：不再需要 SERVER-CENTOS**
→ 直接停用：`wg-quick down SERVER-CENTOS`

---

## 即时修复方案（centos 双隧道共存）

### 实际问题

opskit 安装 wg0 客户端时，**覆盖了原有 Xray 配置文件**：

```
原配置：inbound 4000 → outbound → hongkong:443（给 SERVER-CENTOS）
被覆盖后：inbound 4000 → outbound → aliyun:443（给 wg0）
```

结果：SERVER-CENTOS 的 UDP 流量仍然发到 127.0.0.1:4000，但 Xray 把它转发到了 aliyun，aliyun 的 WireGuard 不认识 SERVER-CENTOS 的公钥 → 丢弃。

### 修复方案：双 inbound + 双 outbound + 路由规则

```
inbound "wg-aliyun"  (127.0.0.1:4000) → route → outbound "proxy-aliyun"  → aliyun:443
inbound "wg-hongkong" (127.0.0.1:4001) → route → outbound "proxy-hongkong" → hongkong:443
```

同时修改 SERVER-CENTOS.conf：`Endpoint = 127.0.0.1:4001`

### 需要用户提供

hongkong 服务器的 Xray 连接参数（被覆盖前的原始配置）：
- 地址/域名
- 端口
- UUID
- WS path
- SNI（如有）

---

## 长期方案：自动端口分配（opskit 代码层面）

### 问题本质

用户系统上可能已有其他 WireGuard 接口（非 opskit 管理），它们占用了 `127.0.0.1:4000`。opskit 安装新的 WG 客户端时，如果盲目使用固定端口 4000，会导致：
1. Xray 启动失败（端口被占）
2. 或两个 WG 接口共用同一隧道，数据到达错误目的地

### 方案设计

```
安装流程：
1. 检测 127.0.0.1:4000 是否已被监听（ss -tlnp 检查 TCP）
2. 如已占用 → 自动递增查找可用端口（4001, 4002, ...）
3. 用找到的可用端口生成 Xray 配置（dokodemo-door inbound）
4. WG 客户端配置的 Endpoint 使用该端口
5. 保存实际使用的端口到 state 文件，供诊断/卸载使用
```

### 改动文件

| 文件 | 改动 |
|---|---|
| `wireguard/utils.py` | 新增 `find_available_port(start=4000)` 函数 |
| `wireguard/client.py` L88-89 | `local_port` 改为调用 `find_available_port()` 而非固定常量 |
| `wireguard/client.py` L196-205 | state 文件中保存实际分配的端口 |

### 函数设计

```python
def find_available_port(start: int = 4000, max_attempts: int = 100) -> int:
    """从 start 开始查找未被占用的本地 TCP 端口"""
    import socket
    for offset in range(max_attempts):
        port = start + offset
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"无法找到可用端口（{start}-{start + max_attempts}）")
```

### 流程图

```
用户执行 opskit → 安装 WG 客户端 → 粘贴令牌
                                         ↓
                              检测端口 4000 是否空闲
                             /                    \
                          空闲                   被占用
                           ↓                       ↓
                     使用 4000              递增查找 4001, 4002...
                           ↓                       ↓
                  生成 Xray 配置（端口=实际分配值）
                           ↓
                  生成 WG 配置（Endpoint=127.0.0.1:实际端口）
                           ↓
                  保存端口到 state → 启动服务 → 验证连通
```

### 影响范围

- **改动量**：~15 行新增 + ~3 行修改
- **风险**：极低（仅影响新安装，不影响已安装实例）
- **向后兼容**：已安装的客户端 state 中没有 port 字段时，默认按 4000 处理
