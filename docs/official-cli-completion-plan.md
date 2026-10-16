# 官方 CLI 参数补齐与验收计划

2026-10-17：`plugin prepare-fetch --packages DIR` 与 `prepared_tool` 已实现。冻结642文件快照 `0fa8c0aa026f6a71be394122f5dc085190c5e5ac34246970855eb1d2ec15e6ec` 在Windows、Linux3.14、macOS、最低Python3.10各76项通过、零跳过；源码、同名XML用例及最低容器约束已独立核对。此批明确不运行未改动的旧宿主Git安装更新整链路测试。真实Linux CLI使用已有锁定离线包准备工具，新解释器通过固定本地端点选出同一内容寻址镜像，不创建配置或插件目录、不授信。证据 `cache/plugin-fetch-preparation-20261016-r1/audit.json`。 本批没有迁移install/update，旧宿主Git主流程仍待替换。

2026-10-16验收范围：当前603文件快照21e9afd3的三端整仓4064项均已独立复核：Windows4010通过54跳过，macOS3930通过134跳过，Linux3934通过130跳过。三端实际安装各113CLI、20生命周期及1项真实阻塞子进程关闭异常检查通过，269运行文件/RECORD已核对；Linux真实Docker基础3×21、9生命周期及完整RPC已独立验收。匹配快照Linux冻结产物的20CLI、2钩子、3×21基础、4信号取消、2强制退出恢复及7根最终reconcile已独立验收，追加113项冻结CLI矩阵及340份原始证据复核通过；仅测试密钥变体，不是生产发布批准。原599 Windows取消worker后PID断言失败及595辅助PowerShell超时的根因仍开放，后续绿色结果不关闭原问题。历史r3三端已安装CLI各6项真实IPv4/IPv6回环端口与localhost DNS检查仅对应原快照。完整源码、跳过及证据见[验收台账](validation-status-20261015.md)。这些结果不关闭下列官方写事务、真实系统服务、外网/代理、更新恢复或发布签名门槛，也不把插件业务参数加入平台。

核对日期：2026-10-13。依据当前工作区 `main.py`、各模块 CLI、Recipe 注册表及菜单服务调用链。这里的“已接入”表示代码入口存在，不表示真实安装、跨平台或生产安全验收通过。当前变更尚未提交，各批完整/相关回归结果见实施台账，跨平台与真实部署仍待验收。项目整体的安全、健康、性能及发布队列见 `project-health-roadmap.md`。

## 1. 总体结论和设计边界

插件获取边界现已实现固定工具镜像、显式来源认证、候选准备/审查、事务发布、撤销与记录退役；642增加prepared显式中止，最低Python回归60通过、1项工作区分配setup超时，尚未通过。**install/update仍使用宿主Git**，底层局部验收不表示主流程迁移完成，也没有把插件业务参数注册进平台。原快照与证据见[Git获取边界](plugin-git-acquisition-security.md)。

### 安装与更新迁移的参数及接入顺序（工具准备已局部验收）

先完成一条可用主流程，再注册相应CLI并迁移菜单/服务引导安装；不先发布空命令或长期保留宿主Git回退。下表仅prepare-fetch已实现并完成上述局部验收，其余参数及主流程仍是计划。

| 平台生命周期入口 | 补齐内容 | 行为及验收要求 |
|---|---|---|
| `plugin prepare-fetch --packages DIR` | 已实现并局部验收：从锁定离线包与固定基础镜像准备工具；镜像保存在daemon目录，使用时按当前策略/源码标签查询唯一image ID并重核 | 使用现有获取镜像构建器；绑定实际本地Docker端点及当前worker/锁摘要，受监督且不使用插件Dockerfile；缺包/错摘要/不支持架构拒绝，不自行安装宿主Git或回退 |
| `plugin install SOURCE` | 保留`--branch/-b`、`--as`，补`--auth-profile FILE`和默认关闭的`--allow-private` | 凭据只从显式私有来源绑定文件进入，不接受URL密码或继承宿主Git/SSH配置；私网放行只覆盖本次精确来源；缺少有效工具准备时明确拒绝并指出准备入口；完成后仍不自动授信 |
| `plugin update NAME` | 补`--auth-profile FILE`、默认关闭的`--allow-private`；从已安装凭据读取来源/分支 | 不读取被克隆插件自行提供的origin作为平台更新权威；默认不改变来源或分支。每次新候选重新获取与审查，撤销旧授权后事务切换；版本回退须有独立显式决策，不能先覆盖再报拒绝 |
| 安装检查与恢复 | 接入已实现的inspect/recover/abort/withdraw/retire API，最终命令命名随主流程统一 | 只接受平台事务token和已绑定对象，不接受任意待删目录；inspect不创建或变更，recover不替用户发布/中止/删除；不确定时保留token及工作区，并提供可执行的下一步 |
| 菜单及服务引导安装 | 复用相同业务服务和受保护工具选择；私有源配置只传文件路径 | 拒绝信任走精确撤销；失败不会无条件清理未知目录。手工clone仍只影响插件独立目录，不能凭发现结果获得自动更新或回滚删除权限 |

来源、分支和原始commit须与安装事务及当前目录身份一起持久绑定；凭据内容、profile文件路径及代理token不写入插件源码或安装来源记录。平台维护准入覆盖首次分配到信任/缓存/资源清理结束，所有helper继承同一可信会话。菜单、CLI及服务调用必须测试同一条业务链路，验证失败后的重试、拒绝信任、开发者改动保留、跨进程竞争、中途取消与崩溃恢复；完成后删除原宿主clone/pull实现及仅服务旧实现的代码。命令退出继续遵守既有CLI契约，错误信息不包含凭据或Git原始诊断。完整I/O期限和匹配安装/冻结产物验收仍是交付条件。

工作目录发现增量：`plugin inspect-workspaces --limit 256`（1..256）只读观察直接条目，最多扫描4096项，输出token与directory/unsafe以及unknown计数。不递归、不读取内容、不调用Docker，未知名称脱敏；不把观察结果当作归属或删除授权。失败/超限退出1，参数错误2，取消130。该入口用于暴露身份绑定前崩溃留下的目录，自动恢复缺口仍开放；具体语义及验收记录见[资源元数据恢复](plugin-resource-metadata-recovery.md)。

最新插件生命周期补齐：`plugin authorize-service CALLER SERVICE --caller-fingerprint ... --provider-fingerprint ...` 与 `plugin revoke-service CALLER SERVICE` 管理精确RPC调用边；`plugin reconcile` 在整体预算内恢复非活动且具有所有权证明的Docker资源，输出recovered/active/pending/invalid，pending或invalid退出1。这些是平台通用生命周期参数，不是第三方业务参数。当前源码、新wheel及真实RPC/资源恢复证据见implementation-acceptance.md；不能据此关闭完整网络、真实OS取消和多租户部署门槛。

补充恢复入口：`plugin retire-lock TOKEN` 按32位小写十六进制token退役无日志、非活动的锁元数据，不调用Docker、不接受任意路径；已有/损坏日志及活动锁保留。退出0为retired/absent，1为保留或未确认，2为输入错误，取消130。三端各48项及实际wheel安装命令已验收。新增 `plugin inspect-locks --limit 256`（1..256），只读发现 token 与日志/锁状态、计数未知条目，最多扫描4096项；无日志不表示无活动，退役必须重新复核。详见[孤立资源锁恢复](plugin-resource-metadata-recovery.md)，损坏日志修复仍开放。

当前官方 CLI **尚未完善**。查询、监控、网络、官方管理及安装配置已接入部分能力；通用软件变更操作已统一输出并迁移编排，WireGuard 客户端管理已接入基础入口，完整诊断、驱动事务恢复及显式更新流程仍需完整验收。

分三层实现：命令负责解析/验证和展示；业务服务负责执行、事务和返回结构化结果；平台驱动负责系统差异。菜单和 CLI 调用同一个业务服务。`main.py` 逐步只保留应用组装与注册，不复制菜单逻辑。命令参数在所属官方模块定义。

