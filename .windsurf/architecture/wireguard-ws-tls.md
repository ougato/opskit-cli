# WireGuard over VLESS+WS+TLS 完整部署方案

> 经过实测调通的完整方案，适用于 **阿里云 ECS（LVS Full-NAT 环境）** 通过域名（nginx 反代）建立 WireGuard 隧道。

---

## 1. 背景与根因

### 1.1 阿里云 LVS 限制

阿里云 ECS 公网 IP 经过 LVS Full-NAT 转发，**LVS 在 TCP 三次握手阶段改写 ACK 序列号**，导致：

- xray REALITY/TCP 直连 → 客户端 ACK 号被 LVS 改写 → xray 发 RST → `connection reset by peer`
- openssl s_client 直连 → ClientHello 后收到 RST
- **所有直连公网 IP 的 TCP 均受影响**

### 1.2 为何 nginx 不受影响

nginx 使用系统内核 TCP 栈，LVS 对内核 TCP 是透明的（LVS 维护完整 NAT 状态表），nginx 可以正常建立 TLS 连接。

### 1.3 为何 UDP 不受影响

WireGuard UDP / xray QUIC 均不受 LVS TCP 干扰，UDP 连接直通。

### 1.4 最终绕过方案

```
客户端 WireGuard
    ↓  UDP → 127.0.0.1:4000
客户端 xray (dokodemo-door → VLESS+WS+TLS outbound)
    ↓  TCP → {域名}:443（DNS 解析到 CDN 或直接到服务器，经 nginx 处理）
nginx 443 (TLS 终止 + WebSocket 反代)
    ↓  HTTP WS → 127.0.0.1:2443
服务端 xray (VLESS+WS inbound)
    ↓  UDP → 127.0.0.1:3002
服务端 WireGuard
    ↓  wg0 VPN 隧道
```

**关键**：客户端连接目标必须使用**域名**（SNI），让 DNS 解析走本地网络/CDN 路径，而不是直接连公网 IP `47.109.207.95`。

---

## 2. 服务端（阿里云 ECS）部署

### 2.1 环境信息（实测）

| 项目 | 值 |
|------|-----|
| 操作系统 | Debian 12 |
| 内网 IP | `172.19.53.40` |
| 公网 IP | `47.109.207.95` |
| 域名 | `aliyun.icerror.top`（解析到 `47.109.207.95`） |
| TLS 证书目录 | `/etc/nginx/ssl/` |
| xray 版本 | 26.3.27 |

### 2.2 证书申请（acme.sh）

```bash
# 安装 acme.sh
curl https://get.acme.sh | sh -s email=your@email.com

# 申请证书（HTTP-01 验证，需要 nginx 先监听 80）
~/.acme.sh/acme.sh --issue -d aliyun.icerror.top --webroot /usr/share/nginx/html

# 安装证书
mkdir -p /etc/nginx/ssl
~/.acme.sh/acme.sh --install-cert -d aliyun.icerror.top \
    --cert-file /etc/nginx/ssl/aliyun.icerror.top.cer \
    --key-file  /etc/nginx/ssl/aliyun.icerror.top.key \
    --reloadcmd "systemctl reload nginx"
```

### 2.3 xray 安装

```bash
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
# 或通过国内镜像
bash -c "$(curl -L https://mirror.ghproxy.com/https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
```

### 2.4 xray 服务端配置

文件路径：`/usr/local/etc/xray/config.json`

```json
{
  "log": {
    "loglevel": "warning",
    "access": "/var/log/xray/access.log",
    "error": "/var/log/xray/error.log"
  },
  "inbounds": [
    {
      "listen": "127.0.0.1",
      "port": 2443,
      "protocol": "vless",
      "settings": {
        "clients": [
          {
            "id": "3106437f-64ff-4654-8c4c-36fd327bcf26"
          }
        ],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "ws",
        "security": "none",
        "wsSettings": {
          "path": "/vless-ws"
        }
      },
      "sniffing": {
        "enabled": false
      }
    }
  ],
  "outbounds": [
    {
      "protocol": "freedom",
      "tag": "direct"
    }
  ]
}
```

> **要点**：xray 仅监听 `127.0.0.1:2443`（不暴露公网），TLS 由 nginx 处理。

### 2.5 nginx 配置

