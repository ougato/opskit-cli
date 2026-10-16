# 插件沙箱工作目录监督

## 2026-10-15 工作目录准入数量限制

新增 MAX_WORKSPACES=8 和临时父目录有界扫描（最多 16384 个直接条目）。分配 worker 在 platform_state_guard 内扫描同一 OS 临时父目录的全部 opskit-sandbox-* 名称，包括旧版/崩溃/响应未确认的遗留目录，然后独占创建新目录；创建完成即占位，请求写入期间也占位。达到上限拒绝新增，不按名称删除未知证据。此前缀的普通文件、链接或 reparse 条目拒绝准入。清理保持原身份校验和额度，不因准入已满阻止合法回收。

这是同一平台、同一临时父目录的数量门槛：不同 data_dir 不共享锁，切换 TEMP/TMPDIR 形成不同扫描范围，异常遗留目录真实大小尚未计账。不能用 8×192MiB 宣称物理磁盘或未知内容总容量受控。跨目录持久预留、未知归属恢复、实际逻辑字节及物理容量继续开放。

552 文件快照 SHA256 8bbdd85625f2fb319615e341b80615f1a6253dbcc6c1df86b3576af76e69c3c2；三组 36 项在 Windows/Linux/macOS 均通过，分别 10.12/6.66/13.43 秒。覆盖真实 worker 满额分配前拒绝、释放一槽后成功分配/清理、未知证据保留、异常条目和扫描上限及既有 I/O 超时取消与身份拒绝。首轮 Windows 4 项失败来自测试 fdopen 包装误作用于新增锁文件，原 XML 保留；注入限定 wb 请求文件后原故障断言全部通过。Ruff 通过。

同快照 Linux 实际 Docker 两次调用确认持久状态超限拒绝、释放 1 字节后正常提交及 reconcile 零遗留。此轮使用冻结源码目录，不是已安装 wheel；验收脚本输出字段中的 installed 指传入来源参数，不能据此当作本批安装包证明。SDK 镜像未改变。旧 platform-budget-r2 wheel 不包含此变更，本批安装包、完整回归与多平台真实容器验收尚待完成。

后续安装包验证：同快照 wheel SHA256 3bb91f33fcbf688f4cf561b81820546355ff179494a615e060fe424cde9632ac，264 运行文件/269 成员，包清单与 RECORD 校验通过。Windows/Linux/macOS 隔离安装各 11 次 CLI 命令通过，六个关键模块（含 plugin_workspace）的安装路径、字节与 wheel 一致。三端实际安装后 worker 另确认第九目录拒绝、释放一槽后分配/清理成功、其余七份遗留证据保留。macOS 首版探针把 multiprocessing 的 pymp-* 临时目录也纳入“工作目录”全等断言而失败；r2 将检查限定到实际 opskit-sandbox-* 命名空间，并记录额外 pymp-hli32pe9，未改生产逻辑或工作目录数量断言。原脚本和失败夹具保留。

证据位于 cache/workspace-slot-evidence-20261015-r1 的 wheel.json、installed-*.json 和 installed-slot-*.json。三端完整回归各收集 3489 项，当前已启动，尚无最终结论；源码快照和安装包通过不替代完整回归或全部发布门槛。

后续终态：本次全量三端均在 test_failed_allocation_callback_retires_event 失败并停止。Windows 3355 通过/40 跳过/1 失败（974.49 秒），Linux 3274/121/1（540.49 秒），macOS 3271/124/1（681.25 秒）；其后 93 项未运行。失败 XML 已保留为 full-*-failure.xml，不能记为全量通过。同一安装包另完成三端真实并发准入：七份遗留目录、六个并发启动器共十二次尝试，仅一次成功，清理后七份证据不变；Linux 实际安装包 RPC 已通过，27 个 worker 消失且 reconcile 四项零，证据 concurrent-*.json 与 installed-rpc-*.json。

