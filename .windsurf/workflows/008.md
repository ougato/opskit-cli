1. 修复 client.py 诊断页 "Xray local port" 硬编码英文，改为 t() 国际化调用
2. 更新 zh.yaml 新增 wireguard.client_diagnose.xray_local_port key
3. 更新 en.yaml 新增 wireguard.client_diagnose.xray_local_port key
4. 修改 client.py 所有面包屑中 "WireGuard" 硬编码改为 t("software.wireguard")
5. 修改 server.py 所有面包屑中 "WireGuard" 硬编码改为 t("software.wireguard")
6. 修复 client.py tunnel_label_dup 调用方式从 .format() 改为 t() 关键字参数
7. 更新 README.md 中 wireguard/client.py 描述补充"管理"