#### 主配置 `/etc/nginx/nginx.conf`

```nginx
user www-data;
worker_processes auto;
pid /run/nginx.pid;
error_log /var/log/nginx/error.log;

events {
    worker_connections 768;
}

http {
    sendfile on;
    tcp_nopush on;
    types_hash_max_size 2048;
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    access_log /var/log/nginx/access.log;
    gzip on;
    include /etc/nginx/conf.d/*.conf;
    include /etc/nginx/sites-enabled/*;
}
```

#### VLESS-WS 反代配置 `/etc/nginx/conf.d/vless-ws.conf`

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    listen 1443 ssl;
    listen [::]:1443 ssl;
    server_name aliyun.icerror.top;

    ssl_certificate     /etc/nginx/ssl/aliyun.icerror.top.cer;
    ssl_certificate_key /etc/nginx/ssl/aliyun.icerror.top.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    location /vless-ws {
        proxy_pass          http://127.0.0.1:2443;
        proxy_http_version  1.1;
        proxy_set_header    Upgrade $http_upgrade;
        proxy_set_header    Connection "upgrade";
        proxy_set_header    Host $host;
        proxy_set_header    X-Real-IP $remote_addr;
        proxy_read_timeout  86400s;
        proxy_send_timeout  86400s;
    }

    location / {
        return 200 "OK\n";
        add_header Content-Type text/plain;
    }
}
```

> **说明**：同时监听 443 和 1443，客户端可连任一端口。

### 2.6 WireGuard 服务端配置

文件路径：`/etc/wireguard/wg0.conf`

```ini
[Interface]
Address = 10.10.10.1/24
PostUp   = iptables -I FORWARD 1 -i wg0 -j ACCEPT; iptables -I FORWARD 1 -o wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -s 10.10.10.0/24 -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -D FORWARD -o wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -s 10.10.10.0/24 -o eth0 -j MASQUERADE
ListenPort = 3002
PrivateKey = <服务端 WG 私钥>

[Peer]
PublicKey  = <客户端 WG 公钥>
PresharedKey = <PSK>
AllowedIPs = 10.10.10.2/32
```

> **要点**：
> - `iptables -I FORWARD 1` 必须插到链首（绕过 Docker FORWARD 规则）
> - WireGuard 监听 `3002` UDP，不对公网直接暴露也可，xray 会转发

### 2.7 开启 IP 转发

```bash
echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-wg.conf
sysctl -w net.ipv4.ip_forward=1
```

### 2.8 启动服务

```bash
systemctl enable --now xray
systemctl enable --now wg-quick@wg0
nginx -t && systemctl reload nginx
```

### 2.9 验证服务端

```bash
# 检查监听端口
ss -tlnp | grep -E ':443|:1443|:2443'
ss -ulnp | grep :3002

# 检查 xray 状态
systemctl status xray

# 检查 WireGuard
wg show wg0
```

---

## 3. 客户端（局域网 Linux）部署

### 3.1 环境信息（实测）

| 项目 | 值 |
|------|-----|
| 操作系统 | Debian 12 |
| 本机 IP | `192.168.88.250` |
| 本地 DNS | 路由器 DNS，将 `aliyun.icerror.top` 解析到本地反代（`198.18.28.114`）或直接解析到公网 IP |
| WireGuard VPN IP | `10.10.10.2/24` |

> **注意**：客户端连接目标必须用**域名** `aliyun.icerror.top`，不能直连 `47.109.207.95`（会被 LVS RST）。

### 3.2 xray 安装

```bash
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
```

### 3.3 xray 客户端配置

文件路径：`/usr/local/etc/xray/config.json`

```json
{
  "log": {
    "loglevel": "warning",
    "error": "/var/log/xray/error.log",
    "access": "/var/log/xray/access.log"
  },
  "inbounds": [
    {
      "listen": "127.0.0.1",
      "port": 4000,
      "protocol": "dokodemo-door",
      "settings": {
        "address": "127.0.0.1",
        "port": 3002,
        "network": "udp",
        "followRedirect": false
      },
      "tag": "wg-udp-in"
    }
  ],
  "outbounds": [
    {
      "protocol": "vless",
      "settings": {
        "vnext": [
          {
            "address": "aliyun.icerror.top",
            "port": 443,
            "users": [
              {
                "id": "3106437f-64ff-4654-8c4c-36fd327bcf26",
                "encryption": "none"
              }
            ]
          }
        ]
      },
      "streamSettings": {
        "network": "ws",
        "security": "tls",
        "tlsSettings": {
          "serverName": "aliyun.icerror.top",
          "allowInsecure": false
        },
        "wsSettings": {
          "path": "/vless-ws",
          "host": "aliyun.icerror.top"
        }
      },
      "tag": "proxy"
    },
    {
      "protocol": "freedom",
      "tag": "direct"
    }
  ]
}
```

> **要点**：
> - `inbound` dokodemo-door 监听 `127.0.0.1:4000`，将 UDP 流量映射到服务端 WG `127.0.0.1:3002`
> - `outbound` 连接**域名** `aliyun.icerror.top:443`（不要用 IP）
> - `security: tls` + `allowInsecure: false`（使用有效证书）

### 3.4 WireGuard 客户端配置

文件路径：`/etc/wireguard/wg0.conf`

```ini
[Interface]
PrivateKey = <客户端 WG 私钥>
Address = 10.10.10.2/24
MTU = 1280
PostUp = nmcli device set %i managed no 2>/dev/null || true