根因是 core/plugin_state_prepare.py 新建的局部 arguments 元组持续引用分配 Event；异常堆栈保留时，单清空 allocated 不足以释放。现将参数交给 owned_process 后清空局部元组，所有者仍负责进程结束后释放其参数，未改变清理断言。修复快照 552 文件 SHA256 99a4e044203bf84342e07da9fdfd130044a797eb35705348bf82a8a8921b5364：Windows 相关 52 项通过（23.99 秒），尾部 92 通过/1 跳过（11.53 秒）；Linux/macOS 合并 145 项各 138 通过/7 跳过（18.71/31.97 秒）。Ruff 通过。修复后全量已重新启动，并收集全部失败，不遇首错停止；新完整回归及修复后 wheel 尚未完成，旧 wheel 不包含此修复。

修复后 wheel 现已构建并通过包清单/RECORD 校验：SHA256 9033daef44b9d2de108f0aed705a9c385d6b17685d5954213cd0217053d09dcf，264 运行文件/269 成员。三端隔离安装各 11 次 CLI 通过，七个关键模块来源及内容与 wheel 匹配，包括 plugin_state_prepare。独立安装探针主动保留回调异常堆栈，三端均确认创建 1 个 Event、存活 0 个；同一探针对旧 Windows wheel 实际失败，保留反例脚本及隔离目录。没有通过手工清除 traceback 或放宽引用断言使其通过。

新增证据在 cache/event-retirement-evidence-20261015-r1 的 wheel.json、installed-*.json、event-probe-*.json。完整回归仍在原有三个句柄继续执行，未因观测等待而重启；尚无最终全量结论。本页补记晚于冻结快照，不改变运行代码。

## 2026-10-15 固定平台工作目录范围

新分配根从 OS 临时目录迁到 data_dir/plugin-runtimes/workspaces。同一平台切换 TEMP/TMPDIR 不再形成独立额度；global 锁内校验所有祖先和规范路径，再创建缺失父目录、计数及独占分配。拒绝链接/reparse 祖先，不通过 resolve 跟随到其他目录。清理仍要求租约中的父/子目录身份，可处理具有完整旧租约的历史路径；不按名称迁移或删除没有归属凭据的 OS 临时遗留目录。

更新真实 worker 回归：占满固定父目录后切换 TEMP/TMPDIR，再次分配仍拒绝，备用临时目录无新内容；释放槽位后正常分配/清理。固定布局不等于完整字节预算：遗留目录实际逻辑字节、未知归属、物理分配、跨不同 data_dir 的主机总量继续开放。旧 OS 临时目录的未知证据保持原地，不计为已自动恢复或清理。

552 文件快照 SHA256 7b7add98c7d735cf9bb0c5c7d4c2afe9a4c6027b778d03abe889955730c3e5e2。Windows 初轮相关 59 项为 58 通过/1 跳过（96.51 秒），之后仅追加规范路径拒绝行；最终快照 Windows 工作目录 36 项通过（21.22 秒）。最终快照 Linux 59 项为 58 通过/1 跳过（37.43 秒），macOS 57 通过/2 跳过（44.42 秒）。Ruff 与差异空白检查通过。同快照 Linux 真实 Docker 两次调用验证新工作目录下持久状态超限拒绝、释放 1 字节后正常发布、reconcile 四项零；使用冻结源码而非本批 wheel，验收脚本字段中的 installed 不作为安装来源证明。

证据归档 cache/workspace-namespace-evidence-20261015-r1。本布局晚于 Event 修复快照，前一快照全量不能证明本布局：前一快照 Linux 全量 3361 通过/128 跳过（580.35 秒），macOS 3358 通过/131 跳过（689.31 秒），Windows 仍在原句柄运行。本布局的安装包、最终整仓及多平台真实容器验收仍待完成。

## 2026-10-15 工作目录逻辑字节及条目预算

新增宿主 plugin_workspace_budget，替代仅计前缀目录数量的实现，不保留双轨。实际反例：七个已预留目录加一字节无前缀未知证据，旧逻辑仍允许新目录，失败 XML 为 cache/workspace-bytes-before-20261015-r1.xml。当前在原 global 锁内执行只读有界扫描并创建新目录：默认 8 个目录、1.5GiB 逻辑字节、131104 个预留条目；每个直接目录（包括未知名称）按 max(实际字节,192MiB)、max(实际条目含根,16388)计账，另预留一个新目录的同等增长空间。直接未知普通文件按实际逻辑大小和条目计账；前缀名普通文件、所有链接/reparse、多链接及特殊文件拒绝。深度上限66、直接父目录条目上限16384；扫描 O(entries)，栈 O(depth)，身份/时间/内容长度变化或权限错误拒绝，不读文件内容。

