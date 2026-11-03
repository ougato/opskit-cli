# 原生沙盒交付验收 / Native sandbox acceptance

当前状态：**本期 Windows 原生沙盒改造已完成验证，可供本地交付评审**。
保留独立 Docker 软件管理，Linux/macOS 按约定预留契约与待办，尚未实现运行后端。
下文保留实际证据范围及后续事项，不将其描述为全部平台或生产发布已经完成。
Current status: this Windows native-sandbox migration is verified for local delivery review.
Standalone Docker management remains. Linux/macOS contracts and follow-up work are reserved;
their runtime backends and production deployment are not claimed complete.

最终汇总：`cache/native-delivery-audit-20261103.json` 核对 14 份当前包报告均为同一
SHA256，并确认当时源码与已通过全量回归的快照一致。所有本轮验证进程已结束。
The final audit binds 14 reports to one binary digest and confirms the then-current source
still matches the passing regression snapshot. This verification batch has finished.

## 后续磁盘策略 / Disk-policy follow-up

新增磁盘策略已经改变源码；下方全量回归与 EXE 摘要只覆盖此前原生迁移快照，不覆盖此增量。
The disk-policy follow-up changes source; the full regression and binary digest below cover the earlier migration only.

配置和边界见 [磁盘策略](native-disk-budget-design.md)。增量验证记录在该文档后附。
See the disk policy document for configuration, limits and follow-up validation.

## 当前源码与发行包 / Current source and binary

- Windows 包：`cache/native-pyinstaller-build-20261103-ssh-url/source/dist/opskit-windows-x64.exe`。
- SHA256：`97c6665bcbb39f85519088e6893a8f0ddcc2eb6182b2b742a5786825062fd47e`。
- 与前包 b0cb44 的构建输入仅 `plugin/commands.py` 不同，修复完整 SSH URL 被当成 SCP 地址的问题。
  对比：`cache/native-ssh-url-build-delta-20261103.json`。输入相同不等于新包全部运行路径已复验。
- 相关插件与生命周期 CLI：62 passed；语言/授权/辅助进程补验：60 passed、2 skipped。
- 静态解析 `core`/`plugin` 中原生错误及 `plugin_native.*` 固定文案引用共 155 处，中英文均无缺键；动态拼接仍以流程测试验证。
- 前轮全量：4462 passed、129 skipped；运行中源码改变，不是最终不变快照。
- 当前全量终态：**4463 passed、129 skipped、39 warnings**，2281.32 秒。
  `cache/native-snapshot-regression-20261103-ssh-url.result.json` 确认 exit_code=0、
  source_unchanged=true、changed_paths=[]；对应日志同前缀 `.log`。
- 跳过模块的源码声明审计保存在 `cache/native-skipped-source-audit-20261103-ssh-url.json`，
  共对齐 129 个日志跳过结果，涉及 POSIX/Linux/macOS 限定及本机符号链接权限等。
  这不是逐测试捕获的运行时跳过原因，不把跳过用例说成通过，也不修改系统权限来消除跳过。

The current binary fixes SSH installation-name parsing. Build-input equality is not runtime proof.
The current full regression passed with 4463 tests and 129 skips on an unchanged source
snapshot. Skip declarations were inspected by module; per-test runtime reasons were not
captured, and skipped tests are not counted as passes.

## 当前包已核对证据 / Inspected current-binary evidence

以下报告均绑定上述完整 SHA256。缓存路径相对项目根目录。
These reports bind to the complete digest above. Paths are relative to the repository root.

