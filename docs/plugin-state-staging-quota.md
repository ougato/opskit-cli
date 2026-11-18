# 插件状态暂存额度与验收

更新：2026-10-15。此门槛控制创建增长，不代表状态恢复总计划完成。

## 行为与边界

`reserve_staging`在插件状态锁内执行：一次在网络/容器创建前，一次在实际分配候选前。每个状态父目录最多接纳8个暂存目录，逻辑文件大小总预算128MiB；为新候选预留16MiB。self所在provider目录和callers共享父目录分别计账。

未知`.state-*`只计占用，不按名称认领、恢复或删除。父目录最多检查4096条目，暂存树合计最多65536条目、深度64；不跳过隐藏目录，不读文件内容。链接、特殊/多链接文件、身份变化及超额均拒绝，旧状态与证据保留。平台转换为运行故障，避免误报用户参数错误，并隐藏底层文件路径。

扫描时间O(P+T)，额外遍历栈O(depth)。这是逻辑预算，不是磁盘物理分配、free-space预留、平台总配额或已提交状态总量限制。同步系统调用仍可能阻塞；遍历中的期限回调不能替代I/O监督。

## 当前证据

最终499文件快照：`a11d055b59447f877fec4450dc8ebb16507364afb2cf63288eba1d53a3403555`。

| 系统 | 相关专项 | 跳过 | 耗时 |
|---|---:|---:|---:|
| Windows | 205通过 | 0 | 55.73秒 |
| Linux | 205通过 | 0 | 21.76秒 |
| macOS Intel | 205通过 | 0 | 38.30秒 |

包括mkdtemp创建后、写事务记录前真实os._exit(73)：遗留目录占用额度，满额时不再创建或解码，旧状态和未知目录保留。self/caller在恢复后满额时于_network之前拒绝，跨进程探针确认持有插件状态锁。其余协议、授权、归档、恢复与清理测试同时执行。前一版r2通过后补运行错误分类，以上结果来自最终r3。Ruff通过；静态94候选、0新增、0漂移。

当前wheel：`a58fc5cac7fa757ba0343338608c3f9bcb9c71e1d7bd3a3c8a175727db73ab14`，253运行文件、258成员，源码与RECORD验证通过。Windows离线安装、pip check及-I实际site-packages验证通过，覆盖授权/候选保留/恢复/正常发布及8个未知暂存条目触发拒绝后数据保持不变。

证据位于任务F盘cache：源码`state-quota-source-20261015-r3`；Windows专项`state-quota-validation-windows-20261015-r3`；Linux/macOS复制证据`state-quota-evidence-20261015-r3/{linux,macos}`；wheel及安装证明`wheel-state-quota-20261015-r3`。所有相关进程已终态。文档更新晚于快照，未提交/推送。

## 剩余交付

额度实现晚于498文件快照的三端全量、真实容器与Windows/macOS入口验收，不能沿用旧结果宣称当前额度版已全量/容器验收。

额度扫描现已接入调用内复用的ContentInspector，只读worker返回严格校验的数量和逻辑大小，逐次重新读取，父进程继续持有状态锁。独立调用会关闭自有reader；调用内使用剩余期限并交由已有生命周期关闭。实际阻塞os.read的worker在超时或KeyboardInterrupt后被回收；配额操作父进程被强制终止后worker退出也已验证。原崩溃测试注入收窄为仅拦截.state-临时目录，避免误中multiprocessing启动目录。

本批Windows/Linux/macOS分别213项专项通过，耗时44.67/16.23/40.57秒。另有worker父进程生命周期组：Windows4通过，Linux/macOS各3通过1跳过；跳过为Windows专属Job分配失败检查。覆盖状态记录、恢复、清理、配额、授权并发及归档预算等13组，不代表全项目回归或真实容器验收。证据复制到F盘cache的`quota-supervision-evidence-20261015`，包含三端XML与本批源码SHA256清单。macOS原证据为`/private/tmp/opskit-acceptance-20261015.Dzybna/quota-supervision-r1/result-r3.xml`及`parent-results.xml`。

以上源码晚于499文件快照及其wheel；旧wheel不包含reader配额操作。下一步按[候选准备监督方案](plugin-state-preparation-supervision.md)实现并补新源码全量、容器和安装包验证。准备worker只能操作拥有的暂存目录，不能发布root；父进程取消、超时、死亡后必须确认worker结束再清理。发布继续保留提交锁内新鲜授权，不能移到锁外或让孤立worker继续发布。

未知目录归属恢复、损坏记录修复、物理与平台总量、完整发布故障及断电、发布/清理I/O监督仍未完成。不能为了释放额度删除无归属证据的数据。

## 2026-10-15 平台总量原语（尚未接入强制路径）