原清理worker改为引用同一字节/条目/深度常量，数值不变，避免未来预算与回收边界漂移。预算满仍不阻止凭有效租约进行原有身份校验清理。未知数据保持原地，不自动删除。正在写入的快照可能使新分配的扫描不确定并拒绝；没有延长期限或绕开检查，也没有性能提速声明。

553 文件快照 SHA256 c9321397726c259aceb167feaab9a5a64156b9e5839eaae8c7343440c64944d1。五组各60项：Windows/Linux/macOS均通过，24.50/15.19/16.66秒；预算9项覆盖未知字节反例、完整增长预留、精确边界及超一字节、条目预留、链接证据保留、同内容时间变化拒绝、真实worker满额后切换TEMP仍拒绝。Ruff及差异空白检查通过。同快照真实Linux沙箱入口确认七个预留目录加一字节未知证据在容器启动前拒绝，未创建状态self/事务日志；移除测试spill后正常启动并提交状态，七份原证据不变，reconcile四项零。首版探针误断言self为空而非不存在，原脚本/夹具保留，r2仅纠正测试假设。另有原持久状态1GiB真实沙箱边界复验通过，未替换授权/预算逻辑。当前均是冻结源码验证，不是本批安装包证明。

证据归档 cache/workspace-bytes-evidence-20261015-r1。本预算仅覆盖当前平台固定workspaces根；不含旧OS临时未知目录、其他data_dir、容器镜像/依赖构建缓存及持久plugin-data（后者独立1GiB）。它不是物理块配额、free-space预留或断电保证。跨主机部署、物理配额、旧归属恢复及本批安装包/全量验收继续开放。

补齐前一批Event修复快照Windows最终全量：3448通过/41跳过（1099.78秒），至此该快照三端全量全部通过。此结论不覆盖后续固定目录及本字节预算改造。

### 同快照安装包与真实默认边界

wheel SHA256 3afa592db7c99426fb7da30dd6bdcf93aa12e3ac99cff8b42a8e3a58a66d8320，265运行文件/270成员，包清单及RECORD匹配。三端隔离安装各11次CLI通过，八个关键模块路径和字节与wheel一致，包含新增workspace_budget。三端直接调用已安装真实worker，确认未知spill拒绝、切换TEMP不绕过、默认1.5GiB额度下超新候选准入边界1字节拒绝、精确边界允许分配并清理。Linux/macOS使用零分配块稀疏文件，Windows显式FSCTL_SET_SPARSE；没有实际写入1.3GiB填充数据，不作为物理容量测试。

同安装包三端六个并发启动器共12次尝试，七份既有目录时仅一次成功，清理后七份证据保留。Linux已安装包实际沙箱入口也通过未知spill在容器启动前拒绝、释放后执行/提交、七份证据不变和reconcile四项零。完整三端回归各收集3495项，正在独立运行；真实安装包RPC仍待收齐。当前安装探针通过不能作为全量通过结论。

最新安装包另完成真实Linux16MiB loopback ext4满盘：预先建立global锁及固定workspace根，再从范围外filler填满测试卷；请求写入因ENOSPC失败，原有未知目录证据保持不变，保留一份零字节request.json及其未知归属目录。释放filler后新分配及按租约清理成功，两个旧目录均保留。最后umount成功。这是物理空间耗尽时保留/重试证据，不是物理硬配额或设备断电证明。报告real-enospc-installed-linux.json及其他installed-*.json保存在本批证据目录。

调用入口已移除TemporaryDirectory及主进程resolve。新增host-only plugin_workspace：一次性worker解析OS临时父目录（允许macOS系统/var别名），独占创建带随机请求ID的目录，返回父目录和目录dev/inode。父进程仅校验严格响应形状、请求ID、绝对路径及身份值；不读取目录元数据。