第三方业务参数属于插件自己的目录和版本控制：`opskit plugin run NAME -- ARGS`，包括插件自己的 `--help`，平台保持参数大小写和顺序并原样传递。平台只定义安装、信任、准备、启停、隔离执行等生命周期参数，不为具体第三方插件添加专属参数。插件不可直接导入宿主业务服务；通过受限 RPC 使用获批能力。插件依赖、缓存、配置、状态分别隔离，克隆插件不修改平台源码和平台依赖文件。

该业务参数路由已有真实Linux/amd64容器和新wheel安装CLI证据：MiXeD、--help与两个--repeat保持原样，插件自身cli返回0。最新policy4/schema2运行时三次状态续写及各21项基础检查通过，包含宿主业务模块不可导入与共享SDK子进程。可复用scripts/verify_plugin_native.py；完整授权/RPC/网络/取消与多架构仍待完成，不能将基础路由通过标记插件整体验收。

最新生产监督器已通过九项真实资源/生命周期验收，包含运行中撤权、注入取消、OOM、PID/临时盘及状态/ASCII/Unicode输出额度；新冻结CLI基础路由通过。修复管道字节计量和清理顺序，容器禁用daemon日志落盘。--lifecycle是开发验收脚本参数，合成exercise参数只存在其独立插件，不注册到平台CLI。整体期限、实际OS取消、获批出口/RPC、多调用方隔离及daemon恢复仍需补齐，原生证据详见实施台账。

后续宿主监督器已将准备、线程锁等待、Docker控制和嵌套服务纳入共享父预算；本批UI读键/读行期间增加期限和调用链授权守卫，启动与提交前重查，相关152项、Windows隐藏控制台3项及Linux PTY6项通过。不新增插件业务参数；同步输出/文件操作仍不能抢占，完整原生/发布验证须对应新监督器和SDK摘要，不沿用先前冻结程序的通过结果。

## 2. 所有官方命令的统一契约（P0）

| 项目 | 现状/缺口 | 补齐方案和验收 |
|---|---|---|
| 输出 | `--output text\|json`、生命周期、prepare/fingerprint和解析错误已接入 | 单次 stdout 只含一个 JSON envelope；连续结果为 JSONL；固定进度/诊断写 stderr。继续核对尚未迁移入口及实际运行输出 |
| 无交互 | `--non-interactive`、JSON 默认无交互、`--yes` 已有 | 缺必要参数立即退出 2；不进入菜单、密码提示或后台更新；无 stdin 情况执行不挂起 |
| 退出码 | 共享异常边界及 Typer JSON 解析错误已接入 | 0 成功，1 运行失败，2 参数/配置错误，130 取消；仍需统一失败诊断 envelope 的 ok/error 与退出码 |
| 参数适用性 | Recipe 能力查询已存在 | 不支持版本/升级/切换的 Recipe 显式拒绝；未知参数、互斥参数、平台不支持均不得静默忽略 |
| 敏感输入 | `--token-file FILE\|-`、配置密码文件已接入；描述符绑定、大小/身份及读取前后私密权限核验已补齐；606三端58项矩阵、最低Python3.10相关验收及新wheel三端安装各113CLI+12敏感检查见台账 | Linux当前owner及私密mode、macOS拒绝扩展ACL、Windows仅当前用户/SYSTEM/admin授权；不自动修改输入权限。继续受保护父目录、完整/冻结产物验收及敏感导出事务；安装验证复用锁定环境。不把令牌写到日志；保留旧 `--token` 兼容，推荐文件/stdin |
| 敏感导出 | 已接入序列化、私密暂存、同步和不覆盖发布；609增量补齐目录/权限重查及Windows名称验证，三端定向/最低Python、Linux真实跨用户证据见台账；安装验证仍仅覆盖608 | 受保护父目录；Windows创建时ACL及句柄绑定操作，POSIX目录描述符及link发布。POSIX目录失去保护时保留私密暂存，继续完整恢复、其他平台跨用户、网络文件系统、断电及安装/服务补偿验收；609安装/整仓/产物需补齐。不泄漏到普通结果，不把局部保证当作整次业务事务 |
| 期限/取消 | 子进程监督已接入部分网络命令 | 网络、登录和诊断采用 monotonic 总截止时间；DNS/连接/读取/子进程各阶段共用剩余预算，取消后无子进程/线程残留 |
| 副作用 | 只读查询已有基本边界 | help/list/status 不安装、不新建配置、不自动登录；危险操作普通确认或显式 --yes，信任仍必须显式指纹 |

不增加大量全局参数。`--timeout` 仅放在需要期限的操作；`--version` 放在版本型软件操作。`--dry-run` 暂为后续可选能力，只有能准确列出实际变更且不触发副作用时才实现，不用占位输出冒充支持。

## 3. 每个官方软件的生命周期（P1）

公共入口已经存在：`software list/search/installed/versions/capabilities/install/uninstall/upgrade/switch/diagnose/manage`。

查询：list/search 的 `--category`、`--platform` 和 JSON 已接入；versions 区分可安装版本和本地安装版本，失败不能输出空列表冒充正常结果。跨平台查询不执行异平台检测。

版本型统一目标：`install NAME --version VERSION`、`upgrade NAME [--version VERSION]`、`switch NAME --version VERSION`、`uninstall NAME [--version VERSION | --all]`。无交互安装缺版本的策略必须明确：显式解析到最新稳定版并返回实际版本，或拒绝；不能偷偷打开版本选择器。卸载不默认删除用户数据库或项目数据。

当前统一生命周期已接入：版本型 install/upgrade 缺 --version 时拒绝；多版本 uninstall 缺 --version/--all 时拒绝，无交互卸载要求 --yes。18 个 Recipe 无效参数拒绝矩阵通过。版本型 switch 已接入快照/链接失败补偿和实际 PATH 命令版本检查；九种版本软件在 Recipe 层接入共享 home 资源锁，真实进程竞争和相关 88 项回归通过。仍需系统资源跨用户锁、驱动不吞异常、伴随命令与真实系统恢复验收，不能用入口通过代替业务验收。

| Recipe | 当前平台声明 | 当前安装类型 | 明确需补齐/验收 |
|---|---|---|---|
| python | Linux/macOS/Windows | 版本 | 版本解析、升级/切换、PATH 恢复、按版本卸载和 --all 互斥；编译失败/取消正确回收 |
| nodejs | Linux/macOS/Windows | 版本 | 同上；切换后的 node/npm 与 PATH 一致 |
| golang | Linux/macOS/Windows | 版本 | 同上；GOROOT/PATH 恢复，go version 验证 |
| java | Linux/macOS/Windows | 版本 | 同上；JAVA_HOME/PATH 与实际 java 一致 |
| mysql | Linux/macOS/Windows | 版本 | 同上；现有诊断/管理能力未声明，不虚构数据库管理参数；卸载保留数据并说明服务影响 |
| mongodb | Linux/macOS/Windows | 版本 | 同上；检测、切换、服务状态与数据目录保留 |
| postgresql | Linux/macOS/Windows | 版本 | 同上；检查实际版本实现差异和服务重建失败恢复 |
| redis | Linux/macOS/Windows | 版本 | 同上；各驱动能力单独验证，不能把统一元数据当平台等价证据 |
| clickhouse | Linux/macOS | 版本 | 同上；Windows 提前拒绝，保留数据 |
| docker | Linux | 系统包 | 无版本选择；拒绝无意义 --version/upgrade/switch；脚本提权、包管理失败及卸载保留镜像/卷验收 |
| nginx | Linux | 系统包 | 同上；保留用户站点与配置；现有能力没有管理入口，不为凑参数扩功能 |
| rustdesk | Linux | 系统安装 | 只读 diagnostic_report 已接入，失败非零且无密钥/状态写入；继续补监听/配置/真实服务检查与结构化安装/卸载；无版本选择 |
| ohmyzsh | Linux/macOS | YAML 配置 | with_p10k/switch_shell 已接入；继续验证 shell 切换失败语义与配置恢复；无升级/版本选择 |
| tailscale | Linux/macOS | YAML 配置 | exit_node 已接入；统一菜单安装与 prepare_client；安装不隐式登录，macOS 不支持出口选项提前拒绝 |
| wg_server | Linux | YAML 配置 | domain/public_ip/credentials_output_file、subnet_octet/label/acme_email 已接入；服务/状态/导出失败回滚 |
| wg_client | Linux | 令牌文件 | --token-file 和隧道管理已接入；显式令牌错误已改为抛出失败；真实无交互安装和跨进程操作一致性仍待验收 |
| xui | Linux | YAML 配置 | host/端口/用户/路径/sni/dest/密码文件及凭据输出已接入；完整事务恢复和真实 API 验收 |
| wireguard | Linux | 分类入口 | 仅分组，不是可安装软件；脚本明确选 wg_server/wg_client，不能打开子菜单或宣称安装成功 |