现有每父目录额度不能限制多个provider或caller累计。新增宿主core/plugin_state_budget.py，提供稳定platform-budget OS锁及只读有界扫描。原语当前默认逻辑1GiB、262144条目、64暂存，扫描时另预留一个16MiB/8192条目候选及一个暂存槽；全部已提交状态、旧备份、未知条目、事务/修复证据都计入。平台wrapper位置的.state-*按max(实际子树占用,16MiB)计账，覆盖尚未提取的并发增长；业务payload内同名目录仅作数据。所有链接、多链接/特殊文件、权限错误、目录身份/时间变化均拒绝；缺失根不创建。扫描O(entries)，递归栈O(depth)，深度上限64。

Windows9项针对性测试通过0.52秒及Ruff通过，JUnit cache/platform-state-budget-r2-20261015.xml；测试覆盖跨provider/caller/backup/evidence累计、空候选预留、共享OS锁、条目预留与变更拒绝。原语尚未接准备/发布/恢复调用链，不能宣称平台总配额已生效，也不是物理容量或free-space预留。新模块未进行三端、wheel或最终整仓验收。

接入要求：固定platform→partition-staging→transaction锁顺序；候选从计账到分配必须在同一全局锁内，锁释放后仍按完整候选计账；发布移动及恢复清理不能错误重复计费或因额度耗尽拒绝恢复，元数据增长必须有独立预留。包括repair证据的增长也必须纳入，不能仅接prepare就宣布完成。所有现有测试及实际RPC需重新验证，生产逻辑不能以测试临时路径为由绕过额度。总量配置/迁移、物理分配、故障文件系统仍开放。

### 2026-10-15 准备阶段接入平台预算

prepare_candidate新增显式budget_base：底层独立宿主准备工具默认扫描其目标父目录，同样执行预算，没有按临时路径豁免；实际sandbox执行入口固定传入data_dir/plugin-data，并经_save_state/_state_staging传到真实worker。worker要求scope为绝对路径且覆盖目标，再检查所有祖先与规范路径。固定global→partition-staging→transaction锁顺序，在共享锁内重新扫描总量并完成分配、归属记录、ACK和提取。平台wrapper预留扫描也支持独立宿主父目录布局。

Windows六组61项通过34.90秒；追加两项真实worker专项后预算11项通过1.98秒。其他provider/caller占用导致分配回调前拒绝，未创建候选/日志且旧数据不变；实际跨进程OS锁在分配ACK前仍拒绝另一分配者。Ruff通过。证据cache/platform-budget-integration-r1-20261015.xml与r2 XML。

这是准备增长路径的增量，不是最终平台总配额：发布/恢复/修复证据增长和元数据预留尚待统一，运行期回归在进行，三端、安装包、真实RPC和完整额度并发故障门槛尚待完成。全局锁目前覆盖整个受管提取，跨provider并发可能受影响，未测量前不宣称性能优化；后续缩短临界区必须保留完整预留和崩溃归属，不通过放开锁换取表面速度。
补充：Windows状态集成、服务授权执行和运行期三组35项通过23.56秒，结果cache/platform-budget-runtime-r1-20261015.xml；此前“运行期回归在进行”已取得本组三组终态，不替代三端或真实容器验收。

### 2026-10-15 生命周期协调及同快照三端验收

新增全局锁已接准备、发布、正常恢复、候选回收、父目录初始化及损坏尾修复。准备按global→partition-staging→transaction，发布按global→configuration→trust→transaction，其他变更按global→transaction。原有新鲜授权在同worker持锁内保留。新候选预留16MiB+4096字节及8195条目；每个尚保留的wrapper按max(实际占用,16MiB)+4096元数据余量计账。修复独立预留8192字节/2条目且不占新暂存槽。初始化缺失父目录先预留条目，防止新增caller目录绕过总额度。

正常恢复和回收只取得共享锁，不因总量已经超额而拒绝有界恢复/删除；需要新建证据的损坏尾修复仍需足够预算，否则保留原始记录。14项预算测试包含真实worker跨provider拒绝、锁持有、超额仍可恢复、修复前拒绝新证据及初始化目录额度。原语reserve参数可分别指定字节/条目/暂存预留并严格验证。

551文件源码快照SHA256 9b708633519acefed9f48617173e7288c0ac9f30e2ab8cacdcc4a7e27e7b5be4，19组各295项：Windows294通过1平台跳过170.82秒；Linux294通过1平台跳过105.16秒；macOS293通过2平台跳过144.96秒。Ruff通过。wheel264运行文件/269成员，摘要41374a267c87c4782650e342f0b8dd63ef96d14ca8a680302b51ccc88c81ed98，源码清单与RECORD通过。三端隔离安装各11次实际生命周期CLI命令通过，五个关键模块路径和字节匹配wheel。证据cache/platform-budget-evidence-20261015-r1，全部进程已终态；文档更新晚于快照。

这是平台plugin-data逻辑预算，不是物理分配、主机free-space预留、运行工作目录总额度或最终整仓验收。仍需默认额度实际安装边界、真实RPC/网关并发、容量故障下新锁调用链、平台部署与性能实测；全局锁当前覆盖提取，跨provider并发受限，不能宣称提速。已有超额数据不删除，合法恢复保留；可配置限额/迁移策略及其他八类目标继续开放。