退出容器资源作用域后，由清理worker复核父目录及工作目录身份，完整扫描后才开始删除；拒绝链接、reparse点、特殊文件、硬链接及身份变化。复用已有身份清理实现并为工作目录指定独立额度：16387条目、8193普通文件、192MiB逻辑字节、深度66，覆盖两个8192条目的受限快照及请求文件。原持久状态清理仍使用8192条目、4096文件、16MiB、深度64，不扩大其权限。

创建受调用剩余期限约束；清理使用单独30秒额度。业务错误或取消是主异常，清理失败仅附注；正常业务遇到清理不确定则报错并阻止后续状态发布。清理结果绑定随机请求ID和目录路径；超时/不确定结果保留证据。创建后响应丢失、父进程被杀可能保留未知临时目录，当前不依据名称回收，尚无跨重启工作目录归属及全局容量保证。宿主账户失陷/并发恶意替换不在本隔离保证内。

工作目录创建和清理各使用一个受监督进程，增加启动成本；没有性能提升声明。后续代表性性能验收应测插件启动p50/p95及取消残留，再决定是否与现有快照worker合并。请求文件写入、资源日志、宿主锁等其他同步I/O仍有独立审查项，不能声称整个invoke已消除阻塞I/O。

固定518文件快照SHA256：182349ac45279d71220f79ed3074f9c22a91e6dbf9045c9cc21df7e718449edc。wheel：6de617be5e1dfa760629d63c03f4a83e914f5977a1cb994aac4646879b79c4f4，257运行文件/262成员，源码与RECORD匹配。12组各222项相关测试：Windows221通过1跳过（117.27秒），Linux222通过（58.77秒），macOS222通过（101.69秒）。19项工作目录专项含真实阻塞mkdir/unlink取消与超时、回收后重试、父进程禁止创建/删除、身份/父身份/硬链接/符号链接拒绝、主错误保留、响应绑定及大于16MiB快照回收。Windows/macOS离线重装、pip check、-I十模块来源及真实状态/工作目录探针通过；CLI与真实容器仍按句柄收齐。运行代码此后未变，本说明晚于快照；整仓全量及发布门槛未关闭。

macOS旧514批次警告诊断：配额单组8项及高GC频率8项均通过，纠正spawn主入口保护后的原顺序前缀840通过3跳过、322.40秒，无警告复现。没有定位根因，原有10条ResourceTracker重入警告仍保留待观察，不能据未复现认定零泄漏。

本批剩余任务全部终态：Windows/macOS安装CLI各94help/103项检查通过；真实Linux RPC27worker删除、恢复四字段0，网关活跃CONNECT下取消/身份撤销/私网撤销保持旧状态、恢复四字段0。SDK继续为7e58a4a6...，没有因宿主改造重建镜像。证据为F盘cache/workspace-evidence-20261015-r1，任务台账workspace-live-jobs-20261015-r1.json中sessions为空。全仓Ruff通过；静态94候选、0新增、0基线漂移。快照后仅删除测试文件一处未使用导入并补文档，运行源码与验收wheel一致；该清理经静态检查验证，不冒充重新运行三端测试。

## 请求文件写入合并（520文件批次）

工作目录创建worker现同时独占创建request.json、写入共享缓冲的有界内容并核对文件/目录身份，父进程不再write_text。请求先经原JSON编码预算检查，再受32MiB UTF-8字节上限；共享缓冲避免将大请求作为spawn参数经管道序列化。共享内存分配和复制计入创建总期限，最终等待使用剩余额度。该分配本身仍是同步操作，不声称所有宿主资源申请均可即时取消。

文件使用O_EXCL及可用的O_NOFOLLOW，拒绝覆盖；普通单链接文件的fd/path身份及长度必须一致。POSIX写完设为0444，保证容器非特权UID可读；宿主工作目录保持0700。写入/刷新/身份读取受同一worker监督，没有额外请求写入进程。创建过程失败可能留下尚未交接身份的未知目录，继续保留，持久归属和容量门槛仍开放。Path参数转换中的resolve、宿主锁及资源日志仍待处理。