所有平台声明来自当前注册表，实际支持程度必须以平台测试确认。`has_upgrade=False` 不等于遗漏参数，不为无实现的功能添加空入口。`manage` 是菜单兼容入口，脚本必须使用下面的具体命令。

## 4. 各官方模块管理命令（P0/P1）

| 功能 | 已接入入口/参数 | 需补齐与验收 |
|---|---|---|
| WireGuard 服务端 | `server status/diagnose`；`peer list/add --name/rename ID --name/remove ID/export ID --output-file` | 完整诊断包含配置、wg/xray/nginx、监听/转发及路由；节点 runtime/config/state 更新失败须补偿；稳定公钥 ID，重名和未知 ID 语义明确 |
| WireGuard 客户端 | 安装 --token-file；客户端隧道命令已接入 | **已接入，待完整验收** `client status/diagnose [--tunnel ID]`、`client tunnel list/add --token-file/remove ID/update-token ID --token-file`；ID 为受验证 wg_iface，名称/菜单序号不充当 ID。删除要求 --yes；拒绝受污染 state 的路径/服务标识 |
| 客户端事务 | 更新令牌流程已有 | 已修复服务失败后仍保存新 token/state；配置和状态快照、健康检查及恢复已接入，失败注入通过；真实部署、并发一致性和完整恢复验收待完成 |
| XUI | `status/diagnose/traffic/logs --lines --follow/links --output-file` | 已补面板配置一致性/监听归属、只读 DB 入站启用与 REALITY、loopback CSRF API；53 项联合通过、1 项跳过。共享预算及 --timeout已接入；认证API、Xray监听、REALITY握手及真实平台验收仍待补齐；HTTPS 证书检查仅对实际配置 HTTPS 的部署适用，不能把当前 HTTP 面板/REALITY 当缺证书故障。日志持续取消/脱敏及菜单实际能力继续核对，禁止任意 systemctl/shell 透传 |
| Tailscale | `status/diagnose/login --wait --timeout --advertise-exit-node/down/restart` | 登录总预算已包含初始命令、每次状态查询和等待，拒绝晚到的成功状态；macOS 不支持出口发布显式拒绝；相关联合 61 项通过。服务诊断细化及真实守护进程/提权取消回收仍待验收；授权 URL 只在显式 login 返回 |
| Oh My Zsh | `status/p10k apply-preset/p10k configure/shell switch` | configure 保持交互专属、无交互拒绝；preset 失败检测，切换验证和备份恢复，勿把通用 software switch 的版本参数用于 shell |
| RustDesk | `software diagnose rustdesk` 只读报告已接入 | 二进制/服务/密钥文件和 TCP 21115/21116/21117、UDP 21116 的监听及实际程序归属检查已接入。端口由其他程序占用或不可见不能作为健康；相关联合 49 项通过。共享预算及 --timeout已接入；继续补配置/可选WebSocket和真实RustDesk Linux验收，无须新增没有业务需求的管理组 |
| 监控 | dashboard/cpu/memory/disk/network/processes；once/watch/count/interval、pid/limit/sort/interface/mount | 已按功能注册准确签名，无关参数在解析阶段拒绝；PID直接读取，消失/权限/僵尸保留错误报告并非零退出；CPU、网络、进程schema2明确单位/时间窗口/nullable。最新93项相关回归见台账；其他平台、同步查询整体监督、真实身份变化及性能基线仍待验收 |
| 网络 | ping count/timeout；traceroute max-hops/timeout；dns family/reverse/timeout；port-scan ports/timeout/concurrency/total-timeout；speed-test url/duration；public-ip timeout | DNS/扫描/公网 IP/测速总预算及回收已接入；HTTP 联合 39 项通过。ping 前置解析共享总预算及失败 envelope 已修复，相关联合 55 项通过。公网响应/下载上限已实施，测速采样精度与字节语义见契约；真实代理/TLS、单端口旧 helper、打包与跨平台验收仍待完成。不未经实现增加 UDP 扫描 |
| 偏好 | preferences show、theme list/set、language list/set | 无效名称、写失败、重复 invocation 状态隔离；只读不导出其他配置/秘密；不增加任意配置写入命令 |

上述 WireGuard client 子命令已接入，基础回归通过；真实 Linux 安装和完整诊断验收待完成。

## 5. 平台插件与更新命令（P1/P2）

插件已有 list/show/install/update/remove/enable/disable/trust/revoke，安装支持 --branch/--as，授权要求 --fingerprint。prepare/fingerprint已迁移生命周期模块，统一结果/错误契约且不boot；prepare不授权，fingerprint校验只读。prepare要求--base-image的真实registry digest及--lock-file完整wheel哈希锁，--dependency只核对锁内版本；SDK四项根依赖不沿用平台完整requirements。执行时核对policy7/schema2、锁、来源digest和当前SDK摘要的镜像label绑定，旧运行时要求重建。真实Linux基础隔离通过；完整生命周期、多架构及漏洞审查仍待验收。

`plugin run` 的子进程退出码和业务 stdout 属于插件协议；不要强行用平台 JSON 包住插件任意输出。平台侧前置错误仍用平台 envelope。帮助与参数原样传递，验证插件与官方同名不劫持命令、缺 sandbox 不退回宿主执行。

显式更新CLI：`update status/check`已接入只读受监督查询；`apply`已接入源码模式拒绝、确认、可信缓存版本和真实签名门槛。Windows成功结果仅为scheduled，Unix为applied，均返回health_confirmed=false。download已接入总预算、独立签名候选和原子指针发布，完整应用事务/恢复仍待完成，需要回滚操作时先定义有效备份ID和确认语义。check只查询，download不自动应用；保留签名、SHA256、可信版本、健康确认与恢复机制。apply尚无完整受监督总期限，不注册占位--timeout；源码运行明确拒绝。沿用现有更新服务，不再写第二套下载/替换逻辑。status显示待更新文件但不把它称为已验证；check只返回候选版本，不代表签名通过。

Windows延迟应用现使用独立任务产物、摘要绑定和目标旁原子提交，保留原ACL；移除直接覆盖及共享pending重启移动回退。真实PowerShell临时文件成功、篡改、占用、等待和平台锁竞争已有验证。status提供缓存任务的白名单state及实际health_confirmed；scheduled不代表进程当前存活。跨数据目录目标锁已有本地验证；失联任务恢复、失败启动索引、marker恢复记录及任务产物保留上限仍需完成。

## 6. 实施顺序和完成门槛

1. **P0：先修业务失败语义与边界。** WireGuard 客户端/节点事务、受污染标识拒绝、全 CLI 无交互和退出码/输出一致性。验收包含恶意 state、失败注入、缺参、取消、stdin 关闭，确认无系统越界操作。新命令不得放大既有故障。
2. **P1：补齐业务覆盖。** 客户端隧道命令、RustDesk/各服务诊断、18 个 Recipe 生命周期矩阵、监控准确 help；菜单与 CLI 使用同服务，每项附正常/失败/权限/不支持平台用例。
3. **P2：平台更新与维护边界。** 显式更新入口、插件 prepare/fingerprint、main 注册化、能力矩阵由真实能力生成；没有双套实现和第三方参数耦合。
4. **P3：完整验收。** 全套测试、wheel 入口、Windows/Linux/macOS 与 Python 3.10/3.12 CI；在隔离环境执行支持平台的真实安装/卸载/服务重启/取消恢复。生产签名和真实原生发布需要实际信任根及发布环境，不能用测试密钥证明完成。

