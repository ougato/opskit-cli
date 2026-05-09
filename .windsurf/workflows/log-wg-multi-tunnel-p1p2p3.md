1. 修改 constants.py 添加 VPN 子网模板 VPN_SUBNET_TPL/VPN_GW_TPL、SNI 白名单 SNI_WHITELIST（移除敏感域名）、多隧道端口范围 CLIENT_XRAY_LOCAL_PORT_MIN/MAX
2. 修改 token.py 升级令牌 v2 格式，新增 vpn_subnet/vpn_gateway/label 字段，向后兼容 v1（缺失字段自动补默认值）
3. 修改 server.py 安装向导新增子网 octet3（1~254）和标签输入，_generate_client_token 添加 vpn_subnet/vpn_gateway/label 参数，add_peer 从 state 读取并传入新字段，_next_peer_ip 支持动态 octet3
4. 修改 templates.py wg_server_config 使用动态子网，xray_client_config 添加 fake_sni 参数从 SNI 白名单随机选取
5. 更新 zh.yaml/en.yaml 添加 input_vpn_octet3/input_vpn_octet3_invalid/input_tunnel_label/remove_tunnel/remove_tunnel_ok/tunnel_label_dup 等多隧道相关 locale key
6. 修改 client.py 重写为多隧道支持：tunnels state 列表、_alloc_local_port 端口自动分配、_ensure_xray_template_service 生成 systemd 模板单元 xray@.service、独立 wg-{label} 接口和 xray@{label} 实例、remove_tunnel 单条移除、update_client_token/view_client_info 适配多隧道
7. 更新 tests/test_wg_multi_tunnel.py 新增 25 项单元测试全部通过（token v2/v1 兼容、_alloc_local_port、_alloc_client_ip、_next_peer_ip、SNI 白名单、wg_server_config 动态子网、xray_client_config 随机 SNI）