r1源码SHA256：01d90c0dfa7d38cdfdcdc53f5896dfdedf340d612d7235dbb86486f65b306fcd；wheel：e0c2fc4b26490c4f9d5cd6451156d978a4454cc2140b6b137038ea6185b6fb01（257运行文件/262成员，源码/RECORD匹配）。Windows首轮100通过；三端扩大回归各178通过后，均因恢复阶段测试清单缺entry/kind失败并停止，原XML保留。补齐夹具后r2源码SHA256：8cb00ff1b6568abb56416a1439b4750792c5f76d1f200df10f8b78aa78e92657，清单核对只有该测试文件变化，生产文件完全一致。r2复测恢复及后续调用链并新增restart组：Windows73通过1跳过，Linux/macOS各74通过。

按r2收集的247个唯一测试ID核对两轮最新结果，Windows246通过1跳过，Linux/macOS各247通过；无缺失/残留失败。这是分轮相关验证，不是一次247项完整运行，也不是整仓全量。14项请求专项覆盖打开/写入/刷新/元数据实际阻塞时超时及取消、未知目录保留、父进程不写文件、同一创建worker和UTF-8字节限额。

Windows/macOS新wheel离线重装、pip check、-I十模块来源、状态初始化和请求文件实际读回/回收均通过。Linux真实RPC27worker删除、恢复四字段0；网关活跃CONNECT取消/撤权保持旧状态、恢复四字段0，SDK不变。本批未重复安装CLI全103检查，最终源码收尾时统一复验。全仓Ruff及静态基线通过（94候选、0新增、0漂移）。证据为F盘cache/workspace-request-evidence-20261015-r1，其中combined-case-audit.json记录逐ID覆盖及源码差异；工作目录监督和整仓/发布门槛继续开放。

## 2026-10-15 工作目录预算、权限与归属恢复批次（归档）

## 当前验收证据

本批冻结源码为 553 文件，SHA256 `c9321397726c259aceb167feaab9a5a64156b9e5839eaae8c7343440c64944d1`；wheel SHA256 `3afa592db7c99426fb7da30dd6bdcf93aa12e3ac99cff8b42a8e3a58a66d8320`，265 运行文件、270 包成员，清单与 RECORD 匹配。

- Windows/Linux/macOS 各 60 项专项通过。未知文件多出 1 字节的修复前失败反例保留，修复后断言未放宽。
- 三端隔离安装各 11 次 CLI 通过；八个关键模块来源及字节匹配 wheel。
- 三端真实安装后 worker 验证默认边界：超出准入边界 1 字节拒绝，精确边界准入并清理；切换临时目录不能绕过，未知证据不被删除。大文件使用稀疏逻辑大小，不作为物理容量证明。
- 三端六个并发启动器各进行 12 次尝试，七份既有目录时仅一次成功，清理后七份证据保留。
- Linux 已安装包真实沙箱入口在未知 spill 超额时于容器启动前拒绝；移除测试 spill 后正常执行和发布，七份原证据不变，恢复检查四项为零。
- Linux 已安装包实际 RPC 验证调用方 A/B/A 状态为 1/1/2；非法协议/结果、未授权调用、版本不匹配及撤权后提交均拒绝，27 个 worker 消失，恢复检查四项为零。
- Linux 16 MiB loopback ext4 实际满盘时分配失败，原证据及零字节失败请求保留；释放 filler 后重试和按租约清理成功，卷已卸载。这不是设备断电或物理硬配额证明。

原始 XML、JSON、失败记录和摘要在本机忽略目录 `cache/workspace-bytes-evidence-20261015-r1`。本批 Linux 完整回归 3367 通过、128 跳过（628.28 秒）；macOS 3364 通过、131 跳过（687.29 秒）；Windows 3454 通过、41 跳过（XML 1169.059 秒），三端均零失败。此结果仅覆盖上述 c932 快照。更早阶段的失败、脚本假设修正与各快照摘要见[历史记录](plugin-workspace-history.md)。