每项完成必须同时满足：help 与文档一致；正确平台；无隐藏输入；成功/失败退出码准确；结构化结果可解析；不泄漏秘密；取消回收；状态一致/失败恢复；旧菜单回归通过。单测通过不等于生产安全达标。

性能验收另记录冷启动、只读列表、监控采样、端口扫描 p50/p95、峰值内存、导入模块数和取消后残留；相同负载比较再宣称优化。先保证每项参数对应真实可测试的能力，不靠增加参数数量或无业务依据的抽象提升“完善度”。

进度与已执行测试以 `implementation-acceptance.md` 为准，本文件作为缺口和验收依据；新增命令后同步 `cli-contract.md` 与示例。

## 7. 参数补齐的可执行批次

以下为待执行任务，目标参数不表示目前可用。不承诺无依据的工期：完成一批并通过验收再进入下一批。已有参数优先补行为，配置字段继续留在所属模块，不把第三方参数加入 main。

| 批次 | 范围与参数决策 | 具体交付 | 完成检查 |
|---|---|---|---|
| A / P0 | 九种版本软件：保留现有 --version/--all，不增加重复选项 | home 资源写操作互斥已实施，覆盖 Recipe 直接调用；仍需系统路径跨用户锁、node/npm、java/JAVA_HOME、go/GOROOT 等伴随状态及真实恢复；损坏快照拒绝覆盖已实施 | 同一 home 两进程竞争拒绝、保存/链接/取消补偿通过；系统服务恢复及跨平台验收仍未完成 |
| B / P1 | monitor 六种命令：共用 --once/--watch/--count/--interval；仅 processes 有 --pid/--limit/--sort，network 有 --interface，disk 有 --mount | 准确签名和未知过滤器错误已实施；count=0在watch中无限采样、非watch默认一次；PID避免全进程枚举；CPU/网络/进程schema2采样元数据已接入 | 参数/帮助/JSONL/取消及Windows/Linux部分原生检查通过；其他平台、同步查询整体监督和性能验收尚未完成 |
| C / P0 | network dns 的 --timeout 和 port-scan 的 --total-timeout 已实施；其余现有期限选项完善语义 | DNS 默认 10 秒，扫描总预算默认 30 秒、单连接默认 0.5 秒；DNS/扫描/HTTP/ping 的预算与资源上限已接入，继续核对旧单端口 helper、代理/TLS及打包行为 | 本地慢 DNS、扫描、慢 HTTP、取消及真实 IPv4/IPv6相关回归通过；全部平台与代表性性能验收尚未完成 |
| D / P1 | tailscale login 保留 --wait/--timeout/--advertise-exit-node；五种诊断已统一 --timeout（30秒，0.1..3600） | 五种Recipe及专用CLI已共享期限参数和只读报告；继续完善配置/监听/API/TLS/路由、其他菜单复用及实际平台验收 | 基础报告只读/无秘密/健康故障矩阵通过；完整检查与实际支持平台待验收；登录初始命令已计入总预算 |
| E / P1 | 四种 --config 安装与 wg_client --token-file | 保持第 3/4 节已有字段；版本化 schema、失败恢复、示例验证；敏感输出路径不可覆盖；不增加通用任意配置写入参数 | 示例可以无交互执行；未知字段/权限/安装中断/导出失败有测试；真实 Linux 服务验收 |
| F / P2 | update status/check/download/apply/reconcile | download已按现有策略选择版本，--timeout覆盖查询/下载/签名/发布；候选配额/清理已接入，任务配额及索引任务reconcile已接入；继续未索引/旧任务、退役及marker恢复；不新增无完整行为的rollback | check 不修改；download 不应用；无效签名/旧版本/取消拒绝；真实信任根发布验收另列 |
| G / P2 | plugin prepare/fingerprint 与 run NAME -- ARGS | 生命周期envelope/本地构建监督已接入；prepare新增必填--lock-file和digest来源--base-image，要求全部wheel哈希。插件自己的帮助、选项、错误码由插件维护 | 带 --output/--help/大小写/重复参数原样传递；同名不劫持官方命令；旧未锁运行时拒绝；继续daemon取消与真实容器/多架构验收 |
| H / 发布门槛 | 全部官方入口 | 文档、help、能力表、菜单与服务一致；wheel 入口；Windows/Linux/macOS 与 Python 3.10/3.12 CI | 每种声明平台正常/失败/权限/取消验证；测试跳过项解释；未完成的真实发布验证不标完成 |

不列为必补参数：无实现的数据库管理命令、Docker/Nginx 的任意版本选择、UDP 扫描、通用 shell 透传，以及不能真实模拟变更的 --dry-run。若后续有明确需求，再先定义业务服务与安全边界。

## 8. 每个功能的完成记录模板

每项在实施台账记录：命令和所属模块；已支持参数/默认值/范围/互斥关系；缺口是新参数还是行为修复；支持平台；stdout/stderr/退出码；权限与敏感输入；期限与取消；正常、失败、恢复测试；真实环境是否验收。只有所有适用项通过才标记完成。

性能跟随功能验收：冷启动/只读查询/监控/端口扫描测 p50/p95、峰值内存和取消残留；先做基线再优化。代码清理需要调用链、动态注册和打包证据，不能因为命令未完全脚本化就删除官方能力。

## 9. 参数逐项核对清单

本节按当前入口源码核对。命令中的 NAME/HOST/ID 是位置参数；全局参数放在子命令前，例如 `opskit --output json --non-interactive software list`。目标参数必须实现服务行为后才注册，不能先加 help 占位。