[Peer]
PublicKey    = <服务端 WG 公钥>
PresharedKey = <PSK>
AllowedIPs   = 10.10.10.0/24
PersistentKeepalive = 25
Endpoint     = 127.0.0.1:4000
```

> **要点**：
> - `Endpoint = 127.0.0.1:4000`：WireGuard UDP 包发到本机 xray dokodemo-door
> - `MTU = 1280`：防止分片（xray WS 封装后包头增大）
> - `AllowedIPs = 10.10.10.0/24`：split tunnel，仅 VPN 子网走 wg0

### 3.5 启动服务

```bash
systemctl enable --now xray
wg-quick up wg0
```

### 3.6 验证客户端

```bash
# 检查 xray 监听
ss -tlnp | grep :4000

# 检查 WireGuard 状态（握手时间应为秒级）
wg show wg0

# 测试 VPN 连通性
ping -c 5 -W 5 10.10.10.1 -I wg0
```

期望输出：

```
latest handshake: 5 seconds ago
transfer: 2.49 KiB received, 4.60 KiB sent

5 packets transmitted, 5 received, 0% packet loss
rtt min/avg/max/mdev = 382/391/401/7 ms
```

---

## 4. 端口规划

| 组件 | 主机 | 协议 | 端口 | 说明 |
|------|------|------|------|------|
| nginx | 服务端公网 | TCP | 443 / 1443 | TLS 入口，反代 xray WS |
| xray (服务端) | 服务端本机 | TCP | 2443 | VLESS+WS，仅本机监听 |
| WireGuard | 服务端公网 | UDP | 3002 | 直连端口（备用，xray 转发用本机） |
| xray (客户端) | 客户端本机 | UDP | 4000 | dokodemo-door，WG 出口 |
| WireGuard VPN | 客户端 wg0 | - | - | Endpoint = 127.0.0.1:4000 |

---

## 5. 数据流详细说明

```
客户端 wg0 发包 (目标: 10.10.10.1)
    │
    ▼ UDP:4000 (WG 加密包)
客户端 xray dokodemo-door
    │ 把 UDP 包通过 VLESS 协议封装
    ▼ TCP 连接到 aliyun.icerror.top:443
    │ DNS: aliyun.icerror.top → 47.109.207.95（或本地反代 IP）
    │ TLS 握手（使用有效证书，SNI=aliyun.icerror.top）
    ▼ WebSocket Upgrade: GET /vless-ws
服务端 nginx:443
    │ TLS 终止
    │ WebSocket 升级 → 反代到 127.0.0.1:2443
    ▼ HTTP WS 连接
服务端 xray VLESS+WS:2443
    │ 解封 VLESS 包，提取 UDP 数据
    ▼ UDP → 127.0.0.1:3002
服务端 WireGuard:3002
    │ 解密 WG 包，还原原始 IP 包
    ▼ wg0 接口 (10.10.10.1)
    │
    ▼ iptables MASQUERADE → eth0（若需要访问外网）