随后修复清理存在性判断：`os.path.lexists` 会将权限错误折叠为不存在，导致目录仍在却报告成功；现在使用只将 `FileNotFoundError` 视为不存在的共享 `path_present`。回归通过真实监督 worker 注入目标元数据权限错误，同时拦截 `os.lstat` 和 `os.stat`，覆盖 lexists 与 pathlib 两种实际调用。旧实现的隔离副本仍发生预期的 DID NOT RAISE，修复后保留目录证据并报告 cleanup unconfirmed，最终恢复正常清理。首次夹具仅拦截 lstat，未覆盖 pathlib，失败记录保留；隔离副本首次运行的工作目录错误亦保留，不计为行为反例。

修复冻结快照 554 文件，SHA256 `8f57af0daf6fa6ce9790b4ff6cde20744dc95ce548e1bd4ce8877bd42cdd6ab0`；三端各 43 项相关回归通过（Windows 16.73 秒、Linux 6.43 秒、macOS 14.77 秒），Ruff 通过。原始 XML 在忽略目录 `cache/workspace-presence-*-20261015-r2*.xml`，源码及旧实现副本在 `cache/workspace-presence-source-20261015-r2`。这是故障注入回归，不替代实际 ACL、安装包或新快照全量验收；上一快照的全量结果不能直接用于本次修改。

随后构建并校验新 wheel（265 运行文件、270 成员，SHA256 `318984f2af4c061bd121ffb910e5700dd7e472d5a78dd3df3016f50da1a74a09`）。Linux 非 root 账户和 macOS 原生账户在隔离安装后，将自建 workspace 父目录 chmod 为 000：先确认实际 lstat 抛 PermissionError、lexists 却为 False，再确认真实监督清理拒绝且未报成功；finally 恢复原权限后原始 evidence 字节仍在，正常清理和重复清理均通过。安装模块路径及字节与 wheel 匹配。Windows 安装包正常/不存在清理通过，本批未新增 Windows 实际 ACL 验收。Linux 首次非 root 探针因原测试环境父目录不可遍历而未启动，改用独立复制的测试环境，未修改原环境权限。原始探针及 JSON 见忽略目录 `cache/probe-workspace-permission-20261015-r2.py`、`cache/workspace-presence-real-*-20261015-r2.json`；真实权限证据仍不代替最终全量或设备故障验收。

本轮进一步修复“资源删除失败后仍删除挂载源目录”：真实调用链回归旧实现 1 失败/2 通过；修复后的 worker 正常/非法协议 × 正常清理/清理失败/撤权共六种组合验证目录保留和禁止提交。冻结 554 文件 SHA256 `8b6cd56497ad420d078af454b91833578d3941dc349b02c28243328e11e7d4bb`，六组 184 项回归 Windows 183 通过/1 跳过（102.77 秒）、Linux 184 通过（64.63 秒）、macOS 184 通过（133.47 秒），Ruff 通过。原始 XML 为 `cache/workspace-resource-cleanup-*-20261015-r1.xml`，旧实现失败记录为 `cache/workspace-resource-cleanup-before-20261015-r1.xml`。

新 wheel SHA256 `a451ac2156d0696f2558c845f83587ea5f850a78e300fa225596b3c73086e4a3`，265 运行文件/270 成员及 RECORD 校验通过。Linux 隔离安装后使用现有精确 SDK 镜像创建非 root、无网络、只读、限资源测试容器，绑定自建 evidence 目录；仅注入 Docker rm 失败，其余 daemon/list/exec 为真实命令。资源清理失败后目录仍在、容器仍能读取原 evidence，JSON 资源日志保留。恢复原控制路径后 reconcile 精确恢复 1 个容器，原始测试租约再清理目录成功。原始脚本及 JSON 位于 `cache/probe-workspace-resource-real-20261015-r1.py`、`cache/workspace-resource-real-20261015-r1.json` 和 `cache/workspace-resource-recovery-20261015-r1.json`。此证据覆盖挂载期间保留及容器恢复，未实现目录租约跨重启恢复，也不替代完整 invoke 的全部真实部署矩阵或最终全量。