| 官方功能 | 当前参数及约束 | 必须补齐的内容 | 类型/优先级 |
|---|---|---|---|
| 根命令 | --output text/json（默认 text）、--non-interactive、--yes、--theme/-t、--lang/-l、--version/-v | 全部入口一致的输出/缺参/无输入/取消；全局 --version 与软件版本参数的作用域写清 | 行为验收/P0 |
| software 查询 | list/search：--category、--platform；search QUERY、versions NAME、capabilities NAME、installed | 能力表与各平台实际驱动一致；远端版本获取失败明确报告；help 不隐式查询网络 | 行为验收/P1 |
| 九种版本软件 | install/upgrade/switch NAME --version；uninstall NAME --version 或 --all；版本标识最长128字符 | 第3节逐软件伴随状态、数据保留、失败恢复；同一资源并发互斥；不是继续增加重复版本参数 | 行为修复/P0 |
| Docker/Nginx/RustDesk | software install/uninstall NAME；RustDesk 可 diagnose | 系统包操作失败/取消恢复、用户数据保留；RustDesk深度诊断 | 行为验收/P1 |
| 配置安装 | software install NAME --config FILE；仅 ohmyzsh/tailscale/xui/wg_server；schema_version: 1，文件≤64KiB | 四种配置的已支持字段见第3节和示例；未知字段拒绝、配置/令牌/版本互斥，实际安装和导出失败恢复 | 行为验收/P0 |
| WG客户端安装 | software install wg_client --token-file FILE或-；旧 --token/-t仍兼容 | 推荐文件/stdin；秘密不进命令行；旧参数如废弃需兼容周期和迁移说明，不直接移除 | 安全契约/P0 |
| WG服务端管理 | server status/diagnose；peer list；add --name；rename ID --name；remove ID；export ID --output-file | diagnose已提供 --timeout（30秒，0.1..3600）；配置/服务/监听/转发/路由检查；节点修改事务与并发；无交互删除用根级 --yes | 新参数+行为/P0 |
| WG客户端管理 | client status/diagnose --tunnel（默认全部）；tunnel list/add --token-file/remove ID/update-token ID --token-file | diagnose已提供 --timeout（30秒，0.1..3600）；隧道逐项健康、并发更新与恢复；ID必须为已验证接口ID | 新参数+行为/P0 |
| XUI | status/diagnose/traffic；logs --lines（80，1..10000）/--follow；links --output-file必填 | diagnose已提供 --timeout（30秒，0.1..3600）；认证API/入站归属/REALITY配置验证；follow取消、输出脱敏；安装恢复 | 新参数+行为/P0 |
| Tailscale | status/diagnose/down/restart；login --wait/--timeout（60秒，1..3600）/--advertise-exit-node | diagnose已提供 --timeout（30秒，0.1..3600）；状态/服务的共享预算；实际提权进程树回收；macOS不支持出口发布提前拒绝 | 新参数+行为/P1 |
| Oh My Zsh | status；p10k apply-preset/configure；shell switch | configure保持交互专属；预设与shell修改验证/备份恢复，不新增任意shell执行参数 | 行为验收/P1 |
| monitor 六种采样 | --once/--watch互斥；--interval（1秒，0.1..3600）；--count（0起，watch中0无限，否则默认1次） | 时间戳/单位与JSONL契约；有限采样、无限采样取消与跨平台负载验证 | 行为验收/P1 |
| monitor processes | 另有 --pid（1..4294967295）、--limit（15，1..10000）、--sort cpu/memory（cpu） | pid与显式limit/sort已互斥（含默认值）；单PID不枚举全部；CPU窗口/单核百分比/null及身份复核已实现；其他平台、真实权限/复用与性能仍待验收 | 参数组合契约/P1 |
| monitor network/disk | 分别另有 --interface/--mount；其他采样命令无这两项 | 不存在过滤器返回参数错误；不可读取与不存在的原因应可辨识 | 行为验收/P1 |
| network ping | HOST；--count（4，1..10000）、--timeout（30秒，0.1..3600） | 总预算而非每包重置；不可达失败且保留测量；原生各平台验证 | 行为验收/P1 |
| network traceroute | HOST；--max-hops（20，1..255）、--timeout（120秒，0.1..3600） | 每跳JSONL和退出结果一致；无输出时超时、取消后进程树无残留 | 行为验收/P1 |
| network dns | HOST；--family any/ipv4/ipv6（any）、--reverse、--timeout（10秒，0.1..3600） | 数字IP自动反查；反查仅允许family=any，其他值已在resolver前拒绝，本地矩阵通过；原生打包验证仍待完成 | 参数组合契约/P1 |
| network port-scan | HOST；--ports逗号/范围（默认常见端口）；--timeout（0.5秒，0.01..60）；--concurrency（16，1..50）；--total-timeout（30秒，0.1..3600） | 明确目前只扫描首个解析地址的TCP；总预算耗尽不能伪装完整结果；旧helper统一与代表性负载验证 | 行为验收/P1 |
| network speed-test/public-ip | speed-test --url（默认平台测速地址）/--duration（15秒，0.1..300）；public-ip --timeout（10秒，0.1..60） | URL无凭据、字节和时间语义、代理/TLS/取消；保留响应体/下载体积上限 | 行为验收/P1 |
| preferences/theme/language | preferences show；theme、language均list/set NAME | 只读无初始化写入；无效名称/持久化失败及配置权限测试 | 行为验收/P2 |
| 平台更新 | status --timeout（10秒）、check/reconcile --timeout（30秒）、download --timeout（300秒），范围0.1..3600；apply要求打包模式及确认 | download已接入--timeout（300秒，0.1..3600）和不可变候选发布；reconcile已核对当前索引任务并修复记录；继续未索引/旧任务/marker及退役；apply期限须先有事务监督；Windows scheduled不等于完成 | 新入口+事务/P0 |
| 插件生命周期 | install URL --branch/-b/--as；trust NAME --fingerprint必填；prepare PATH --dependency可重复，--base-image digest及--lock-file必填；fingerprint PATH --check/-c | 输出/监督及哈希锁策略已接入，真实pip验证错误哈希/缺传递依赖拒绝；继续daemon取消、Linux多架构及恶意包/插件验收；不扩大授权 | 行为+安全/P1 |
| 插件业务 | plugin run NAME -- ARGS | ARGS由插件独立解析，包括help/重复参数/大小写；平台不注册业务选项、不导入插件业务代码 | 边界验收/P0 |

`software manage`及隐藏的`software xui-snapshot`属于现有兼容/内部入口。先确认菜单、构建或子进程调用证据，再决定迁移或删除；不把它们当新的公开脚本接口推广。

## 10. 下一步执行队列与验收门槛

最新Windows写入边界：四驱动命令文件已统一受管内容/名字/类型预检、原子发布及失败/取消补偿；Node/Go/Java不再在新shim内解析JSON，gofmt路由已修正。命令复核纳入用户registry/profile作用域，原生cmd参数/退出码与Unicode账户路径已有临时验证。本批完整2252通过/18跳过及wheel证据见实施台账；Python活动路由、junction与多PATH写入、快照/版本目录和断电的完整事务仍属第2项，不能视为九软件整体完成。GitLab两版本Linux门槛现有配置，尚待实际Runner；GitHub六组合需验证镜像及当前提交，不把本地通过充当远端发布门槛。

1. **诊断期限和进程监督。** 先实现报告服务的共享预算，再接CLI的 --timeout；五种Recipe及专用入口必须一致支持。SQLite查询、监听枚举、状态读取和API不得绕开预算；前后检查无法中断阻塞调用，必须有实际可终止机制。验证预算跨阶段消耗、耗尽后不再启动下一步、晚到成功拒绝、取消及进程树回收。五种报告已接入共享预算：XUI状态/监听/SQLite路径解析及打开查询、RustDesk文件元数据受一次性worker监督；SQLite打开已用真实FIFO反例修复并完成三端相关回归及Linux已安装helper的超时/SIGINT补验，最终整仓/冻结仍须包含该增量；WG状态与服务、Tailscale查询采用进程树监督。通用software diagnose及专用入口已提供 --timeout（默认30秒，0.1..3600）。仍需真实Linux/原生打包、特殊文件系统及完整服务诊断验收；不承诺进程创建/回收的硬实时上限。
2. **写操作事务和并发。** 按九种版本软件→WG→XUI→其他系统服务推进；存储、配置、链接、服务操作明确补偿顺序。失败注入验证旧状态可用，涉及系统路径的跨用户锁不能由home锁代替。
3. **完善脚本契约。** 核对上表参数组合、所有help及stdout/stderr；prepare/fingerprint已统一，继续实际构建/隔离验收，插件参数原样传递。明确拒绝无效组合或文档化合理组合，不能静默忽略。
4. **补显式更新入口。** 重用现有签名更新服务，先查询和下载，再应用/恢复；无效签名、降级、取消及恢复失败必须有验收证据。
5. **清理和发布门槛。** main只保留注册；逐条验证冗余再删除；CI覆盖声明平台/Python版本；秘密历史扫描、漏洞/SBOM、wheel/原生产物、插件攻击样例和实际服务部署。性能测冷/热启动、p50/p95、RSS、取消残留，不以参数覆盖率代替可维护性和安全验收。

每批交付都同步本表、cli-contract和implementation-acceptance。最终通过标准是“参数对应真实行为、失败不会误报成功、状态可恢复、第三方隔离成立”；当前仍未达到完整发布验收标准。

## 11. 更新链路当前实施状态

下面是当前代码能力；测试明细和各批历史统一保留在implementation-acceptance.md，不在计划中累积重复测试数字。