### 2026-10-15 精确准入边界修复及 r2 验收

前节 r1 是历史快照。新增精确边界反例发现：wrapper 预留之外，旁置事务日志还会独立计账，原新候选预留少算一个 RECORD_LIMIT。已将新候选预留修正为 16MiB+8192 字节，保留 wrapper 的 16MiB+4096 计账；没有放宽容量断言。修复前失败保留在 cache/platform-budget-boundary-before-20261015.xml，修复后受影响四组 89 项通过。预算测试现为 15 项。

最新 551 文件快照 SHA256 为 206f55889999abbf61a395d501ac31d85009613b6220e130f2f28a01592eecb7。同一快照 19 组各 296 项：Windows 295 通过/1 跳过（210.82 秒），Linux 295 通过/1 跳过（115.79 秒），macOS 294 通过/2 跳过（145.55 秒）。wheel SHA256 为 da0ba755c155df949c0a451bcb28e2fa1f4d1d3d3b010155827f1fb2ce320316；三端隔离安装各 11 次 CLI 命令及五个关键模块路径、字节校验通过。

安装后使用真实准备 worker、显式平台 plugin-data scope 和未改写的默认 1GiB 限额：三端均确认超过新候选准入边界 1 字节时，在 allocation 回调前拒绝且不创建候选/日志；恰好满足预留时准入，实际计账不超过上限，随后合法恢复回收候选。Linux/macOS 使用零分配块稀疏文件，Windows 显式 FSCTL_SET_SPARSE；这是逻辑容量验证，不是物理磁盘占满验证，也不是整个 sandbox 调用入口的边界测试。首版验收脚本误捕获 ValueError，实际为 SandboxError；保留 r2 脚本及其隔离目录，r3 仅纠正脚本异常类型，生产代码未变。

同快照真实 Linux RPC 验证 caller A/B/A 状态计数 1/1/2、非法结果/版本/撤权拒绝及原状态保留，27 个 worker 清理完成；真实网关验证直接私网连接被拒、显式授权代理及执行期间撤权/取消，恢复报告均无遗留。该撤权是轮询检测，不是逐请求瞬时授权。证据集中于 cache/platform-budget-evidence-20261015-r2，含三端 XML、安装/边界、RPC/网关及 SHA256 索引。

本批进程全部终态。仍未完成：新锁路径下真实文件系统容量故障、完整 sandbox 默认额度边界、工作目录和物理总量、可配置限额迁移、部署矩阵及性能基准。不能将本批专项通过等同第 3 类或全部八类验收完成。

### 2026-10-15 安装后完整调用边界及新锁满盘复验

继续使用上述 r2 wheel 的隔离安装目录，未改限额或替换授权/监督器。Linux 实际 Docker sandbox.invoke 两次：其他 provider 的 caller 稀疏文件占用为默认总限额减新候选预留加 1 字节时，插件执行后的状态保存拒绝，self 保持空目录且无候选/日志遗留；将占用减少 1 字节后，同一插件完成状态发布，平台计账 1056956419 字节不超过 1073741824。SDK 镜像仍为 fc5464719889485ecd5ec670e976e74e43e84d2eb13b05ef5609e2d6af09ec9e；reconcile 各项为零。此前“完整 sandbox 默认额度边界”现已有 Linux 已安装包实际调用证据，不扩展为其他部署组合的证明。

新全局锁路径下使用同一安装包重跑真实满盘：Linux 1MiB tmpfs、16MiB loopback ext4，以及 macOS13 128MiB APFS 镜像。预先取得/释放 global→transaction 锁后填满隔离卷，修复确实因 ENOSPC 拒绝，原日志、旧状态和候选不变；释放 filler 后重试 aborted，原始记录证据和失败产物保留。ext4 失败可发生在完整 evidence 写入后、pending 写入前，保留 314 字节 evidence 和空 pending；APFS 另创建 3099 个压力文件才触发元数据 ENOSPC，保留 381 字节 evidence。未放宽失败或数据完整性断言。Linux 两卷经 findmnt 确认无挂载，macOS detach 成功。测试卷与生产数据隔离，不是物理设备故障或断电证明。

新增证据仍归档 cache/platform-budget-evidence-20261015-r2：sandbox-boundary-linux*.json、real-enospc-linux-{tmpfs,ext4}.json、real-enospc-macos-apfs.json。验收脚本保存在 cache，生产代码未因本轮验证修改。其他 OS/文件系统组合、完整 EIO/fsync/断电、工作目录总量、物理容量及迁移/部署/性能门槛继续开放。

Windows 同一 wheel 另复验当前账户显式 deny DACL：拒读日志、拒写父目录两种故障，共 6 次实际 CLI。拒绝时原日志/旧状态/候选保留且无修复产物；finally 通过预先保留的 WRITE_DAC 句柄恢复原权限，恢复后修复成功。证据 real-acl-windows.json；此结果不代表跨用户服务部署验收。
