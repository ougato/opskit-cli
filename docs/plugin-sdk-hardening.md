# SDK 镜像策略 7 加固

SDK 正式构建在哈希锁定的 wheel 安装完成后、切换非 root 用户前执行固定加固脚本。SDK 与出口网关共用 `core/plugin_image_hardening.py`，不复制规则，不执行插件提供的构建脚本。该模块只属于宿主构建端，不加入插件 SDK guest 源码清单。

规则清除 `/usr/bin`、`/usr/sbin`、`/usr/local/bin` 中普通文件的 setuid/setgid 位，并移除 `/usr/bin/infocmp`；保留 Python 使用的共享库和 terminfo。此规则限定上述目录，不声称消除了任意基础镜像全部攻击面。运行时继续使用非 root、只读根、移除 capabilities、禁止新增权限和资源限制。

SDK 策略由 6 升至 7；执行端拒绝旧策略镜像，用户须重新 `plugin prepare`。这不改变插件的独立目录、参数解析和权限授权，不自动扩大授权。依赖特权工具或 infocmp 的插件须调整设计，平台不恢复这些能力。网关固定脚本内容未变，网关策略仍为 2。

## 2026-10-15 证据

- 535 文件快照 SHA256：`68cc447fc8b11c6c4ed3bb9248184d7c9e8b982ae02050518f5521bc49250869`。
- 正式构建 SDK ImageID：`sha256:fc5464719889485ecd5ec670e976e74e43e84d2eb13b05ef5609e2d6af09ec9e`。使用已审阅不可变基础镜像及完整哈希锁；实际镜像清单确认特权位为空、infocmp 不存在、13 个 Python 包版本保持一致，SDK/SSL/SQLite/curses/readline 检查通过。
- 构建及镜像身份回归共 91 项：Windows 90 通过、1 跳过；Linux/macOS 各 91 通过。
- 正式构建真实 RPC：调用方状态 A=1、B=1、A=2，未授权/未声明方法、业务版本不匹配、非法协议和结果、终结后调用均拒绝；身份及服务边授权撤销后状态保留。27 个 worker 容器确认消失；最终资源恢复四项均为 0。没有替换构建器、镜像目录或策略校验。
- 实际网关和策略 7 插件复验通过：直接访问受控 HTTP peer 被阻断，经代理按 private grant 拒绝/允许/再次拒绝；活跃 CONNECT 隧道下取消、身份撤销、private grant 撤销和清理阶段撤销均保留旧状态，资源恢复四项为 0。首轮在查询受控 peer 时失败；复验仅增加本地诊断日志，未改策略或断言。该首次失败原因尚未定，不能用复验通过将其抹去；证据分别保留。撤销是轮询检测，不承诺逐请求即时撤销。
- wheel SHA256：`5a3771f9664bbd1e81acd8f981ab070dc556508e0c0d68f91e8fc1d6777aa748`，260 个运行文件、265 个包成员，RECORD 与源码校验通过。Windows 从隔离安装目录导入实际模块并核对策略和共享脚本。
- Linux/macOS 也完成实际 wheel 的隔离安装导入；三端四个变更构建模块的文件哈希完全一致，确认来自安装目标而非源码目录。这是安装导入检查，不替代三端原生 Docker 或完整安装/卸载验收。
- 精确镜像 Trivy OS 扫描：Critical 0、High 44、Medium 58、Low 61、Unknown 3；缓存数据库更新时间为 2026-10-14T01:19:20Z。不得把减少工具或清除特权位当成修复包漏洞，门槛继续开放，见[漏洞处置](plugin-os-vulnerability-disposition.md)。

原始证据位于本机忽略目录 `cache/sdk-policy7-related-evidence-20261015-r1`、`cache/sdk-policy7-inventory-20261015-r1`、`cache/sdk-policy7-os-audit-20261015-r1`。独立候选的 RPC 通过不替代本页正式构建验收；旧策略全量不替代新策略全量。正式网关网络矩阵、三端最终整仓和安装/部署范围仍按路线图继续验收。