| 能力 | 当前实现 | 尚缺验收 |
|---|---|---|
| 显式查询与下载 | status/check/download；download总预算覆盖查询、传输、签名及不可变候选发布 | 生产信任根、原生打包和全部平台 |
| 原子应用与版本权威 | 跨数据目录目标锁、from/to摘要和事务ID、Windows独立快照及提交前prepared绑定 | apply总监督期限、真实多用户和断电环境 |
| 启动与进程身份 | 启动前索引及preparing/launching；running.json精确创建ticks、映像和File参数；健康归属job_id | 无ACK且已尝试启动的任务仍无法证明退出，保持deferred |
| 缓存与备份 | 候选4条目，任务8条目，分别2GiB+1MiB含预留；备份保留最近3份及health/marker/job引用 | 成功任务退役、旧嵌套目录迁移、未知内容人工处置 |
| 失联记录核对 | update reconcile --timeout（默认30秒，0.1..3600）；根锁与目标锁内验证当前索引schema2任务、恢复备份及实际摘要 | 未索引任务、旧schema、无ACK启动意图、其他目标或被新事务替代的任务继续保留 |

reconcile不会执行产物、替换程序或杀进程。确认helper退出后，新字节重建applied/health；旧字节关闭为aborted并记录failed；明确未启动的preparing只允许关闭旧字节事务。现有健康确认和失败次数保留，摘要不匹配或权限不足拒绝。recovery.json与helper结果分离，status优先使用有效恢复凭据。启动先以30秒预算核对已索引任务，未解决时暂停自动更新写入，包括post-update清理；普通官方功能不因此删除。

## 12. 更新剩余安全验收

1. **未索引与旧任务。** 在有界库存、当前目标和真实事务权威下迁移/核对；不能只按目录时间或PID删除。无ACK启动记录需要明确可证的恢复协议。
2. **任务退役与引用。** 当前已支持已索引schema2任务在确认helper退出、实际目标恢复、反向事务及备份/健康归属一致后持久化回滚退役凭据，再将活动索引转为last_apply_job历史。历史及健康引用继续保护备份；其他成功/失败、未索引与旧任务仍需退役协议，容量不足安全拒绝。

   健康记录的发布/确认/计数/回滚判断已统一根锁，并有真实进程竞争验证；后台只对明确竞争有界重试。目标记录已持久化upgrade_floor：后续升级build必须严格高于历史最高回滚build，后续准备失败不能消除下限。check过滤已拒绝build，download在传输前拒绝，apply在备份前及事务准备时复核。已接入索引任务prepared回滚的启动重试（保留原事务ID）；仍需覆盖无索引回滚、真实运行程序替换与跨数据目录健康归属，不能以持锁替代完整退役设计。
3. **marker恢复。** 补完整备份与事务凭据、签名候选引用及失败启动一致性；避免重试绕开原有可信校验或覆盖新事务。
4. **总期限与崩溃恢复。** apply需要事务监督后才增加timeout；补真实中止/断电和记录写入中断的重启验收，模拟I/O故障不能代替断电。
5. **原生发布。** Windows/Linux/macOS声明范围、多账户权限、wheel/原生构建及正式签名；合成密钥和本地单测不能证明生产完成。

## 13. 本次最终排期建议

第9节是逐功能参数清单，第3节是18个Recipe覆盖表。执行时将“已有参数但行为未验收”和“确实缺新入口”分开；不要将表内目标参数当作已支持，也不要给没有业务实现的功能加空选项。

| 顺序 | 本轮交付边界 | 参数需要补什么 | 必须通过才结束 |
|---|---|---|---|
| 1 / P0 | 更新任务存储与恢复 | 先不新增cleanup/force/rollback参数；apply总期限必须先具备事务监督 | 已有配额与索引任务核对；继续退役、未索引/旧任务、marker及真实断电恢复；实际更新完成与scheduled区分 |
| 2 / P0 | 九种版本软件及WG/XUI写操作 | 保留已有version/all/config/token-file/output-file，修互斥及不支持参数拒绝 | 系统资源跨用户锁、伴随PATH/服务状态、取消与补偿；卸载保留用户数据 |
| 3 / P1 | 插件生命周期 | prepare/fingerprint已统一并迁出main；来源digest和完整wheel锁已要求，source wrapper复用CLI；插件业务参数仅由插件解析 | 继续实际容器的完整安装、最小代码运行时、授权竞态及恶意插件样例；不写平台依赖/源码 |
| 4 / P1 | 网络及监控参数组合 | 明确dns family/reverse、processes pid/limit/sort；各命令准确help | 参数无静默忽略、单位/时间戳、期限/取消、真实平台采样与TCP扫描性能基线 |
| 5 / P1 | 五种服务诊断及配置安装 | diagnose已有timeout，重点补实际检查；四类安装schema与示例一致 | WG路由/转发、XUI认证及入站、Tailscale守护进程、RustDesk实际配置；只读与脱敏 |
| 6 / 发布门槛 | 全部官方入口 | 所有help、示例和能力表一致；不增加无实现选项 | 六组合远端CI、wheel/原生产物、真实服务操作、正式签名及秘密/漏洞/SBOM检查 |

Windows任务已增加schema_version=2的backup_id/transaction_id恢复引用；status兼容旧schema1。内部存储限制8个任务、2GiB+1MiB（计入下次快照和元数据预留），仅在配额压力下非递归清理无目标事务且可证明未启动的preparing任务，或无目标事务且有确认退出helper及失败结果的任务。活动索引、health/marker任务和备份引用受保护；未知/旧任务、应用成功、恢复不完整、仍绑定事务的失败任务保留。安全空间不足时拒绝调度，不删除不确定内容。该机制不是完整失联恢复，也不需要新增公开cleanup参数。

当前回归结果与源码时点统一以implementation-acceptance.md为准；完整安全及跨平台发布验收仍待完成。

## 14. 逐功能补齐的交付规则

第3节逐Recipe、第4节逐管理模块、第9节逐参数是完整清单，第13节为执行顺序。每项以对应模块为修改边界，交付时同时登记以下内容：

| 交付项 | 必须明确 |
|---|---|
| 参数清单 | 必填、默认值、范围、重复参数、互斥组合、不支持平台；已有参数与计划参数分别列出 |
| 业务缺口 | 缺入口、缺实际行为或缺验收证据；无实现的选项不注册，不以添加参数替代修复 |
| 实现边界 | CLI只解析与输出，业务服务统一菜单/CLI，驱动处理系统差异；第三方参数留在插件目录 |
| 失败与安全 | 缺参退出2、执行失败退出1、取消130；无隐藏提示、秘密不入普通输出、失败可恢复 |
| 验收证据 | 正常、缺参、互斥、权限、不支持平台、超时/取消及恢复；记录实际平台与跳过原因 |
| 完成状态 | 入口已接入、行为已验证、原生验收完成分别记录；未通过适用项不标记完善 |

优先处理更新与官方写事务，其次插件供应链、网络/监控组合、服务诊断，最后统一发布验证。apply的--timeout、公开rollback与cleanup等均不得先增加占位参数；应先完成事务监督、有效备份身份和安全恢复协议再决定接口。

## 15. 参数补齐决策与分批验收

后续故障补验已将Linux受控拓扑扩为8项，增加两种地址族各一次真实超时和SIGINT取消；连同回环共10项。终态/退出码、已知工具子进程退出、命名空间清理、267安装字节及原始输出均已独立复核，详见验收台账及 `cache/linux-traceroute-netns-20261016-r3/audit.json`。仅关闭该拓扑的超时/取消缺口，公网、普通用户权限、其他工具版本与冻结产物继续开放。

2026-10-16原生补验：当前能力批次实际安装wheel在Linux/WSL x64完成6项traceroute检查，包含IPv4/IPv6回环、隔离两跳到达和限制一跳未到达。均核对流最后唯一complete事件及退出码，未到达时即使底层工具退出0，CLI仍退出1。267安装文件与wheel核对一致，三个测试网络命名空间已清除；证据见验收台账及 `cache/linux-traceroute-{native,netns}-20261016-r1/audit.json`。下表“Linux原生待验收”已部分补齐，公网、不同版本、普通用户权限、取消/超时和最终原生产物仍开放，不将6项结果算作全部网络入口完成。