schema2 绑定及恢复的冻结快照共 555 文件，SHA256 `06d62ab675135736ab75c75541c1baf3212669d5068565735ab8054766f76400`。十组 248 项相关回归：Windows 247 通过/1 跳过（180.46 秒）、Linux 248 通过（120.38 秒）、macOS 248 通过（170.72 秒），Ruff 通过。新增 13 项覆盖活动租约、目标/父目录替换、目录清理失败后日志保留与重试、严格绑定解析、真实持有进程 os._exit(17) 后恢复；Docker 成功删除或初次查询为空期间的 daemon 切换必须保留日志。初轮原测试依赖“倒数第二条命令就是删除”的位置假设，新增终态 daemon 查询后两项失败，改为断言全部删除命令只包含精确原 ID，断言更严格，原失败 XML 保留。

新 wheel SHA256 `5d5cf7886186a3d47b6e5b33b275f8a1c5e0542bcbeb8992a4bea7c450a04cd9`（265 运行文件、270 成员，清单/RECORD 通过）。Linux 隔离安装真实 Docker 验收：子进程持有效 schema2 日志和真实 OS 锁时，reconcile 报 active=1，容器能读挂载证据；持有进程 os._exit(19) 后，新恢复调用仅依赖磁盘日志，移除精确容器和绑定目录，recovered=1，其余计数为零，未知目录字节不变。测试未使用内存中的原 Workspace 租约完成清理。另有新包完整 invoke 的字节超额拒绝、释放后执行/状态发布、七份既有证据保留和零待恢复通过。脚本和 JSON 为 `cache/probe-workspace-journal-crash-20261015-r2.py`、`cache/workspace-journal-real-20261015-r2.json`、`cache/workspace-journal-invoke-20261015-r2.json`；三端 XML 为 `cache/workspace-journal-*-20261015-r2.xml`。非文档 522 文件与冻结快照逐项匹配。此批是有效记录后的进程崩溃恢复，不能代替设备重启/断电、日志创建前窗口和全部真实部署证明。

分配确认前绑定的 556 文件冻结快照 SHA256 `f0f193a9de5322ba0d65098b201f98b2eb31826b47893d870728e7864f2a06c1`，14 组 293 项：Windows 292 通过/1 跳过（218.76 秒），Linux 293 通过（135.04 秒），macOS 293 通过（194.99 秒），Ruff 通过。新增五项通过真实分配 worker 覆盖请求 open 的 ENOSPC、原子绑定写入后确认丢失、成功响应前超时/取消、旧父记录对应新磁盘绑定的关闭恢复与禁止不可变身份漂移；暂停 worker 均已退出，活动 token 锁仍阻止并发恢复。非文档 523 文件与冻结清单一致。

wheel SHA256 `eded6dbac80245c5f57cf089f884813765dfa71341a7174eca874c6565351172`，265 运行文件/270 成员及 RECORD 通过。Linux 新安装包真实容器再次验证先有资源租约、分配 worker 升级日志、活动进程跳过、os._exit(19) 后仅依赖磁盘日志清理容器和工作目录、未知目录保留；完整 invoke 的字节拒绝/执行/状态发布也通过。另用自建 16 MiB loopback ext4，在绑定成功后、请求 open 前实际填满卷：请求分配失败，父记录仍为 schema1、磁盘 schema2 可读；释放 filler 后原租约关闭按新绑定清理，未知字节不变。首轮仅 4 KiB 填充，残余空间容纳了小请求，断言失败；r3 补充逐字节尾填充及 fsync 后才完成验收，未修改生产代码迎合该夹具。两次测试卷均通过 findmnt 确认卸载，原脚本、卷和失败记录保留。

本批 XML 为 `cache/workspace-preack-*-20261015-r2.xml`；脚本为 `cache/probe-workspace-preack-crash-20261015-r2.py`、`cache/probe-workspace-preack-enospc-20261015-r2.py` 和 `cache/probe-workspace-preack-enospc-20261015-r3.py`；JSON 为 `cache/workspace-preack-real-crash-20261015-r2.json`、`cache/workspace-preack-real-invoke-20261015-r2.json`、`cache/workspace-preack-real-enospc-20261015-r3.json`、`cache/workspace-preack-enospc-attempts-20261015.json`。这不是物理硬配额、断电持久性、最终整仓或所有部署平台的证明。