```

---

## 6. 故障排查

### 6.1 `connection reset by peer`（客户端 xray 日志）

**症状**：`transport/internet/websocket: failed to dial to IP:443 > read: connection reset by peer`

**原因**：客户端 xray 直连公网 IP（如 `47.109.207.95`），被 LVS RST。

**解决**：客户端 xray config 中 `address` 改为域名 `aliyun.icerror.top`，不要填 IP。

### 6.2 WireGuard 握手成功但 ping 不通

**检查**：
```bash
# 服务端
wg show wg0           # 查看 latest handshake 和 endpoint
tcpdump -i wg0 icmp   # 检查 ICMP 包是否到达 wg0 接口
```

**常见原因**：
- iptables FORWARD 规则被 Docker 规则排在前面 → 用 `iptables -I FORWARD 1` 插到链首
- IP 转发未开启 → `sysctl net.ipv4.ip_forward`

### 6.3 WireGuard 无 latest handshake

**检查**：
```bash
# 客户端
tail -f /var/log/xray/error.log   # 查看 xray 错误
ss -tlnp | grep 4000              # xray 是否在监听
```

**原因**：xray 未启动，或配置文件有误。

### 6.4 nginx SSL 握手错误

**症状**：nginx error log 出现 `unexpected ccs message`

**原因**：客户端连接到了错误的 nginx 配置（如 `steal-oneself.conf` 的 `127.0.0.1:8443`），配置路径冲突。

**解决**：检查 `/etc/nginx/conf.d/` 下所有配置文件，确保 443 端口只有 `vless-ws.conf` 一个配置。

---

## 7. 关键参数（代码常量）

对应 `wireguard/constants.py`：

| 常量 | 值 | 说明 |
|------|----|------|
| `XRAY_REALITY_PORT` | `443` | 客户端连接服务端的端口 |
| `XRAY_WS_PORT` | `2443` | 服务端 xray WS 监听端口（本机） |
| `XRAY_WS_PATH` | `/vless-ws` | WebSocket 路径 |
| `WG_UDP_PORT` | `3002` | 服务端 WireGuard UDP 端口 |
| `CLIENT_XRAY_LOCAL_PORT` | `4000` | 客户端 xray dokodemo 监听端口 |
| `NGINX_VLESS_WS_CONF` | `/etc/nginx/conf.d/vless-ws.conf` | nginx 配置文件路径 |
| `ACME_CERT_DIR` | `/etc/nginx/ssl` | TLS 证书目录 |
| `VPN_SUBNET` | `10.10.10.0/24` | VPN 子网 |
| `VPN_SERVER_IP` | `10.10.10.1` | 服务端 VPN IP |
| `WG_CLIENT_MTU` | `1280` | 客户端 WireGuard MTU |
| `WG_KEEPALIVE` | `25` | WireGuard 保活间隔（秒） |

---

## 8. 代码模板函数

| 函数 | 文件 | 用途 |
|------|------|------|
| `xray_server_config_ws(uuid, ws_port, ws_path)` | `wireguard/templates.py` | 生成服务端 xray WS 配置 |
| `xray_client_config(sni, server_port, uuid, local_port, wg_port, ws_path)` | `wireguard/templates.py` | 生成客户端 xray WS+TLS 配置 |
| `nginx_vless_ws_config(sni, ws_port, cert_dir, ws_path)` | `wireguard/templates.py` | 生成 nginx WS+TLS 反代配置 |
| `wg_server_config(server_private_key, server_ip, wg_port, iface)` | `wireguard/templates.py` | 生成服务端 WireGuard 配置 |
| `wg_client_config(...)` | `wireguard/templates.py` | 生成客户端 WireGuard 配置（`Endpoint=127.0.0.1:{local_port}`） |

---

## 9. 已验证的实测结果

```
interface: wg0
  listening port: 39999

peer: aADU5S3qJAPjrzI7TfMiYDigp80Skg1pyjUMGGboJX0=
  endpoint: 127.0.0.1:4000
  allowed ips: 10.10.10.0/24
  latest handshake: 5 seconds ago
  transfer: 2.49 KiB received, 4.60 KiB sent
  persistent keepalive: every 25 seconds

5 packets transmitted, 5 received, 0% packet loss
rtt min/avg/max/mdev = 382.696/391.372/401.402/7 ms
```

隧道通过 `nginx WS+TLS` 建立，RTT 约 390ms（服务器距离约 1500km），稳定无丢包。