本次重新核对monitor/cli.py、network/cli.py与core/update_cli.py：现有第9节清单继续适用。下表前两项已实施并通过本地参数组合回归；其余项目仍是目标契约，不表示已经实现。当前完整回归结果见实施台账。

| 缺口 | 目标契约与实施方式 | 验收样例 |
|---|---|---|
| DNS反查时family静默失效（已修复） | 保留数字IP自动反查；反查仅允许family=any，其他值退出2，说明family仅用于正向解析；先检查组合再启动resolver | IPv4/IPv6数字地址分别结合any/ipv4/ipv6和reverse；错误组合无解析调用，本地矩阵通过 |
| processes指定pid时limit/sort静默失效（已修复） | pid查询仅返回该进程；显式指定limit或sort与pid组合退出2。用Click参数来源区分默认值与用户显式输入，不能只比较值 | pid单独成功；pid加显式limit=15或sort=cpu也拒绝；无pid时默认列表参数仍成功，本地矩阵通过 |
| 监控interval与真实采样单位 | interval定义为两次采样之间的等待时间；记录采样时间和各字段单位；CPU百分比需要明确采样窗口，网络累计字节不得标成每秒速率 | once/count/watch组合、JSONL数量、时间与单位、有限采样结束、无限采样取消 |
| traceroute流结束缺完整结果语义（本地已修复，原生范围继续验收） | 保留逐行输出；最后event=complete记录status=reached/not_reached/failed/timeout/cancelled。仅命令退出0且数字hop匹配固定目标才成功；DNS和命令共享timeout | 双平台40项相关回归各通过，Windows实际loopback到达通过；macOS原生IPv4/IPv6回环CLI均退出0且complete/reached、固定目标一致，证据见验收台账。本轮WSL缺少traceroute；Linux原生、非回环、不同版本与权限仍待验收 |
| install/uninstall/upgrade等长写操作无总期限 | 先实现事务监督和取消恢复，再决定各操作timeout默认值；安装编译、下载与服务重建不能强行共用一个未经实测的默认值 | 下载中断、编译卡住、服务重建失败、状态写入失败；旧版本/数据恢复后才能推出参数 |
| update apply缺timeout | 前置条件是helper/事务的独立监督和重启恢复；期限耗尽返回实际事务状态，不能杀掉helper后直接声称回滚 | 调度、替换、健康等待各阶段超时；重启后的目标摘要与事务日志一致 |
| 服务管理命令的阻塞预算 | 审查status/traffic/logs/down/restart等真实调用；有阻塞的命令复用Deadline与进程监督后提供timeout；follow需要取消与单次读取预算，不用总期限破坏持续日志用途 | 守护进程无响应、提权等待、API卡住、日志空闲取消；没有子进程残留 |

交付按六批执行，不按新增参数数量计算完成度：

1. **参数组合修复**：DNS、进程过滤器、once/count/watch；先补失败用例，再修解析与文档。每项错误组合必须在业务调用前退出2。
2. **官方软件写事务**：第3节逐一验收九种版本软件和非版本Recipe；完成资源锁、补偿和数据保留，再开放长操作期限。
3. **服务安装与管理**：四种配置安装、WG客户端令牌及WG/XUI/Tailscale/RustDesk/Oh My Zsh的实际行为；菜单与CLI复用服务。
4. **输出与期限**：监控单位/时间、traceroute终态、网络/诊断预算、日志取消；验证实际stdout/stderr和退出码。
5. **更新与插件边界**：更新恢复完成后决定apply期限；第三方参数只存在插件目录，平台负责生命周期和受限执行，继续真实容器验收。
6. **发布验收**：每个声明平台的help快照、正常/失败/无交互/权限/取消矩阵、干净wheel与原生产物；同步实际远端CI及安全扫描结果。

每批输出“命令→参数→业务服务→支持平台→测试证据→剩余风险”的记录。参数已注册、行为本地通过、真实平台验收三种状态分别记录，不合并成一个完成标记。第3、4、9节仍是完整功能库存，本节不缩减任何官方能力或整体八类健康计划。

索引schema2任务的prepared回滚已接入启动专属恢复：公开reconcile只返回rollback_pending；启动worker在锁内证明helper已退出、健康/备份/目标绑定后复用原事务重试，恢复后先重启再清理。真实PowerShell与临时字节文件验证通过，详细证据见实施台账。仍需Windows正在运行的冻结程序延迟替换、无索引回滚及真实断电验收。

## 16. 本次复核：哪些需要加参数，哪些需要补行为

再次直接核对monitor/cli.py、network/cli.py和core/update_cli.py。结论仍是官方CLI未完全完善；第3节18个Recipe、第4节管理模块和第9节参数表共同构成库存，不以此节替代完整清单。

| 分类 | 涉及命令 | 明确下一步 | 开放条件 |
|---|---|---|---|
| 确实缺操作期限参数 | update apply；软件install/upgrade/uninstall；存在阻塞的服务管理操作 | 先列出各阶段阻塞点、事务记录与取消补偿，再设计所属命令的timeout；默认值由实际负载决定 | 耗尽预算后返回真实状态；重启可恢复；不将杀进程等同回滚 |
| 参数已存在、输出契约不足 | monitor六种采样；network traceroute | 明确采样时间、CPU窗口、累计字节/速率单位；traceroute增加可区分完成/未到达/超时/取消的终态 | 有限JSONL条数与退出码一致；无限采样可取消；更新输出schema和示例 |
| 参数已存在、行为待完整验证 | 九种版本软件；WG/XUI写操作；配置安装；五种服务诊断 | 验证伴随环境/服务、权限、数据保留、故障补偿和原生平台；诊断不能仅检测二进制存在 | 失败不误报成功；跨用户锁覆盖实际系统资源；正常/失败均有原生证据 |
| 参数组合已修复、需持续门槛 | dns family/reverse；processes pid/limit/sort | 保留参数来源判断，防止显式默认值绕过互斥；在声明平台和独立安装环境复核 | 错误组合在业务调用之前退出2；help与文档一致 |
| 第三方参数必须独立 | plugin run NAME -- ARGS | 插件自行声明和解析业务参数；平台仅处理生命周期、身份、权限、预算和路由 | help、重复参数、大小写和顺序原样传递；无宿主业务导入和依赖污染 |
| 无需为完整性增加参数 | 不支持版本的系统包；不存在的数据库管理功能；任意shell/force/cleanup | 依据真实能力拒绝不适用参数；不存在安全实现的功能不注册占位入口 | 能力表与实际驱动相符；兼容入口按调用证据决定保留或迁移 |

官方安装命令的参数完善还必须包含包提取安全。Windows版本型ZIP、Linux/macOS各软件tar、PostgreSQL/Redis Debian及Redis源码构建已迁移共享提取和独占staging；Windows Python UV引导已取消远程PowerShell执行。共享ZIP重名/布局冲突、Windows别名、既有文件及竞争创建拒绝已修复。归档读取已统一core.archive_reader，在stdlib构建成员之前限制扩展头、ZIP目录及解压读取；PAX原始记录前置限额及显式/隐式目录布局预算已接入，插件使用更小额度。Linux UTF-8/ASCII原生机制各22项通过。Redis 7.4.2真实源码包1697成员通过新解析和布局检查，但未编译，观测摘要不是独立来源信任。仍须验收生产来源与固定可信摘要、多平台真实包及伴随工具、整次预算和并发临时磁盘；后续PATH/服务事务未完成，不能把包提取完成标为安装事务完成。无生产调用链的MySQL apt.py已移除，不为补参数重新引入废弃功能。

建议下一批的具体顺序：先复核共享包提取及生产来源，继续更新恢复与系统写事务；随后补监控输出和traceroute终态，再逐服务核对管理预算与诊断。每批复用已有参数、服务和验证工具，避免为同一功能新建另一套命令。每轮源码稳定后执行完整回归、wheel重建与独立安装检查；具体结果及对应源码范围以implementation-acceptance为准，历史产物不能作为后续源码的发布证据。