| 验证 / Check | 报告 / Report | 范围 / Scope |
| --- | --- | --- |
| 正式 CLI / Official CLI | `cache/frozen-cli-20261103-ssh-url/verification.json` | 32 项只读命令检查 / 32 read-only command checks |
| 发行包模块库存 / Embedded modules | `cache/frozen-module-inventory-20261103-ssh-url.json` | 直接读取 EXE 的 1710 个 Python 模块，12 个旧容器模块均不存在，原生后端/权限及独立 Docker 软件配方存在 / actual EXE inventory, retired modules absent and required modules present |
| 原生隔离与状态 / Native isolation and state | `cache/frozen-native-20261103-ssh-url-en/verification.json` | 英文准备、清理、宿主文件/环境及本机 TCP/UDP 边界、提交、回滚、撤权 / English preparation, cleanup, local boundaries, commit, rollback, revocation |
| SSH 英文 / English SSH | `cache/frozen-lifecycle-20261103-ssh-url-en/verification.json` | 默认私网拒绝、错误主机密钥/口令拒绝、安装、更新重新信任、运行、移除保留状态 / private/host-key/passphrase refusal and full lifecycle |
| SSH 中文 / Chinese SSH | `cache/frozen-lifecycle-20261103-ssh-url-zh/verification.json` | 同流程及中文失败提示 / same lifecycle and localized failures |
| HTTPS 中文 / Chinese HTTPS | `cache/frozen-lifecycle-20261103-https-url-zh/verification.json` | 私网/不可信 CA/重定向拒绝、认证安装更新、重新信任、移除保留状态 / refusals and authenticated lifecycle |
| 永久授权真实终端 / Always-allow TTY | `cache/frozen-always-consent-20261103-ssh-url.json` | 中英文选 2、正式列表查到、失败保留原状态、撤销后空列表 / bilingual selection, persistence, failure-state preservation, revocation |
| 中文原生复验 / Chinese native | `cache/frozen-native-20261103-final-zh/verification.json` | 当前包中文准备、隔离、状态提交/回滚及撤权 / current-binary Chinese preparation, isolation, state and revocation |
| 中英文恢复 / Bilingual recovery | `cache/frozen-recovery-20261103-final-zh/verification.json`、`cache/frozen-recovery-20261103-final-en/verification.json` | 当前包正式恢复命令；活动、摘要和目录身份拒绝及精确凭据清理 / current-binary recovery commands and identity-bound refusal/cleanup |
| 中英文上传 / Bilingual upload | `cache/frozen-upload-20261103-final-zh/verification.json`、`cache/frozen-upload-20261103-final-en/verification.json` | 当前包完整接收字节/MD5/SHA256、状态及未授权/撤权拒绝；本机 TLS，非生产 S3 / current-binary bytes, hashes, state and refusal on local TLS |
| 中英文 Creator / Bilingual Creator | `cache/frozen-creator-20261103-final-zh/verification.json`、`cache/frozen-creator-20261103-final-en/verification.json` | 当前包真实独立网页产物、语言状态、未授权与撤权拒绝 / current-binary fresh web artifact, language and refusal |

真实终端证据使用终端注入按键，不称为物理键盘操作；全部资源和凭据为测试专用。
SSH/HTTPS 服务是本机独立端口的合成真实服务，宿主 Git 仅提供服务端测试仓库，
客户端实际走正式冻结代理及隔离 Git。没有修改系统证书信任、连接生产仓库或使用生产凭据。
TTY keys were injected. Fixtures and credentials were test-owned. Loopback Git servers exercise
real transports and frozen acquisition, without system trust changes or production access.

## 前包证据 / Previous-binary evidence

b0cb44 完整 SHA256：`b0cb447b5d2e86fe57ea879258b63a25ebe70186e57ec2f5703e0e2129113934`。
保留其证据，不转述为当前包全部通过。报告分别为：
Retain these reports under their original binary identity; do not relabel them as current-binary passes.

| 验证 / Check | 报告 / Report |
| --- | --- |
| Creator 中英文真实独立网页产物 / bilingual real fresh web build | `cache/frozen-creator-20261103-console-signal-zh/verification.json`、`cache/frozen-creator-20261103-console-signal-en/verification.json` |
| 上传中英文完整字节、MD5、SHA256 / bilingual upload bytes and hashes | `cache/frozen-upload-20261103-console-signal-zh/verification.json`、`cache/frozen-upload-20261103-console-signal-en/verification.json` |
| 恢复中英文 / bilingual recovery | `cache/frozen-recovery-20261103-console-signal-zh/verification.json`、`cache/frozen-recovery-20261103-console-signal-en/verification.json` |
| Ctrl+C 中英文真实 TTY / bilingual TTY cancellation | `cache/frozen-console-signal-20261103.json` |
| HTTPS 英文 / English HTTPS | `cache/frozen-lifecycle-20261103-https-en/verification.json` |
| 本地 Git 生命周期 / local Git lifecycle | `cache/frozen-lifecycle-20261103-console-signal-en2/verification.json` |