完成记录采用四列：参数是否存在、业务行为是否验证、原生平台是否验收、当前源码是否经过发布检查。四列均满足适用条件才标记完成；缺运行环境的项明确待验收，不按通过的子集关闭整个计划。

## 17. 监控参数背后的具体采样缺口

本次直接复核monitor/cli.py和monitor/commands.py。以下是实际实现需要梳理的行为，不需要再添加一套监控参数。

| 现有入口 | 实现证据与影响 | 最小改进与验收 |
|---|---|---|
| cpu/dashboard | 已改为同一对每核时间计数，只等待一次并按CPU时间增量加权总量；CPU schema2记录UTC起止、实际窗口和单位，异常计数nullable且CLI非零 | Windows/Linux及macOS Intel真实同计数psutil对照已有分批证据，见台账及性能记录；继续声明Python版本、实际热插拔/回退和阻塞元数据查询监督，不承诺精确周期 |
| processes --pid | 已使用会话基线及创建时间复核；首样本null，同身份增量计算，消失/权限/僵尸有不同原因并非零退出；PID范围1..4294967295 | Windows/Linux真实自身负载及安装CLI证据见台账；继续其他平台、真实权限/PID复用及同步调用监督，创建时间仅用于观察身份，不是授权凭据 |
| processes --limit/--sort | 与单PID共享采集器，CPU首次排序先初始化并重新采样，内存排序无等待；库存/基线16384上限、清理消失PID；已按k*8<P使用稳定堆选择O(P log k)，否则完整排序O(P log P)，总库存存储O(P) | 策略边界与并列顺序已有测试；继续代表性规模下的采样/身份复核及端到端p50/p95/RSS，不能从合成选择阶段推算整命令提速；权限不可见项不能冒充完整排名 |
| network --interface | 已移除全局_net_prev；CLI调用/菜单页面独立会话，原始计数读取，首样本/回退/无效窗口返回null及原因，消失接口清理；网络rate_schema_version=2 | 回归及实际安装采样证据见台账；仍需其他原生平台及同名接口身份策略，不能以当前计数判断未观察到的替换；CPU窗口另行完善 |
| disk --mount | 无权限分区在get_disks中跳过，CLI最终合并报告为不可用或不可访问 | 保持默认查询容错；显式mount查询返回可区分的不存在/权限错误，限定输出字段，不为了诊断读取用户文件 |

采样元数据在既有CLI输出契约内版本化，保持菜单对原始dataclass的使用；先核对现有脚本消费者与JSONL测试，再决定兼容方式。每条验收覆盖首样本、重复样本、资源消失/重建、权限、计数回退、有限采样以及取消。原生证据和性能证据独立记录，不能用增加字段代替正确采样。

## 18. MySQL安装参数的依赖前置条件与下一批交付

XUI官方写操作增量（2026-10-16）：菜单/脚本现在统一传播服务重启、启用和定时器安装异常，三项systemctl非零状态不再被忽略；四个失败阶段不会写installed或导出成功凭据。三端各58项回归及wheel库存通过，原代码反例和源码摘要见验收台账。该修复关闭失败被报成功的局部缺口，不等于安装事务回滚、真实服务验收或整次timeout完成；API pending入站的既有显式降级行为另行保留。

`software install mysql --version VERSION`不需要增加允许任意包名或跳过依赖检查的参数。Linux驱动现从mysql、mysqld、mysqladmin、mysqldump及安装目录内私有库的有界ELF元数据读取实际依赖，不执行下载的程序或ldd。精确匹配同架构soname，拒绝用libaio.so.10代替libaio.so.1；依赖检查、包管理及复核共享180秒内部预算，包管理失败、超额输出和超时显式失败，在版本发布前终止。180秒是依赖阶段预算，不是整次安装timeout。

官方9.7.0 Linux x86_64 minimal样本的离线元数据确认需要ncurses/tinfo6、libaio和NUMA，不能沿用旧固定ncurses/tinfo5清单。发行版包名映射只是候选：还须验证支持发行版、glibc符号版本、真实四个命令和服务。观测下载摘要不是独立来源认证。未知缺失库拒绝并要求受控人工配置，不根据不可信元数据构造任意安装包名。

下一批交付按依赖排序：

| 顺序 | 命令范围 | 参数决策 | 交付和结束条件 |
|---|---|---|---|
| 1 / P0 | software install/upgrade/uninstall/switch | 沿用version/all/config/token-file；暂不添加整次timeout | 补系统资源跨用户锁、服务/PATH/伴随命令补偿、实际发行版依赖和来源信任；失败保留用户数据，取消可恢复 |
| 2 / P1 | monitor全部采样 | 沿用once/watch/count/interval及各自过滤参数 | 网络基线/回退、CPU共同窗口、进程创建时间基线/失败原因及有比例门槛的top-k已实现；继续其他原生平台、真实权限/PID复用、同步查询整体监督和端到端性能实测，保留菜单兼容 |
| 3 / P1 | network traceroute及各网络操作 | 不增加重复参数；复核现有timeout/total-timeout | 明确终态与退出码，验证代理/TLS、真实打包DNS和取消；部分结果不能冒充完整成功 |
| 4 / P0 | WG/XUI/Tailscale/RustDesk/Oh My Zsh | 有阻塞的有限操作先监督再决定timeout；follow保持持续模式 | 真实服务安装/管理、认证API、配置恢复与权限矩阵；五种diagnose已有timeout，继续深度诊断 |
| 5 / P0 | update apply及恢复 | apply timeout仍待事务监督；不开放force/cleanup占位 | 未索引/旧任务、marker、退役及运行中冻结程序恢复；scheduled和实际完成区分 |
| 6 / P0 | plugin run及生命周期 | 第三方业务参数只经run NAME -- ARGS透传 | RPC调用方/提供方身份、撤权、获批出口、整体预算、daemon故障恢复；不允许业务参数或依赖进入平台 |
| 7 / 发布门槛 | 第3、4、9节全部官方库存 | help、参数、能力声明由同一实现核对 | 声明平台及Python版本CI、干净wheel/原生包、生产签名；逐命令正常/失败/权限/取消/恢复证据 |

每批独立记录“参数入口存在／业务验证／原生平台验证／当前源码发布验证”。当前这些批次仍有未满足项；第3节18个Recipe和第9节逐功能库存全部保留，不以少量新参数或一个平台通过关闭整个计划。

2026-10-15 补充生命周期：`plugin inspect-state NAME [--caller CALLER]`及`plugin repair-state NAME --record-sha256 HASH [--caller CALLER]`已实现。省略caller选择self，显式platform仍选择服务分区。只修复完整绑定头且目录归属可确认的损坏尾，保留原始证据，损坏头/未知归属仍开放。三端相关回归、安装CLI、父进程强杀、Linux真实容量错误和普通用户权限拒绝已有分批证据，范围与限制见plugin-state-repair.md；不关闭第三类其余门槛或官方CLI整体完成计划。


## 本机网络 CLI 实测补充（2026-10-15）

当前网络五个入口/实现文件摘要与候选源码核对一致，三端各六项真实源码 CLI 检查通过：IPv4/IPv6 分别使用独占临时端口，监听端口为 open、绑定但不监听端口为 closed；并发 1/50、重复端口去重、排序、TCP 字段及非负耗时正确；localhost 的 ipv4/ipv6 DNS 均返回对应回环地址。每次 stdout 为单一 JSON envelope、退出 0，无 stdin，不创建平台数据。仅本机回环，不扫描外部主机。

证据为 F 盘 `cache/network-loopback-windows-20261015-r2` 和 `cache/network-loopback-posix-20261015-r1/{linux,macos,audit.json}`。Windows 首轮在首项被 60 秒外部期限终止，原失败目录保留；独立 faulthandler 诊断未复现，原期限重跑六项通过，不能据此声称超时根因已解决。该结果不关闭外网/DNS 故障、扫描总期限/取消、真实权限或最终冻结产物验收。