## 需求核对与剩余 / Requirements and remaining work

资源限制新增实际分配/创建验证：
`cache/native-resource-enforcement-20261103/verification.json`。
正常限额能分配 128MiB、创建子进程；64MiB Job 内存限额及单进程限额分别拒绝相同操作。
这是源码后端真实 AppContainer 证据，不是冻结 CLI 或磁盘配额证据。
The real source backend accepted control allocations/spawns and refused the same
operations under lower Job limits. This is not frozen CLI or disk-quota evidence.
磁盘硬预算候选、授权边界及落地门槛见 [待选设计](native-disk-budget-design.md)。
See the pending design for disk-budget options and implementation gates.

| 要求 / Requirement | 现状与边界 / Status and boundary |
| --- | --- |
| 删除插件 Docker/WSL 依赖 | 已删除旧容器模块；架构测试覆盖不可导入及 guest 不含宿主权限。源码复核剩余 Docker/WSL 引用属于独立软件管理、镜像及其说明。保留独立 Docker 软件管理。旧资源按 `plugin-docker-retirement.md` 核对，不自动删除未知对象。 |
| Windows 原生隔离，无不受限回退 | AppContainer 启动前验证、Job 进程树限制、私有运行时及受限 RPC 已实现并有真实冻结证据。完整外部网络/恶意负载矩阵仍未全部验证。 |
| Linux/macOS 预留 | 共用 Backend/Process/Limits、权限、状态与任务协议；未实现平台本地化拒绝。后端实现待办见 `native-backend-porting.md`，本期不宣称两平台可运行。 |
| 本次/永久/拒绝、指纹绑定及撤销 | 源码与冻结拒绝/撤权检查；前包真实 TTY 本次/拒绝和当前包永久授权均有记录。权限与工具路径配置分离。 |
| 多语言 | 沿用原程序 zh/en；菜单、帮助、授权、错误、进度、取消及恢复共用语言目录；worker 显式继承语言。键及插值参数一致性和实际双语流程已检查。错误码、命令、JSON 字段不翻译，第三方日志保留原文。 |
| Creator 宿主工具 | 便捷别名、精确工具/项目授权、唯一输出目录和产物检查已实现；真实网页构建有双语证据。项目脚本以用户权限运行，不承诺宿主目录隔离；其他目标的完整安装包未验证。 |
| 上传及摘要 | 目录授权下的 artifact_info/upload_file 已实现；前包本机 TLS 验证全字节与摘要。尚未验证真实 S3、生产签名链接及生产发布。 |
| 取消、超时及回收 | 期限监督、进程树回收、状态回滚及双语 TTY 取消已验证；不承诺撤回外部构建文件或已经发送的网络字节。 |
| 崩溃恢复及归属 | profile/工作区/更新/catalog 凭据、租约与正式恢复入口已实现。冻结恢复使用源码 os._exit 产生的真实准备凭据；不是冻结进程任意崩溃点的完整矩阵。未知目录不按名称删除。 |
| 资源预算 | 内核内存/CPU/进程数、目录/条目/传输及完成后产物检查已实现。磁盘硬配额未实现，管理员隔离磁盘组件选择尚待用户答复；扫描/估算不算硬配额。 |
| 安装更新下载 | 正式隔离 Git 及本机真实 HTTPS/SSH 生命周期已验证；公网/所有网络拒绝路径未全部验证。 |
| 最终源码与发行包 | 当前全量 4463 passed、129 skipped，源码摘要未变；已复核跳过模块声明。不同包证据不得混称，逐项包验收边界以上表为准。 |
| 交付说明 | 双语升级步骤、SDK、工具配置与授权、输出/取消边界见 `plugin-native-upgrade-guide.md` 和 `plugin-spec.md`。 |

The follow-up items and verification boundaries above remain recorded. Disk hard quotas require a separate authorized design;
post-build inspection is not a quota. Local TLS is not production S3, and local network refusal
does not prove every external route. Recovery fixtures do not prove every frozen crash point.

详细实施历史见 `plugin-native-sandbox-migration.md`；此前逐次验收记录归档在
[历史验收记录](history/native-sandbox-acceptance-20261103.md)，其中“最新/运行中”仅代表记录当时。
See the migration record for implementation details. Historical status wording applies only to
its original observation time; this document is the current acceptance index.
