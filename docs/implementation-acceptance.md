# 下一阶段实施与验收台账

最新暂存额度批次（2026-10-15）：[额度设计与验收](plugin-state-staging-quota.md)。499文件最终快照三端相关专项各205通过、无跳过，当前wheel安装额度验证通过；未知目录只计占用，不按名称删除。下方498文件全量/容器结果早于此额度改动，不能作为当前额度版完整验收。

## 当前批次：状态事务归属记录（2026-10-15）

本批最终终态（498文件固定快照47563f01e2c7b681e6b274eb5385d363dd3357860b0894bf8fc216208ab29484）：Windows92463退出0，2948通过/32跳过/721.21秒；Linux98374退出0，2854通过/126跳过/370.72秒；macOS27733退出0，2852通过/128跳过/428.20秒。三端各2980用例，全部终态，无失败。后续只有恢复测试移除未用os导入及文档更新，运行代码未变；该测试另有14项通过。Linux/macOS完整日志、XML与verification.json已复制F盘cache/state-runtime-full-evidence-20261015-r2/{linux,macos}，Windows原证据在cache/state-runtime-full-windows-20261015-r2。

同一wheel a2429048在Windows及macOS均离线安装、pip check通过，六个宿主模块实际来自site-packages；候选改名失败后的保留/旧状态恢复及正常新状态发布均通过。macOS2511整个安装管线退出0，94个help/103项入口通过，与Windows入口检查相同。macOS实际接收wheel的SHA256与Windows构建结果完全一致；证据已复制cache/state-runtime-wheel-evidence-20261015-r2/macos。复用了任务拥有的r3安装环境，旧产物证据仍绑定当时hash，不代表该环境仍安装旧wheel。

真实RPC29986退出0：实际SDK b76f2f99，a1/b1/a2状态分区、未批准边/未声明方法/API8对7、协议与额度拒绝、身份及服务边撤权后旧状态保留通过，27个拥有worker移除，恢复四字段均0。首次原生准备因锁文件迁移路径不存在而在read_lock前失败，未创建容器；保留/tmp/opskit-state-runtime-rpc-20261015-r2，复制锁到新输入目录并核对1690a8af摘要后在r3目录完成。正式网关73301退出0：同一SDK，实际HTTP拒绝/获批/撤销、CONNECT就绪后的注入取消/身份和私网撤权/清理后撤权均保留状态；网关库存策略及权限屏障通过，恢复四字段0。证明在cache/state-runtime-native-evidence-20261015-r2，SHA清单已保存。仅当前Linux Docker合成插件范围，不覆盖其他daemon/平台或完整网络矩阵。

额外真实OS信号探针在/tmp/opskit-state-os-signals-20261015-r3完成：仅对任务拥有的CLI PID发送SIGINT/SIGTERM，插件与客体后代均明确回报就绪后发信号；SIGINT退出130、自行清理后recovered0，其余恢复字段0；SIGTERM退出-15，显式reconcile回收1条遗留记录，active/pending/invalid均0。两者拥有worker确认移除，已提交状态摘要完全不变，客体未提交文件未发布。首轮包装器缺少__main__保护导致spawn重复执行CLI、未到达就绪点；恢复四字段0并保留失败目录。r2已通过但后代只确认创建，r3增加后代就绪回报后重新通过。缓存探针及实际日志/恢复JSON已保存cache/state-runtime-native-evidence-20261015-r2/os-signals；不是macOS/Windows信号、live CONNECT信号、断电、SIGKILL或daemon重启证明。

本批所有进程已终态；state-runtime-live-jobs-20261015-r2.json中sessions为空。当前状态恢复已有运行接入、三端完整回归、安装包和当前宿主真实容器证据；监督I/O、父死亡/崩溃在任意发布点、未记录暂存归属与容量、残缺记录人工修复、磁盘满/权限/断电持久化及完整网络/daemon矩阵仍开放。SDK漏洞和八类总目标也未完成。本批仍未提交/推送。

运行接入批次：_invoke在网络/容器创建前调用_recover_state，认证调用方分区由宿主显式传入；invoke既有进程内锁和OS状态锁覆盖恢复。_save_state显式接收plugin/caller，候选权限准备移到configuration→trust提交锁之外，锁内继续剩余期限与新鲜授权检查。_publish_state已替换原无日志双改名：先独占创建归属记录，再改名旧/新目录，最后finish_transaction；失败不猜测回滚。_state_staging替代TemporaryDirectory自动清理：存在事务记录时保留候选，无记录时才清理本次临时目录。提交后清理异常明确报告state committed，不把新状态回滚成普通失败；遗留无记录.previous仍拒绝。

498文件固定源码快照47563f01e2c7b681e6b274eb5385d363dd3357860b0894bf8fc216208ab29484的11组专项：Windows191通过/44.92秒、Linux191通过/21.33秒、macOS191通过/36.71秒，均无跳过、退出0。新增9项运行接入测试包括真实_save_state改名后os._exit(73)、异常候选保留、提交后清理失败、权限准备失败、self/caller恢复；另一进程在恢复期间实际尝试获取同一状态锁，均确认busy；资源边界断言恢复已完成后才到达_network。未运行真实Docker业务，本批不能替代实际容器复验。首轮77通过/2失败来自旧协议测试mock未接受新增宿主绑定关键字，已迁移并在固定快照验证；保留首轮XML。Ruff全项目通过；静态检查首次解释器缺Ruff，改用固定环境后发现一个新增未用os导入，已移除并重跑14项通过，最终94候选/0新增/0漂移。此测试文件单行删除晚于快照，宿主运行代码未变化。

同一固定运行源码wheel SHA256 a24290484d98d6829c889f9527d375439d852f0281b72cac54f59868ff281477，253运行文件/258成员，源码及RECORD验证通过。离线无依赖安装、pip check、-I实际site-packages的六模块来源、真实配置/信任授权读取、候选改名失败后保留/恢复旧状态、随后正常发布新状态均通过；证明在cache/wheel-state-runtime-20261015-r2/{verification.json,installed-state/verification.json}。SDK代码库存摘要仍3b64a5019608b8ada1a83514169223f3ca357e0d6ab3dae525db079881f3991f，未改guest库存，不据此沿用旧宿主真实容器验收。

当前同一固定快照的三系统全量回归已启动，macOS handle27733、Linux98374、Windows92463；在记录时均未终态，不算通过，不重启。日志分别在远端state-runtime-r2/full、Linux/tmp/opskit-state-runtime-full-20261015-r2、F盘cache/state-runtime-full-windows-20261015-r2。安装CLI检查handle76926仍在运行；后续读取同一handle并补终态。专项证据已复制F盘cache/state-runtime-evidence-20261015-r2/{linux,macos}；Windows在cache/state-runtime-validation-windows-20261015-r2。新运行接入仍需完整回归、安装CLI、真实容器、监督I/O、断电/磁盘满/部分写入人工修复与父死亡矩阵；本批未提交/推送。

随后安装CLI76926已退出0：94个help命令、103项入口检查均通过，证明在F盘cache/wheel-state-runtime-20261015-r2/installed-cli/verification.json。三系统全量27733/98374/92463最近一次均仍活跃，继续同一handle等待终态。

后续清理批次：finish_transaction先恢复旧root（需要时），追加绑定同一事务ID的aborted/committed结果并同步，再删除身份匹配的候选或备份，最后删除空wrapper及记录。已记录结果允许恢复部分清理，包含首次事务候选已删后的缺失root；结果未记录时仍拒绝缺失候选的歧义。清理先有界验证整棵树，再按身份/类型/文件大小和mtime逐项检查删除，最多8192条目、4096文件、16MiB；无跳过目录规则，链接/特殊/多链接文件拒绝。恢复时每次重新同步结果，即使上次写完整但fsync失败，也不能直接开始删除。StateCleanupError携带已知outcome，明确区分提交后清理失败；取消保留记录。残缺结果追加保持失败时拒绝，不自动截断或猜测修复。本批仍未接入发布/运行路径。

497文件固定快照56b7bcc7a3015ef63d9f944365845f5486f62958456976a3947d98113b7f8c3b：三端均验证归档、所有成员摘要和实际模块导入路径；Windows82通过/14.82秒、Linux82通过/4.56秒、macOS82通过/7.37秒，均无跳过、退出0。包括首次/已有状态的结果同步后退出、删除部分文件后退出、wrapper删除后退出，真实子进程退出73后可继续收尾；持续同步失败、权限失败、取消、残缺结果和外来链接均保留证据。首轮80通过/1跳过是无旧树可删除的无效参数组合，已从参数集合中排除；第二轮82通过后补充重试同步实现，最终仅以r3固定快照为本批证据。Ruff与diff检查通过；不是全量、wheel、运行接入、父进程监督或断电验收。

源码/清单在F盘cache/state-cleanup-source-20261015-r3，Windows日志/XML/verification.json在cache/state-cleanup-validation-windows-20261015-r3；Linux/macOS相同证据已复制cache/state-cleanup-evidence-20261015-r3/{linux,macos}。后续必须替换原无日志的发布路径、发布前准备候选权限、在插件锁内恢复self/caller分区并保留锁内新鲜授权，避免TemporaryDirectory异常清理提前删除带记录的候选；还需验证记录写入/追加失败的明确人工修复方式、期限/取消和轮转磁盘容量。本批文档修改晚于源码快照，未提交/推送。

后续恢复判定批次：core/plugin_state.py新增inspect_transaction/restore_previous。实际root/backup/wrapper/candidate身份均需匹配记录；wrapper只允许单个new条目，扫描最多看到第二条即拒绝，不递归遍历状态内容。只接受uncommitted、restore_previous和committed三种可证明拓扑，候选在提交前缺失或目录被替换则拒绝。restore_previous仅将有记录的旧备份恢复原位，保留记录与候选；恢复改名后真实进程退出73，再次恢复报告uncommitted且不重复改名。此为恢复辅助函数，仍未接入运行入口，没有清理/记录退役或自动续跑。

496文件固定快照53dba2128bc0bdb998e585dd9eaf3a2208570176b682c0dffd152e48741783cd，源码归档/成员哈希及实际模块导入路径验证通过。恢复14项加记录/崩溃/授权44项：Windows58通过/6.91秒、Linux58通过/2.55秒、macOS58通过/4.74秒，均无跳过且退出0，Ruff和diff检查通过。Windows XML在F盘cache/state-recovery-windows-20261015-r1.xml；Linux/macOS日志、XML与verification.json已复制F盘cache/state-recovery-evidence-20261015-r1/{linux,macos}；源码在cache/state-recovery-source-20261015-r1。仅新增宿主模块和测试，未修改SDK库存；清理幂等、状态锁接入、发布授权、持久化、监督I/O及wheel/完整回归继续开放。随后文档修改不属于该固定快照。

新增宿主core/plugin_state.py与30项记录专项；只实现独占创建、文件同步和有界验证，不进行发布、恢复或清理，尚未接入运行路径。4096字节精确结构绑定插件、认证调用方、父路径/目标与目录dev/inode；拒绝旧.previous、已存在记录、重复字段、链接/特殊文件、错分区、父目录替换及重叠身份。实际子进程在记录同步后os._exit(73)保留可读记录与旧新状态；同步失败也保留证据。后续还需接入状态锁/发布、实际目录拓扑验证、恢复幂等、部分清理与持久化/监督I/O，不宣称自动恢复已完成。

495文件源码ZIP快照SHA256 80e1380597911160d02067ddf00e3a39be6b7ce7d6c02c04c8178534bcc3f462；Linux与macOS均先核对归档及每个成员摘要，再验证实际导入此快照的core/plugin_state.py。记录/重启/授权三组在Windows44通过/5.18秒、Linux44通过/1.89秒、macOS44通过/4.16秒，均无跳过、退出0；Ruff通过。Windows XML为F盘cache/state-records-windows-20261015-r2.xml；Linux/macOS日志、XML和verification.json已复制F盘cache/state-records-evidence-20261015-r2/{linux,macos}。源码归档/清单在F盘cache/state-records-source-20261015-r2。此为专项，不替代全量、wheel、安装入口或真实断电验收；随后文档更新不属于该快照。新模块未列入guest SDK库存，本批未改变SDK策略或源码摘要。

本任务旧cache目录迁移到F盘cache/windows-route-transactions-cache-20261015，56个剩余普通目录逐一迁移并创建原路径junction；已迁出目录和559个文件的原路径入口恢复。首轮整体Move-Item遇到pytest特殊路径失败，三个pytest残留目录继续保留在C盘，未删除未知内容。迁移后C盘约569MB可用，仍建议扩大余量；原路径安装环境在-I模式下实际导入其site-packages成功。缓存迁移不属于产品性能测量。

## 历史批次：独立出口代理与SDK策略6（2026-10-14）

独立实时授权当前wheel c81def912717b02f66c83ced5e65616cd03226e8def0ac3a09c28710ae16aef8 构建退出0，251运行文件/256成员，源码/RECORD核对通过；安装入口正在复验。当前487个跟踪/未忽略候选文件快照秘密扫描退出0、0匹配，快照secrets-bytequota-0eq7_d4l；晚于该快照的台账文档更新不属于本次扫描，不作为最终提交/历史秘密扫描已完成。63686正式gateway策略2/独立实时回退修正前Windows完整回归2773通过/30跳过/652.48秒退出0，20667对应Linux完整回归仍运行；当前独立回退修正73项双平台均通过，Windows相关更广已开始。

新增独立实时授权监督：_check_live_authorizations原作用域外同步配置/信任读取回退已移除，未进入作用域时建立一次性_execution_budget，复用同一启动/业务期限及读取进程，在finally回收；已有作用域不额外创建进程。单次读/配置/信任/身份/服务/私网仍复用同一谓词。新增真实阻塞在authorize阶段、KeyboardInterrupt回收、同进程两次独立复核看见真实服务撤权，固定错误不泄漏存储内容；纯读取谓词单次trust加载测试直接验证worker helper。41175 Windows73通过/55.66秒、67270 Linux73通过/89.57秒，均退出0。全项目Ruff与静态94候选/0新增/0漂移通过。此改动不解决manifest/catalog同步I/O、服务声明读取和状态发布/崩溃账本的剩余边界。

63686/20667策略2完整回归先于此独立回退修正启动，未终态前继续同一handle，不用其未来通过替代当前源码全部验收。当前源码wheel与候选秘密快照扫描已启动，结果需单独登记。

正式gateway硬化批次：POLICY升2，固定构建清除/usr/bin、/usr/sbin、/usr/local/bin普通文件setid位并移除infocmp，保留共享库；旧policy1库存拒绝，SDK仍policy6/库存不变。20542 Windows95通过/48.52秒、88614 Linux95通过/61.14秒，均退出0。98809实际正式prepare/身份校验/物理库存/完整network合成插件及CONNECT取消/身份撤销/私网撤销/清理后撤权全部退出0；新gateway359ebe4520dd3b516b0894ef81fcb9a3784eec7390a2498d37b546dc03b6daa2，真实标签/catalog策略2，没有解析器覆盖。库存证明infocmp不存在、三个可执行目录setid列表为空、Python分发为空、SSL/SQLite/策略导入正常、UID65534/CapEff0/NoNewPrivs1/Seccomp2，全部具名资源清理确认/恢复账本四项0。

正式新gateway实际OS扫描12844退出0，ArtifactName匹配359ebe45，同一数据库仍Critical0/High44/Medium58/Low61/Unknown3；dpkg包记录未删除，不能用组件删除宣称扫描已清零。8编号逐项处置保留，已移除infocmp的网关组件适用性与SDK仍包含命令的范围分开。新wheel33ed507bc0d4a09ff2218be79db6abbecbcee7fd62751efa47bc591ea3e76146 构建退出0，251运行文件/256成员、源码/RECORD通过；65823当前33ed507b安装/pip check/94help/103入口检查已poll退出0；63686正式policy2 Windows及20667Linux完整回归运行中。

前批最终私网提交宿主完整回归93012 Windows2772通过/30跳过/682.20秒、61332原生Linux2676通过/126跳过/624.50秒，均退出0。其父进程早于正式gateway policy2启动，不能代替策略2构建的专项/原生验收；没有复用此前Linux3失败作为通过，原挂载环境失败历史仍保留。

本批基础镜像八个High编号已逐一读取Debian官方Security Tracker，均HTTP200并保留响应SHA256；Trixie仍vulnerable，发行版minor/no-dsa/postponed不等于项目豁免。实际SDK/gateway的UID65534、CapEff0、NoNewPrivs1、Seccomp2及mount/user namespace/mount namespace拒绝已复验，具名容器清理确认。候选27649亦已poll退出0，infocmp/可执行目录setid移除后SSL/SQLite/策略导入与受控HTTP边界正常；仍是解析器显式覆盖的隔离实验，非正式生产policy或全部High关闭。完整细节见plugin-base-image-audit.md。

17796原生依赖环境下原期限HTTP/真实pip重查50通过/42.51秒退出0；61332当前源码完整Linux回归已启动，93012Windows完整回归仍运行。27649代理瘦身候选传输/运行已poll退出0：仅在隔离候选层移除/usr/bin/infocmp与可执行目录setid位，不改共享库；SSL/SQLite/代理策略导入、私网拒绝/授权及直连隔离通过，恢复账本四项0。此实验覆盖时显式替换镜像解析器以选候选，不是生产身份验证通过，未写批准catalog、未更改生产POLICY，OS漏洞处置仍开放。

71541 Linux原生依赖对照退出0，同Python3.11、32项分发包清单逐项相同；mounted三次pip导入17.592/13.773/10.959秒，native0.667/0.667/0.566秒。证明当前挂载盘导入成本显著，不能单独判定所有超时由它引起；原期限下的失败用例重查已启动。未修改产品期限/测试预算，未安装新依赖。原始清单与任务独占venv路径记录native-linux-validation-20261014.json，性能范围见performance-baselines.md。

24770当前29b919ad wheel安装、pip check、94help/103入口检查全部退出0。93012当前完整Windows回归仍运行，71541原生Linux依赖复制/对照仍运行；没有以旧完整回归代替当前源码，也没有将未终态进程当失败后重复启动。

最终提交边界专项62041 Windows85通过/33.28秒，30309 Linux85通过/81.73秒，均退出0；保留清理后的已使用网络授权、嵌套作用域恢复及无效请求预算用例。授权依赖最多256项/编码64KiB，避免把调用嵌套深度4错误用于顺序调用多个提供方的已使用依赖，仍受原256KiB邮箱限制。62340原生验证已poll终态退出0，全部场景成功；新增cleanup_revoke在实际网络清理结束后、状态发布前撤销私网授权，拒绝发布并保留原状态。当前29b919ad6a3098b94cadafec74d90716e6ff0ba61048f22b072c0d0767b051fd wheel构建97852退出0，251运行文件/256成员，源码/RECORD通过；24770安装入口、93012当前Windows完整回归进行中。

22064前一版Windows完整回归2769通过/30跳过/788.48秒退出0，36594 Linux更广691通过/2跳过/2106未选择/509.37秒退出0。均早于最终清理提交竞态修正，不登记当前完整源码通过。89262 Linux前批失败重查49通过/1失败，HTTP已通过但真实pip valid仍20秒超时，未放宽测试或产品期限。71541正在同一WSL内复制既有依赖到任务独占原生文件系统venv并核对分发清单、对比pip导入时间，检验挂载盘导入开销；不是安装新依赖或宣称产品优化完成。

后续提交路径复核发现_network退出恢复快照会使清理后的最终提交复核漏掉私网授权；已改为实际invoke保留已使用列表直到状态发布结束，嵌套已使用授权沿用到外层提交，顶层invoke finally恢复原作用域，普通独立_network仍恢复自身。新增清理后撤销拒绝用例；5823/38458双平台84项专项正在验证，此修正晚于19314原生私网撤销及79c54907 wheel，不能复用旧通过关闭提交竞态。22064当前Windows完整回归及36594Linux更广亦在此修正前启动，终态须按加载范围记录。

新增运行中私网撤销批次：此前代理只保存启动列表，身份/服务检查未覆盖私网项。现在_network在作用域内绑定原始私网授权，嵌套调用保留外层授权；同一次受监督配置复核检查原授权仍是当前列表子集，删除任一项拒绝调用，新增项不扩展已有代理。每秒轮询检测及提交前复核，不宣称即时逐请求撤权。19314实际network插件prepare/invoke/CONNECT保持期间private_revoke通过，状态保留、资源恢复四项0；94946 Windows82通过/61.13秒，52609 Linux82通过/95.06秒，均退出0。首轮25733/92203各10失败来自新增测试误把ContentInspector当作context manager，改用closing后复验；生产接口未为迁就测试添加无用方法。

前批gateway完整回归35400 Windows2755通过/30跳过/837.58秒退出0；47583 Linux2656通过/126跳过/3失败/1100.73秒退出1，失败为实际HTTP 10秒预算及两个pip20秒超时，原因仍在核实，不能算Linux通过。两进程先于当前私网撤权代码启动，不是当前完整源码证据。当前22064 Windows全量、36594 Linux插件/config更广仍运行。当前wheel79c54907c57dfaee09f8fd0be283a64a59c487ca4dab9a4a6f992ec945321c14 构建31627退出0、251运行文件/256成员、源码/RECORD核对通过，安装入口复验进行中。首轮wheel构建误用无setuptools的审计环境，BackendUnavailable退出1，换用既有项目构建环境成功，没有安装新工具或修改构建要求。

8325最终完整网络插件验收退出0，cache/gateway-plugin-lifecycle-20261014/verification.json：实际prepare、三次私网授权切换调用、保持真实CONNECT隧道时注入取消及插件身份撤销全部符合预期；两种失败保留原提交状态，不发布uncommitted文件，资源账本四项0。仅当前Linux Docker IPv4和自有peer；不证明运行中私网列表撤销或真实OS信号。验证器参数前置校验与最终返回包含plugin结果均在此轮实际执行。全项目Ruff通过、静态94候选/0新增/0基线漂移、diff检查通过；首次静态调用误用了无ruff元数据的测试环境，改用现有审计环境后退出0，未修改门槛或新增依赖。

64970当前fa07622f wheel安装/pip check及94help/103入口检查退出0。43245完整合成网络插件prepare/invoke初轮退出0，SDK仍ad31a18c，未授权拒绝/精确授权成功/撤销后新调用拒绝，直连全部拒绝、恢复账本四项0，证据gateway-full-plugin-20261014。新增CONNECT隧道期间注入取消/身份撤销验证8325运行中；不是运行中私网授权撤销。14408实际SDK/gateway整树组件盘点退出0，infocmp真实存在，各11个setid文件；两个APT partial路径权限拒绝，不能泛化为完整不存在证明，细节见基础镜像审计。

补充终态：45460 Windows164通过/1跳过/62.11秒、87300 Linux165通过/106.33秒；桥接参数99709 Windows64通过/28.12秒、40152 Linux64通过/38.07秒，均退出0。SDK镜像ad31a18c真实RPC46435退出0，27worker清理、恢复各项0。6723仓库gateway验证器退出0，未授权拒绝/精确私网授权成功、两场景直连拒绝、宿主网关不可绑定、资源清理确认。物理包检查以显式site-packages路径完成。SDK13包审计退出0/已知匹配0；SDK及代理OS扫描均Critical0/High44/Medium58/Low61/Unknown3，八个High编号处置开放。32395最新wheel构建退出0，fa07622f28fd639b6931cbcc96b5174a40b617ade5ce6f269e88aae88052ac80，251运行文件/256成员、源码/RECORD通过。35400/47583完整双平台回归及64970安装入口仍运行，不计为通过。完整网络插件prepare/invoke、CONNECT/DNS/动态撤权及其他八类门槛仍未关闭。

前批首次授权/启动语言源码完整终态：88383 Windows2741通过/30跳过/705.83秒，23106 Linux2645通过/126跳过/788.50秒，均退出0。21512真实SDK RPC退出0，仍30ce3718、27worker清除、状态撤销保留、恢复账本四项0；e17d3cc5860184b28becb224e62dba392dedf13a83e04897cdb50d611597474c wheel源码/RECORD与安装/pip check通过，27759入口94help/103检查退出0。482文件候选秘密扫描退出0/0匹配，快照secrets-bytequota-y5w5ekg7；此扫描早于本批gateway文件/文档，不是当前最终提交或新的历史扫描。

安全边界样例已复现：gateway-dependency-boundary-4119360085a04517896cf5e6515e9a25记录旧实际SDK默认拒绝127.0.0.1，挂入模拟依赖.pth后策略接受；仅字面IP解析/0连接，两个容器清理确认，没有安装真实恶意wheel。旧代理复用插件镜像，依赖摘要锁不能使插件依赖成为平台可信代码。新增平台独立gateway构建/库存/实际镜像身份校验；只含固定来源与平台stdlib脚本，去除site-packages/pip工具，运行显式entrypoint与-I -S。SDK策略6移除代理源；源码与冻结包分开选取两种库存。缺失/不匹配网关拒绝network执行，不回退旧插件镜像。细节见plugin-gateway-isolation.md。

51225首轮Windows163通过/1跳过/48.70秒、77381 Linux164通过/70.50秒，均退出0。随后发现PyYAML错误类型不属于ValueError，已补YAMLError脱敏与坏YAML用例；构建约束补充site-packages/pip移除，尚需最终专项。80274实际gateway构建/启动钩子复验退出0，image=c692074f5280c0f677fb9b195b23b99ab26dca1d35fb79537a210ba7f4e94532；基线与同一合成.pth均拒绝回环/0连接，两个容器清理确认。原分发清单探测基于-I -S的默认sys.path，不能独立证明物理安装包全部为空，后续补显式site-packages路径检查。当前尚无policy6 SDK、完整网络或当前源码wheel终态，不登记整体验收通过。

## 当前批次：首次授权读取监督与启动语言清理（2026-10-14）

check_authorization在作用域外建立_execution_budget与owned reader，在已有调用中复用同一reader；先通过窄enabled查询拒绝禁用插件，再做受监督内容身份检查，最后沿用_check_live_authorizations的实时配置/信任/服务边谓词复核当前身份。删除宿主直接load_config/is_trusted读取，不另建授权算法，也不缓存许可。独立检查恢复原deadline/scope并回收reader；root权限继续拒绝。enabled查询返回单一bool，沿用原enabled真值语义，不能据此宣称配置schema已全面类型严格化。

71479 Windows85通过/18.01秒、74541 Linux85通过/28.17秒，均退出0；新增实际信任存储撤权、同PID复用、禁用时不读内容、root拒绝及禁止父进程同步config/trust读取用例。随后补充独立入口首次配置/内容检查成功后、真实授权os.read阻塞时的期限/取消回收，完整该模块Windows18通过/8.53秒、Linux18通过/12.90秒（52725）退出0；原配置保留、进程已不存在、线程scope/deadline恢复。前述启动语言清理Windows短ID重试79250退出0：72通过/1跳过/13.44秒，Linux73通过；短ID仅修正pytest环境变量限制，产品限制不变。

Ruff全项目、静态94候选/0新增/0漂移及diff检查通过。当前合并这两项修改后Windows88383/Linux23106全量、当前宿主真实SDK RPC21512与干净库存wheel23019运行中，终态前不登记通过；生产源码暂保持稳定。通用_check_live_authorizations作用域外分支、plugin_services权限解析、manifest/catalog、状态发布及私网动态撤权仍开放，本批不代替整体授权链/平台安全/八类验收。

## 当前批次：启动语言配置读取复用（2026-10-14）

main._preboot_locale删除直接打开配置/yaml.safe_load的重复解析路径，复用不写盘的load_config及现有1MiB/64层/20000事件/禁用别名限制；有效zh/en继续保留，坏/超额配置仍仅回退系统语言，原文件不改写。新增真实配置专项证明超额/别名/深层文档在YAML构造前拒绝，无配置不创建目录，有效配置/坏类型保留既有回退语义。

80441 Linux专项73通过/18.92秒退出0。15119 Windows首轮71通过/1跳过/2错误：pytest自动将超额文本用作测试ID，设置PYTEST_CURRENT_TEST时超出Windows环境变量32767字符限制，setup/teardown均错误，不是产品解析失败。为三个额度用例显式设置短ID，原输入/额度/断言保留，Windows专项重试中；未放宽产品限制。失败日志含巨长合成文本行，后续只读有界/过滤尾部，避免再次将其输出到对话。

前一WSL/私网配置监督Linux复验23737现已退出0：651通过/2跳过/2103未选择/357.71秒；Windows修正后649通过/4跳过，当前插件矩阵、8cafa026安装包与真实RPC证据齐备。该包早于本批main修改，不代替当前源码的发布检查。

## 当前批次：WSL与私网授权配置读取监督（2026-10-14）

51328当前修正后宿主真实RPC退出0：verification.json仍绑定实际SDK30ce3718，27worker清理、身份/服务边撤销拒绝、撤销后状态保留；recovery.json四项0。原30秒既有矩阵未放宽。当前Linux更广23737经handle复核仍运行，不能登记通过；此RPC矩阵不代替完整出口/daemon故障恢复或八类发布门槛。

当前终态：修正后10774 Windows649通过/4跳过/2103未选择/236.48秒退出0。Linux首轮44215退出1：同一期限分类用例失败、650通过/2跳过/2103未选择/526.03秒；修正后23737更广Linux复验运行中。当前8cafa026 wheel已无索引/无依赖重解析安装到专用环境，pip check退出0，11395安装入口退出0，94help/103检查、源码来源绑定和runtime_storage_created=false；51328当前宿主真实SDK RPC复验运行中，不能登记通过。此前16420f03与a64faffa仅作为各自批次产物，不替代当前wheel。

性能基线：42789退出0，startup-baseline-settings-3eb94d9871ee4c479e3e6460f16d121f/verification.json绑定当前修正后wheel8cafa026。Windows已安装opskit --help连续12次新进程，首次1.651390秒，后11次中位1.690601秒、最近秩p95为2.243220秒，10ms采样的进程树RSS峰值48496640字节；所有样本退出0且未创建运行数据。没有清空OS文件缓存，其他测试同期运行，RSS可能漏峰值且树求和可能包含共享页；这是单一入口基线，不是冷启动严格基准、优化前后比较或完整平台性能通过。下一步定位导入开销、扩大官方列表/监控/TCP/插件启动负载后再提出算法优化。顺带复核发现main._preboot_locale仍直接yaml.safe_load未复用既有有界config读取；后续应迁移至不写盘的load_config并验证坏配置/超额/无配置help行为，尚未修改此路径。

已安装同wheel的独立外部cwd importtime探测退出0（from main import app; app() --help），trace/stdout保存在同一基线目录；main累计导入约992922微秒，tailscale.cli约163834微秒，经tailscale.server顶层core.http引入httpx（约155834微秒），rich/typer亦有累计导入成本。累计时间含子模块，不能相加或当作各模块独占耗时；单次探测不证明因果提速。后续在调用点懒加载官方服务实现并复验全部help/实际命令，可以减少无关HTTP实现进入help路径，不能删除官方能力或随意修改命令注册。

修正后专项：56619 Windows22通过/15.55秒、83984 Linux22通过/30.63秒，均退出0，settings-supervision-final日志。新增实际结果返回后父期限耗尽测试发现_read_sandbox_setting将SandboxError也纳入RuntimeError包装，仍然拒绝执行但丢失期限分类；已在一般worker错误前显式保留SandboxError。首轮更广Windows69047退出1：该用例失败、648通过/4跳过/2103未选择/280.34秒；该父进程加载的是分类修正前源码，不用后续磁盘源码假称首轮通过。修正后Windows更广已重新启动，Linux首轮44215仍运行。Ruff全项目与静态94候选/0新增/0漂移通过。

51330首次有效wheel构建退出0，a64faffaa327c8e7909093c1b1fc1c21c72275bb3b60a30cf8cf5af0f6eaf4cd（分类修正前）250运行文件/255成员，无索引安装/pip check通过，入口9170仍运行；随后3467为分类修正后源码重新建立独立库存并验证wheel8cafa026f78f308ee8835d1c826dae16d6e9780e5ededfd607880f044793ae31，250/255、源码/RECORD通过。旧入口终态前不替换它正在验证的环境，当前修正后wheel尚未安装验收。

65515本批真实候选SDK RPC退出0，实际镜像仍30ce3718（guest库存未变），结果/身份/服务边撤销等现有矩阵通过；实际运行父进程在错误分类修正前启动，该矩阵不覆盖新增设置结果耗尽文案，不能代替当前宿主全部验收。

docker_command仅在Windows WSL回退时使用受监督distribution查询，原生Docker不读取无关设置；_network仅有network权限时、创建代理前重新读取当前插件private_targets，不再在_invoke同步读取整份sandbox配置。查询复用调用owned reader和当前剩余期限，作用域外创建一次性reader并在finally回收，每次读取最多30秒。固定setting请求与响应严格核对形状；发行版最多128字符并沿用名称规则，私网列表最多256项、单项512字符、编码后16KiB，客户端/子进程共享验证。不返回其他插件授权或整份配置，不缓存许可。

首轮21120/82525专项均退出0，各83通过（Windows32.68秒、Linux52.32秒），settings-supervision-targeted-{windows,linux}-20261014.log。覆盖实际配置同PID重读/撤销列表、作用域外回收、实际阻塞读取/取消回收、坏授权在网络创建前拒绝、无网络不读配置、严格响应；随后将配额提为命名常量并新增实际WSL路径和结果返回后父期限耗尽测试。更广69047/44215回归及当前宿主候选镜像RPC65515运行中，终态前不登记通过。Ruff当前变更通过；首次静态命令误用无ruff元数据的测试环境，改用既有审计环境后94候选/0新增/0漂移通过，未安装新依赖或放宽门槛。

首轮wheel构建误用不含setuptools的审计环境，BackendUnavailable退出1，日志/库存副本保留。确认既有测试环境setuptools82.0.1与wheel0.48.0后，在F盘临时路径、独立final库存目录重试51330；构建/安装未取得终态前不宣称完成。本批仍不关闭check_authorization、manifest/catalog、服务访问解析、状态发布I/O和完整八类安全验收。

前一启动监督源码已取得完整终态：70248 Windows2704通过/30跳过/727.18秒；10401 Linux2608通过/126跳过/790.08秒，均退出0。两份full-startup-supervision日志只证明本批WSL/私网读取修改之前源码，不替代当前批验证。

## 当前批次：启动配置读取监督与基础镜像OS审计（2026-10-14）

续验终态：86605安装入口退出0，当前16420f033e5146816e4dfa40236b30ec4017061d56854774a4dc71a0382b492d wheel的94help/103CLI检查通过。29655官方基础候选真实RPC退出0，实际SDK30ce37186ec642e502797c0d38ed6a37515ffed01ba4642ac80d4c8ae0fb5a62，27worker清除、撤销后状态保留、recovery各项0。58523同数据库实际SDK OS扫描退出0：166包记录/73不同编号，Critical0/High44/Medium58/Low61/Unknown3，与基础候选一致。实际SDK13包清单读取、只读/无网络/UID65534解释器与SDK导入、SSL CA/SQLite探测成功；CapEff0、NoNewPrivs1、Seccomp2，两个审计容器均确认不存在。独立pip-audit退出0，无已知漏洞，SBOM绑定该实际13包清单。不能将Python清单或OS扫描退出0当作完整安全通过；44条High的组件/权限路径评估见plugin-base-image-audit.md。Windows70248/Linux10401当前源码全量仍运行，未登记通过；生产基础配置/catalog未改。

_execution_budget在读取配置前建立CONFIG_READ_TIMEOUT=30秒启动期限与读取器所有权；嵌套调用取父剩余期限，复用同一进程。固定timeout查询只读宿主指定配置，返回单一数值或固定错误，不返回整份配置；_sandbox_timeout共用原有范围/类型校验。读取后业务期限仍为原开始时间+配置timeout与父期限较小值，不能从读取结束重新计时。清理finally覆盖yield前的失败/取消，外层回收及作用域恢复，内层不新建独立许可缓存。

66513退出0：Windows57通过/16.70秒；20444退出0：Linux56通过/1 Windows-only跳过/26.74秒，startup-supervision-targeted-{windows,linux}-20261014.log。覆盖真实配置重读/同PID、启动阻塞os.read回收、启动取消、真实配置结果返回后预算耗尽及坏存储不泄密。原预算时钟/状态测试在测试模块中注入读取接口，保留原断言；新增实际I/O专项不使用该注入。随后用命名常量统一两处30秒及测试启动窗口，无行为变化。更广13420退出0：Windows627通过/4跳过/2103未选择/196.15秒；32538退出0：Linux629通过/2跳过/2103未选择/316.87秒。2951当前宿主真实RPC亦退出0，原30秒既有矩阵通过，SDK仍7dfd1f16（本批仅宿主修改），27worker清除，状态撤销保留，recovery各项0。Ruff F/E9、静态94候选/0新增/0漂移及diff检查通过。本批尚未重建wheel或跑当前全量；其他WSL distribution/private_targets/check_authorization/manifest/catalog及状态提交同步I/O仍开放。

.gitignore补充/backups/：core.updater与update_reconcile在开发模式将程序备份写入data_dir()/backups，原规则未覆盖。git check-ignore确认根备份文件命中，tests/fixtures/backups样例不命中；git ls-files backups为空，没有删除真实备份或历史文件。

OS审计工具Trivy0.75.0官方发布压缩包校验和匹配。首次提取因172765344字节可执行文件超出预设100MiB而退出，后按200MiB上限验证并只提取指定普通文件。第一次扫描97217退出1：127MiB数据库下载后写入C盘发生I/O错误，检查确认C可用空间0。将本轮新增且已校验的可执行文件/压缩包复制到F盘指定缓存、复核摘要后仅删除对应C盘两份文件，释放约208MiB；未删除用户其他缓存、数据或服务。报告/失败日志保留，工具/DB缓存现位于F:/ougato/opskit/cli/cache/trivy-os-audit-20261014-rnmbq2。

38208重试退出0仅表示扫描完成：os-vulnerabilities-retry.json的ArtifactName/ImageID均为当前7dfd1f16镜像，Debian13.6、87个OS包，235条包漏洞记录/114个不同编号（Critical3/High58/Medium103/Low68/Unknown3）。数据库UpdatedAt2026-10-14T01:19:20Z；部分严重度来自其他厂商。Critical记录为perl-base，另有gzip/PCRE2/SQLite/OpenSSL等可修复High，其他记录未提供修复版本。详情及最小处置顺序见plugin-base-image-audit.md；未按扫描进程exit0宣称安全通过，也未以ignore-unfixed隐藏记录。

固定来源候选查询：Docker manifest命令49459退出1；公共registry API备用查询74781退出0，Bearer token只在内存使用，未落盘/打印。官方python:3.12-slim index响应摘要核对为a6e34c59，linux/amd64候选docker.io/library/python@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe。仅保存公开来源/平台/摘要，不等于候选已拉取、修复包已存在、生产信任或新镜像验收通过。

84574固定候选拉取退出0，image ID为sha256:df18428df8a0f9189c84e7424d214b4c50092af1f9dca5842a00c41c39bda316；检查WSL实际BasePath在D盘且余量约5.3GiB后执行，未改生产配置、catalog或真实服务。使用同一已下载数据库、skip-db-update重新扫描该实际候选，当前仍运行；不把官方tag更新或pull成功称作OS漏洞已修。

6750候选OS扫描退出0：image ID与拉取报告匹配，Debian13.7/87包，166包记录/73不同编号（Critical0/High44/Medium58/Low61/Unknown3），可修复Critical/High记录0；原SDK为235记录/114不同编号、Critical3/High58。两者使用同一数据库时点及扫描器，未忽略未修复项；这是基础候选比较，不是完整SDK安全通过或原SDK实际替换。29655新来源SDK真实RPC已启动，输出plugin-rpc-base-candidate-20261014，终态待核实，44条High继续留待评估。

本批宿主57019构建退出0：新wheel16420f033e5146816e4dfa40236b30ec4017061d56854774a4dc71a0382b492d，250运行文件/255成员，独立库存副本与源码/RECORD一致。专用环境无索引/无依赖重解析安装和pip check通过；新安装入口仍运行。宿主当前全量70248 Windows/10401 Linux已启动，full-startup-supervision-{windows,linux}-20261014.log，保持生产源码稳定；尚无当前全量终态，不沿用前批2694/2598证明本批宿主源码。

## 当前待验收：SDK构建工具固定与policy5（2026-10-14）

针对实际SDK镜像pip25.0.1的漏洞记录，plugin_runtime构建在SDK/插件依赖安装前增加平台固定pip26.2通用wheel及SHA256锁文件，bootstrap只安装该包、带哈希/only-binary/no-deps，禁用版本自检和缓存；后续完整SDK/插件锁安装也禁用版本自检。plugin_lock拒绝插件锁中的pip（含大小写规范化），避免覆盖平台构建工具。RUNTIME_POLICY升为5，旧policy4镜像执行时拒绝，要求重新prepare；catalog schema仍2，基础镜像/SDK/依赖锁身份检查保留。

独立原型不登记为生产SDK：policy0、无catalog，22235首次构建后包清单含非JSON输出导致退出1，容器/原型标签均清理。13036重试显式禁用pip缓存，原始输出保存，确认13包中pip26.2，退出0且资源清理确认。原型不替代正式构建/RPC或新镜像已知漏洞审计。

首轮21326（Windows）/62160（Linux）均1失败/91通过/1跳过：镜像合法夹具仍硬编码4，生产新策略正确拒绝。夹具已改为当前RUNTIME_POLICY，上一策略拒绝用例随之验证4；新插件锁pip拒绝专项Windows2通过。修正后62455退出0：Windows94通过/1跳过/21.21秒；90310退出0：Linux94通过/1跳过/50.02秒，pip-bootstrap-targeted-{windows,linux}-20261014.log。69258正式SDK真实RPC仍运行；本批尚无新镜像原生/审计终态。Ruff F/E9、静态94候选/0新增/0漂移及diff检查通过。前批2692/2596全量和2f194506 wheel不作为当前policy5源码完整验证。

正式镜像验收取得终态：69258退出0，cache/plugin-rpc-pip-bootstrap-20261014/verification.json记录provider/a/b均使用sha256:7dfd1f1684d32fb37f2e13c0bcc5361a4e12ecaab254f53f44b1c89b858c73ad，原30秒既有RPC矩阵通过，身份/服务边撤销拒绝、撤销后状态保留，27worker清除，recovery各项0。76894实际新镜像列包退出0，13发行包中pip26.2；SDK漏洞复验退出0，无已知漏洞，CycloneDX1.4 SBOM13组件/0漏洞。sdk-pip-bootstrap-final-audit-20261014绑定实际镜像并确认该次无网络/只读/非root审计容器已不存在。旧25.0.1失败证据保留；不把本次Python发行包审计扩展为基础镜像OS或未知漏洞通过。

11480当前wheel构建退出0：独立库存副本、当前源码/RECORD一致，250运行文件/255成员，SHA256=570bc145315ffc3f49ef8dd801ab5058c0dc64ccf076571284e06da865d2dba9。专用环境无索引/无依赖重解析安装与pip check通过，安装入口54642仍运行。Windows84885/Linux31536本批完整回归已启动，full-pip-bootstrap-{windows,linux}-20261014.log；生产源码保持不变，终态前不登记当前全量通过。

54642安装入口现已退出0：当前570bc145 wheel的94help/103CLI检查，报告绑定site-packages来源及runtime_storage_created=false。新wheel实际安装元数据闭包仍25项，新的dependency-audit/provenance.json绑定当前wheel；resolved-runtime.txt与前批已审计清单逐字节一致，SHA256=A75980530D088BD399B7B95DFADCAA483AB03EBA8527D2C5E29A289A45C5D3F8，复用该确切版本集合的审计证据，不宣称又运行一次运行依赖漏洞查询。

当前policy5源码/测试及本条追加前文档候选扫描退出0：478文件/0规则匹配，cache/secrets-pip-bootstrap-submission-20261014.log，快照cache/secrets-bytequota-qw0oobfo；已验证工具摘要、脱敏报告及默认规则。本次仍不是新的Git历史扫描。双平台全量84885/31536仍运行，源码保持稳定；SDK Python已知漏洞修复、既有RPC和安装包入口均有当前产物证据，其他完整平台门槛仍未关闭。

policy5批次完整终态已核实：84885退出0，Windows2694通过/30跳过/666.97秒；31536退出0，Linux2598通过/126跳过/750.68秒，full-pip-bootstrap-{windows,linux}-20261014.log。此结果发生在后续启动配置监督修改之前，不作为后续宿主源码完整回归证据；OS审计仍暴露未处置记录，不能以该全量或Python包审计关闭整体发布安全。

## 当前待验收：作用域内实时授权读取监督（2026-10-14）

config.load_config提取共用_load_config_path；trust._load提取共用_load_path，authorization_snapshot支持宿主指定路径。原有入口继续使用同一有界读取算法。sandbox实时复核集中到_read_live_authorizations，身份、模块启用和服务授权仍使用既有谓词，未建立第二套权限算法。

调用inspection_scope内的_check_live_authorizations改由已有ContentInspector进程读取，内容与授权请求串行共用256KiB固定邮箱、owned_process及wait_result；最多256身份/256服务边、路径65536字符、文本128字符、严格指纹与API整数范围。响应只有固定ok/failure，不返回存储内容；失败/超时/取消回收，结果返回后再检查宿主共享期限。最终发布的configuration→trust锁及顺序未改，读取子进程不申请写锁。作用域外的实时检查、check_authorization和预算建立前配置读取等同步路径仍开放。

14184退出0：Windows61通过/11.01秒；79611退出0：Linux60通过/1 Windows专属跳过/25.96秒。这两次早于追加内容/授权同PID、取消和损坏存储测试，后续更广回归覆盖补充。Windows广泛回归首次启动退出4，命令引号丢失导致pytest把or识别为路径，0测试执行，日志保留；固定启动器92215重跑已退出0：615通过/4跳过/2103未选择，231.84秒。Linux86032更广回归仍运行，不能登记通过。Windows全量44956已启动，full-authorization-supervision-windows-20261014.log，终态前不登记通过。生产源码保持稳定以便同批验证。

75249构建退出0：独立源码库存副本、当前源码/RECORD一致，250运行文件/255成员，wheel SHA256=2f19450626b062272d7da43cacefe73df2aa18051737c622b0c72517e0f168c0。专用环境无索引/无依赖重解析安装及pip check通过；安装入口51362退出0：94help/103CLI检查，installed-cli/verification.json确认site-packages来源和runtime_storage_created=false。Ruff F/E9、94静态候选/0新增/0漂移及diff检查通过。完整源码全量尚未取得终态；上批wheel与完整回归不得代替本批证据。

真实RPC81690退出0：当前SDK重建为sha256:c5ce1600f03f7c75ebcfeee68fdea0cb0f281bc08b71f8e53b5a427cd3faefff，cache/plugin-rpc-authorization-supervision-20261014/verification.json。provider/a/b既有调用方状态分区、运行中身份/服务边撤销、协议版本/结果信封等正负矩阵通过，原30秒预算未改。27worker已清除，recovery的recovered/active/pending/invalid均0。该矩阵范围不等于完整daemon故障、出口/OS取消或生产供应链验收，仍需其余计划范围。

Linux更广86032已退出0：617通过/2跳过/2103未选择，387.77秒。Linux全量42813也已启动，full-authorization-supervision-linux-20261014.log；Windows全量44956继续运行。双平台全量保持同批生产源码，运行中不作为通过证据，不因观察等待重新启动。

本批wheel依赖审计：专用安装环境通过本项目发行元数据遍历实际启用的依赖闭包（不启用dev extra），核对各版本满足Requires-Dist，得到25项第三方运行依赖。dependency-audit/provenance.json绑定当前2f194506 wheel摘要、解释器和平台，resolved-runtime.txt记录确切版本。独立审计环境pip-audit查询退出0，No known vulnerabilities found；runtime-sbom.json为CycloneDX1.4、25组件/0已知漏洞，未忽略漏洞、未自动修复或重新解析依赖。该结果不涵盖本项目逻辑、Linux/macOS解析差异、OS/基础镜像、构建/审计工具和未知漏洞；确切版本清单不是带wheel哈希的供应链锁。当前git ls-files -ci --exclude-standard无输出，未发现被忽略规则命中却仍跟踪的文件。

本批候选秘密扫描退出0：478文件快照，Gitleaks8.30.1规则匹配0；cache/secrets-authorization-submission-20261014.log、cache/secrets-bytequota-prdqhe5w保存摘要库存和脱敏报告。工具官方发布ZIP摘要及ZIP内exe摘要已复核，默认规则/空ignore/ignore-gitleaks-allow/redact100与先前扫描一致。该快照包含本次生产/测试/文档及本条记录追加前的候选，不包含缓存与克隆插件；不能证明未知秘密或所有远端历史安全。HEAD仍aa9b035，未提交；本次未重复运行Git历史扫描，不把候选扫描说成新历史验收。

授权监督批次全量均取得终态：Windows44956退出0，2692通过/30跳过，700.08秒；Linux42813退出0，2596通过/126跳过，746.25秒，full-authorization-supervision-{windows,linux}-20261014.log。这是下述后续policy5构建工具修复之前源码的全量证据。

SDK Python供应链检查发现真实缺口：固定c5ce1600镜像的无网络/只读/非root临时审计容器列出13项Python发行包（含pip），读取命令及指定容器缺失确认均退出0，cleanup.json保存精确容器名及absent=true。pip-audit CycloneDX及JSON两次均退出1，报告pip25.0.1有12条已知漏洞记录；记录可能含别名/重复，不能称12个独立漏洞。sdk-dependency-audit保存原清单、SBOM和vulnerabilities.json；其余12包未报告匹配。这不能被Windows运行依赖审计0漏洞覆盖，也不代表已完成OS/基础镜像组件扫描。

修复候选已核实：审计服务列出的修复版本最高到26.2；PyPI pip26.2通用wheel元数据已取得，SHA256=931c303696af6fa3417112103b1cad26890e5a07eccb5b99783700e33f2b8aad、大小1816475、Python>=3.10；pip-fix-audit查询退出0/无已知漏洞。元数据观察不是独立发布签名，也不是SDK已修。下一批需在SDK依赖安装前固定并校验构建用pip，禁用旧pip安装后的自检导入，不能无版本升级、忽略漏洞或只修改扫描清单。重建后的实际镜像须重新列包审计，并重跑SDK/RPC、镜像执行权威及供应链标签验证；生产源码在Linux全量运行中保持不变。

## 单次实时授权读取快照（2026-10-14）

plugin_trust.authorization_snapshot在每次复核入口重新读取信任存储，读取失败则拒绝授权；作用域限定到当前线程并在finally恢复，不跨独立复核缓存许可。plugin_sandbox._check_live_authorizations在同一作用域内复用现有身份与服务授权谓词，避免同一复核重复读取文件。trusted_record返回深拷贝，调用方不能修改快照内的指纹或服务权限。修改操作仍直接严格读取存储，不使用快照；原configuration→trust提交锁顺序保留。

初轮专项Windows/Linux各102项通过，但该结果早于补充的记录防修改用例。包含该补充用例的更广插件/config回归已取得终态：64350退出0，Windows602通过/4跳过/2103未选择，185.14秒；98829退出0，Linux604通过/2跳过/2103未选择，290.18秒。日志authorization-snapshot-all-{windows,linux}-20261014.log；本次Ruff F/E9与git diff --check通过，静态94候选/0新增/0基线漂移。上一批2675/2579全量和779e5456安装包仅证明上一批源码，不能作为本次源码的完整或安装验证；本批未启动新构建或原生任务，两组回归会话均已结束。

该改动减少重复读取，仍在宿主同步读取config/trust，不解决文件系统阻塞时的硬期限。下一步设计锁内受监督读取时，必须保留当前配置、身份、服务授权链和状态提交的同一锁区间复核，子进程不得申请父进程已持有的写锁。八类整体目标及Git提交/推送仍未完成。

## Windows命令文件所有权、补偿与静态路由（2026-10-13）

四种Windows驱动的shim文件和Node/Go/Java junction失败后的包装器已统一到windows_commands。所有命令名、所有权及文件类型先完整预检，再同目录原子发布；未知内容、软/硬链接、reparse路径、超过1MiB或非UTF8文件拒绝。新命令和新建目录使用当前用户私有权限；已有PowerShell profile原子替换时在写入内容前复制原DACL，ACL复制失败不替换原文件。发布或删除失败/取消补偿全部已尝试文件；后续PATH失败也补偿命令文件，外部不同内容保留且明确rollback incomplete。命令复核在registry/profile作用域内执行，避免复核失败后仅恢复文件而遗留新PATH。restore_original在命令预检通过后才移除junction；本批尚非整个恢复操作的持久事务。

Node/Go/Java新shim使用固定active目录的具体命令，fallback使用受管版本目录的具体命令，不再通过findstr解析JSON或where递归查找自身。Go的gofmt现在路由到gofmt.exe；Node npm/npx路由到各自.cmd。包装器为ASCII源，账户/平台绝对路径由%~dp0取得，关闭delayed expansion，直接尾转交.cmd而不用CALL二次展开。保留旧模板及旧二行包装器仅作为受管根/命令匹配的精确迁移凭据；接受旧Windows写入产生的双CR，不把任意marker认作所有权。Python仍使用旧快照路由模板，只补齐文件所有权与事务，JSON路径解析及Python活动路由另待修复。

当前最终相关Windows205 passed /5 skipped /34.81秒，cache/windows-commands-final-20261013.log；Linux135 passed /20 skipped /12.93秒，cache/windows-commands-linux-20261013.log。新增40项命令检查含8场景公开入口保护未知文件、旧模板迁移、写入/删除后部分失败及取消、PATH后续失败、外部文件变更保留、ACL、名字/根链接拒绝、恢复预检，以及3项原生cmd检查。原生临时Node活动junction和fallback均验证中文/空格/&/%/!账户目录、参数A!B及退出7，Go/gofmt验证分别执行where/findstr夹具，仅使用系统自带可执行文件的临时副本，不执行下载软件或用户真实安装。两项联合registry检查验证外部命令改变时恢复registry/profile，外部registry改变时补偿命令/profile。首次89通过/1失败为复制改名后的where帮助文本为空，改以实际行为区分路由后通过；失败日志保留。静态107存量/新增0/差异0，diff通过。

本批完整Windows回归终态2252 passed /18 skipped /515.22秒，cache/full-windows-commands-20261013.log，进程exit0；回归期间未改生产/测试源码。wheel源码及RECORD核验240运行文件/245成员，SHA256 4e8064ac237bb565bb1f4081f63650a9bf7e6ec3c698e74b79c1abd25a70dbf0，独立安装pip check及103 CLI检查通过，cache/wheel-windows-commands-20261013.log，cache/wheel-archive-reader-w3r3p1_k。候选秘密扫描436文件/0命中，cache/secrets-windows-commands-20261013.log；之后CI及文档更新需在提交前复核。额外临时批处理通过真实core.process Job监督及verify_command_version，cache/native-command-supervision-20261013.log，不代表原生服务或插件取消矩阵完成。

只读核对origin为GitLab，远端develop与当前本地基准同为aa9b03583aabf5f035cc940438ee5e3a59e017bc；公开GitHub develop为d44d4c556e2c4bda0a006ab1a0e7f2df7ba740bd，不能据此证明自动镜像已同步。此前.gitlab-ci.yml只有标签release任务，不会在develop运行已有GitHub六组合回归。已增加GitLab Linux/Python3.10和3.12两任务矩阵、静态/wheel独立安装/完整测试/JUnit、标签release前test阶段门槛；本地YAML解析、矩阵及阶段顺序检查通过，尚未推送或实际Runner验证。GitHub六组合仍需当前提交的实际运行证据，Windows/macOS镜像同步或Runner信息已询问用户；本地配置不是远端验收。

现有目录的实际多账户ACL、最后检查后的外部竞态、Python路由、junction与两个PATH删除步骤的整体补偿、快照与版本目录完整安装/卸载事务及断电恢复仍开放；此前Linux整批九配方子进程超时未因本批其他Linux测试通过而关闭。局部文件事务不等于完整系统状态恢复；八类门槛仍未全部完成，未提交推送。

## Windows边界批次发布核验与Linux失败（2026-10-13）

当前稳定源码重新构建wheel，239个运行文件/244个wheel成员经源码及RECORD校验；SHA256为46c020467b2db2a66796b0f51fc2818b756cdc05a5b8013d0ca53108962e9df1。独立安装环境pip check通过，94个帮助入口及9个实际行为检查共103项通过。日志cache/wheel-windows-boundaries-20261013.log，产物目录cache/wheel-archive-reader-irsn8pmr。候选提交秘密扫描434文件/0命中，cache/secrets-windows-boundaries-20261013.log；扫描结果不能证明不存在未知秘密。

Linux本批相关117项的实际结果为99 passed /17 skipped /1 failed，48.81秒，cache/windows-boundaries-linux-20261013.log。失败为test_all_nine_recipes_reject_competing_process_before_state_read的子进程communicate超过原10秒期限；不将PowerShell包装进程exit0误记为pytest通过。单独importtime探针全配方发现18项约7.998秒，cache/linux-recipe-import-time-20261013.log，显示加载成本接近测试总期限；这只是诊断线索，不证明失败完全由挂载盘或导入引起，也不证明锁正确。原失败保留，未提高期限或修改生产源码掩盖结果。

单独诊断同一跨进程锁测试保持原10秒子进程期限，1 passed /10.87秒（含pytest/父进程），cache/linux-recipe-contention-diagnostic-20261013.log；没有改生产或测试源码。该结果不能取消整批失败，原超时根因尚未完全定位，仍需拆分加载成本与锁竞争预算。

当前Windows完整回归终态为2210 passed /18 skipped /634.08秒，cache/full-windows-boundaries-20261013.log，进程exit0。回归期间未修改生产及测试源码；本批wheel包含同一稳定运行源码。静态检查107存量/新增0/差异0，cache/static-windows-boundaries-20261013.log；git diff --check通过，git ls-files -ci --exclude-standard为空。18项跳过不算验收通过；Linux整批的超时失败仍开放。完整快照/shim/fallback事务、断电、多账户、历史HKLM迁移及八类发布门槛仍开放，未提交推送。

## Windows junction移除shell依赖（2026-10-13）

Node/Go/Java的_set_junction及restore_original已接入windows_junction，移除cmd /c mklink、rmdir。使用CreateFileW以OPEN_REPARSE_POINT打开对象，DeviceIoControl读/写mount-point数据；所有句柄finally关闭。先核对junction直属受管根、父路径不为链接、目标目录在同根，拒绝普通目录、未知reparse tag及外部目标。首次建立在随机同级临时目录上设置junction，再rename到空入口，失败清理临时项；切换已存在junction就地更新reparse数据，不先删除旧路由，失败时复核当前目标并补偿旧数据，外部不同内容明确rollback incomplete。移除只删除受管junction入口，不删除目标目录内容。安全拒绝不转换fallback；原生OSError仍允许原已有包装器fallback，其完整写事务另待验收。

相关77 passed /4.53秒，cache/windows-junction-driver-final-20261013.log，含9项当前Windows原生检查：Unicode/空格/单引号/&路径创建切换移除、普通目录/外部junction拒绝、失败保持旧路由、原生更新后注入异常恢复旧目标、失败创建无临时残留，以及三个真实驱动入口禁止subprocess.run仍可创建/恢复。实际注册表/profile更新在驱动入口测试中替换为空操作，仅junction及目标文件位于临时home。未执行含注入路径的cmd或访问用户真实安装目录。静态107存量/新增0/差异0，diff通过。

本批不证明外部非平台程序在最终检查后换路径、磁盘/共享盘同步I/O总期限、完整shim/fallback/快照补偿或断电持久恢复。历史HKLM迁移、多账户及最新发布验收继续开放；前完整2135/wheel不含此批源码。

## Windows账户锁独立于home提示（2026-10-13）

Windows版本软件/用户环境锁不再从Path.home派生，改由windows_account.local_app_data通过明确当前进程账户token调用SHGetKnownFolderPath，位于该账户LocalAppData/OpsKit/account-locks。HOME、USERPROFILE、LOCALAPPDATA、OPSKIT_DATA_DIR不能选择另一把账户锁；目录解析失败明确拒绝，不回退home。沿用ResourceLock的路径链接/普通单链接文件检查、私有ACL及Windows文件锁，退出由OS释放。POSIX原home+root系统锁保持原策略。

新原生验证涵盖同一进程及全新子进程携带上述环境覆盖仍解析相同账户目录，比较目录摘要不输出账户路径；不同home子进程在隔离的OS账户目录provider上竞争同一真实Windows文件锁，被拒绝并在退出后可嵌套重入。首次仅用token=NULL的KnownFolder解析在新进程环境覆盖下失败；改为OpenProcessToken显式账户后两项原生验证通过，缓冲区CoTaskMemFree和token CloseHandle均释放。当前相关74 passed /26.92秒，cache/windows-account-lock-token-final-20261013.log；静态107存量/新增0/差异0，diff通过。

本验收只证明当前账户及隔离锁，不证明不同SID的实际账户部署、所有权检查目录竞态、HKLM全机资源锁或进程终止后的事务恢复。此前旧版本仍只持home锁，不能与新版本安全混跑；历史HKLM Node/Go候选迁移、junction、完整安装与发布验收继续开放。当前完整2135/wheel不含本批。

## Windows默认用户环境边界（2026-10-13）

Python/Node/Go/Java均安装到当前用户可写目录，旧驱动却自动将该目录加入HKLM全机PATH，形成其他账户执行用户可写程序的风险。已移除四驱动默认HKLM注入/移除及相应吞异常、重复通知；默认仅当前用户registry/profile和当前进程路由。系统级安装须采用独立管理员所有权目录及显式策略，不能把用户目录作为全机命令权威；本批没有提供新的机器安装功能。

相关124 passed /26.75秒，cache/windows-user-scope-20261013.log；新增四驱动×注入/移除8场景使用没有HKEY_LOCAL_MACHINE属性的假registry，要求仅访问用户hive；原profile失败恢复检查同时确认机器值保持原样。最后仅改测试进程PATH恢复fixture，避免测试环境污染，不改生产行为。静态107存量/新增0/差异0，diff通过。

历史已写入HKLM的目录不能因新版本停止写入就认为消失。当前本机额外只读检查机器PATH，对当前账户Python shim及Node/Go/Java shim、active、共享fallback bin精确规范化匹配，报告cache/windows-machine-path-audit-20261013.json：Python0、Node1、Go1、Java0匹配，确有Node/Go相关历史候选残留，尚未删除；不输出完整机器PATH、不写注册表。该范围不包含其他账户、不同历史目录或外部环境，不作为历史迁移全部完成。后续历史清理需明确目录所有权及管理员能力，避免自动删除管理员自定义路径。Windows SID锁、junction、完整安装状态和断电验收仍开放，旧完整回归/wheel不含此批。

## 用户PATH与PowerShell联合补偿（2026-10-13）

windows_profile提取注入/删除计划及profile_transaction，保持文件事务直到调用方确认。change_user_path作为四驱动统一用户环境入口，外层profile事务覆盖内层registry发布/最终复核错误；在registry作用域内验证profile输出，profile校验失败时registry恢复，registry校验失败时profile恢复。恢复前按本次输出复核，外部不同内容保留并明确rollback incomplete；其他仍受管文件继续逐项恢复。单独inject_profile/remove_profile也复用此计划及事务，避免两套行为。

新增两类联合故障：最终registry查询被外部替换时三个profile恢复原字节且外部registry保留；registry写入阶段外部更换第一个profile时registry恢复旧值、其余两个profile恢复，外部profile保留。相关116 passed /24.88秒，cache/windows-joint-path-final-20261013.log，包含原生PowerShell及真实临时注册表键；首次8失败为旧故障注入函数未接受remove关键字参数，调整测试适配后通过。静态107存量/新增0/差异0，diff通过。

联合补偿仅覆盖当前进程内用户注册表/profile。四驱动旧HKLM路径仍在前面，未纳入补偿且可能吞异常；AutoRun迁移、shim/junction/快照等完整操作仍非同一持久事务。home锁不是Windows SID/机器全局锁，未协调外部程序最后校验后的修改，断电及系统级验收仍开放。此前完整2135/wheel不含本批源码，不能复用为最新发布证明。

## Windows用户注册表PATH共享事务（2026-10-13）

四驱动HKCU PATH已接入windows_registry_path.path_transaction，将PowerShell修改置于上下文内，替代四份吞异常/未关闭句柄的用户注册表逻辑。保留原REG_SZ/REG_EXPAND_SZ和无关空项，Windows大小写/分隔符匹配移除重复受管项；拒绝控制符、分隔符、引号及会被REG_EXPAND_SZ展开的百分号目录。有界PATH长度32767，发布前及回调后复核值/类型；写入失败、部分写入或取消补偿旧值，原值缺失则恢复缺失。外部修改保留并报告rollback incomplete，finally关闭句柄。默认用户hive，机器hive须由调用方显式指定；本primitive不证明机器锁。

相关114 passed /17.02秒，cache/windows-registry-path-commit-final2-20261013.log。tests/test_windows_registry_path.py含20项：原类型/空项、缺失值、取消/权限/部分写入、外部修改及提交复核、大小写移除、四驱动×注入/移除profile失败恢复用户值，真实Windows临时HKCU Software/随机OpsKitPathTest键两种类型读写及故障恢复。随机测试键finally删除，不读写真实Environment PATH。旧AutoRun假registry未存储PATH导致提交复核4失败，修正假registry按HKCU/HKLM分离存储后通过，未移除生产复核。静态107存量/新增0/差异0，diff通过。

重要未完成：四驱动原HKLM写入仍在用户事务之前，可能失败被吞/用户失败后机器残留；最终注册表复核失败时已提交profile的反向补偿未涵盖，因此不能宣称跨资源完整事务。账户锁仍按home，没有Windows SID/机器全局权威；junction、完整安装/卸载、断电与发布产物仍开放。前完整2135和wheel不含本批源码。

## Windows PowerShell profile共享事务（2026-10-13）

四驱动注入/移除PowerShell profile已迁移到windows_profile，共用现有有界普通文件/链接拒绝、标记块歧义拒绝及原子发布/补偿。完整规划三文件后才写入，权限失败或取消向上报告；首次创建文件失败补偿后移除本次创建的空目录，外部新增内容保留。单引号按PowerShell规则双写，PATH分隔符/控制符/双引号路径拒绝；非ASCII路径使用ASCII UTF-8字节解码表达式，避免Windows PowerShell5读取无BOM脚本时乱码。已有UTF-8 BOM在块查找前分离、发布/移除时保留，CRLF及无关内容不丢失。

当前相关104 passed /11.32秒，cache/windows-profile-final4-20261013.log，含profile10项、AutoRun24项及原官方软件/静态驱动/profile边界。Windows PowerShell5（SystemRoot固定路径）及PowerShell7（已观察到的pwsh）对ASCII/中文、单引号、&及命令替换路径原生4项全通过，无SENTINEL创建。测试用自身生成的临时脚本，不加载用户profile；记录启动时实际PATH再验证新增单一路径，输出显式UTF-8。之前测试默认GBK解码/假定pwsh不改启动PATH造成3失败，修正观测边界；BOM测试默认read_text编码造成1失败，改用文件声明UTF-8，不改生产期限。静态107存量/新增0/差异0，diff通过。

此批尚不包含HKLM/HKCU注册表写入、profile与注册表跨资源补偿、Windows账户/机器锁、junction或断电恢复；既有注册表副作用仍可能在profile失败之前发生，整套安装事务继续开放。前2135完整回归及wheel不含AutoRun/profile新源码，最终稳定批次仍需重新发布验收。

## Windows AutoRun 官方入口迁移（2026-10-13）

四驱动已接入software/_shared/windows_autorun.py，移除各自新建bat/覆盖AutoRun及卸载无条件DeleteValue逻辑。新安装依靠既有HKCU PATH/PowerShell/当前进程路由，不再创建或争抢单一cmd AutoRun。共享迁移在账户home锁内，只有注册表REG_SZ值精确绑定本驱动固定.opskit脚本位置且内容精确匹配已知shim/active/fallback路径的旧模板才删除；接受历史Windows换行翻译产生的双CR。外部值不更改，未知文件拒绝清理；删除文件失败/取消时复核外部修改，恢复旧文件和绑定，恢复不完整明确报告。迁移在PATH函数其他副作用之前执行。

tests/test_windows_autorun.py当前24项覆盖6种迁移失败边界、四驱动安装/卸载8场景保留管理员值、10种已知旧路由。相关100 passed /9.57秒，cache/windows-autorun-migration-final2-20261013.log。之前扩展测试8失败源于测试将整个ctypes.windll替换而破坏真实临时锁ACL所需kernel32；修正为只替换user32通知，未绕过生产锁。静态107存量/新增0/差异0，diff通过。

未访问真实注册表、cmd AutoRun或用户profile；只使用假winreg和临时home。此次改变生产源码，先前2135全量和系统事务wheel不包含该迁移。HKLM/HKCU PATH异常吞噬、PowerShell模板/补偿、Windows账户/机器权限与锁、junction shell及完整安装/卸载事务仍开放，不能将AutoRun修复视为全部Windows安全通过。

## 系统事务批完整回归终态及AutoRun草稿（2026-10-13）

cache/full-system-transactions-20261013.log终态exit0：2135 passed /18 skipped /700.33秒，包含当前shell profile、POSIX完整操作系统锁、链接安装/卸载事务。相同生产源码wheel及103 CLI检查证据见下节。未增加pip测试20秒期限、未重试失败测试；本次离线wheel安装通过不证明此前超时根因，也不关闭Windows AutoRun/junction等独立发现的缺口。18跳过项不得作为验收成功。

cache/windows_autorun_draft.py仅为隔离迁移草稿，尚未进入生产源码或wheel。cache/test_windows_autorun_draft.py六场景6 passed /11.75秒：外部AutoRun保留、确切绑定旧脚本退役、内容不同拒绝、删除权限失败/取消补偿、外部注册表修改保留并报告回滚不完整。使用假winreg和临时home；本轮忘记设置任务TEMP导致pytest尝试清理旧系统TEMP测试目录并报告WinError145，未手动删除这些旧目录，后续恢复任务专用TEMP。草稿仍须覆盖旧shim/active/fallback路径在正常升级中的迁移，不能只让六个简化场景通过就接入；目前严格匹配当前shims_path可能误拒绝旧active脚本，属于接入前待完善项。四驱动仍保留旧实现，Windows安全缺口尚未修复。

## Windows junction shell 输入风险（2026-10-13，待修复）

Node/Go/Java WindowsDriver._set_junction均先cmd /c rmdir再cmd /c mklink /J，没有总期限/受监督进程树，异常转换为False；包装器及恢复多处继续吞异常。cache/probe_windows_junction_shell.py替换subprocess.run并将Path.home隔离到cache临时目录，不执行native命令，用含&的假target_dir捕获三个mklink参数及subprocess.list2cmdline序列化结果：3/3路径末尾元字符未被引号保护，exit0（cache/windows-junction-shell-probe-20261013.json）。该结果证明传给cmd的输入边界存在风险，不是已执行的攻击或真实junction验收。

下一批优先移除junction对cmd shell的依赖，采用受约束Win32 reparse-point适配；必须核对原junction所属根/类型，先建替代项再切换，拒绝普通目录及外部reparse目标。不得仅扩大timeout或继续将失败当fallback成功；包装器/原环境/快照需要一致失败传播和补偿。正式测试使用独立临时目录与已知程序，不执行含注入载荷的cmd。原完整系统事务回归仍保持同一次进程，未改生产/测试源码；其通过也不能关闭此次新发现的Windows缺口。

## Windows PATH/AutoRun 独立失败复现（2026-10-13，待修复）

完整回归仍运行期间保持生产及测试源码不变，只在cache运行隔离探针。Python/Node/Go/Java的WindowsDriver._inject_path/_remove_path复制四层环境写入：HKLM/HKCU PATH、PowerShell profile及单一HKCU Command Processor AutoRun。各自安装直接SetValueEx AutoRun，卸载直接DeleteValue，没有已有值归属检查；注册表等多处Exception被吞掉。

cache/probe_windows_path_boundaries.py替换sys.modules winreg、ctypes通知、临时Path.home及进程PATH，不访问真实注册表或用户profile。四驱动×3场景共12项全部复现：安装覆盖administrator-init.cmd、卸载删除管理员AutoRun、SetValueEx明确PermissionError仍成功返回。结果cache/windows-path-boundary-probe-20261013.json；探针首次脚本导入路径缺software，修正cache脚本sys.path后exit0，不修改平台。此证据反驳Windows写入已安全的判断，但不是新修复验收。

下一批将四驱动环境管理收敛为共享Windows适配层，采用明确的用户/机器写入策略和相应账户/机器互斥，保留原注册表类型并使用上下文关闭句柄，写入失败向上报告。PowerShell路径单引号转义及标记块边界、链接拒绝/原子发布/补偿必须验证；cmd AutoRun全用户只有一个入口，不允许四驱动互相覆盖或删除管理员值。须决定以用户PATH为主要持久路由、淘汰自动覆盖AutoRun，旧平台值仅在确切身份/内容证明下迁移清理；不能按文件名删除。真实注册表安全验收只在独立测试键/可销毁环境，不直接安装到用户系统。当前完整回归/新wheel验证不关闭该P0缺口。

## 系统事务批发布包与提交候选验收（2026-10-13）

当前生产源码wheel构建终态exit0（cache/wheel-system-transactions-20261013.log），包含shell profile、完整入口系统锁和安装/卸载链接补偿。产物cache/wheel-archive-reader-tkj4wy_l，234运行文件/239成员，SHA256 a0e780cd0876f28cac2ab319dc4b59be7d0bb3895cb3a227e5ca49a930d52107；源码库存/RECORD一致、独立安装pip check及94帮助+9实际检查共103通过。此产物证明包与源码一致及CLI入口，不代替原生服务或多租户安全验收。

424提交候选秘密扫描exit0/0匹配，cache/secrets-system-transactions-20261013.log；此清单快照早于本段台账文字更新，未包含运行cache、克隆插件等忽略路径。当前完整回归cache/full-system-transactions-20261013.log已启动，进程句柄69200仍运行，终态前不登记完整通过。保持生产/测试源码稳定。此前完整2100通过/13跳过/1失败仍保留：离线pip有效wheel测试20秒超时，隔离通过不证明根因，未修改生产期限或测试超时来消除失败。

## 系统链接卸载事务及目标读取（2026-10-13）

安装与卸载共用_apply_command_links：先规划全组并核对原始readlink，发布/删除失败或取消时逆序恢复；外部文件或不同链接不被补偿覆盖，明确报告rollback incomplete。卸载无效后续名称在删除前拒绝；归属检查采用本次捕获的链接目标，不再重新跟随目标后另读旧值。新增8项包含正常/失效链接×权限失败/取消、后续无效名称、外部新增文件保护，以及安装/卸载读到归属后链接被换成外部目标时拒绝。

捕获目标调整首轮Windows暴露扩展盘符前缀比较回归（12失败/93通过），已修复_command_target仅规范化比较路径，原始readlink仍用于恢复。当前Windows相关105 passed /10.24秒，cache/system-link-uninstall-final2-20261013.log；当前Linux相关54 passed /11.18秒，cache/system-link-uninstall-linux-final2-20261013.log。静态107存量/新增0/差异0，diff通过。全部操作在临时目录；本批不证明跨资源的整个安装/卸载补偿、外部程序在最后检查后抢写、目录竞态或断电恢复完成，当前全量与新发布包仍待收口。

## 九种版本软件完整入口系统锁（2026-10-13）

versioned_mutation现将system_mutation作为最外层：POSIX有效root按系统锁→home锁固定顺序进入，覆盖Python/Node/Go/Java/MySQL/MongoDB/PostgreSQL/Redis/ClickHouse的install/upgrade/switch/uninstall共36入口。系统锁竞争在账户状态读取、目录下载/发布和快照更改之前拒绝；嵌套升级/安装及共享shell入口使用可重入同一系统锁。普通用户保持home锁，Windows仍不以此证明机器级注册表互斥。

新增36入口拒绝边界检查，Windows相关97 passed /9.84秒，cache/versioned-system-lock-20261013.log。Linux首次扩展脚本因隔离环境缺pyyaml退出1；补齐任务专用环境测试依赖后重跑当前源码，36生命周期及4共享入口在真实flock竞争时全部拒绝，正常/强制退出后释放及嵌套重入通过，exit0（cache/verify_versioned_system_lock.py）。Linux相关pytest 46 passed /10.34秒，cache/versioned-system-lock-linux-20261013.log，包含系统锁、链接补偿、profile、shell字面量和原home锁；测试均隔离锁及系统文件路径。未改项目依赖清单或系统Python。静态107存量/新增0/差异0，diff通过。系统锁只协调平台调用，不是目录竞态或断电持久回滚证明。

## POSIX 系统共享写入口锁（2026-10-13）

software/_shared/mutation 新增系统锁，与既有home锁分开：默认/var/lib/opskit/system-locks/versioned-software.lock，不从home或OPSKIT_DATA_DIR派生；POSIX有效root才使用，普通用户不取得系统锁。生产路径已有节点拒绝链接、非root所有权及组/其他用户可写权限，再交由ResourceLock进行普通单链接文件校验和flock。inject_rc_path/remove_rc_path/link_into_system_bin/unlink_system_bin四个入口共用该锁，覆盖整次规划、发布及补偿；锁永久保留，支持同线程重入，不因退出删除锁文件。

测试总fixture将系统锁路径也隔离到测试临时目录。新增独立子进程持锁时四入口拒绝，以及普通用户不申请系统锁，共5项；相关96 passed /17.81秒，cache/system-software-lock-20261013.log。Linux当前源码真实flock在自身临时目录验证四入口拒绝、正常退出/强制终止自动释放及嵌套重入，exit0（cache/verify_system_software_lock.py）。静态107存量/新增0/差异0，diff通过。

此锁只覆盖共享shell/命令链接入口，不能当作整个安装/服务操作的跨账户事务锁。Windows机器级注册表、其他服务/驱动直接写入、目录替换竞态、非平台程序并发修改和断电恢复仍需处理；此次没有访问真实/etc/profile.d或/usr/local/bin，也未用本地临时目录证明真实多账户安装验收。

## 系统命令链接组失败补偿（2026-10-13）

link_into_system_bin 记录每个旧链接的原始readlink值，在发布前重新检查；各次发布继续同目录临时链接原子替换，不先删除活动命令。异常或KeyboardInterrupt时逆序补偿：已有链接恢复旧目标，新建链接移除；当前内容不属于本次输出则保留外部内容并报告command link operation failed; rollback incomplete。抽取单一_command_link/_publish_command_link实现，避免正常发布与补偿复制临时文件生命周期。

新增5项回归覆盖已有/不存在旧链接×权限失败/取消，以及失败期间管理员将已发布链接替换成普通文件。相关91 passed /13.59秒，cache/system-link-rollback-20261013.log；Linux使用当前源码在临时目录对第二项原子替换注入PermissionError，实测两个旧链接恢复、无临时链接残留，exit0（cache/verify_system_link_rollback.py）。静态107存量/新增0/差异0，diff通过。补偿为进程内异常恢复，不是断电持久事务；跨进程系统资源锁、目录竞争及检查到替换间窗口继续开放，不能以此关闭全部官方写操作验收。

## 官方系统命令链接所有权（2026-10-13）

shared shell_path.link_into_system_bin 现在要求调用方显式提供 managed_root。Node/Go/Java/MySQL/MongoDB/ClickHouse 六个驱动已接入各自版本目录；待发布目录及每个实际源文件的解析路径须在此根内，已有目标符号链接也须指向此根。普通文件、其他工具链接及失效的其他工具链接拒绝覆盖。整组预检查后才开始发布，避免第二个命令冲突时第一个已被替换。

新增4项行为检查：正常/失效外部链接冲突时整组不写、同根版本切换、源链接逃逸拒绝。首轮84通过/2失败是Windows readlink返回扩展盘符路径导致字面比较失配；改为比较操作前后的原始readlink目标以验证没有修改。最终官方软件/profile/激活/锁相关86 passed /8.74秒，cache/system-link-authority-final-20261013.log。Linux当前源码在自身临时目录实测外部失效链接拒绝、整组预检查、正常发布及卸载，exit0；脚本cache/verify_system_link_authority.py不进入发布包。静态107存量/新增0/差异0，diff通过。尚未解决系统跨进程锁、预检查到替换的竞态及多链接发布中途失败的完整补偿，因此官方安装事务仍开放。

## PATH 单目录字面量边界（2026-10-13）

共享 shell_path 拒绝 shim 路径中的 PATH 冒号分隔符：shell 引用只能防止展开，不能阻止冒号分割成多个搜索目录。Windows 本地模拟保留盘符识别，只检查 splitdrive 后的路径部分。新增 tests/test_shell_path_literals.py 验证拒绝发生在 profile 修改之前，原文件 CRLF 字节不变，并在 POSIX shell 中验证空格、单引号、命令替换、反引号、环境变量字符均保持单一字面量目录，不执行 touch。

Windows 相关回归 34 passed /5 skipped /4.98秒，日志 cache/shell-path-literals-windows-20261013.log；Linux 专用环境没有 pytest，未安装新依赖，改用同一源码调用 /bin/sh 对上述5种路径执行原生断言，exit0，5 native shell literal cases passed。静态107存量/新增0/差异0，diff通过。此项不关闭跨用户锁、目录竞态、完整安装事务或发布包验收；最新完整回归仍存在离线 pip 测试超时，尚无包含本批的发布包。

## 官方shell profile边界接入（2026-10-13）

最新相关cache/shell-profile-authority-related2-20261013.log终态exit0：93 passed /6.40秒，包含10项profile边界、链接/版本激活/资源竞争/官方软件及驱动静态回归。静态107存量/新增0/差异0、diff通过。尚无包含本批profile生产源码的新wheel或完整回归，不把上述局部结果登记为整体完成。

上一完整cache/full-top-selection-20261013.log终态exit1：2100 passed /13 skipped /1 failed /777.05秒，test_plugin_lock有效离线wheel pip安装超过测试20秒上限。原测试隔离1 passed /2.76秒（cache/plugin-pip-lock-isolated-20261013.log），根因尚不确定，不改写全量失败或提高生产期限。新profile测试在该次收集之后创建，不在上述结果中。

shared shell_path现规划全部RC/profile变更后再发布：1MiB+1有界读取、普通单链接文件/路径拒绝链接、打开身份复核、UTF-8原始换行保留；路径使用shlex引用，标记歧义拒绝；系统profile只有root分支可写/删，既有文件须为平台规范标记块。注入允许精确匹配当前受管目录的旧单行export迁移，包含shell扩展字符的旧声明不迁移；未绑定旧文件删除拒绝，不靠文件名判定所有权。新内容同目录临时文件、fsync及原权限在原子替换前建立；多文件失败补偿只恢复仍匹配本次输出的内容，出现外部变更或恢复失败明确rollback incomplete。

Python Linux install_shim/remove_shim已复用共享profile入口，移除重复RC/profile逻辑，保留自身shim模板/目录和原进程PATH行为。首轮6项缺口及链接/版本/锁相关29 passed /3.89秒（cache/shell-profile-authority-related-20261013.log）；随后补有界读取及CRLF/权限回滚、幂等/移除、硬链接与歧义标记检查，最新相关结果见cache/shell-profile-authority-related2-20261013.log。系统跨用户锁、真正服务/系统恢复、目录竞争/权限、旧profile卸载迁移及原生shell仍未完全验收，不能据此关闭官方安装事务。

## 官方shell profile写事务缺口（2026-10-13，待修复）

进一步发现software/recipes/python/linux.py的install_shim/remove_shim复制了独立profile逻辑，未使用Node/Go/Java共享shell_path。扩展临时目录测试以共享失败边界注入验证两入口，cache/shell-profile-authority-expanded-before-20261013.log终态6 failed：原4项缺口及Python安装/移除绕过共享边界。只修共享工具不能关闭Python路径，下一批同时迁移此重复逻辑，保留Python独立shim内容与实际版本行为；尚未修改生产代码。

复核software/_shared/shell_path.py：inject_rc_path的root profile写入异常被吞掉且直接覆盖既有文件；remove_rc_path吞掉RC读取/写入与profile删除异常，profile分支没有root检查。新增tests/test_shell_profile_authority.py只访问自身临时home/profile.d，cache/shell-profile-authority-before-20261013.log终态4 failed /0.93秒，分别证明权限发布失败未抛出、管理员同名文件被覆盖、nonroot分支删除传入profile、RC读取失败仍返回成功。

当前全量cache/full-top-selection-20261013.log在新增文件之前收集，不包含这4项，不得因当前全量通过而关闭官方写事务。生产源码仍保持稳定。下一批需要失败传播、文件内容/归属保护、root-only系统写入及原子发布/回滚验证；home级版本锁不能证明系统资源跨用户互斥。根目录、symlink/hardlink、输入shell引用及真正系统锁另须补齐，不能只让四项单测通过就称整个安装事务完成。

## Top-N当前源码原生与提交候选检查（2026-10-13）

当前新wheel已终态exit0（cache/wheel-top-selection-20261013.log）：cache/wheel-archive-reader-k2u1ot7v，234运行文件/239成员，SHA256 d40d2fc62d9c6017611d0e466ff6ea36c057c69eccac2576fffc025d16a879b7，源码/RECORD一致、独立安装pip check与94帮助+9实际检查共103通过。完整源码回归cache/full-top-selection-20261013.log已启动，终态前不登记完整通过。全量运行期间保持生产源码稳定，不用前两次全量失败或相关通过代替此次终态。

scripts/verify_process_native.py在当前源码Windows3.12.10及Linux3.11.2/psutil7.2.2两环境均exit0。Windows自身0.25秒有界CPU循环测量52.1%、独立对象参考64.6%、窗口约0.390秒；Linux98.6%/98.6%、窗口约0.254秒。参考观察窗口不同，不要求精确相等。两环境首样本initial/null、新会话基线隔离、CPU/内存Top5数量及排序通过。证据cache/process-native-top-selection-20261013.json与process-native-top-selection-linux-20261013.json含相同源码摘要：commands df1cc7f04ef7b55f9b454cda5418d737fa56e8152bbd477e8bf1450c25c9e1af，process_sampling 14cdeff5671c56526d3bc9ef8e349b7da7795dc0bc94f81eccfc43876e673536。

本验证不保存进程名/列表，不是正式p50/p95/RSS、实际PID复用/权限、其他平台或同步属性监督证明。当前421提交候选秘密扫描exit0/0匹配（cache/secrets-top-selection-20261013.log）。新wheel安装后103 CLI检查仍运行，尚无包/新全量终态，不能把前述原生检查代替整个平台验收。

## Top-N选择接入及监督最终复验（2026-10-13，运行中）

最新相关集合cache/ready-top-selection-final-20261013.log已终态exit0：101 passed /51.55秒，包含最终具名排序键、真实请求后取消/期限、受控后代进程树及阻塞写回收、Top-N16项策略顺序、进程采样与静态门槛。包含新算法的wheel构建cache/wheel-top-selection-20261013.log已启动，尚无包/全量终态。

monitor/process_sampling.top采用N*8<行数才用heapq.nlargest，否则保留原排序；相同nullable排序键，并列保持库存顺序，不修改采样/身份/窗口逻辑。首轮包含16项策略边界的监控相关cache/monitor-top-selection-related-20261013.log终态102 passed /19.50秒；随后将局部key lambda改为具名函数，最终监督/Top-N/静态集合cache/ready-top-selection-final-20261013.log复验运行中。优化依据32组合成选择阶段探索，不代表整体监控耗时或真实跨平台性能。

监督related2终态58 passed /1 failed /56.13秒，新增失败为真实下载窗口没有数据：冷启动预算5秒内不能保证收到数据。下载真实I/O测试复用就绪进程/pipe门槛、原5秒窗口/7秒含回收上限、真实时钟及进度断言；冷启动耗尽预算与无数据失败另有独立测试。无生产网络/沙箱期限修改、无失败重试。清理断言保持在测试兜底清理之前。静态现107存量/新增0/差异0；减少1项是tests/test_plugin_control.py的subprocess导入现在实际使用，按证据移除旧F401基线，不是新增删除代码的数量。

Top-N生产代码变化后，当前退役wheel不能作为包含新算法的包证据；仍需新包与最终全量。此前完整2083通过/2失败证据保留。整体验收、真实平台/服务/不可信插件及其他八类计划未完成。

## 退役批全量终态与监督测试就绪边界（2026-10-13）

cache/full-terminal-retirement-20261013.log终态exit1：2083 passed /13 skipped /2 failed /830.64秒。HTTP取消测试started未置位，却在started.wait(5)返回False后仍置取消事件；插件2秒期限测试的child.json不存在，未证明真实后代已建立。不能由这些断言判断具体系统调度根因，也不能登记完整成功。Top-N新增16项文件在本次收集后创建，不包含于该全量。

HTTP真实I/O预备进程/pipe逻辑复用为测试context，取消只在实际started置位后触发，保留8秒调用预算及真实时钟。插件真实树/阻塞写测试先用受控process.start创建已知本地客户端和后代，在30秒fixture准备上限内等待完整身份记录，再以stdin实际请求解除门槛交给原sandbox监督，调用仍2秒；冷启动预算由独立execution_budget及HTTP冷启动测试保留。状态、管道、实际PID/创建时间及active_children断言都放在fixture兜底清理之前，不以测试清理冒充生产回收成功。不执行第三方插件、不扩大生产期限。

首轮cache/ready-supervision-related-20261013.log终态59 passed /51.60秒；最终断言位置修正后cache/ready-supervision-related2-20261013.log复验运行中，终态前不登记最终通过。该批只改测试，当前退役wheel生产源码保持有效，但当前完整验收仍需新全量。生产Top-N仍全排序，其他八类门槛继续开放。

## Top-N优化前的行为基线（2026-10-13）

新增tests/test_monitor_top_selection.py，显式按8/3/0/None分组构造策略期望，不调用生产排序或堆作为断言权威；验证CPU/内存字段、N=1/20/32/33/257/10000、257行并列截断顺序，以及空库存/全部denied的无等待行为。cache/monitor-top-selection-before-20261013.log终态16 passed /0.73秒，当前仍是原全排序，结果用于优化前行为基线，不是已实施算法优化的证据。当前完整回归在新增文件之前收集，因此不包含该16项。

当前cache/full-terminal-retirement-20261013.log仍运行，test_plugin_control中已出现一项失败标记，尚无终态/完整失败原因。不得预先认定是偶发或修改期限；生产源码继续保持稳定，待失败详情后处理。

## 退役与监控清理当前产物（2026-10-13，全量运行中）

cache/wheel-terminal-retirement-20261013.log终态exit0：cache/wheel-archive-reader-sbfu0c5z，234运行文件/239成员，SHA256 d8147ef0ebfe76cdef3b200873c40f0a285e4109ae74653633a135bc08392b9c；当前源码/RECORD一致、独立安装pip check及94帮助+9实际检查共103项通过。该产物包含终态退役和四项监控导入清理，未包含尚未实施的Top-N算法变化。

当前完整源码回归cache/full-terminal-retirement-20261013.log已启动，终态前不记通过；此前完整回归失败证据保留。全量运行期间生产源码保持不变。Top-N选择阶段32组有序结果等价及合成性能探索已完成，局部选择可能改善小N，但大N可能更慢；详见performance-baselines.md，尚未修改生产排序，不宣称整体监控提速。

## 终态退役相关验收与监控导入清理（2026-10-13）

最终相关集合cache/update-terminal-retirement-related2-20261013.log终态exit0：193 passed /204.95秒，包含16项退役/保护/恢复/清理/历史检查、原生Windows助手有索引/无索引退出证据后的健康确认退役，以及HTTP冷启动和真实I/O期限。原生健康确认仍是fixture输入，未执行真正的新冻结程序，不能关闭运行程序替换/断电/跨账户门槛。

按docs/code-health-review.md预先核对证据删除monitor/commands.py的Any及monitor/menu.py的Columns、Layout、_read_key四项冗余导入；官方监控功能未删除，文件不属于固定guest库存。同步仅移除对应四项静态基线，现108存量/0新增/0差异。清理后相关菜单/采样/CLI/静态门槛cache/monitor-import-cleanup-20261013.log终态86 passed /17.33秒，diff检查通过。没有代表性性能测量，不宣称导入清理显著提速。

包含退役实现和监控清理的新wheel构建cache/wheel-terminal-retirement-20261013.log已启动。此前marker wheel、完整2067通过/1失败结果不替代当前源码完整验收；新完整回归尚未启动。旧任务迁移、原生marker助手重试、真实运行冻结程序、系统写事务、跨平台和其他计划项继续开放。

## 成功及未提交任务退役接入（2026-10-13，最终相关复验中）

三个退役缺口已接入update_retirement，首轮cache/update-terminal-retirement-related-20261013.log终态170 passed /174.49秒，覆盖原7项缺口/保护检查、恢复、回滚退役、未索引回滚、CLI和配额。新增schema2退役凭据支持applied/failed，保留既有schema1回滚凭据。持久job.json发布在索引转历史之前；成功需绑定job/build/backup的confirmed健康、闭合事务和实际新摘要，失败需aborted及旧摘要。退出证据再次核对助手身份与ACK；缺ACK保留恢复和活动索引。preparing且无ACK/result记录not_launched，不伪造created_ticks。历史status区分applied/failed/rolled_back，持久健康成功可报告confirmed，继续只读。

随后补充所有权/索引发布中断恢复、五类凭据篡改拒绝、无引用退役任务回收及只读失败历史，共16项terminal检查；原生Windows真实exited ACK两场景延伸健康确认后的退役/历史验证。健康确认仍是fixture输入，不代表真实新程序已运行成功。最新源码及新增测试的相关集合cache/update-terminal-retirement-related2-20261013.log已启动；首轮170不包含上述新增检查，也早于最终status标识和类型校验修正，不能代替最终源码全部通过。静态112/新增0/差异0，diff检查通过。

最新集合已执行16项terminal检查全部通过，193项整组尚无终态。420提交候选秘密扫描exit0/0匹配（cache/secrets-terminal-retirement-20261013.log）；固定工具、规则及完全脱敏保持。生产实现变化后安装包尚未重建，当前marker wheel不能作为新退役实现的包验收证据。

## 更新标记批完整回归及HTTP冷启动测试拆分（2026-10-13）

cache/full-update-marker-final-20261013.log终态exit1：2067 passed /13 skipped /1 failed /849.69秒。失败是test_http_deadline的started事件未置位，表示请求未到服务端，不能解释为服务端线程退出失败。原测试5秒预算包含Windows spawn/httpx导入，却要求请求一定开始；源码监督器本就必须在准备阶段耗尽预算时终止。原测试隔离1 passed /5.33秒（cache/http-total-deadline-isolated-20261013.log），不据此改写完整失败或认定具体系统根因。

测试拆为真实冷启动总预算和真实持续HTTP I/O两场景。冷启动仍5秒、7秒含回收上限，不要求在预算内一定连接；I/O场景预先spawn并完成httpx导入，通过事件释放真正请求，将实际进程/pipe交给未修改的network.worker.run，使用真实时钟、同样5秒及7秒回收上限，仍要求started、TimeoutError及无新增子进程。准备等待仅用于测试fixture，不改生产预算、不重试失败。相关HTTP/网络期限回归cache/http-total-deadline-separated-20261013.log已启动，终态前不记通过。

相关HTTP/网络期限回归现已终态exit0：14 passed /22.86秒（cache/http-total-deadline-separated-20261013.log），覆盖真实冷启动、持续HTTP I/O、取消回收、流式额度及网络deadline。diff检查通过。尚无包含拆分测试与待实现退役功能的新全量通过结果。

当前完整回归收集在新终态退役文件创建前，不含该文件已证明的3项缺口。marker当前wheel及103 CLI检查证据保留；本批仅测试变化，生产代码尚未改变，后续仍需退役实现、相关回归及新的全量验收。

## 本地可达Git历史秘密检查（2026-10-13）

cache/secrets-history-release-20261013.log终态exit0/0规则匹配。使用已核对发布ZIP摘要及ZIP内可执行文件摘要的Gitleaks8.30.1、固定默认规则、空ignore文件、ignore-gitleaks-allow及redact100；git --log-opts=--all扫描本地可达引用，没有fetch。仓库非shallow；rev-list清单323个提交，清单SHA256 b8def97a7bd318e90cf6d9cd3d98f56d882ce264c78d8768ae885d5c634389ab，工具日志报告280 commits scanned、约2.93MB差异/50.4秒。不能把可达清单数量当实际差异扫描计数，也不能证明远端独有/不可达/已删除历史或未知秘密安全。扫描与脱敏报告保存在cache/secrets-history-kmf3j8xk，不提交运行报告。git ls-files -ci --exclude-standard当前无输出，当前不存在被忽略规则命中但仍跟踪的文件。

## 成功及未提交任务退役缺口（2026-10-13，待实现）

tests/test_update_terminal_retirement.py新增7项检查；cache/update-terminal-retirement-before-20261013.log终态3 failed /4 passed /8.97秒。确认健康且助手已退出的成功任务、目标仍旧摘要且助手已退出的失败任务，以及始终preparing且无ACK/result的未启动任务，reconcile后仍保持scheduled/preparing和活动索引，缺少持久退役。未确认健康及alive/unknown/identity_mismatch保护检查通过。新文件在当前完整回归已收集之后创建，因此当前全量不包含该7项；不把这三个缺口登记为完成。

下一步复用update_retirement的原子job.json凭据与clear_index顺序，增加applied/failed终态。成功须完成事务、目标新摘要、备份验证、绑定job/build/backup的confirmed健康及精确助手退出证明；失败须事务关闭且目标旧摘要，助手退出证明或持久preparing且从未发布launch intent/ACK/result。结果文件或PID本身不证明终态，未知助手不退役。凭据必须区分exited和not_launched，不能伪造created_ticks。状态历史同步支持终态；配额清理继续保护active/last/health/marker引用，并验证发布中断、PID复用、部分清理与篡改。当前全量运行期间不修改生产实现。

## 更新兜底标记权威与重启证据保护（2026-10-13，相关复验中）

修正后的相关回归cache/update-marker-related4-20261013.log终态exit0：105 passed /1 skipped /88.21秒，包含16项标记权威/重启证据测试、更新应用及自动更新验收和Git clone诊断回归。跳过的是要求显式启用的真实网络更新验收，不计完成。当前419候选秘密扫描exit0/0匹配（cache/secrets-update-marker-final-20261013.log）。包含本次修复的新wheel正在构建，完整回归及真实冻结程序验收仍未关闭。

新wheel现已终态exit0（cache/wheel-update-marker-final-20261013.log）：cache/wheel-archive-reader-ze0w5aoa，234运行文件/239成员，SHA256 defdd1e191aa01295991f41201e00467222919708f55550df5f59afdde3d9762，源码/RECORD一致、pip check及94帮助+9实际检查共103通过。完整源码回归cache/full-update-marker-final-20261013.log已启动，终态前不登记完整通过；此期间保持生产源码稳定。

新增标记权威测试先复现4项失败（cache/update-marker-before-20261013.log）：旧实现在验证归属前探测缓存声明路径，缺少备份/事务绑定仍可替换，且候选丢失或健康发布失败可能丢弃恢复证据。现仅接受schema2，声明必须词法匹配平台自身pending/target；文件探测始终使用平台路径。事务ID、版本、新旧摘要、备份侧车及实际备份字节必须一致；已有其他更新健康记录拒绝。复用当前事务，不创建第二事务；实际目标已是新摘要时可修复完成/健康发布，未提交时仍要求签名、版本下限和原子复制摘要检查。

旧格式或不明确标记保留并阻止另一次自动应用。健康发布失败时已提交目标要求旧代码重启，但保留pending/marker/backup；新代码带--post-update仍先处理标记，不能先删除pending。新增回归注入健康写入失败，验证新运行版本不循环重启、恢复证据完整，解除故障后健康归属正确并清理候选及标记。此为临时文件场景，不替代正在运行的Windows冻结程序、延迟助手重试、断电或跨账户验收。

cache/update-marker-related2-20261013.log为103 passed /1 skipped /1 failed，唯一失败是旧测试要求删除缺失候选的未绑定标记；已改为验证原字节保留且不初始化目标。related3为104 passed /1 skipped /1 failed，新测试严格字典断言遗漏合法time字段，实际四项健康权威字段一致；修正后related4复验已启动，终态前不记全部通过。静态112存量/0新增/0差异、diff检查通过。

最近完整回归cache/full-unindexed-rollback-20261013.log实际终态为2051 passed /13 skipped /1 failed /599.25秒，本地生成仓库Git clone返回1；孤立原测试1 passed，根因尚未确定。已增加stderr诊断而不增加重试，related3中的该测试通过，但不替代新的完整回归。之前完整2040 passed /13 skipped仅对应此前源码。

回滚发现批wheel终态exit0（cache/wheel-unindexed-rollback-20261013.log）：cache/wheel-archive-reader-b77p3kd6，234运行文件/239成员，SHA256 b9c98f1f347264b13636587d9551ca3a3ee059bad05f92b9cad1b1cdb7145fe0，pip check及103 CLI检查通过；该产物不包含当前marker安全修复，仍需重建。

## 无索引回滚发现接入（2026-10-13，相关回归运行中）

相关集合100 passed /83.20秒（cache/unindexed-rollback-related-20261013.log）。目标单次哈希修正及新增五种拒绝输入后，最终独立12 passed /13.38秒（cache/unindexed-rollback-final-20261013.log），包含正常退役、启动继续prepared回滚及9种权威错误。全套cache/full-unindexed-rollback-20261013.log、新wheel已启动，终态前不登记完整通过。当前417候选秘密扫描exit0/0规则匹配，静态112/新增0/差异0。临时文件/fixture验证不替代实际正在运行的冻结程序恢复或断电验收。

上一批完整源码终态exit0：2040 passed /13 skipped /592.47秒（cache/full-unindexed-update-20261013.log），包含现代upgrade索引丢失发现与原生Windows两种ACK场景；新增回滚测试文件是在该次收集之后创建，不算入此结果。

随后 `_discover_index`支持rollback：健康job_id选择原任务、backup_id/build绑定，原任务transaction ID必须合法且不同于当前rollback ID，反向版本/摘要经备份侧车和实际备份摘要复核。原任务/当前事务关闭或实际目标字节与事务phase矛盾时拒绝索引发布。唯一证据建立后仍复用既有 `_reconcile_locked`、rollback及retirement顺序，不执行artifact、不杀缓存PID、不按mtime挑最新任务。prepared回滚启动可继续原事务并返回restart_required；已恢复字节的任务持久退役后移索引到历史。

7项缺口测试初步全部通过，100项集合cache/unindexed-rollback-related-20261013.log仍运行。发现阶段目标哈希只读取一次，避免同阶段对大文件重复I/O；既有恢复阶段仍重新核对实际字节，不以缓存省略安全检查。最终修正单次哈希后的源码还需相关复验，旧任务迁移、marker、真实运行冻结程序与跨账户仍开放。静态与diff检查通过。

## 无索引回滚缺口证据（2026-10-13，生产实现待接入）

新增tests/test_update_unindexed_rollback.py，在既有临时目标/备份/任务fixture中删除或移除apply_job索引。cache/unindexed-rollback-before-20261013.log终态7失败，正常完成回滚不能退役、prepared回滚启动不能继续；健康任务/备份/目标变更场景当前也只按升级发现返回deferred而未进入回滚权威校验。新测试仍为未通过项，不加入当前完整回归的通过数；当前完整回归在新增文件之前收集，生产源码保持稳定，待终态后接入。

回滚事务ID不同于原升级事务，候选应由健康job_id与backup_id、当前rollback的反向版本/摘要及原任务身份联合绑定；按现有rollback/retirement服务恢复，不新建第二套替换逻辑。需拒绝无健康/身份不符、篡改备份/目标、关闭事务及模糊候选，不按最新文件名或PID猜测。

此前现代未索引升级新wheel验收exit0（cache/wheel-unindexed-update-20261013.log），cache/wheel-archive-reader-ok2u6oo5，234运行文件/239成员，SHA256 5b3a99bb5e19fd3cb98a070747b7fb008970837cfc6e1c4bb25524efc50be50a，源码/RECORD、pip check和103 CLI检查通过。本产物尚无新回滚发现实现。

## 未索引现代更新任务发现（2026-10-13，相关回归运行中）

当前416提交候选秘密扫描exit0/0规则匹配（cache/secrets-unindexed-update-20261013.log）；固定工具摘要与可执行文件核对、全量脱敏与默认规则保持。当前源码静态112存量/0新增、diff检查通过。

实际Windows助手有索引/删除索引两场景2 passed /7.10秒（cache/unindexed-update-native-20261013.log）。使用真实进程executable、FILETIME创建身份和已知临时目标/备份字节，删除缓存后索引与applied恢复证据一致，readonly status仍识别exited；不执行下载产物。当前完整回归cache/full-unindexed-update-20261013.log已启动，秘密候选扫描与新wheel尚未终结，未登记完整通过。

第二轮相关集合终态exit0：214 passed /117.45秒，cache/unindexed-update-related2-20261013.log，覆盖发现/恢复/CLI/任务额度/退役及自动更新。静态112/新增0/差异0。随后将原生exited ACK验收参数化为有索引/删除索引两场景，实际Windows助手身份及临时目标字节恢复单独验收已启动；新wheel仍待终态。不将单个现代upgrade发现代替旧任务/rollback/marker完整门槛。

首轮148项终态147通过/1失败（cache/unindexed-update-related-20261013.log），退役测试现在因启动发现新增路径进入真实worker，但source fixture的mock frozen/target身份不跨spawn，故返回deferred。测试改为在相同临时目标中调用真正boot discovery，保留所有退役/健康/摘要断言；不放宽生产身份校验。补充prepared目标存在而整个任务目录/任务证据缺失的拒绝：入口仍进入受监督核对，返回deferred/missing_job_evidence，保留事务与备份，不冒充no_job。214项相关集合及新wheel已启动，终态前不登记通过；无索引rollback和旧任务迁移仍未实现。

修复显式reconcile及启动检查在apply_job缺失时直接no_job的入口遗漏。已有update-jobs目录时，在缓存根锁→当前可执行目标锁内有界扫描64个任务/每任务16个成员，仅使用当前程序目标的事务权威；不解析/打开metadata.target声明。唯一schema2任务必须绑定当前upgrade transaction ID、版本与新旧摘要，目标实际摘要属于事务两端且备份/恢复receipt验证通过后，才修复缓存apply_job（保留其他字段）。原有助手精确身份和目标字节恢复随后执行；活动/未知助手可以恢复索引但不得提交事务结果。无任务/无运行目录时仍不初始化目录。

重复候选、旧格式、未知成员及无法绑定的本目标任务均保留并返回deferred；不杀PID、不执行产物、不删除旧任务、不猜测事务结局。旧任务迁移、无索引rollback、缺失整个任务目录但目标仍prepared、marker及其他终态退役仍未关闭，不能把本批唯一现代upgrade发现称为全部恢复完成。相关148项集合cache/unindexed-update-related-20261013.log已启动，未取得终态前不记整组通过；静态112/新增0/差异0，diff通过。

此前仅宿主导入清理新wheel已exit0（cache/wheel-host-import-cleanup-20261013.log），cache/wheel-archive-reader-kpd95prt，234运行文件/239成员，SHA256 99480a8639f6fb0787d396fb880f4d6bf9b585e56c1d792e477bbd7b821ea346，源码/RECORD、pip check及103 CLI检查通过。该产物早于本次更新发现源码，不作本次发布证据。

## 完整复验与宿主导入清理（2026-10-13）

清理后相关回归终态exit0：78 passed /50.06秒（cache/host-import-cleanup-related-20261013.log），覆盖平台、镜像切换/缓存、更新下载、进程运行及静态基线门槛。Ruff当前112/新增0/基线差异0。安装包完整CLI检查仍在运行，尚未记通过。

cache/full-image-inspect-final-20261013.log终态exit0：2022 passed /13 skipped /517.29秒，无并行构建/原生测试。该结果对应镜像查询合并、资源清理后提交、RPC授权等此前生产源码，Windows助手读取就绪等待修正已包含；此前整套失败仍保留，不以孤立通过改写历史。

终态后按docs/code-health-review.md证据删除11项宿主未使用导入（mirror/platform/privilege/progress/telemetry provider/update_download），同步移除对应静态基线。没有删除官方功能或常量定义，guest21库存未变，摘要e206ac9a0961d3e34567d2dc4c39439b3c478b3ed264cf29329f5b8c04d3de6d保持不变。进度/提权/遥测模块导入烟测、静态与diff检查通过；相关回归cache/host-import-cleanup-related-20261013.log及wheel重建cache/wheel-host-import-cleanup-20261013.log已启动，未取得终态前不将清理前全套通过当清理后全部行为证明。保留112候选继续逐项复核，不宣称导入清理显著提速。

## 合并镜像查询当前产物与真实 RPC 验收（2026-10-13）

Windows助手整文件最终15 passed /20.25秒（cache/update-helper-ack-final-20261013.log）。随后启动无并行构建/原生测试的完整复验cache/full-image-inspect-final-20261013.log，未取得终态前仍保留此前整套失败。生产源码与已验收wheel不因测试就绪等待修改而改变，未重建相同wheel。

完整回归终态exit1：2021 passed /13 skipped /1 failed /823.63秒（cache/full-image-inspect-20261013.log），Windows助手测试在文件可见后直接读取running.json遇到PermissionError。文件已经原子临时写入/Flush/关闭再MoveFileEx发布，不能认定写入逻辑非原子或特定系统服务导致拒绝。孤立原测试通过（cache/update-helper-ack-isolated-20261013.log）；测试就绪等待改为在原8秒上限内等待成功读取完整ack，仅FileNotFound与native winerror5/32允许轮询，永久错误/非预期错误仍失败，保留PID/ticks/alive/exited/目标不变断言。生产读取/权限策略不变，整文件回归已启动；未获得新完整回归前不将此批记成功。

九种真实生命周期复验cache/plugin-native-resource-lifecycle-20261013.log已exit0、对应verification.json通过：源码2次和新wheel1次各21基础检查，以及期限、撤权、注入取消、状态/ASCII/Unicode输出额度、OOM、PID与tmpfs限制。额度被捕获的正常结果允许提交，异常/撤权/取消保留状态；具名worker均确认不存在。真实OS信号和完整获批出口仍未完成。当前415提交候选秘密扫描exit0、0规则匹配（cache/secrets-image-inspect-20261013.log），这是当前候选扫描，不替代全部历史/未知秘密证明。

新wheel已exit0（cache/wheel-image-inspect-20261013.log）：cache/wheel-archive-reader-7zguyzo1，234运行文件/239成员，SHA256 62990e83a3a89991724271e6bdfb5155431306e6d676c1d5b9511a251b831a88；源码/RECORD、pip check和94帮助+9实际检查共103通过。源码监督器2次和新安装CLI1次各21项真实基础隔离/参数/状态检查通过。

真实RPC工具cache/plugin-native-image-inspect-20261013.log exit0、对应verification/rpc-verification.json：30秒策略未扩大；批准前提供方不启动；业务7/SDK2、A1/B1/A2状态分区；身份撤权及边撤权捕获失败后仍禁止状态发布；真实存在但未声明的delete方法拒绝；实际业务8/清单7拒绝且观察到实际8。20个具名RPC worker确认不存在。本批不重复九生命周期，修改后的资源scope仍须复验实际取消/配额故障；真实OS取消、出口/SSRF、并发多用户和生产隔离仍开放。

追加真实Docker资源工具cache/plugin-resource-native-ownership-final-20261013.log exit0、verification.json共7项，通过活动租约保护、容器/网络清理失败保留及恢复，以及同名无label网络不得删除。测试最终仅按自身创建返回的完整ID清理该网络。此前失败保留；WSL true探针exit0，Docker10秒查询再次超时，现有30秒控制查询随后返回20.10.24+dfsg1，未重启服务、不对根因作无证据断言。完整回归与当前候选秘密扫描尚未取得终态。

## 镜像身份查询合并及 RPC 期限定位（2026-10-13）

追加真实无label同名网络保护场景工具已编写，但cache/plugin-resource-native-ownership-20261013.log exit1于最初镜像查询，未创建测试资源或进入新场景，不登记7项通过。只读Docker Server.Version就绪探针10秒超时（cache/docker-readiness-image-inspect-20261013.log）；查询超时不证明daemon自身故障，未重启WSL/Docker或提高期限。此前6项真实恢复证据保留，新场景待就绪后在新隔离目录验收。

使用上一批已知合成插件、同一30秒策略定位RPC控制耗时，cache/rpc-cleanup-timing-20261013.log正常返回0/23.438秒，首次镜像查询11.109秒；普通调用为外层caller、provider describe、provider call三个worker，每worker原5次镜像查询，总15次。失败与冷启动/系统开销有关的解释尚不能仅凭该结果定性，原失败保留。

`_image`将五次查询合为一个有界JSON数组，只选择ID与四个固定供应链标签，不读取全部镜像环境/元数据，不缓存授权或身份、不跳过任何校验。各字段在同一次daemon查询返回，错误ID、策略、lock/base/code标签、缺失标签、错误类型/长度及非JSON均拒绝。相关58 passed /1 skipped（cache/image-inspect-related-20261013.log）。当前源代码真实RPC仍使用30秒，cache/rpc-cleanup-timing-after-20261013.log正常返回0/23.656秒，镜像查询3次；首次查询14.578秒。两次冷启动耗时不同且不是代表性性能基准，只证明查询/控制进程启动次数15→3，不宣称总体耗时显著下降。

新完整回归cache/full-image-inspect-20261013.log、wheel重建cache/wheel-image-inspect-20261013.log已启动，未取得终态前不记通过。本批整体源码/wheel RPC负面矩阵仍需重验；单次正常RPC成功不关闭先前完整工具失败。静态检查与diff检查通过。

## 清理后状态提交与真实 Docker 恢复（2026-10-13）

完整回归终态exit0：2014 passed /13 skipped /681.51秒，cache/full-resource-scope-20261013.log；另新增cleanup提交3项单独通过，不合并当同一次收集。新wheel终态exit0，cache/wheel-archive-reader-ges7uhgp，234运行文件/239成员，SHA256 905ad7b7bfb032ff0bf87a931132dbfecb531ddf2035ecb55828c6f78ad310bc；源码/RECORD、pip check及94帮助+9实际检查共103通过（cache/wheel-resource-scope-20261013.log）。这些通过不关闭本批真实RPC超时、13跳过和八类完整生产门槛。

追加源码/wheel真实RPC尝试cache/plugin-native-cleanup-commit-20261013.log已exit1：三次基础隔离检查完成后，RPC正常计数在清理后的 `_remaining()` 拒绝，未通过本批RPC验收。工具仍使用30秒共享期限；新增账本/清理控制操作与嵌套执行开销须进一步测量，不提高生产期限绕过失败。状态提交后移保证失败不发布状态，但不能把独立清理预算理解为暂停业务monotonic时钟；必要清理继续执行，清理结束时业务期限已经过期则拒绝发布。新wheel完整CLI验收与完整回归尚在运行。

将 `_save_state` 移至worker、代理、网络scope全部正常退出之后，提交前重新检查共享期限、完整授权链及插件内容指纹。避免清理失败时命令失败但状态已提前发布；清理期间撤权也拒绝提交。新增真实本地客户端+真实账本+受控daemon的3项测试全部通过（cache/resource-cleanup-commit-20261013.log），核对正常cleanup→commit顺序、清理失败无commit并保留记录、清理期间撤权无commit。

scripts/verify_plugin_resource_recovery.py使用已知不可变本地镜像和独立数据目录，仅创建自己的具名label容器/网络。真实Docker6检查通过（cache/plugin-resource-native-20261013/verification.json及同名log），分别验证活动租约跳过且资源存在，注入仅针对已知资源删除请求失败后记录/资源保留，释放租约后reconcile核对完整ID并删除、记录退役。未停止共享daemon、未执行未知clone、未按prefix删除资源。证明删除请求失败后的持久恢复，不证明断电/整机重启、跨用户权限或daemon永久不可用恢复。

完整回归cache/full-resource-scope-20261013.log及重建安装包cache/wheel-resource-scope-20261013.log正在运行，取得终态前不记通过。完整回归收集后新增独立cleanup提交测试，因此最终结果不合并冒充同一次运行。静态检查123存量/0新增/0解决，diff检查通过。

## 沙箱资源账本生命周期接入（2026-10-13，真实 Docker 恢复待验收）

worker、网络及出口代理现在通过 `_resource_scope` 创建前记录意图并持有租约，Docker 创建携带独立所有权 label；清理按 daemon/backend、label、精确名称核对完整 ID 后删除。网络采用逆序退出，先代理后网络；移除旧按名称删除并吞异常路径。清理的查询/删除/确认共享每资源30秒总预算，原执行异常保留并输出固定恢复诊断，原执行成功但清理失败则返回失败且保留账本。官方 `plugin reconcile` 在整体执行预算内恢复非活动记录，pending/invalid 的 JSON ok=false、退出1；active正常跳过。尚不能据此宣称真实daemon故障恢复已完成，状态提交发生在资源scope退出之前这一行为也需继续核对事务语义。

相关最终74 passed（cache/resource-scope-related3-20261013.log），新增CLI3 passed（cache/resource-reconcile-cli-20261013.log）。账本单独17通过包含真实资源scope+受控daemon网络/代理创建、启动中途失败、逆序清理、保留记录后恢复；不冒充真实Docker。旧本地Python client协议/超时测试使用限定fixture模拟资源scope，账本与生命周期新测试不禁用生产校验。首次21失败为旧模拟未提供账本协议；第二次71通过/1失败暴露Windows kill请求与正常退出通知竞态，测试改为等待已知子进程终止后才检查恢复，保留失败日志。静态检查与diff检查通过；完整回归、重建wheel及真实Docker标签/孤立资源恢复仍待本批验收。

## Docker 资源持久账本基础（2026-10-13，沙箱生命周期待接入）

新增host-only core/plugin_resources.py：创建资源前持久保存schema1/token/kind/精确name/固定backend/daemon ID，UUID所有权label与独立OS租约；运行期间租约持有，恢复扫描不使用PID猜测活动状态。写入失败拒绝创建意图，容量256记录/每文件4KiB，索引锁序列化容量检查与落盘。恢复只使用操作方当前提供的Docker命令，不执行账本中的命令；要求backend与daemon ID相同，按label+name查询唯一完整ID，删除ID后确认不存在再退役记录。无label的同名资源不删除；未知类型、非普通文件/硬链接/链接、超额与错误schema拒绝。正常退役/未落盘失败清理空租约文件，观察者仍持有时可保留空锁，不能以其存在认定资源活动。

最终12 passed（cache/resource-journal-final3-related-20261013.log），覆盖container/network清理失败保留意图与恢复、无资源不删除、daemon改变保留、恶意stored backend不执行、活动租约、容量与模糊列表拒绝。真实子进程持租约时扫描active且不查询daemon；强制结束该已知测试进程后OS锁释放，再用模拟daemon恢复对应完整ID。真实进程验证的是租约机制，Docker查询/删除仍为受控模拟，不冒充真实daemon故障恢复。根/plugin-resources/已忽略，不屏蔽源码/正常依赖锁。

本模块尚未接入sandbox worker/proxy/network创建与finally，现有沙箱仍有吞清理异常分支，因此恢复门槛未完成；下一批接入标签和预创建意图、生命周期清理结果及官方reconcile命令，再验证真实具名孤立容器和网络。账本不在guest SDK库存，不扩大不可信插件可见的宿主模块。

## 追加真实 RPC 方法/版本拒绝验收完成（2026-10-13）

cache/plugin-native-service-negative-final-20261013.log工具exit0，当前源码监督器2次和wheel CLI1次各21项基础检查，未重复九生命周期（该批只--rpc）。rpc-verification.json确认未批准边拒绝且提供方不启动；批准后计数1/1/2、真实业务版本7查询、caller身份/边撤销与状态依赖保护继续通过。提供方代码确实有delete但契约不声明，调用拒绝、仅caller及describe两个worker启动而无call worker，持久状态摘要不变。

随后已知provider代码改实际业务版本8、清单仍7，更新信任/运行时及双方批准后调用被拒绝，工具从provide_service阶段观察到actual_business_version_observed=8，排除仅准备未启动或其他早期错误的假通过，状态摘要不变。20个具名RPC工作容器逐一确认不存在。SDK源码与当前wheel仍对应先前e206ac...摘要和32bea6...wheel；改变的是已知测试插件内容/其独立catalog绑定，不将其视为平台代码修改。先前准备超时及WSL/Docker就绪记录保留，没有重启服务或提高生产期限。

完整本地1994通过仍不覆盖全部并发多用户、恶意协议输入、实际OS取消、获批出口和daemon故障恢复。下一P0核对cleanup_control失败后的持久资源恢复；当前finally仍有吞SandboxError/TimeoutExpired分支，没有可靠资源恢复账本，不能因本轮正常daemon回收成功将该门槛关闭。

Debian true探针最终exit0，随后Docker Server.Version探针也exit0（cache/docker-readiness-service-negative-final-20261013.log），启动链路/daemon查询恢复，不把之前超时定性为daemon自身故障或宣称某个启动根因。新输出目录cache/plugin-native-service-negative-final-20261013使用最终带版本ready标记的工具执行--rpc，日志同名；前次准备失败保留，当前未取得终态不登记新增拒绝场景通过。

当前RPC路由/worker/审批/状态依赖批完整回归最终exit0：1994 passed /13 skipped /558.09秒，cache/full-service-enforcement-20261013.log，session8519。生产源码和已收集测试未变，后续仅扩原生方法/版本拒绝工具及.gitignore/文档；WSL准备/就绪探针并行，本轮耗时不作性能比较。13跳过及八类生产/跨平台/故障恢复门槛继续开放，不把完整本地通过当平台最终达标。

WSL列表有界只读查询exit0、当时没有running发行版（cache/wsl-readiness-service-negative-20261013.log），Docker镜像及Server.Version查询分别30/10秒超时。仅凭Docker超时不能断言daemon自身故障；追加Debian true的30秒启动/执行探针，cache/wsl-execution-readiness-service-negative-20261013.log，以区分启动链路。没有重启服务、删除镜像或扩大业务期限，新增真实容器拒绝场景继续未验收。

## 追加真实 RPC 方法/版本拒绝场景（2026-10-13，准备阶段失败）

scripts/verify_plugin_native.py新增提供方存在但未声明的delete方法、caller请求该方法不得启动call worker或改变状态；另在已知提供方代码改为实际业务版本8/清单7并重新信任/准备/批准后，验证worker拒绝且状态不变。为避免仅因其他错误失败而误判版本校验，提供方构造打印已知版本标记，工具必须观察到实际8。当前新尝试cache/plugin-native-service-negative-20261013.log、session28129已exit1，失败在准备阶段WSL Docker image inspect基础镜像ID达到30秒期限，尚未进入这些业务场景，不能登记通过或当代码回归已证实。首次运行早于新增版本ready标记，保留记录；下一次必须用新输出目录和最终工具。

独立Docker server版本探针使用10秒/4KiB/进程树监督，cache/docker-readiness-service-negative-20261013.log；完整回归session8519仍运行，生产源码/已收集测试未改，新增工具与少量系统探针并行，本轮耗时不作为性能比较。此前真实RPC批准/边撤销成功证据仍只覆盖原有场景，不替代本次新增方法/版本拒绝证明。

## 新 SDK 真实 RPC 授权与依赖撤销验收（2026-10-13）

cache/plugin-native-service-enforcement-20261013.log工具exit0，verification.json/rpc-verification.json，当前21文件SDK摘要e206ac9a0961d3e34567d2dc4c39439b3c478b3ed264cf29329f5b8c04d3de6d，新镜像sha256:0ff2bb3f1ceb81d1fb4b06d45fb540b6e27199bf26d3ec1e72b73ea333c05e7a。源码监督器2次及当前wheel安装CLI1次，各21项基础隔离/参数/状态检查，九种生命周期场景继续通过，取消仍注入而非真实OS信号。

迁移RPC使用业务版本7/SDK2，独立信任但未批准调用边时caller执行失败，提供方未启动且持久状态不变。官方批准路径建立两条精确count/wait边后，真实broker describe返回7，两个caller分别计数1/1/2并核对平台身份分区。外层caller身份撤销中断调用并保留状态；重新信任/重新批准后，仅撤销服务边，caller捕获PermissionError并试图正常返回仍被宿主拒绝，错误service authorization revoked during execution，所有持久状态摘要不变。16个具名RPC工作容器逐一确认不存在。该证据不覆盖所有恶意方法/版本输入、提供方替换、并发多用户、实际OS取消或daemon故障恢复，完整门槛仍开放。

补齐根目录/.config.lock及/.plugin-trust.lock忽略规则，git check-ignore确认两者忽略而requirements.lock不忽略；移除空Windsurf标题，不使用全局*.lock规则。当前完整回归cache/full-service-enforcement-20261013.log、session8519已启动，生产源码保持稳定，未取得终态不登记通过。

## RPC 外层状态依赖与原生验收迁移（2026-10-13）

当前wheel验收exit0：cache/wheel-archive-reader-uqu9eg1i，233运行文件/238成员，SHA256 32bea6c5263512666be7f514d264930612220de92c13f8d688d3981cf76c10bf；源码/RECORD、pip check、93 help+9实际查询/拒绝共102通过（cache/wheel-service-enforcement-20261013.log）。复用隔离依赖环境替换平台wheel，不声称全新依赖解析或实际RPC恶意场景已在wheel通过。工具终态后启动新SDK合成插件九生命周期及迁移RPC场景，cache/plugin-native-service-enforcement-20261013.log，session97341，尚未终结，不登记通过。

嵌套invoke返回/失败后，已使用service_edges合并保留在父调用，root结束才恢复前上下文；live检查也验证边两端enabled，防止父调用捕获broker拒绝后提交依赖已撤销服务的状态。相关集合129 passed /33.01秒（cache/service-dependency-related-20261013.log）；随后新增正常返回/捕获PermissionError两项，服务执行单文件最终7 passed /4.35秒（cache/service-dependency-final-20261013.log），不累加当完整结果。

scripts/verify_plugin_native.py的合成服务改业务版本7/SDK2，提供方声明count/wait，调用方绑定native_rpc_provider并声明相同版本/方法；源码通过官方authorize_service路径建立批准。工具新增未批准RPC必须在提供方启动前拒绝且保留状态，以及只撤销服务边、caller捕获PermissionError仍不得发布状态的真实场景。它们尚未取得新运行终态，不能将工具代码当通过。当前21文件SDK摘要e206ac9a0961d3e34567d2dc4c39439b3c478b3ed264cf29329f5b8c04d3de6d，旧镜像不可代替。当前wheel-service-enforcement验收仍运行，生产源码保持稳定；新完整回归/恶意并发/协议版本及daemon恢复继续开放。

## RPC 路由与 worker 契约校验（2026-10-13，原生与发布待验收）

新增宿主resolve_service_access：所有服务执行需提供方明确契约，第三方caller必须唯一、启用/受信任，其uses绑定提供方/版本/方法且有当前双方指纹对应的持久批准。call_service在进入invoke前检查，invoke重新加载清单后再次检查并登记service_edges；运行/UI/状态发布检查同时核对完整边链，revoke_service也会中断依赖该边的调用，嵌套finally恢复父边链。platform直接操作也受提供方方法契约限制，不继承第三方身份。SDK/宿主代理的service_api_version改为沙箱describe实际业务版本，不固定返回2。

worker以平台只读request中的契约检查实际service_api_version严格整数且一致、方法在声明中且可调用；describe返回实际版本与方法集合，宿主拒绝与绑定清单不符的描述结果。SDK业务版本不等于平台/SDK版本。旧RPC清单缺契约或完整uses/批准时明确拒绝并提示迁移，独立入口清单兼容保留。

首轮路由回归4 failed /1 passed准确确认未批准请求仍进入invoke、其他公开方法可调用、代理固定2及边撤销检查缺失（cache/service-enforcement-before-20261013.log）；使用spy证明宿主路由，不当实际容器恶意执行证明。接入初轮97 passed /2 failed，旧服务夹具缺契约且断言固定2，改为明确业务1/open_menu清单并mock描述，保留真实API1业务兼容意图。最终129 passed /31.84秒（cache/service-enforcement-final-related-20261013.log），含guest版本不匹配/非callable/方法拒绝、代理describe、live边撤销、审批/存储/解析/预算与独立SDK导入。尚未迁移原生合成RPC清单/批准和新镜像/wheel/完整回归，worker改变SDK摘要；完整RPC消息版本、并发恶意样例及daemon恢复等剩余门槛继续开放，不凭这些局部检查关闭整体目标。

## 官方 RPC 批准与撤销 CLI（2026-10-13，路由待接入）

新增plugin authorize-service CALLER SERVICE，必填双方当前指纹；在配置→信任事务锁内解析当前唯一提供方、调用方完整要求、业务版本/方法子集、双方启用/信任/校验清单，存储后复查内容变更，变更则撤销新边并拒绝。plugin revoke-service只删除调用边；plugin show增加provides/service_contracts及省略source的service_requirements供核对，不加载插件代码。相关测试入口模块故意raise，批准/show依然成功，说明没有在宿主导入该业务代码。

首轮2 failed /1 passed为命令缺失；错误指纹用例exit2在旧代码因命令不存在也通过，不用该历史通过证明当时已有指纹检查。实施后初轮58通过，补契约show、版本/方法/提供方不匹配后最终62 passed /24.14秒（cache/service-approval-final-related-20261013.log）。正确审批与撤销、错误指纹/禁用提供方及不匹配拒绝均核对持久授权边。运行时路由/worker仍未使用批准，不能宣称无授权RPC已拒绝；下一批完成该边界，当前整套及wheel仍需更新。

## RPC 调用边存储（2026-10-13，调用路由仍待接入）

plugin_trust新增grant_service/is_service_authorized/revoke_service，调用边位于调用方当前指纹对应记录中，绑定服务、提供方名称/指纹、业务版本和精确方法；独立信任两插件不构成RPC批准。新增写操作沿用共用信任事务锁，批准要求双方指纹仍匹配现存信任记录，验证契约类型并限制每调用方最多64服务。单独撤销调用边保留独立执行信任；任一身份grant/revoke/invalidate清除相关入边/出边，避免撤权后重新批准相同内容恢复旧服务权限。

首轮10项失败均为尚无存储API（cache/service-grants-before-20261013.log），不是十次实际越权复现。初轮相关82通过，追加提供方撤权/失效/同内容重新批准不恢复旧边三例后，最终85 passed /36.53秒（cache/service-grants-final-related-20261013.log）。当前call_service/broker/worker还未调用该存储查询，CLI审批入口也未接入，所以不能称RPC已经默认拒绝或该授权设计达标。下一批接入官方调用边审批/撤销命令、宿主路由和运行中边撤销检查、worker契约校验及真实恶意RPC验收；在这些完成前不使用解析/存储单测替代完整门槛。

配置批完整回归最终exit0：1942 passed /13 skipped /522.01秒，cache/full-config-authorization-20261013.log，session87596；未包含后段新增的service_contract测试，随后plugin.py修改需新整套与产物证明。本轮生产/已收集测试未改，少量新契约回归并行，耗时不作为性能比较。

## RPC 清单解析实施（2026-10-13，运行时授权仍待接入）

PluginManifest新增service_contracts并扩展uses字段；提供方契约绑定已声明服务，业务版本严格int1..65535，公开ASCII方法1..64且不可私有/重复/通配。uses允许旧service/source兼容，但一旦声明provider/version/methods必须齐全且验证，限制原样保留；未知字段、重复服务、无效类型拒绝。旧清单加载不代表新RPC授权已取得，当前broker/worker仍未依据该契约执行，不能称安全门槛完成。

新契约17项及插件解析/服务/生命周期/UI/预算联合128 passed /48.98秒（cache/service-contract-related-20261013.log），静态123存量/0新增/0解决（cache/static-service-contract-20261013.log）。此前16失败多数为新契约缺失，不称全部恶意RPC复现。下一批实现宿主持久调用边审批、双方指纹绑定、实际业务版本查询和worker执行点的方法/版本核对，再迁移合成样例及源码/wheel/原生镜像验收；本批解析源码不属于guest库存，不扩大SDK。

## RPC 清单契约回归（2026-10-13，待实施）

新增test_plugin_service_contracts.py明确提供方service_contracts及调用方uses的provider/api_version/methods结构，业务版本独立于SDK；限制字段必须保存或拒绝，不能静默删除。旧实现首轮16 failed /1 passed /2.31秒，cache/service-contract-before-20261013.log：多数为尚不存在的契约/校验，部分确认当前uses解析只保留service/source、额外限制无法表达。旧清单独立入口加载通过。不把新契约API缺失的全部失败称为实际越权攻击复现。新增文件未包含当前full-config-authorization集合，生产源码/已收集测试未变；方法执行、运行时版本及用户授权边尚未实现，不能只以未来解析回归通过关闭RPC安全门槛。

信任/状态批完整回归最终exit0：1935 passed /13 skipped /523.15秒，cache/full-trust-state-20261013.log，session86473。新增配置授权测试未纳入该集合，随后配置/SDK修改不能用此结果替代整体检查。

## 配置写入与状态发布协调（2026-10-13）

真实新21文件SDK/合成插件验收exit0，cache/plugin-native-config-authorization-20261013/verification.json、rpc-verification.json及同名log。镜像sha256:d38609d6ff536fee7eb194289744c8244be4ac895f04cb7b90e87be317cd28c1，构建上下文显式包含core/resource_lock.py，当前SDK摘要绑定检查通过。源码监督器2次及当前wheel CLI1次，各21项基础隔离/参数/状态检查。九种生命周期均到guest ready，失败/额度处理与持久状态规则核对，期限30秒含回收实际30.47秒，不声称硬实时；取消仍注入，PID/tmpfs场景成功保存额度拒绝证明。

真实RPC提供方与两调用方分别计数1/1/2，状态按平台身份分区；运行中撤销外层A后完整链返回authorization revoked during execution，双方及其他调用方状态保持，8个具名RPC工作容器确认不存在。此证明新配置依赖与锁在当前源码/安装CLI基本执行可用，不将其扩大为实际多用户、完整方法/API/提供方授权、真实信号或daemon恢复验收。工具终态后启动当前完整回归cache/full-config-authorization-20261013.log，session87596，尚未终结，不标记整套通过。

当前wheel验收exit0：cache/wheel-archive-reader-rh_6kmdz，233运行文件/238成员，SHA256 dc83801c407bf0812cc4797ed27f82dbdcc11a5fc120bbfc02644ed17e9ab94d；源码/RECORD、pip check、91 help+9实际查询/拒绝共100通过（cache/wheel-config-authorization-20261013.log）。复用隔离依赖环境替换平台wheel，不声称全新依赖解析。静态首轮新测试F401一项新增，删除未使用导入后123存量/0新增/0解决（cache/static-config-authorization-final-20261013.log），不是垃圾代码删除证明。真实合成插件的新21文件SDK构建、资源生命周期和RPC验收已启动，cache/plugin-native-config-authorization-20261013.log，未取得终态不标记通过；不执行第三方未知代码。

最终相关98 passed /37.32秒，cache/config-authorization-final-related-20261013.log；其中三个真实双进程save/ensure/set竞争拒绝并在释放后成功重试，前轮95不累加。当前wheel构建验收cache/wheel-config-authorization-20261013.log已启动，未取得终态前不标记通过。

四个旧快照/提交期间禁用回归失败后，config新增同一ResourceLock守护save_config、ensure_config、set_config_value；单键更新在锁内load最新配置，只修改请求字段，保存成功后刷新调用者字典，保留返回接口。save_config明确整份替换语义，不将过期整份替换误称为单键合并。状态发布固定配置锁→信任锁，最终授权重查、切换和补偿期间两锁保持，竞争明确busy而非报告禁用/撤权成功。首轮相关95项通过；新增save/ensure/set真实双进程竞争、释放后重试，最终相关集合日志cache/config-authorization-final-related-20261013.log。

config实际依赖resource_lock.py，因此显式SDK库存20→21，当前代码摘要45a7eee74316289baed5719369eeb0ce7de6634a2869b6346f68fc1835cb4496；独立捕获guest导入及平台私有模块缺失检查通过。旧SDK镜像拒绝摘要不符并需要重建，不使用旧镜像/wheel/冻结程序支持本批验收。同步I/O整体监督、直接外部编辑配置、RPC方法/版本/调用边及daemon恢复仍未完成，当前新wheel/真实容器/整套继续待验收。

## 通用配置旧快照恢复禁用插件（2026-10-13，待修）

追加提交期间禁用竞争：在真实state目录切换中启动另一线程set_enabled(owner,false)，旧代码成功禁用而继续发布新状态；最终四项全部失败，cache/config-authorization-before-final-20261013.log。下一设计采用统一配置事务锁，状态提交固定先配置锁后信任锁；普通单键设置在配置锁内重读最新文件，ensure_config创建/迁移也在同一锁内，整份save_config保留明确替换语义但必须取得配置锁。ResourceLock成为guest config的实际依赖时，必须显式加入固定SDK库存并重建镜像，不能依赖宿主额外文件碰巧存在；这会改变SDK摘要，当前旧镜像不能作为修改后的验收证据。

test_config_authorization_concurrency.py在隔离数据目录先取得旧配置，完成owner禁用后再用旧配置修改language/theme；旧set_config_value保存整份旧快照，owner恢复为默认启用。另一例first禁用后旧快照设置second禁用，first同样恢复。首轮三项全部失败，cache/config-authorization-before-20261013.log。因此只锁plugin.commands.set_enabled不足以保证禁用持续有效；需要通用配置事务锁、单键更新在锁内重新读最新配置，状态发布与配置写入协调同一锁顺序，并复核ensure_config及整份save_config的用途。新增测试未包含正在运行的full-trust-state集合，生产/已收集测试保持不变；此缺陷未修复，不用已有信任撤权事务代替。

## 信任撤销与状态发布序列化（2026-10-13）

当前wheel构建/安装验收exit0，cache/wheel-archive-reader-i410vdf7，233运行文件/238成员，SHA256 102480a080493cb201195836113860e67450d226c3c29e10a1da6a00f09eb8f8；源码/RECORD、pip check、91 help及9实际查询/拒绝共100项通过（cache/wheel-trust-state-20261013.log）。复用既有隔离依赖环境替换平台wheel，不声称全新依赖解析或真实RPC状态竞争已在wheel执行。该wheel包含本批发布重试、信任事务锁与状态序列化源码。构建验证终态后启动当前完整回归cache/full-trust-state-20261013.log，session86473；未取得终态不标记通过。

旧_save_state在解压后不复查授权，解压期间revoke完成仍发布新状态；状态切换期间另一线程撤权也能完成。两个回归首轮均失败，cache/state-authorization-before-20261013.log。现在信任模块提供authorization_transaction，与grant/revoke/invalidate使用同一ResourceLock；状态解压在锁外，取得锁后检查剩余期限及整条调用链授权，在锁内完成目录切换/回滚/权限收尾，竞争转为固定SandboxError且不提交。撤权先完成则拒绝状态；提交先持锁则撤权返回busy，需要重试，不称为成功撤权。

最终相关73 passed /11.58秒，cache/state-authorization-final-related-20261013.log：解压期间撤权保留旧状态，真实另一线程在两次rename期间撤权均busy，真实另一进程持授权事务时提交拒绝、staging清理、释放后重试成功。原有信任三种写操作双进程竞争与归档额度检查保留。前轮72项结果不累加。禁用配置修改不使用信任锁、同步文件I/O不可抢占、RPC调用边未实施，整体不声称完整原子安全达标；发布/权限收尾原有故障恢复范围仍需全面矩阵。宿主监督器变化不扩大guest SDK库存，当前wheel/整套需更新。

完整发布重试回归最终exit0：1929 passed /13 skipped /672.29秒，cache/full-publication-retry-20261013.log，session3739。生产与已收集测试运行期间未改；后段新增的test_plugin_trust_concurrency.py未纳入集合，单独首轮三项失败在下节。本轮并行少量诊断/相关回归，耗时不用于性能比较。该完整结果早于随后信任存储事务锁修改，不代替当前源码最终回归或发布产物。

## 插件信任存储并发撤权复现（2026-10-13，待修）

随后实施共用ResourceLock：grant/revoke/invalidate在读取前取得同一个动态data_dir/.plugin-trust.lock，覆盖整个读改写/atomic_save，竞争抛ResourceBusyError且不执行修改。test_plugin_trust_concurrency.py使用真实子进程在load后暂停，父进程三个修改均须拒绝，子进程退出后重试成功，second授权保留、first撤权/失效不恢复，invalidate保留source/branch；旧实现三项全部失败（cache/trust-concurrency-before-20261013.log），修复后相关集合114项通过（cache/trust-concurrency-related-20261013.log）。宿主信任模块不属于固定guest SDK库存，本次不扩大guest代码。此项修复并发旧记录覆盖，不证明读取到状态提交的原子撤权或RPC调用边已实施；当前整套和wheel还需更新。

首版探针已输出恢复授权的结果，但退出时自身logger仍持有临时日志导致WinError32清理失败，不能用该清理异常作为目标失败证明。探针finally关闭自身logging后重跑，cache/trust-race-before-final-20261013.log仅输出revoked_identity_restored=true、second_granted=true并exit1，临时目录正常退出清理；以该结果作为最终复现证据。

cache/probe_trust_race.py只在新建临时OPSKIT_DATA_DIR内创建两个合成身份，不运行插件或访问真实信任记录。真实子进程grant(second)在_load取得旧记录后等待，父进程revoke(first)完成并确认first已不受信任，之后允许子进程继续_save。cache/trust-race-before-20261013.log返回revoked_identity_restored=true、second_granted=true，exit1，证明无锁的读改写能恢复已撤销授权，而不仅丢失无关配置。下一修复须让grant/revoke/invalidate共用跨进程读改写锁，竞争明确失败，不用仅atomic_write替代事务锁；RPC调用边与状态提交的序列化仍是独立后续要求。当前完整回归仍运行，未在其中修改生产信任代码或已收集测试。

## 宿主UI输入守卫与嵌套调用方撤权（2026-10-13）

本批完整回归最终exit1：1923 passed /13 skipped /1 failed /539.40秒，cache/full-plugin-prompt-20261013.log。失败为test_normal_layout_with_bundled_library[linux-.tar.xz]：正常MySQL解压后的Windows原生非覆盖rename返回WinError5，失败现场目标不存在，暂存目录已由finally清理，归档仍保留。不能将此错误归类为竞争目录、擅自覆盖目标或放宽测试；操作系统拒绝原因尚未确认，因此当前整套未通过。生产源码/已收集测试运行期间未变，短Linux信号验证与后段并行，本轮耗时不用于性能比较。

同一生产源码MySQL/发布隔离回归33 passed /1.35秒（cache/mysql-publication-isolated-20261013.log），不能覆盖上述失败。独立重复gzip/xz解压与非覆盖发布探针在gzip第43次同样返回WinError5（cache/mysql-publication-probe-20261013.log），确认不是仅完整测试集合触发。后续诊断版200次通过、未出现PermissionError、诊断重试分支零次执行（cache/mysql-publication-diagnostic-20261013.log）；不能据此宣称等待后恢复、文件共享冲突或杀毒软件根因。生产不新增重试、不修改覆盖边界，Windows间歇性正常发布失败仍待定位。

进一步诊断2000次中7次WinError5，源目录存在/目标不存在，100毫秒后相同原生非覆盖操作全部成功（cache/mysql-publication-diagnostic-extended-20261013.log），证明短暂拒绝可恢复，但未确定真实持有者。首个目录句柄实验没有拒绝rename，保留cache/publication-native-handle-20261013.log，不能用其支持触发原因；改为目录内普通文件禁止删除共享的Windows原生句柄实验，打开期间拒绝发布、关闭后成功，12项通过（cache/publication-native-file-handle-20261013.log）。该实验只证明一种触发机制，不将其归因为杀毒软件。

据上述证据，共享发布函数仅Windows原生PermissionError且winerror为5/32、失败后目标不存在时，最多5次原生非覆盖rename，逐次等待50/100/150/200毫秒，共最多500毫秒等待；目标存在立即FileExistsError，其他权限错误立即传播，持续拒绝最终传播原异常，取消不吞。每次原生排他语义保留，不使用replace或删除竞争目标。新增恢复/永久拒绝/等待期间竞争/取消回归及原生句柄验证，MySQL与发布联合38 passed /3.54秒（cache/publication-retry-related-20261013.log）。当前生产修改之后的整套、wheel及冻结产物仍需重新验证；先前wheel不能支持该发布逻辑变更。

修复后生产函数独立2000次gzip/xz解压与发布全部通过，cache/mysql-publication-production-20261013.log；探针已禁用诊断替换函数，仅使用当前生产发布逻辑。日志scope沿用旧诊断文本，不据其措辞声称诊断重试参与成功；未检测原生重试次数，不声称2000次均无瞬时拒绝，也不把该探针当完整安装/系统依赖验收。下一整套日志cache/full-publication-retry-20261013.log，仍需取得终态。

后续原生输入验收新增实际POSIX SIGINT：scripts/verify_prompt_native.py由父进程向自己创建且已进入原始终端读取的子进程发送SIGINT，覆盖读键、方向键序列、秘密文本输入。Linux3.12.14受限非root/无网络容器共9项通过，三个信号场景约0.008..0.011秒观察到KeyboardInterrupt，终端模式与输入守卫均恢复；prompt源码摘要仍为299ccfffbfd59e24ad015deb1b37137a60573fd7ef98a93fe025fec19546ba81。证据cache/prompt-native-linux-signal-20261013/proof/verification.json及同名log，具名容器确认删除。此项证明POSIX输入原语响应真实信号，不证明整个插件监督链的真实OS取消、Windows控制事件或daemon故障恢复。该短原生验证与本批完整回归后段并行，因此本批整套耗时不能作为隔离性能比较；未修改生产源码或已收集测试。

上批1897/13完整结果在下节，不包含本批新回归。新增首轮15 failed /1 passed（cache/plugin-prompt-before-20261013.log）：多数为尚不存在的输入/授权守卫契约检查；实际旧分支还确认过期broker会进入UI、嵌套身份上下文未恢复、清单不再声明服务仍查询运行时。未把缺少新API的所有失败称为实际终端阻塞复现。

core.prompt新增作用域guard_input，Windows kbhit/POSIX select等待期间检查共享预算和授权，覆盖按键、方向键后续字符、文本和缓冲清理，不创建读线程；守卫期间拒绝非TTY读取，官方普通重定向输入原语保留。Ctrl-C/Ctrl-D控制字符在守卫内作为KeyboardInterrupt传播，pause不屏蔽/吞取消，ESC仍UserCancel；finally恢复上下文和终端。宿主监督器登记每层经验证的名称/指纹，在UI/运行循环检查整条链，在启动与状态提交前重查；重新加载的服务清单必须仍provides该服务。嵌套返回/失败恢复父身份上下文，调用方不能在payload自报身份。

首轮相关99 passed /1 failed /17.57秒：旧准备期限夹具mock授权却未建立真实信任，被新的活跃授权检查正确拒绝。改为真实grant后相关141通过，再增加准备期间撤权、Unicode/方向键兼容及取消用例，最终152 passed /29.10秒（cache/plugin-prompt-final3-related-20261013.log）；本批27新例，保留前轮141/144结果，不累加。最终还将启动前授权/期限检查移到launch异常转换之外，保留正确拒绝原因。

scripts/verify_prompt_native.py为已知本机输入验证，不执行第三方插件：Windows新建隐藏控制台3项，Linux受限非root/无网络容器PTY6项（含不完整方向键、部分秘密文本、实际Ctrl-C字节），等待期限、上下文/终端恢复通过；Linux过额操作无关，本工具只验输入。Windows3.12.10、Linux3.12.14，prompt源码SHA256 299ccfffbfd59e24ad015deb1b37137a60573fd7ef98a93fe025fec19546ba81；cache/prompt-native-windows-final-20261013.log、cache/prompt-native-linux-final2-20261013/proof/verification.json。Windows等待约0.407..0.453秒，请求0.4秒，不声称硬实时。首个Linux挂载将脚本放根目录导致parents[1]不存在，保留失败，修正挂载目录后通过；第一版原生证据早于取消修改，只作历史。容器具名清理并确认不存在，不接触用户终端或真实秘密。

本批20文件SDK内容摘要变为d06988e5d6eb470a5c80b7c2b0cb85ba8e5d27a31cbcfc208ed27ad10d5e5e55，旧SDK镜像需重建，最终新wheel/原生证据如下。不用上批wheel或冻结产物证明当前源码发布。真实OS信号、完整嵌套RPC/多调用方状态、方法/API/提供方策略、同步输出与文件/配置读取阻塞、daemon恢复仍开放；检查点授权与提交不是原子撤权事务。整体八类目标未完成，未提交。

当前源码新wheel及安装验收exit0：cache/wheel-archive-reader-vnbgbeqi，233运行文件/238成员，SHA256 88b8782dd52caa9fe28c8f9002eacd9fb5e3ae0af30f665944aef54770ac8d34；源码/RECORD、pip check、91 help+9实际查询/拒绝共100项通过，cache/wheel-plugin-prompt-20261013.log。依赖未变，复用隔离依赖环境替换平台wheel，不声称全新依赖解析。运行文件与最后生产修改一致，后续仅扩验证工具/文档。

重建SDK为image sha256:b7b11efbbda79e4f24af1e788ed440bb953a9394aba8327a68d9e79a0364809d，policy4/schema2、当前code摘要/锁/来源digest绑定检查通过。scripts/verify_plugin_native.py新增开发验收--rpc，业务方法只在独立合成插件内，不注册平台参数。cache/plugin-native-prompt-20261013/verification.json及rpc-verification.json、工具exit0：源码监督器2次及当前wheel CLI1次，各21项基础隔离/参数/状态检查，九种生命周期场景通过。30秒期限含回收实测30.407秒，不声称硬实时；取消仍注入，不是实际OS信号。

真实RPC使用一个已知provider和两个已知caller，同一审核SDK镜像但业务代码/目录/状态独立。A计数1、B计数1、A再次2，返回身份与provider持久分区核对一致，provider self状态未创建。在provider到ready、准备写入尚未提交状态后撤销A，整个调用链返回authorization revoked；双方及其他调用方旧状态的文件摘要不变，8个具名工作容器逐一确认不存在。该证明覆盖实际broker/service/worker路由及调用链撤权，不包含恶意方法/API/发布者绑定、并行竞争、多用户权限或daemon恢复。全部验证在新建隔离目录、合成插件内，不执行未知克隆插件。

本段追加前最终源码/测试/验证工具及文档提交候选快照：402文件、gitleaks0发现，cache/secrets-plugin-prompt-20261013.log（cache/secrets-bytequota-u1sbxnd5）；静态123存量、0新增/解决，diff检查通过，历史Git未变，不重新宣称历史扫描。新源码完整回归cache/full-plugin-prompt-20261013.log已启动，不并行构建或原生负载，未取得终态之前不标记通过；上批1897结果不代替本批27新回归及SDK变化的整体检查。

## 插件嵌套执行和准备阶段共享预算（2026-10-13）

三个回归分别确认旧实现无父预算、镜像准备耗尽预算仍启动客户端、Docker控制操作独立使用30秒。首次准备测试使用错误指纹，只证明快照拒绝；更正夹具后cache/plugin-budget-before2-20261013.log三项失败准确复现目标行为，保留首次记录，不用错误夹具支持结论。

invoke现从策略读取前建立monotonic截止时间，嵌套调用继承父截止且可按更短策略缩小，finally恢复父上下文；线程锁等待受剩余期限约束。镜像/网络Docker控制采用剩余预算和CONTROL_TIMEOUT较小值，拒绝晚到成功；快照逐成员检查，准备后及启动前检查，业务读取与回复继续使用同一截止。到期后的具名自有容器和网络清理通过独立控制预算，避免因业务过期跳过必要清理。不把清理耗时包含在业务预算或声称硬实时终止。

初轮63项通过；扩大生产范围152项通过/61.23秒（cache/plugin-budget-broad-20261013.log），随后新增锁等待/配置耗时两例，最终相关77项通过/15.34秒（cache/plugin-budget-final-related-20261013.log）。新增17项覆盖父预算、更短子策略、无效策略、准备过期、Docker剩余预算、晚到成功、清理独立预算、取消后恢复、真实本地静默客户端短期限、真实线程锁等待及配置读取耗时。原有进程树/阻塞回复回收检查保留；时钟和准备过期注入不是实际daemon延迟证明。

同步broker UI、文件操作及授权/指纹扫描仍不可抢占，状态提交只在开始前检查、不强行中断补偿；完整执行监督、调用中调用方撤权、服务API版本、提供方身份授权与真实daemon恢复仍未完成。uses.source只作安装提示的现行为已明确记录，不擅自将其称为来源约束。上一批wheel不能作为本批supervisor变更的发布证明。八类整体目标继续开放，当前未提交。

首轮完整回归1 failed /1892 passed /13 skipped /510.87秒，cache/full-plugin-budget-20261013.log。PostgreSQL竞争目标实际rename返回WinError5而非FileExistsError，未被分类为拒绝覆盖，测试失败。共享发布函数现仅在Windows原生rename已失败且目标lexists时，将PermissionError转换为FileExistsError；不改排他原语、不重试、不删除目标，不存在目标时保留原权限错误。新增存在/不存在两种故障注入，原生竞争与驱动联合136 passed /16.69秒（cache/plugin-budget-publication-final-20261013.log）。不将故障注入称为真实WinError5根因定位。

最终源码完整回归cache/full-plugin-budget-final-20261013.log、新wheel及独立CLI检查cache/wheel-plugin-budget-20261013.log、真实已知合成插件九场景cache/plugin-native-budget-20261013.log已启动；终态取得后补证据，不将活动进程或阶段输出当通过。完整回归与部分构建/原生验证并行，本批耗时不用于性能比较。最终源码及本段之前文档快照秘密扫描400候选、0发现，cache/secrets-plugin-budget-final-20261013.log（快照cache/secrets-bytequota-z77cr3l_）；静态123存量、0新增/解决，Git历史未变，不重新宣称历史扫描。

新wheel最终构建及独立安装exit0：cache/wheel-archive-reader-aorv06ov，233运行文件/238成员，SHA256 3bc62b47a62078fa2ff8dfcb52fa44b56d0f7051aa7dad4f9a7d903370391f16。源码/RECORD、pip check和91 help+9实际查询/拒绝共100项通过，cache/wheel-plugin-budget-20261013.log；复用既有隔离依赖环境替换平台wheel，依赖未变，不声称重新解析安装全部依赖。

首次原生期限夹具仅5秒，实际容器已启动但未到exercise ready便失败（cache/plugin-native-budget-20261013.log及started-deadline.json）；不能据此宣称准备未启动容器，也不能冒充来宾运行中期限验收。工具现所有场景明确配置30秒，生产默认/范围不变，保留原失败产物。最终原生工具exit0，cache/plugin-native-budget-final-20261013/verification.json：源码监督器两次及本批wheel安装CLI一次，各21项基础隔离/参数/状态检查；九种生命周期均到来宾ready，通过超时、撤权、注入取消、OOM、状态、ASCII/Unicode输出、PID和临时盘场景，daemon销毁事件和容器不存在检查保留。期限场景实际总耗时31.719秒，含必要回收而非硬实时30秒；PID/临时盘成功处理额度拒绝并保存证明，不要求状态不变，其他失败场景保留旧状态。相同SDK镜像sha256:d97c70187827a1e3dc7570952244cc8d24b1e4a44592bd6dbd95706df0b8d480通过当前摘要绑定，未修改guest库存。实际OS取消、恶意RPC及daemon恢复仍未覆盖。

第二轮完整回归1 failed /1894 passed /13 skipped /598.33秒，cache/full-plugin-budget-final-20261013.log：真实HTTP超额响应回退用例3秒总预算耗尽；同期构建/原生验证，不确定根因，不能宣称仅由负载导致。隔离执行同一未改用例1 passed /2.11秒（cache/http-fallback-isolated-20261013.log），不替代完整通过。该用例验证正文额度/回退而非启动速度，现按CLI默认10秒预算并核对进程回收；新增1025/4096字节两种受控流，过额后再读取任何块立即失败，必须关闭该响应并返回下一来源IP，防止增加测试预算掩盖正文无界读取。生产HTTP预算/额度未改变。最终HTTP/插件预算/发布联合36 passed /18.61秒（cache/plugin-budget-http-final-related-20261013.log）。本批wheel及原生证据仍对应当前生产源码；最后调整仅验证工具/测试和文档。下一完整回归单独执行，不并行构建或原生负载，仍需取得终态后登记。

最终生产/测试/验证工具及本段追加前文档的提交候选快照：400文件、gitleaks0发现，cache/secrets-plugin-budget-submission-20261013.log（cache/secrets-bytequota-1e92_8v9）。静态123存量、0新增/解决，diff检查通过。当前独立完整回归日志cache/full-plugin-budget-isolated-20261013.log，未取得终态，不标记整体验收通过。

上述独立完整回归最终exit0：1897 passed /13 skipped /530.90秒，cache/full-plugin-budget-isolated-20261013.log；生产源码和已收集测试运行中未改，不并行构建或原生负载。运行后段新增下一批test_plugin_prompt_guard.py未纳入该次集合，不能用此结果宣称其通过；此前两个失败保留。13跳过及全部八类剩余验收继续开放，后续prompt/监督器修改需新源码/SDK摘要与产物证据。

## 进程CPU会话、身份和失败语义（2026-10-13）

此前真实自身负载查询0.0%而保留psutil对象52.9%的复现是初始化缺口。现monitor/process_sampling.py统一单PID和列表采集，移除两套as_dict/process_iter属性百分比映射及CLI无效None兼容分支。使用oneshot减少同一身份重复属性读取，创建时间前后用两个新对象核对；会话按PID保存创建时间/user/system/monotonic基线，复用PID不继承旧率，异常清理基线。每条ProcessEntry schema2注明UTC、窗口、创建时间和one_logical_cpu百分比（可超过100%，排除子进程时间）。首样本nullable，不把初始化或异常修为0。

CLI --pid范围1..4294967295；NoSuchProcess/AccessDenied/ZombieProcess有不同原因，保留nullable报告并退出1；查询过程中身份变化、计数回退或无效计数也失败。默认列表容错保留错误状态行，未知指标排在已知之后、并列稳定；--limit可截去不可读项，所以不证明全系统可读。CPU首次排序建立基线、等待请求0.1秒并重新枚举，内存排序不等待；库存/基线16384上限、先清消失PID再读取，PID0空闲伪进程不排名。完整排序仍O(P log P)、总存储O(P)，初始化与身份复核有额外I/O成本，未宣称提速。

最终监控/CLI相关93 passed /13.03秒，cache/monitor-process-session-final-20261013.log。新增19例含首样本、150%多线程含义、跨样本及读属性中PID复用、消失/权限/僵尸、计数/时钟/NaN/无穷、真实idle0、嵌套会话、先清陈旧基线、库存限额、CPU初始化/内存无等待/未知排序/并列及CLI PID范围/JSONL；系统CPU、网络及既有JSON输出回归保留。权限及PID复用是注入证据，不冒充真实OS权限/复用。

scripts/verify_process_native.py使用当前解释器自身0.25秒有界CPU循环，不创建负载子进程、不读取用户文件。Windows3.12.10/psutil7.2.2首样本initial/null、测量23.6%、独立保留对象25.0%、窗口约0.265秒；Linux3.11.2/锁定psutil7.2.2测量99.6%、参考99.7%、窗口约0.251秒。两参考的观察边界不同，不要求精确相等；两系统新会话恢复initial，CPU/内存Top5排序及数量通过。结果含源码摘要而不保存进程名/列表：cache/process-native-windows-20261013.json、process-native-linux-20261013.json。不是吞吐/p95/RSS测量，也不是实际PID复用或权限证明。

安装入口验收追加当前父PID首样本、确认不存在PID的非零报告；空PID夹具探测最多100个候选，避免验证工具无界循环。最终当前源码wheel位于cache/wheel-archive-reader-gmmw1y8i，233运行文件/238成员，SHA256 2d6ea3fb79b1afdaae5abf3e2c890f4b5d9557beef10c6d3f2ac4874d1149a41，源码/RECORD与pip check通过，91 help+9实际查询/拒绝共100项通过（cache/wheel-monitor-process-final-20261013.log）。此前o384s7ru快照及process-installed-cli-20261013记录早于最后CLI无效分支清理，不当最新完整源码产物。

同步psutil枚举/属性调用整次监督、其他平台、真实权限与PID复用、内存热变化、Top-N代表性负载仍待验收。基线会话是同步观察状态，不是授权凭据。接下来优先继续P0插件RPC身份、整体期限、阻塞broker与daemon恢复；八类计划保持开放，所有变更未提交。

最终完整回归1876 passed /13 skipped /501.25秒，cache/full-monitor-process-session-20261013.log；运行中生产和测试源码未修改，仅整理文档，无并行原生负载或构建。13项跳过不算通过，耗时不作为性能优化证据。最终diff检查通过，静态123存量候选、0新增、0解决；不能据此批量删除代码。

本节追加扫描结果及最后计划修订之前的候选快照：399文件、gitleaks 0发现，cache/secrets-monitor-process-current-20261013.log，快照cache/secrets-bytequota-7bg40gcm。扫描工具校验既有官方工具摘要；Git历史未变，本次不重新宣称历史扫描。后续仅文档修订不改变上述wheel源码范围。

## CPU共同计数窗口与异常样本（2026-10-13）

旧get_cpu分别调用总量和每核cpu_percent(interval=0.1)，结果来自两个先后窗口。现使用monitor/cpu_sampling.py读取同一对每核时间计数，中间只请求一次0.1秒等待，无psutil全局百分比缓存；总量按各核实际时间增量加权而非平均已舍入百分比。Linux扣除重复guest/guest_nice，iowait不算忙碌；Windows/macOS使用对应CPU时间字段。复杂度O(C×F)、空间O(C×F)，C最多4096、F最多32，未做吞吐/p95基准。

CpuInfo的CPU rate_schema_version=2，记录单位percent、UTC起止和实际monotonic窗口；核心数来自本次读取，频率和物理核心数是后续元数据，不宣称同时测量。计数回退、拓扑变化、NaN/无穷/负值、矛盾guest字段、没有有效增量或无效时钟均不可用，不伪造0。CPU菜单及仪表盘显示N/A；CPU/dashboard CLI保留数据并返回ok=false、退出1，网络schema独立不变。

首轮72通过/1失败（cache/monitor-cpu-window-related-20261013.log）揭示guest增量超出user/nice仍被接受，修复该遗漏并检查聚合溢出。最终相关74 passed /6.24秒（cache/monitor-cpu-window-final2-20261013.log），包含不同核心时间权重、Linux guest/iowait、Windows interrupt/dpc、计数异常、拓扑/字段变化、一次等待/两次读取、取消、库存限额、CLI失败数据保留和菜单nullable显示；原网络会话及JSON输出回归保留。

scripts/verify_cpu_native.py读取实际计数，测试专用参考采用psutil私有算法对同一对计数比较（生产实现不依赖私有API）。Windows Python3.12.10/psutil7.2.2八核心，6.2%与参考6.2%一致、实际窗口约0.094秒；Linux WSL2 Python3.11.2/锁定psutil7.2.2八核心，0.0%与参考0.0%一致、窗口约0.100秒。二者均两次读取，结果及两生产模块摘要记录cache/cpu-native-windows-20261013.json与cpu-native-linux-20261013.json。是不同负载的各一次原生观察，不是性能对比、热插拔或真实计数回退证明；100ms是等待请求，不保证硬实时或精确观察间隔。

macOS、声明Python版本、同数量核心替换、真实热插拔/回退、频率及核心元数据阻塞监督、PID创建时间基线、权限区分和top-k实测仍待完成。新源码的wheel与完整检查另记，不能复用上批旧产物。整体八类目标保留，本批未提交。

当前源码新wheel位于cache/wheel-archive-reader-0qqvakts，232运行文件/237成员，SHA256 403e3bb112b30e0408110c19af0a89a68622bbfabf5944d7641782c4f9af2043，源码库存/RECORD校验及pip check通过。独立安装环境91 help及7个实际查询/拒绝共98项通过，包括新的CPU实际命令；cache/wheel-monitor-cpu-window-20261013.log。完整当前源码回归另记。

下一批PID缺口已有真实证据：同一验证解释器执行有界0.25秒CPU循环，当前get_process新建对象给0.0%，保留的同PID psutil对象给52.9%，记录cache/monitor-pid-before-self-20261013.json及当前commands摘要。是初始化语义证明，不是稳定性能测量。此前独立子进程未在夹具2秒CPU就绪预算内就绪，finally已回收；第二个stdin脚本因根目录推导错误未写报告，改用确认的工作区后取得证据；未提高产品预算或将失败当通过。该短暂验证与全套并行，最终耗时不用于比较。本批不修改PID实现，首样本/创建时间基线及权限错误区分继续开放。

第一次完整回归1855 passed /1 failed /13 skipped /489.96秒，cache/full-monitor-cpu-window-20261013.log。旧test_timeout_kills_grandchild无条件要求Python在整次1秒期限内启动、创建孙进程并写PID，本次marker不存在导致FileNotFoundError，不能证明孙进程阶段已执行。测试改为两个独立证明：原1秒整次期限及父进程回收（marker可尚未出现，出现时已原子完整发布）；另在实际父/孙PID和创建时间记录出现后，测试专用父时钟按真实wall time推进，验证1秒阻塞期限及两个身份不再活跃。后者仅排除夹具启动，30秒startup上限；不代替前者，不改变core.process生产监督或产品期限，Zombie只视为非活跃而非声明完全无进程记录。

安全/监控相关93 passed /13.64秒（cache/monitor-cpu-security-stage-20261013.log）；最后原子PID标记细化后安全19项通过（cache/security-startup-marker-final-20261013.log）。当前生产源码/wheel未因测试变更改变，完整复跑另记。首次失败保留，不以相关检查代替当前源码完整回归。

最终完整复跑1857 passed /13 skipped /491.12秒，cache/full-monitor-cpu-window-security-final-20261013.log。复跑期间生产/测试源码未修改，没有并行镜像构建、原生打包或CPU负载验证；13跳过及全部剩余目标继续保留。当前源码wheel与两系统CPU对照如上，不将实际观察、测试专用阶段时钟或静态检查当整个平台安全/性能完成。

最终生产/测试源码及追加此扫描记录之前的文档快照：396候选文件，gitleaks0发现，cache/secrets-bytequota-pmxc2xyd；日志cache/secrets-monitor-cpu-current-20261013.log。静态123遗留候选，无新增/已解决，diff检查通过。Git历史未改，本次不冒充重新历史扫描；扫描仅覆盖该快照，不证明所有供应链/权限安全。所有本批变更尚未提交。

## 网络监控会话及计数重置（2026-10-13）

根据此前负速率复现，移除全局_net_prev：CLI入口和菜单实时页面用ContextVar上下文明确拥有网络基线，退出/取消还原上下文，嵌套页面不污染外层。无显式会话的get_net独立采样。psutil使用nowrap=False读取原始计数，避免其全局修正缓存掩盖回退；一次采样共用monotonic时间与UTC采样时间。消失接口基线清理，接口库存超过4096显式拒绝。

NetInterface的rate_schema_version=2（外层envelope仍1）：累计计数bytes，速率bytes/s，window_seconds为实际观察间隔；initial/counter_reset/invalid_window的速率及窗口均null，available可为实测0。重置后重新建立基线，而非将负值隐藏成0。仪表盘和网络菜单显示N/A，避免nullable值交给fmt_bytes；旧脚本需要迁移nullable速率处理，契约见cli-contract。采样间隔仍为采样完成后等待，不承诺精确周期。

首轮41通过/1失败源于新增测试预期0 B/s与既有0.0 B/s格式不一致，保留cache/monitor-network-session-related-20261013.log并修测试断言。最终monitor/CLI输出相关58 passed /14.64秒，日志cache/monitor-network-session-final2-20261013.log：原负速率序列现在initial→counter_reset→available，恢复100/200bytes/s；另含单方向回退、时间窗口无效、接口消失重现、嵌套取消、两次CLI调用、菜单清理和库存限额。未将这些模拟检查称为真实网卡重置验收。

当前源码新wheel位于cache/wheel-archive-reader-qo8ik3a6，231运行文件/236成员，SHA256 906fdf65beaa69639dab7e56def4931e9bc2f0c132ad173d0cae4f7e48676d12；源码/RECORD检查、pip check及独立安装98项通过。scripts/verify_monitor_network.py在源码目录之外执行真实Windows安装入口，两次采样四接口均initial→available，新的单次调用四接口均initial，无运行存储创建；cache/monitor-network-installed-20261013/verification.json。工具仅记录状态/数量，不保存接口名或累计用户流量；未生成受控流量、真实重置或吞吐基准。

初次WSL默认Python缺psutil。随后在项目cache中创建独立Python3.11.2虚拟环境，仅离线安装uv.lock指定的psutil7.2.2 abi3 Linux x86_64 wheel，下载大小155560及SHA256 076a2d2f923fd4821644f5ba89f059523da90dc9014e85f8e45a5774ca5bc6f9均匹配；未更改系统Python。真实WSL2/Linux glibc2.36采集器六接口initial→available，新会话六接口initial，记录cache/monitor-linux-native-20261013.json及源码摘要e4df84331dabcec673e83d47c81302cb82f054a03e7043c5dcab1d4ed5d022ae。该证明不包含Linux完整CLI、真实重置、受控流量或性能；未保存接口名/累计用户流量。环境创建和短暂只读验证与完整回归并行，运行耗时不作为性能比较。

macOS/其他声明Python版本、未观察到的同名接口替换、CPU共同窗口、PID创建时间基线、权限区分、top-k实测及整体性能仍待完成。静态123候选，无新增/已解决；全套当前源码检查另记。整体八类目标保持开放，本批未提交。

最终当前源码完整回归1840 passed /13 skipped /603.06秒，cache/full-monitor-network-session-20261013.log；无生产/测试源码中途修改。Linux独立环境创建及只读采样曾并行，耗时不用于与上一批503.41秒比较，不宣称性能改善或退化。当前源码wheel、Windows安装CLI及Linux采集器证据分别如上；13跳过项和全部剩余目标继续保留。

最终源码及本节追加扫描结果之前的文档快照：393候选文件，gitleaks0发现，cache/secrets-bytequota-tstve26t（cache/secrets-monitor-network-current-20261013.log）。工具按既有官方发布摘要验证；历史Git未变，本次未重新宣称历史扫描。静态仍123遗留候选，无新增/已解决，diff检查通过；候选数不是垃圾代码删除证据。

## MySQL Linux依赖检查与官方CLI计划复核（2026-10-13）

旧实现接受libaio.so.10冒充libaio.so.1、吞掉apt退出100且无期限，复现记录见cache/mysql-dependency-before-20261013.json。已移除旧模糊检查：四个命令及私有库采用有界离线ELF64元数据读取（x86_64/AArch64），限定安装目录、文件/图规模及元数据额度；精确匹配ldconfig同架构soname，架构只看箭头前的ABI标记。依赖检查、包管理和重新查询共用180秒，受监督进程有输出上限，失败抛InstallError并阻止staging发布。没有依赖则不执行包管理，未知缺失依赖不构造任意包名；取消保留旧安装和用户数据。

实际Oracle 9.7.0 minimal x86_64包83,260,084字节、455成员，观测SHA256 e8b79dd6efd37d8036b51703e34bf92fb253d47587d697ea0da8ea37ee0085e7。四个实际二进制/私有库离线读取确认12个外部soname，包括ncurses/tinfo6、libaio和NUMA，旧固定5清单不正确。当前解析器重新读取同一样本结果一致，记录cache/mysql-actual-current-20261013.json；程序未执行，观测摘要不是独立发行方认证，不能证明glibc符号版本或真实安装/服务成功。

最终相关回归168 passed /24.37秒（cache/mysql-dependency-final-broad-20261013.log），其中新增依赖/ELF测试47项。真实Linux amd64/Python3.12.14生产驱动八场景通过（cache/mysql-dependency-native-tu_y289n）：已有旧/现代依赖、安装成功、退出100、退出0但缺库、查询失败、阻塞后进程回收和输出超额。只执行自建工具与惰性ELF夹具，不安装真实系统包。3秒阻塞场景无活跃后代，后代曾是僵尸Z，容器删除后命名空间销毁；具名容器确认已删除，不宣称删除前没有僵尸。

保留历史失败：初次测试时钟顺序错误、一次Windows归档目录rename WinError5（未确定根因）、首次原生夹具在noexec tmpfs无法执行。修正测试时钟及工具验收tmpfs显式exec；插件沙箱noexec策略未改变。前一wheel快照早于最后ABI标记修复，其98项通过仅为旧源码证据。当前源码的重新打包和完整回归另记录，不沿用旧产物宣称发布完成。

官方CLI逐功能库存及下一批交付补充到official-cli-completion-plan第18节。MySQL无需增加跳过依赖或任意包名参数；180秒依赖预算不等于整次安装timeout。实际发行版包映射、来源信任、glibc兼容、系统跨用户锁、包管理副作用补偿和真实服务均待验收。八类项目健康目标继续开放。

最后ABI修复后的新wheel位于cache/wheel-archive-reader-7c48bx5j，231运行文件/236成员，源码库存和RECORD校验通过，SHA256 a498432f0151d15877bf46fe54631cc7ede2204a0f75dde29b1b2cb538da7af0。独立审计环境只替换平台wheel，pip check及91个help、7个查询/拒绝共98项通过，未初始化运行存储（cache/wheel-mysql-abi-final-20261013.log）。当前391候选文件秘密扫描0发现（cache/secrets-bytequota-epmjweiw）；不将该快照冒充后续文档编辑或历史扫描。静态123遗留候选、无新增/已解决，diff检查通过。

最后源码完整回归：Windows Python3.12，1829 passed /13 skipped /503.41秒，日志cache/full-mysql-dependencies-final-20261013.log。运行期间未修改生产/测试源码，未并行构建或执行原生资源测试。13跳过及全部声明平台、真实发行版安装/服务、生产签名和八类剩余目标继续开放。本次计划与后续实现变更尚未提交。

## 插件真实资源耗尽及输出管道修复（2026-10-13）

scripts/verify_plugin_native.py新增--lifecycle，使用自己的隔离合成插件与审核来源/锁，不执行未知克隆插件。第一次cache/plugin-native-lifecycle-20261013在输出项立即检查时仍见容器，验收失败并保留日志；后续只读查询确认已消失。这暴露清理请求与实际销毁不是同一时点，验收工具增加最多10秒有界观测和daemon destroy事件要求，记录准确观测耗时而不是等待后无条件通过。

生产worker和代理新增log-driver=none，避免宿主输出额度之外的Docker默认日志落盘增长。按原字符数计量可能放宽多字节UTF-8额度，现改用core.process.start的可选binary模式，插件管道在解码前按原始字节限单行32MiB/累计64MiB，RPC回复显式UTF-8编码。其他进程调用默认文本模式不变。

此前满管道时先rm容器，再结束本地附着客户端，真实ASCII输出超额调用约37.09秒（cache/plugin-native-lifecycle-lognone-20261013），符合清理控制30秒等待后再释放客户端的表现；不声称证明全部daemon内部阻塞原因。现在先结束本地进程树释放附着连接，再始终尝试rm容器，读写线程随后回收。新同样ASCII样例约4.55秒、Unicode约4.58秒，均准确拒绝、旧状态保留且daemon销毁。每种仅单次不同负载观测，不是p50/p95或稳定吞吐提速结论。

最终cache/plugin-native-bytequota-final-20261013/verification.json与lifecycle-results.json：真实Docker20.10.24/Linux amd64、image sha256:d97c70187827a1e3dc7570952244cc8d24b1e4a44592bd6dbd95706df0b8d480；21项基础检查各三次，第三次通过新Windows PyInstaller冻结CLI（cache/native-minimum-e16lgceq/dist/opskit.exe）。另九种生命周期/资源测试使用源码生产监督器：deadline、运行中revoke、guest就绪后注入KeyboardInterrupt、320MiB内存申请、17MiB状态、70MiB ASCII/Unicode输出、PID耗尽、16MiB临时盘写满。七种拒绝均保留旧状态；pids/tmpfs在拒绝过量操作并处理错误后正常提交自己的证明。PID创建63个子进程后EAGAIN；临时盘ENOSPC；内存必须有daemon oom事件；每项ps无具名残留且有destroy事件。guest启动了后台子进程的超时/撤权/取消场景均随容器销毁。

deadline场景实际inspect确认日志驱动none；配置5秒执行预算，整次约10.11秒，包含前置检查、启动与清理，不能当整次5秒预算。额外销毁观测约0.28..1.38秒；仍不声称返回瞬间所有daemon状态已经同步。KeyboardInterrupt为平台处理路径注入，不是实际操作系统Ctrl-C。获批网络/RPC、跨调用方隔离、broker阻塞、整体预算、daemon失败持久化恢复、多架构和生产安全仍待完成。

相关151 passed /1 skipped /72.70秒，新增多字节管道额度回归（cache/plugin-bytequota-related-20261013.log）；执行器/包管理24 passed /3.55秒（cache/plugin-bytequota-process-20261013.log）。新wheel230运行文件/235成员，源码/RECORD及独立安装98项通过，SHA256 6689fd74b05a0cc786d7be29c7c4a2c22c7a16710742a6c9699126c48423dc38，cache/wheel-archive-reader-o6_8g6m1。依赖未变、复用已独立审计的25依赖环境；新冻结程序实物构建通过。静态123候选无新增/已解决，diff检查通过。最新完整回归另记，之前1780通过不覆盖本批生产改动；八类计划继续开放。

新wheel源码外插件CLI也重新运行三次21项基础检查并返回0（cache/plugin-native-bytequota-wheel-20261013/verification.json），与新冻结入口使用同一当前SDK摘要和image。Gitleaks8.30.1复核官方固定摘要ZIP及可执行文件一致，再对387个非忽略提交候选创建精确文件/SHA256快照，默认规则、忽略用户allow注释、自定义空ignore、redact100扫描0发现/退出0。cache/secrets-bytequota-ijt62gc9/{inventory,summary,report}.json及scan.log；只证明本轮规则对该快照未命中，不证明不存在未知秘密或漏洞，历史扫描证据仍单独保留。

首次本批全套1780 passed /1 failed /13 skipped /520.80秒（cache/full-plugin-bytequota-20261013.log），失败是DNS真实worker未在2秒总预算前写出阻塞标记。原测试把“总期限包含spawn”和“已进入目标阶段后回收”合并，可能在进入前正确到期，却不能证明阻塞阶段。独立原用例6 passed /2.51秒（cache/dns-full-failure-recheck-20261013.log），但不据此认定全套通过或断言外部负载根因。

现保留未替换时钟的2秒整次预算及原<4秒/无子进程残留断言；新增单独的阻塞阶段测试：真实spawn/标记/睡眠/终止，父监督器仅在标记前冻结测试时钟（真实启动有30秒上限），标记后按实际墙钟推进并断言经过2..4秒和无残留。生产network.worker及resolver期限未改，也未通过增加产品默认期限处理测试失败。DNS/网络期限/扫描/插件控制46 passed /16.50秒（cache/plugin-dns-stage-related-20261013.log），完整复跑另记。此测试是明确排除启动的阻塞阶段证据，不能作为生产整次耗时证据；两种断言必须同时保留。

等待全套期间重新复核官方MySQL Linux依赖契约：cache/mysql-dependency-before-20261013.json记录当前linux.py摘要0854d6d802461beb236a51d8cb74bfb56a01e843e1c01514e5863da41736ed6e。API替身证明ldconfig只有libaio.so.10仍把libaio.so.1判为存在；apt-get退出100被吞、调用无timeout，helper正常返回。没有执行包管理器或下载程序，这是待修生产行为证据，不是原生安装验收。下一批需精确soname、共享受监督进程和有界期限、失败抛InstallError及安装后复核；不同发行版/版本的依赖映射仍须实际验证，不能通过删除检查或空成功绕过。

monitor/commands.py摘要57fcd2cde14de5ab682077039eb6e763b285237c56158851f8dcb9b61e35717c：合成累计计数(1000,2000)→(500,1000)→(600,1200)，单调时间100/101/102秒，产生速率(0,0)→(-500,-1000)→(100,200)bytes/s（cache/monitor-counter-reset-before-20261013.json）。证明网卡重置没有正确语义，不涉及真实用户网卡采集或吞吐测试。首样本/重置不可计算状态、采样会话缓存、时间戳/单位及CPU窗口仍是待修内容；不通过简单隐藏负数声称采样已正确，不以top-k提速代替此正确性要求。

最终Windows Python3.12全套1782 passed /13 skipped /496.53秒（cache/full-plugin-bytequota-dns-final-20261013.log），覆盖本批字节管道/清理顺序及DNS两种断言；首次失败记录保留。完整复跑期间未进行生产源码修改、镜像构建或原生打包；静态123候选无新增/已解决，diff检查通过。此前387候选秘密扫描快照在DNS测试调整前，新增测试只有合成标记/时钟断言，不将旧快照摘要冒充当前文件摘要。wheel/冻结产物对应生产源码，DNS测试变化不在运行文件库存。整体八类目标、13跳过项、上述MySQL/监控待修项和完整安全矩阵仍未关闭；本批变更尚未提交。

## 最小插件SDK与冻结版兼容验收（2026-10-13）

当前运行时policy4、catalog schema2：固定20个SDK代码/语言/主题文件，构建上下文共22项，不递归复制完整core。宿主SDK服务发现改为惰性调用，guest只适配SDK受限RPC入口，不导入宿主plugin_services；复用SDK依赖检查而非维护重复实现。catalog与镜像label均绑定当前SDK代码/资源SHA256，旧catalog或SDK变更必须重新prepare。

冻结版构建携带同一库存的plugin-runtime-sources.zip；限定目录、普通单链接文件、单文件/总量及ZIP成员库存，拒绝链接、重复/缺失/额外成员和读取竞争。源码/wheel直接读取固定库存。150 passed /1 skipped /74.61秒（cache/plugin-minimum-bundle-20261013.log）；最终读取实现再验证22 passed /10.63秒。静态遗留123候选、无新增/已解决，不据此删除公开接口。

新wheel库存230运行文件/235成员，源码及RECORD校验通过；SHA256 7d8e3844bb4b152151b52ebf21fbea2f081d400abb3839c41f0c3acc8788d469。复用此前独立安装并审计的25依赖环境，仅替换平台wheel，pip check及91 help+7查询/拒绝共98项通过，无运行存储。原始构建与验收位于cache/wheel-archive-reader-9gz9pfyx，日志cache/wheel-plugin-minimum-20261013.log。

Windows Python3.12.10/PyInstaller6.20.0在独占源码快照构建原生程序并运行help；cache/native-minimum-5tiu27jk/dist/opskit.exe。真实构建暴露两个历史问题：spec放在workpath/name被PyInstaller清理；distutils排除与当前依赖钩子冲突。已分别移至独立spec目录、取消该排除；恢复sqlite3打包以保留官方XUI只读诊断。失败日志与最终cache/native-minimum-build-final-20261013.log均保留。Nuitka只有打包入口和模拟库存证据，未实际编译。

使用同一审核过的registry digest和12项完整wheel锁，真实Debian Docker20.10.24/Linux amd64构建policy4 image sha256:a7ddcc6a1952cc4104c25e6bc0b2634e4cff0114a7d03582e889248ee8a5c10d。源码外新wheel CLI与上述冻结CLI各自运行scripts/verify_plugin_native.py：各两次生产监督器及一次实际CLI调用，每次21检查、状态计数1/2/3、退出0，可观测两个worker删除。检查增加宿主业务模块不可导入与SDK子进程；参数MiXeD、--help、重复选项顺序保持。证据cache/plugin-native-policy4-{wheel,frozen}-20261013/verification.json及各自日志；两种入口使用相同SDK摘要和镜像，冻结bundle兼容由真实CLI证明。

源码稳定后Windows Python3.12完整回归1780 passed /13 skipped /494.51秒（cache/full-plugin-minimum-20261013.log），覆盖当前SDK实现。构建和真实容器检查先完成，未与全套同时运行；静态123候选无新增/已解决，git diff --check通过。该批不证明资源耗尽、授权RPC/出口、运行中撤销、取消、daemon故障恢复、多架构、OS无漏洞或发布者签名；八类健康目标仍开放，不能按本批关闭整体目标。

## 真实Docker及已安装插件CLI基础验收（2026-10-13）

此前WSL客户端查询超时不是永久环境缺失；本次重新只读查询确认Debian Docker daemon20.10.24可用。基础镜像python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea，在官方registry-1.docker.io/library/python HTTPS端点读取并核对索引内容摘要；Linux/amd64子manifest为sha256:2fe5997d249a808b8eeea52c58a1dbffbba28754dc11699ef5c029f2d818ce79，config摘要与本地image ID一致。cache/plugin-native-validation-s_w5cwze/base-provenance.json；未验证发布者签名或基础OS漏洞。

SDK完整锁固定12项已知版本，从官方HTTPS PyPI按版本获取非yanked wheel SHA256，read_lock通过；原始来源清单在该目录lock-provenance.json。生产build实际使用require-hashes/only-binary/ignore-installed，构建image sha256:cc1c8e7d992a99c4e375740276e411f332c55168bce6a60ffcf6dc33e331a579；不是替身Docker。初始缓存探针直接invoke返回字典，仅作监督器基础烟测，不作公共CLI证据。

新增可复用scripts/verify_plugin_native.py：要求显式审核来源/原始锁路径/已安装CLI/新输出目录，只生成自己的合成插件并隔离配置、信任、插件目录、状态和canary。正式插件cli返回0，将证明写到自己的state；前两次走生产监督器，第三次通过新wheel安装目录的opskit.exe，在源码目录外真实执行plugin run native_probe -- MiXeD --help --repeat one --repeat two。三次各19项全部通过、状态counter为1/2/3、CLI退出0；两个可观测监督器worker在daemon查询中确认删除。源码final版证据cache/plugin-native-reproducible-final-20261013/verification.json及invocation-results.json，日志同名.log；初次可复用脚本通过后加锁路径早期检查，最终版已重跑，不借旧结果覆盖新工具。

19项覆盖实际uid65534/CapEff=0/NoNewPrivs=1、根目录/插件/seed写拒绝、Docker socket及自有host canary不可见、其他状态路径不可见、仅loopback网卡、cgroup256MiB/64PIDs、tmpfs16/32MiB及/tmp直接执行拒绝、其他SDK命名空间和宿主安装/命令解析拒绝、参数大小写/帮助/重复顺序。额度仅观察生效，不是耗尽证明；未作获批网络代理、RPC提供方/调用方、运行中撤权、取消、daemon清理失败/崩溃恢复及多架构验证。最小运行时代码仍未完成：实际构建上下文75项，仍含完整core内部模块（更新/HTTP/平台发现等），库存已保存，下一批应限定SDK/worker闭包。构建缓存/image及所有证据保留，不全局prune或触碰用户插件/服务/真实数据。

## PAX原始记录与隐式布局预算验收（2026-10-13）

前置复现：10000条重复comment记录在唯一键额度下仍被标准库接受，PAX测试14失败/2通过；仅4条深层有效路径即可越过原有显式成员额度，布局测试7失败/2通过。证据cache/pax-preflight-before-20261013.log、archive-layout-before-20261013.log。修复前均为惰性合成输入，无未知代码执行。首次修复误把TarInfo._block当静态方法调用，相关26失败/113通过记录保留；改成块长计算后139项通过。

core.archive_reader新增原始PAX记录前置格式与数量检查，重复字段也计数；标准库构建记录/解码前拒绝第129条及非法长度/截断/关键词/换行。有效重复字段仍最后值生效，Unicode及BINARY字符集兼容测试通过。枚举同时计费普通成员名/链接名和基础开销，避免仅PAX头计费而漏掉普通长名称。core.archive的tar、映射tar、原始/剥离后ZIP布局均在输出目录创建前，限制不同显式/隐式路径节点200000、UTF-8字节加128字节基础开销64MiB；共享目录只计一次。tar调用方的较小额度同步降低布局预算，插件8192节点/16MiB计费。

新增17项PAX及10项布局回归；真实_save_state用80个128层有效路径验证旧文件保留、无.previous或临时目录残留。最终相关215 passed /1 skipped /31.78秒，cache/archive-resource-final-related-20261013.log；完整Windows/Python3.12.10回归1755 passed /13 skipped /497.15秒，cache/full-archive-resource-20261013.log。全套期间生产源码/测试稳定，文档及独立缓存输入不计入源码范围。Linux Python3.14.6 UTF-8/ASCII各22项实际/tmp检查通过，cache/native-archive-resource-final-utf8-20261013.log及ascii同名日志；首次新增原生夹具忘记原包含链接/Unicode所需选项，两模式失败后修正，失败日志保留。Ruff及静态123候选无新增/消除、diff检查通过。

50000重复记录、650000字节惰性PAX的ABBA资源样例：旧入口接受，Python峰值约6MiB；新入口在标准库构造前拒绝，约0.63MiB。不是正常吞吐/RSS/p50/p95证据，详见performance-baselines。真实Redis7.4.2包1697成员通过新解析与布局，计费元数据584110字节；cache/archive-resource-evidence-20261013.json。没有执行源码或独立信任摘要。

新干净快照wheel源码/资源/RECORD逐字节检查229项运行文件/234成员通过，SHA256 cdf35c502f435e0ab97416313bffda1f7beb0367af81c41244e280918a961df5，cache/wheel-archive-reader-czej6vye/result.json。以该wheel替换已有独立安装/审计依赖环境的平台包，未重新解析25项依赖；pip check及源码外91种help+7项查询/拒绝合计98检查通过，无运行存储。cache/wheel-archive-resource-20261013.log。后续新增原生插件验证脚本不进入运行wheel，单独执行验证，不将此前全套宣称为对新工具的执行证明。

整次预算、并发临时磁盘、更多正式包布局、最低Python/macOS CI及完整系统事务仍未完成；八类计划不关闭。

## 受限归档入口首批验收（2026-10-13）

core.archive_reader已把tar扩展头、递归、成员、有效载荷及元数据额度和ZIP目录/实际成员数检查前移到解析入口；20个生产模块迁移，插件状态使用4096成员、16MiB载荷/元数据额度。压缩tar按需有界缓存，成功返回前验证尾部；XZ字典128MiB、输出块1MiB；ZIP仅允许未加密STORED/DEFLATED。额度只能缩小，具体限制见release-security。历史64MiB PAX读取探针对应的问题已修复；此前失败记录仍保留，不适用作当前未修状态。

Windows Python3.12.10完整回归1728 passed /13 skipped /431.47秒，cache/full-archive-reader-20261013.log；运行期间源码稳定。相关联合191 passed /13.55秒，cache/archive-reader-final-related-20261013.log。Linux Python3.14.6 UTF-8/ASCII各19项通过，cache/native-archive-reader-final-utf8-20261013.log及ascii同名日志。Ruff关键门槛及静态123项候选无新增，diff检查通过。新增45项打开器测试、13项调用链测试；早期接口/读块期望/夹具错误已修正，失败日志保留。这些结果不代表macOS、最低Python版本、真实服务或远端CI通过。

官方HTTPS Redis7.4.2源码包3533864字节、1697成员、声明载荷16094655字节，受限解析及提取后确认两项源文件存在；cache/redis-official-asset-ozisfgih/result.json。观测SHA256 4ddebbf09061cbb589011786febdb34f29767dd7f89dbe712d2b68e808af6a1f不是独立可信摘要；未执行源码/编译/服务，不标记生产信任或安装完成。

16MiB惰性gzip样例ABBA结果摘要相同，压缩读取量约32→16MiB，Python分配峰值约3.2→2.2MiB；cache/archive-reader-metrics-kndy7rnt/result.json。未控制系统缓存，不代表RSS/p50/p95或框架提速，新增缓存有磁盘空间/I/O成本。自动审批先后拒绝递归及逐文件清理，均仅返回blocked by policy；生成样例保留于忽略cache，未绕过政策。

当前源码干净快照wheel构建并逐字节/RECORD校验通过：229项运行文件、234成员，SHA256 6a003db5a897a5d53fef6eab49f46e3f953a0f37b868eeb0647dacb4c18dae19，cache/wheel-archive-reader-b9x7_jrf/result.json。复用此前独立安装并审计的25项依赖环境，以新wheel --force-reinstall --no-deps替换平台包；pip check、源码外91种help+7项查询/拒绝合计98检查通过，无运行存储创建。日志cache/wheel-archive-reader-20261013.log与installed-cli/verification.json；未声称重新创建依赖环境或重新查询漏洞。

仍待PAX字段构造临时空间、隐式目录图预算、并发临时磁盘/总期限、stdlib私有扩展点版本CI、生产摘要/多平台官方包和完整事务验收；八类整体计划未关闭。本节作为最新源码状态，以下历史时点记录保留。

目标：完成代码健康、安全、性能与官方 CLI 两份计划，逐项以当前代码和实际测试验收。未验证项不能标记完成。

## 工作清单

| 项目 | 状态 | 验收证据要求 |
|---|---|---|
| 代理活动连接上限和请求头总期限 | 本地回归通过 | tests/test_proxy_limits.py 真实连接测试；仍需 Linux 容器压力验收 |
| 网络任务监督、取消、输出上限 | DNS/扫描/HTTP/Ping/Traceroute 已接入基础监督 | 慢响应/取消/期限/回收本地测试；旧辅助路径和原生跨平台仍待验收 |
| CLI 无交互、输出、退出码统一 | 基础契约、生命周期与结构化诊断已接入 | 插件 prepare/fingerprint 等入口仍待全部迁移；诊断深度继续补齐 |
| 软件查询及能力矩阵 | 查询、矩阵和生命周期编排已实施 | 18 个 Recipe 参数拒绝矩阵通过；真实安装/驱动恢复待验收 |
| 监控采样与网络参数 | 准确参数/期限已接入，本地回归通过 | 跨平台和代表性采样/网络/取消性能验收仍待完成 |
| 安装向导配置与敏感输入 | 四种配置安装和令牌文件已接入 | 模拟配置/安装回归通过；真实安装与事务恢复待验收 |
| WireGuard/XUI/Tailscale/Oh My Zsh 管理 | 部分已接入 | 服务端/客户端管理回归通过；完整诊断及真实部署待完成 |
| 插件生命周期、偏好、更新 CLI | 生命周期、偏好及update status/check/apply已接入 | update download、完整应用/恢复事务及prepare/fingerprint输出迁移待完成 |
| PR/提交跨平台 CI 与静态检查 | 六组合回归工作流已添加，远端运行与静态门槛待完成 | 工作流、遗留基线、新问题阻断 |
| 全仓调用链、依赖、文档清理 | 待实施 | 有证据的清理台账及相关回归 |
| 最小插件运行时与哈希依赖锁 | 待实施 | 源码/wheel/原生准备和依赖一致性 |
| 秘密历史扫描、SBOM、漏洞门槛 | 待实施 | 脱敏扫描报告及可复现检查 |
| 性能基准与实测优化 | 令牌/语言/本地 TCP 基准已有 | 完整平台 p50/p95、RSS、资源残留及代表性负载仍待补齐 |
| 签名生产发布与多平台原生 smoke | 待验收 | 发布负责人信任根、真实产物及环境 |

## 边界

### 同一程序跨数据目录的目标锁（2026-10-13）

updater新增程序目标ResourceLock，身份为规范化程序路径的SHA256，锁放在程序旁私有.opskit-update-locks目录，不依赖OPSKIT_DATA_DIR。frozen self_path解析为实际路径；源代码/无效签名不会进入目标提交锁。_do_apply先验证签名，再在目标锁内复核签名并备份/应用/补偿；rollback和marker原子提交共享该锁。暂存区→目标锁顺序一致。目录不可写/链接/硬链接异常失败，不退回仅暂存区锁，也不修改整个程序目录权限。新运行锁目录已加入.gitignore。

Windows helper获取暂存区及规范化程序目标两个字节锁，直到复制/校验/提交/健康与结果记录后释放；等待第二锁失败时也释放第一锁。新的68 passed（24.11秒，cache/update-target-lock-final-20261013.log）：真实独立进程持有A数据目录及同程序目标锁，B数据目录的公开apply服务使用真实签名产物，在备份/修改前被拒绝；另一程序可独立加锁；终止持有者后锁可重用。另覆盖活动操作内目标身份变化拒绝、真实PowerShell共享目标竞争失败、第一锁释放及解除竞争后的成功提交。此前核心65 passed（19.18秒）。

验证为同一Windows账户的独立进程/数据目录，不是实际多账户权限验收。程序旁锁仅保证互斥，不证明旧进程APP_VERSION与当前已替换程序一致；还需目标级版本/摘要权威和完整异步/断电恢复，以防旧进程及不同缓存选择过期版本。任务失联/失败索引、缓存保留上限、marker恢复凭据和生产多平台验收仍未完成。整体目标不标完成；最终全套见后续记录。

本批最终源码Windows/Python3.12全套961 passed / 11 skipped（182.87秒，cache/full-update-target-lock-20261013.log），TEMP/TMP仅测试进程指向项目私有目录；git diff --check通过，check-ignore确认目标锁目录被忽略。随后在已验证为项目cache内部的独立临时目录，使用真实临时Ed25519签名/产物和真实服务门槛，低层应用以原子替换临时字节文件模拟提交：同一旧运行代码先提交APP_VERSION+2，再切换数据目录提交APP_VERSION+1，后者仍被接受。未模拟或绕开签名/hash验证；没有执行这些字节文件或替换真实程序。该证据证明应用的版本门槛缺少共享目标权威，不证明已完成真实打包降级攻击验收。

该已复现缺口列为下一批P0：目标共享版本/摘要与事务阶段记录，锁内检查实际目标状态、旧运行代码和候选一致性；Windows排队任务绑定最新目标事务；回滚使用明确恢复授权更新共享状态。不能用961通过宣称完整安全验收。工作区仍未提交/推送。

### Windows独立延迟任务和真实PowerShell执行（2026-10-13）

新增core/update_windows.py，将Windows调度从updater中独立。每次使用私有随机任务目录和摘要绑定产物副本，脚本/产物不共享固定名称；Python复制前验证备份目录链接并设置私有权限。脚本路径用正确单引号转义并采用.NET精确路径API；等待原父进程句柄、获取与ResourceLock兼容的暂存区字节锁、流式暂存复制/flush/fsync/摘要、保留目标ACL，再以MoveFileEx REPLACE_EXISTING|WRITE_THROUGH原子提交。旧Rename-Then-Copy、直接覆盖、Users权限扩大及共享pending重启MoveFileEx回退已移除；原启动marker保留验证后重试。

实际提交后才写入新build健康记录，结果明确记录applied/failed/applied_recovery_incomplete。CLI status按缓存任务ID读取白名单结果和实际健康确认，不回显路径/异常；调度仍返回scheduled，不能证明helper当前存活。Windows启动调度不立即exec旧程序；调度阶段不清理恢复备份，健康确认后再清理。

首次真实PowerShell6项中1失败：该运行环境Windows PowerShell找不到Get-FileHash，负例也不能单凭失败码证明到达目标分支。已迁移到.NET SHA256、进程句柄及ACL API，并对负例断言明确阶段；随后6 passed（原生临时文件执行）。加入真实平台字节锁竞争后联合174 passed / 1 skipped（74.00秒，cache/windows-update-regression-20261013.log），覆盖Windows helper7项与现有签名/恢复/查询/旧更新服务。四个重复旧脚本形状断言移除，以实际原子替换和故障阶段验证替代；旧重启回退测试改为启动失败保留程序/记录/marker。

启动/CLI/恢复/native专项82 passed / 2 skipped（24.31秒，cache/windows-job-final-20261013.log）。之后追加恢复记录提交失败后的applied_recovery_incomplete原生验证及备份链接复制前拒绝，最终全套见后续记录。测试只替换临时字节文件，不运行新程序，不替换工作区/宿主程序，不使用生产私钥。

仍缺跨数据目录/跨用户目标锁、完整异步调度和断电恢复、失联任务/失败索引、marker恢复凭据统一、任务保留上限与清理及生产签名/原生产物验收。helper120秒预算是阶段监督，并非文件系统调用硬实时承诺；本批不标完整更新或整体目标完成。

首轮全套957 passed / 11 skipped / 1 xfailed（183.05秒，cache/full-windows-job-20261013.log）。复核xfail发现test_install_windows_py的旧测试实际检查已移除的固定脚本路径，mock所有摘要后启动真实helper，并以“脚本未生成”xfail掩盖验证无效；已删除，由原生helper与签名门槛测试替代。该模块另有三段无调用的清理/模拟安装辅助函数，rg仅查到定义，已删除；目录测试现隔离LOCALAPPDATA与安装目录，不在真实用户目录创建文件。删除的另一个hash“失败”测试只比较计算值与全零串，没有调用拒绝路径；真实签名篡改和helper摘要变化验证已覆盖拒绝。移除对应无效导入、未使用安装路径常量和重复sys.path插入。

安装/native/helper/core相关67 passed（20.50秒，cache/windows-cleanup-tests-20261013.log），之后仅移除无效hash比较与其无用导入，最终全套见后续记录。进程清单按首轮临时任务路径过滤，未发现该旧测试遗留helper进程；不扫描/回显其他进程命令行。旧脚本测试xfail不能记成产品通过。

下一轮全套954 passed / 11 skipped / 2 failed（197.29秒，cache/full-windows-job-final-20261013.log）。两处失败均发生在Rich进度输出的tempfile写入，报ENOSPC；当时C盘可用约0.9 GiB、F盘约72 GiB，没有执行系统临时目录清理。仅为测试进程把TEMP/TMP指向项目cache内私有目录，WG客户端/XUI/native相关38 passed（11.97秒，cache/windows-low-temp-recheck-20261013.log）；这不能抹去原失败，全套重新验证见后续。

额外临时文件实测证明Python_atomic_copy没有保留Windows私有DACL。新增core/windows_permissions.py，通过≤64 KiB安全描述符读取，复制DACL和继承保护位后再原子提交；不复制owner/SACL，不将其称为跨账户完整权限验收。原生验证受限DACL字节及保护位替换前后相同，权限复制失败不改当前程序且回收本次暂存。相关49 passed（18.71秒，cache/windows-dacl-regression-20261013.log），包含10项真实native/DACL及39项应用/恢复；最终全套继续在私有F盘测试临时目录执行。没有修改用户全局环境或清理未知临时文件。

本批最终源码Windows/Python3.12全套958 passed / 11 skipped（176.93秒，cache/full-windows-dacl-final-20261013.log），无xfail，覆盖DACL修复、原生helper10项、启动/状态与此前全量行为。TEMP/TMP仅对测试进程指向私有项目cache目录；git diff --check通过。前述ENOSPC失败记录保留，不改写成通过。远端CI、原生打包/其他平台、跨用户owner/SACL及生产签名未验收；工作区尚未提交/推送，整体目标仍未完成。

### 本地备份身份、原子回滚与恢复记录（2026-10-13）

_do_apply在应用之前写入私有原子备份旁车，绑定backup_id、真实目标、升级前后build、旧/新程序摘要；旁车写失败不进入替换。健康记录引用明确backup_id。rollback拒绝源码模式、不猜最新备份，要求普通非链接/非硬链接备份和有效旁车，目标/版本/摘要匹配；使用指定摘要的目标旁暂存复制、fsync、os.replace，提交失败不截断当前程序；已经恢复的同摘要操作可重试。备份凭据只证明本地记录的一致性，不是发布者认证，不抵御宿主同账户同时篡改数据和记录。

健康/marker元数据新增64 KiB普通对象限制，拒绝链接/硬链接/特殊文件，读取用nofollow/nonblock及打开前后身份比对；写入使用私有原子持久化。健康回滚失败或记录无效时保留记录；仅实际成功才记录update_rolled_back事件。备份清理仅针对有本程序记录的新式文件，保留最近3个及健康记录引用的恢复备份，删除旧备份时同步清理其旁车；不动未记录旧式文件。

marker要求明确更高整数build，不再猜版本；复制绑定校验前摘要，拒绝路径链接。提交成功后的pending/marker删除或缓存刷新失败不误报“未应用”，启动仍可进入新版本。当前未为marker重建独立事务和备份凭据，仍依赖既有调度记录。

初版恢复/旧更新联合140 passed / 1 skipped（56.36秒），marker及保留策略追加后181 passed / 1 skipped（64.20秒，cache/update-recovery-final-20261013.log）。之后追加源码回滚无数据副作用及marker路径拒绝，最终全套证据见后续记录。测试覆盖真实临时签名、备份旁车产生/损坏/缺失、目标变化、原子恢复失败、健康失败后再次重试、元数据体积/形状/硬链接、预期摘要变化、marker清理失败及固定恢复备份保留。

仍缺跨用户目标锁、断电日志、Windows不可变延迟任务/最终状态和生产信任根/真实多平台执行；不标整个更新或整体计划完成。

本批最终源码Windows/Python3.12全套946 passed / 11 skipped（168.45秒，cache/full-update-recovery-20261013.log），覆盖后续源码回滚无数据创建、marker路径拒绝和准确恢复失败事件。git diff --check通过。没有调用真实安装/替换工作区程序、生产密钥或远端发布；工作区仍未提交/推送。Windows延迟脚本路径转义及直接覆盖回退列入下一批，不用本批本地原子复制测试代替该脚本真实执行验收。

### 显式应用与替换失败补偿（2026-10-13）

update apply已接入共享更新服务：源码模式拒绝且不创建运行数据；JSON/无交互需要根级--yes；缓存必须提供明确更高整数版本；真实签名再次验证，不猜版本。Windows结果仅scheduled，其他平台applied，两者均health_confirmed=false。目标旁随机暂存复制、fsync及SHA256校验后os.replace，避免跨文件系统失败时截断当前程序；EXDEV以外错误不降级为直接复制。启动marker使用该原子复制，源码模式不处理marker。旧exe清理仅限本程序精确名称，保留其他程序文件。

更新apply/旧更新/CLI联合141 passed / 1 skipped（59.10秒，cache/update-apply-final-20261013.log）。旧备份失败用例实际因无签名而提前返回，已删除，以真实临时Ed25519签名且断言备份分支确实执行的用例替代；测试密钥不进入生产配置。跨文件系统成功/部分写失败和marker替换失败均操作临时文件，不替换真实程序。

随后Unix同步应用失败/KeyboardInterrupt增加补偿：仅在当前摘要确为本次新产物时，从当前程序保留重试产物并使用未变更的已校验备份原子恢复；外部目标变化不覆盖，恢复不完整记录明确错误并保留备份。正常提交后的备份清理失败不误报应用失败。备份名称增加随机身份避免同秒覆盖。专项15 passed（2.06秒，cache/update-compensation-20261013.log）；之后仅强化成功字节断言，全套结果见后续记录。

仍缺断电日志/恢复ID、跨数据目录目标锁、备份信任清单和Windows异步最终状态/健康确认；marker提交后清理失败语义仍待完善。apply未提供完整总期限；download及完整恢复尚未完成，不标生产验收。当前整体队列见project-health-roadmap.md，官方逐参数缺口见official-cli-completion-plan.md第9节。

本批最终源码Windows/Python3.12全套925 passed / 11 skipped（172.50秒，cache/full-update-apply-20261013.log），包含强化后的完整成功字节断言及Unix取消补偿。git diff --check通过；git ls-files检查plugins、plugin-data、plugin-runtimes、cache、data、logs、.venv、.env/.env.local、config/common.yaml无跟踪项，check-ignore确认典型插件/数据/缓存/虚拟环境/环境文件路径被忽略。本次不等于秘密历史、所有实际文件或构建包扫描完成。工作区仍未提交/推送，远端CI及原生其他平台尚未执行。

### 官方参数计划复核（2026-10-12）

官方CLI计划新增逐功能参数清单，区分当前默认值/范围、新入口及行为缺口，并修正网络批次中HTTP尚未实施的过期描述。新增优先队列：诊断共享预算→写操作事务→脚本契约→显式更新→清理与发布验收。核对范围包含main、network/monitor/WG/XUI/Tailscale/Oh My Zsh及安装配置和插件生命周期入口。

规划复核时XUI相关回归19 passed（7.28秒，Windows/Python 3.12）；当时报告和CLI尚未传递共享预算，不覆盖core.deadline的专项行为。后续实施证据见下条，本次未执行真实安装或生产安全验收。

诊断预算实施：XUI report(timeout=30)现在将剩余预算传给版本/服务/设置/只读SQLite/API，检查前后拒绝晚到成功并停止后续阶段。SQLite busy timeout和progress handler限制锁等待/查询；监听枚举增加一次性spawn执行路径，避免psutil无原生超时长期阻塞，worker不启动系统命令。联合43 passed（13.90秒），追加真实SQLite锁等待回归后13 passed（8.86秒）。仍缺状态文件读取中断、sudo/systemctl子进程树监督及其他四种报告共享预算，因此尚未注册诊断CLI --timeout，不标诊断完整期限完成。

本批Windows/Python 3.12全套835项：824 passed、10 skipped、1 failed（173.19秒，cache/full-diagnostic-budget-20261012.log）。失败是旧HTTP用例在2.5秒spawn预算内未必发出请求；提高预算后发现3秒读取超时先结束完全停滞响应。改为真实持续慢响应，保持读取活跃且不完成，验证5秒总期限仍终止并回收，保留请求已开始和耗时上限断言；监听超时用例也增加实际worker启动标记。最终HTTP/监听/XUI联合28 passed（24.84秒）。最终改动仅测试，未重跑全套，不把此前有失败的全套写成通过；最终全套发布验收仍待完成。

### 诊断状态与命令监督（2026-10-13）

core.process新增有界合并文本capture，复用既有进程树监督；stream从启动命令前计算预算，拒绝无效/非有限期限。core.privilege新增仅sudo -n的诊断capture，XUI服务/版本/面板设置查询采用它；命令非零不把面板设置当成功，版本超时不回退为安装成功。XUI诊断状态在一次性worker读取，只接受普通文件、最多64 KiB，传回所需端口/路径，密码/私钥/链接不进入结果；不修改正常业务load_state协议。

Windows/Python 3.12联合69 passed / 1 skipped（22.45秒），包含真实派生子进程在超时后终止、输出超限关闭回收、无效预算不启动进程、sudo -n命令构造、真实状态文件内容/mtime不变及超大/缺失文件处理。最终源码全套835 passed / 10 skipped（186.23秒），日志cache/full-supervised-diagnostics-20261012.log；也覆盖修正后的慢HTTP总期限测试。仍需其他报告共享预算、文件元数据/非本地文件系统边界、真实Linux sudo及原生打包验收；不以Windows进程树测试代替Linux权限边界验证，不标整个计划完成。

### WireGuard/Tailscale预算与CI（2026-10-13）

WireGuard service查询采用有界capture/进程树监督；server/client报告共用30秒预算，全部隧道先验证后查询。客户端诊断状态采用test -f + cat --的Unix有界监督读取，不交互提权；配置存在性改为test -f/-s，仅查普通文件/大小，不读取私钥。读取命令失败明确抛错，替换成FIFO的竞态仍受命令期限约束。相关联合100 passed / 1 skipped（5.58秒），含跨阶段预算、晚到成功拒绝、不读配置秘密、静默服务命令超时及文件读取失败；真实Unix test/cat用例在Windows跳过，待Linux/macOS CI，不代表原生Unix已验收。

Tailscale普通命令_run迁移到监督capture，保留env/退出状态/超时的已收集输出；诊断版本/状态/服务共用预算，macOS复用同一次状态快照，报告不输出AuthURL。专项与此前模块最终联合68 passed / 1 skipped（11.32秒）。真实派生进程用例曾在2秒内未创建子进程，改为8秒覆盖两次Python启动与Job绑定，并保留实际子进程marker、超时和回收断言；不修改生产期限。此批未重新运行全套，先前835通过早于本批源码变更。

新增.github/workflows/test.yml：PR、develop/main push及手工运行，Windows/Linux/macOS × Python3.10/3.12六组合；最小contents:read权限、20分钟job上限、pytest20秒用例上限、依赖metadata检查和JUnit产物。本地YAML/六组合/权限/命令结构校验通过；尚未推送运行，不能宣称远端绿灯。静态问题门槛、SBOM/漏洞/秘密历史扫描和真实服务部署仍待完成。

共享capture受影响模块追加52 passed（17.30秒），覆盖XUI/网络/版本验证/runner。输出超限现归类RuntimeError运行失败，避免CLI误报参数错误；最终capture/WG/Tailscale专项18 passed / 1 skipped（8.93秒）。原生Unix验证仍跳过，未执行真实系统安装、秘密读取或远端CI。

### 五种诊断CLI统一期限（2026-10-13）

RustDesk report共用30秒预算：普通文件/密钥文件元数据在可终止worker内查询，仅stat不读取密钥；服务查询用capture，非systemd的PID读取有64字节输出限制并拒绝非正ASCII数字，kill -0受同一剩余预算监督。监听检查用一次性worker。XUI严格诊断版本检查不再把版本命令失败的“已安装”回退判为健康；普通安装检测仍保留兼容行为。

Recipe.diagnostic_report及五种实现统一timeout关键字参数；software diagnose NAME、XUI/Tailscale及WG server/client diagnose均提供 --timeout（30秒，0.1..3600）。全部入口传入同一报告，正常成功、超时退出1、取消130、越界参数退出2；不boot或进入菜单。相关原有79 passed（6.28秒）；新增五Recipe/四专用入口的默认/显式预算、超时/取消/无效参数及RustDesk元数据/PID/阶段预算联合54 passed（19.06秒）。最终源码Windows/Python3.12全套878 passed / 11 skipped（167.75秒），日志cache/full-diagnose-timeout-20261013.log，包含WG/Tailscale此前预算、共享capture及XUI严格版本检查。真实Linux/macOS服务、特殊文件系统/进程创建和回收时延、打包执行仍待验收，不宣称硬实时；新增Unix原生命令用例在Windows跳过，其他平台CI尚未远端运行。

### 显式更新只读入口（2026-10-13）

update status/check已接入受监督一次性worker，分别默认10/30秒，可调0.1..3600秒；状态只返回白名单元数据，待更新文件不标为已验证。check复用updater._resolve_update_target，新增显式只读ContextVar：不写缓存、机器标识或遥测，完整放量不创建身份；部分灰度缺已有身份时报错。查询配置读入≤64 KiB、身份≤256字节、原始未压缩元数据≤256 KiB，拒绝无效版本和非HTTPS/含凭据产物URL。网络失败/无匹配产物不冒充无更新；候选版本尚未下载/验证签名，返回signature_verified=false。

最初更新/原生入口联合144 passed / 1 skipped（70.23秒）；只读更新/config/原生入口27 passed（11.55秒），含真实status不建运行目录、缓存内容/mtime不变、秘密不进结果、真实已启动worker超时后回收、完整/部分灰度、实际流式元数据成功和体积/形状拒绝。更新/旧更新服务最终批128 passed / 1 skipped（72.14秒），日志cache/update-query-final-20261013.log；之后追加只读版本/URL验证，最终专项24 passed（6.71秒）。超大响应测试一度因pytest自动参数ID过长触发fixture路径错误，已使用短ID重跑通过；不把该次错误结果记成通过。

本批尚未重跑全套（此前878通过早于更新入口）。download/apply/recovery、共享写锁、完整下载总期限/体积上限、Windows调度状态、原生多平台和真实生产签名仍待实施/验收；没有生成生产替代密钥、替换真实程序或宣称完整更新功能已完成。

### 更新写入体积/路径与共享锁（2026-10-13）

updater下载现限制产物≤1 GiB，检查已有断点、Content-Length及实际写入，Accept-Encoding: identity并拒绝压缩。超限删除本次残包，不覆盖既有pending；304复用与基本格式检查也限制大小。基本格式检查新增Mach-O/Universal头（不代替签名或真实运行验证）。私有暂存不可写时明确失败，移除共享系统temp固定文件及固定.write_test探针；使用随机探针、私有目录权限，拒绝产物路径链接/非普通文件/硬链接。旧T10没有有效验证回退且操作真实共享temp文件，已替换为不可写失败前不触网/不访问共享temp的测试。

版本软件已有可重入线程+OS锁提取为core.resource_lock.ResourceLock，原mutation模块仅保留home资源身份和装饰器；更新下载、签名侧文件写入及_do_apply复用同一暂存区锁，竞争立即失败，正常/异常进程退出OS释放，锁文件不删除。嵌套期间资源身份改变明确拒绝。锁按pending目录定位，尚不等于不同OPSKIT_DATA_DIR/用户对同一程序目标的全系统锁；启动/恢复/Windows异步helper仍需完整生命周期统一，不能标完整更新并发验收。

重构初版软件锁/版本16 passed（1.85秒），旧更新/软件锁/查询联合139 passed / 1 skipped（73.72秒，cache/update-lock-limits-20261013.log）；下载实际块超限、声明大小/压缩拒绝、断点超限、旧pending保留、八种Mach-O头、真实竞争进程/强制退出/嵌套及资源变更等联合31 passed（4.26秒）。移除共享temp回退并补硬链接保护后24 passed（4.46秒）。最终源码Windows/Python3.12全套911 passed / 11 skipped（189.19秒），日志cache/full-update-write-20261013.log，涵盖只读更新入口、本批锁/体积/路径及此前诊断改动。显式download/apply/recovery及总预算/不可变发布事务仍待继续实施；原生其他平台和生产安全不因本地全套通过而标完成。

官方逐功能参数缺口、18 个 Recipe 生命周期及实施顺序见 [官方 CLI 补齐计划](official-cli-completion-plan.md)。其中目标命令不代表已实现入口。

保留官方能力，第三方插件独立仓库。不得以单元测试代替生产发布验收，不生成替代生产身份的密钥，不修改真实用户服务器或清理用户数据。大文件传输与常驻 Worker 池按原计划作为独立后续设计，不通过取消隔离配额实现。

## 本轮本地证据

- 代理/安全边界 20 项通过，网络/runner 15 项通过，真实监督回归 2 项通过。
- CLI/监控 39 项通过；真实 `--output json monitor memory --once` 输出单个可解析结果。
- 监控 CLI 已移到官方模块，支持采样、次数、间隔及进程/接口/挂载点筛选。其他命令尚未全部接入统一输出，不宣称全 CLI 验收完成。
- 网络 CLI 已移到官方模块，补齐次数、期限、跳数、端口范围、并发、DNS 地址族及测速 URL/时长；网络相关联合回归 49 项通过，包括真实本地监听端口和 DNS。
- 端口扫描的在途任务数受并发上限约束，不一次性创建全端口 Future；公网地址查询移除遗留后台请求线程。DNS 系统解析和 HTTP 传输的严格总期限仍需进一步验证，不能将 socket 读超时称为完整操作截止时间。
- 插件生命周期 CLI 已实施；隔离真实 Git 仓库完整安装/指纹授权/启停/更新撤销/删除流程通过。插件与 CLI 联合 68 项通过，统一异常边界后的相关 36 项通过。信任文件写失败不再吞掉；安装记录分支不授予权限；删除拒绝越界目录。偏好和更新 CLI 仍待实施。
- 软件 list/search/installed/versions 增加 JSON，list/search 支持分类和平台筛选；`software capabilities NAME` 对全部官方 Recipe 验证。`--token-file` 支持有界文件/stdin 输入。相关回归 35 项通过。
- `preferences show`、`theme list/set`、`language list/set` 已实施；查询不初始化配置，不导出其他配置或凭据。偏好与软件 CLI 联合 32 项通过；更新 CLI 仍未实施。
- Oh My Zsh/Tailscale 安装配置初版接入，严格校验显式布尔选项；Tailscale 安装与登录分离。管理 CLI 已接入 Tailscale 状态/诊断/登录/断开/重启和 Oh My Zsh 状态/P10k/切换 shell。相关回归 50 项通过，未执行真实系统安装；WireGuard/XUI 向导及管理仍待实施。
- XUI 安装配置和 status/diagnose/traffic/logs/links 已接入，敏感文件独占创建并限制权限；普通状态增加 UUID/link 脱敏。XUI 联合回归 57 passed / 1 skipped，追加不输入的模拟安装服务验证后 XUI CLI 9 项通过。模拟系统副作用不代表真实 Linux 安装验收，WireGuard 仍待实施。
- WireGuard 服务端安装配置及节点 CLI 已接入，菜单与 CLI 复用操作路径；公钥作为稳定 ID，令牌只显式导出。443 冲突前置拒绝、节点失败不报成功、稳定 ID 与敏感导出回归通过；联合 81 项通过。客户端隧道 CLI、完整诊断和真实 Linux 部署仍未验收。
- WireGuard 客户端 status/diagnose 和 tunnel list/add/remove/update-token 已接入；菜单与 CLI 复用服务。删除和整批卸载前拒绝污染标识；无效状态不作为空状态覆盖。安装拒绝配置碰撞，失败清理自身产物；更新失败恢复旧配置、持久化状态和原服务状态。新增失败注入及相关联合回归 102 项通过；尚未实际安装 Linux 服务，完整诊断及并发操作一致性待验证。
- Windows / Python 3.12 全套回归：678 passed / 10 skipped（118.98 秒）。两项旧路径用例改为验证当前 core.paths 标准路径及无 platformdirs 的回退路径，避免 OPSKIT_DATA_DIR 隔离环境掩盖检查。随后增加令牌更新停止旧服务失败的严格检查及回归；WireGuard 相关 70 项通过。全套结果早于最后这条停止失败修复，最终源码的对应模块已重新验证；Linux/macOS、完整 CLI 迁移和真实部署仍未完成。
- 通用软件安装/卸载/升级/切换编排移至 software/lifecycle_cli.py，复用 software.actions，统一结构化结果并把进度送往 stderr。指定名称不启动菜单或应用平台 pending 更新；卸载在无交互模式要求 --yes，参数验证前置。根级 JSON 参数解析失败和意外查询异常也返回单个安全错误，不解析插件业务选项。全部 18 个 Recipe 无效操作拒绝矩阵已验证；安装无法 detect 改为失败。
- 此批 Windows / Python 3.12 全套：708 passed / 10 skipped（116.45 秒）。随后补充意外查询异常边界、令牌解压上限和客户端标签前置验证；最终相关联合回归 134 passed（5.46 秒）。令牌输入和解压内容均限制 64 KiB，压缩扩张测试通过。全套结果早于这些追加修复，追加涉及模块已重新验证；未以此证明实际服务部署。
- 真实子进程检查发现日志 getter 初始化会在 help/版本/无效参数操作时建空日志；文件 handler 改为收到第一条记录再建目录/打开文件，保留轮转与脱敏。4 个真实入口检查确认不创建运行数据，日志事件测试确认仍写入并脱敏；相关联合 154 passed / 2 skipped（9.77 秒）。旧日志测试更新为验证首次事件写入，并隔离/恢复全局 handlers；随后全套 716 passed / 10 skipped（141.40 秒）。
- 命名命令只读初始化本地化，避免跳过 boot 后返回原始文案 key。语言资源按路径/mtime_ns/大小缓存最多 8 份，优先 CSafeLoader，缺扩展回退 SafeLoader；预加载 help 和正式初始化共用入口，文件变化自动刷新，插件文案仍动态叠加。相关 CLI/语言联合 137 passed（13.93 秒）；本批最终源码 Windows / Python 3.12 全套 718 passed / 10 skipped（118.49 秒），包含真实子进程只读及可读文案检查。
- 资源实测见 [性能记录](performance-baselines.md)：异常压缩令牌的 Python 分配峰值从原解压链约 9.85 MB 降到约 0.20 MB，并全部拒绝；语言解析冷读中位 8.103 ms、缓存读 0.444 ms，原 SafeLoader 解析 72.291 ms。均提供复现脚本、输入与环境，尚未作为完整平台性能验收。

## 已发现的后续业务风险

- 版本切换已接入共享失败补偿：基类、Python/PostgreSQL/Redis 保存新快照、应用链接后验证实际 PATH 命令版本；失败/取消恢复旧快照、链接和进程 PATH，恢复失败明确报错。快照写入使用原子替换，损坏内容拒绝作为空状态覆盖。相关 80 passed（4.95 秒），包括真实受监督 Python 版本检查及失败注入；不是本批全套或真实安装验收。仍缺跨进程资源锁、伴随命令验证和实际系统恢复检查，部分驱动内部吞异常需要进一步修复。
- software diagnose 已迁移到 Recipe 只读 diagnostic_report，不再 boot/调用旧菜单；RustDesk 菜单诊断也移除状态写入和连接配置展示。各报告已统一失败 envelope，完整检查深度、诊断总预算和其他菜单服务复用仍待验收。
- 监控六种命令按适用能力注册参数，无关选项（即使传默认值）在解析阶段拒绝；明确未知接口/不可读挂载点错误，保留参数退出码。指定 PID 直接读目标进程，不再枚举和排序最多一万个进程；进程消失/权限不足返回空集合。监控/共享输出/主入口相关 65 passed（5.26 秒），含真实本进程读取、持续模式取消 130 和有界 watch 采样。全套与跨平台结果另行记录。
- 本批最终源码 Windows / Python 3.12 全套 743 passed / 10 skipped（122.54 秒），日志 cache/full-monitor-20261012.log；覆盖本轮监控参数/采样与此前版本切换修改。跳过项目及其他平台、真实部署仍未验收。接下来处理软件写操作跨进程锁：锁必须覆盖 Recipe 直接调用和依赖解析的路径，不能只保护 CLI 编排；版本快照不随 OPSKIT_DATA_DIR 变化，锁身份应与实际资源位置一致。
- 九种版本软件的 install/upgrade/switch/uninstall 在 Recipe 层增加同一把可重入、非阻塞 OS 锁，覆盖菜单、CLI 和依赖解析直接调用；共享 shell/profile 修改也互斥。锁固定于 home/.opskit/locks，不随数据目录覆盖变化；保留锁文件，正常/强制退出由 OS 释放。目录私有权限、链接/硬链接拒绝已接入。88 项相关回归通过（5.22 秒），含真实第二进程竞争、强制终止、同线程嵌套、其他线程拒绝、九种 switch 在读状态前拒绝。Redis 原继承 upgrade 会先卸载全部，现改为保留旧版的 install，相关回归通过。系统级共享路径跨用户互斥、其他非版本软件/服务事务锁及跨平台原生验证仍待完成，不将此项称为全平台并发验收。
- 本批最终源码 Windows / Python 3.12 全套：751 passed / 10 skipped（129.35 秒），日志 cache/full-mutation-20261012.log；测试锁目录独立于真实用户 home，未修改真实安装数据。下一批仍须修复驱动内部吞掉链接/恢复错误、快照删除静默失败及登录/网络总期限。
- 后续文件系统失败语义修复：SnapshotStore.delete 不再吞权限/I/O 错误；共享系统链接先创建临时 symlink，再原子替换，替换失败保留旧入口，拒绝覆盖普通命令文件；卸载归属由目录边界判断替代字符串包含，拒绝误删相似目录。PATH 已有 shim 时移到首位而非继续使用后面的项。基类/Python/PostgreSQL 安装不再吞外层 shim/link 失败，缺安装二进制不作为验证成功。Windows 上旧 Go 验证测试实际因缺 go.exe 提前返回，现补有效分支检查；POSIX 保留真实脚本执行。最终相关联合 116 passed（7.77 秒），包括真实临时 symlink 与失败注入；本批未重跑全套。仍缺安装完整事务补偿、多个伴随链接的成组原子性、驱动内部静默失败及原生 Linux/macOS 验收，不以这些文件系统测试代替真实服务部署。
- Tailscale login 的 --timeout 从初始命令前计算 deadline，start_login 和每次 status 接收剩余预算；等待不重新开始计时，晚到的已登录结果拒绝成功。命令非零明确失败且不回显可能敏感的输出；macOS 不支持的出口发布选项登录前拒绝。最终相关联合 61 passed（3.57 秒），含预算消耗/耗尽不再查询/晚到结果/取消 130 与真实 0.2 秒 subprocess 状态超时验证。本批未重跑全套，真实 Linux/macOS 守护进程、sudo 子进程树及服务副作用仍待验收；不作为整个网络总期限完成证据。
- DNS 正向/反向系统解析移入一次性 spawn 进程，保持系统 hosts/地址族语义；--timeout 默认 10 秒，预算覆盖进程启动和结果等待，消息最大 64 KiB，超时/取消终止、join/kill 回收。入口增加 multiprocessing.freeze_support，打包后的实际支持仍待原生验证。最终 Windows/Python 3.12 相关联合 31 passed（8.56 秒），包括真实已启动的慢解析进程、两秒超时后无 active_children 残留、取消回收、localhost IPv4 和无效期限拒绝；真实 main.py JSON DNS 调用成功。本批未重跑全套。进程启动调度/必要回收耗时不能作为硬实时保证；端口扫描与 ping 的其他解析路径、HTTP 总期限和 wheel/原生跨平台验收仍未完成。
- 批量端口扫描改为 selector + 非阻塞 TCP，最多 concurrency 个在途 socket，不创建扫描线程/Future。数字 IP 不解析，主机名仅一次受监督 DNS，选择首个排序地址；总预算默认 30 秒包含 DNS，单连接默认 0.5 秒，CLI 补 --total-timeout。总预算耗尽不返回部分成功；finally 关闭全部在途 socket。相关联合 32 passed（5.85 秒），包括单连接/总期限、DNS 消耗预算、并发上限、取消无扫描线程以及真实 IPv4/IPv6 行为。新增 scripts/bench_port_scan.py 复现相同 64 个本地绑定但未监听端口、并发 16 的旧/新实现对照；性能数字待独立测量后记录，不预先宣称吞吐或内存改善。
- 本批最终源码 Windows/Python 3.12 全套 778 passed / 10 skipped（148.80 秒），日志 cache/full-network-scan-20261012.log，覆盖文件系统、登录、DNS 和扫描更新；随后新增 CLI 总期限错误用例，网络 CLI 11 passed（1.95 秒），源码未追加修改。独立预热基准：扫描中位耗时旧 2053.647 ms / 新 2043.213 ms，Python 分配峰值旧 116,559 / 新 14,986 字节；耗时近似，不宣称显著提速。方法、p95 和限制见 performance-baselines.md。HTTP、ping 前置解析及原生跨平台验证仍未完成。
- HTTP 公网查询/测速移入一次性进程，与 DNS 共用 network.worker 的有界 JSON/期限/取消回收。公网响应上限 1 KiB，拒绝压缩，全部候选共享预算；测速原始字节计数、最多 5 次跳转/1 GiB，窗口结束返回最后确认的累计字节，无数据失败，菜单事件取消保留已采集速率。最终联合 39 passed（17.80 秒），含真实本地停滞/慢速响应、超时和请求开始后取消、无 active_children 残留、超大 IP 响应回退及下载计数上限。初始 1.5 秒的测试有时在请求开始前耗尽，改为 2.5 秒并保留“服务端已收到请求”的证据断言，未放宽实际预算算法。本批未重跑全套；原生打包/多平台、真实代理/TLS、进程调度和必要回收开销仍待验收，不宣称硬实时。
- ping 前置 DNS 纳入总预算；数字 IP 跳过解析，主机名使用一次受监督解析，把数字 IP 传给命令，DNS 耗尽不启动 ping。不可达 envelope 改为 ok=false/退出 1，保留测量数据。Windows/Python 3.12 最终联合 55 passed（7.40 秒），包括预算传递/耗尽拒绝启动、数字 IP 不解析、真实静默命令超时回收及不可达 JSON 语义；真实 main.py 的本机 ping --count 1 --timeout 5 成功。macOS IPv6 选择 ping6 尚未原生验收；单端口旧 helper 的解析、诊断输出、完整网络/全平台验收仍待处理。本批未重跑全套。
- 通用 software diagnose 改用 Recipe.diagnostic_report，只读且不 boot/应用更新/调菜单；RustDesk/Tailscale/XUI/WG server/client 五种诊断接入模块报告，专用 CLI 复用同一服务。emit_diagnostics 保留失败检查数据，ok=false/退出 1，空检查不算成功。RustDesk 诊断不读/展示密钥内容、不写状态、不查询公网；菜单也展示相同检查项。服务查询加超时，PID 查询拒绝非正 ASCII 数字。最终相关联合 79 passed（3.28 秒），含五种健康/故障矩阵、RustDesk 真实临时文件不变和密钥不泄漏、污染 WG 标识在全部校验前不进行系统查询。当前报告主要检查文件/服务基础状态，不能代替监听/API/TLS/路由/连通性完整验收；诊断总期限及跨平台部署待补齐。
- 本批最终源码 Windows/Python 3.12 全套 802 passed / 10 skipped（162.06 秒），日志 cache/full-diagnostics-20261012.log，覆盖 HTTP、ping 与只读诊断。随后补未知诊断名称真实入口的无写入检查，原生只读入口 6 passed（4.91 秒），未追加源码修改。全套并非生产安全认证；支持平台真实服务、诊断深度/总期限、更新 CLI、依赖/发布/清理计划仍待完成。
- RustDesk 补 TCP 21115/21116/21117、UDP 21116 的本机监听和实际可执行文件归属检查；其他程序占同端口不能通过。core.diagnostics 只读取 psutil 本机连接/进程表，单次枚举按协议/端口建索引，同 PID 只查询一次映像；拒绝无效检查参数，权限不足/无可见性明确失败而非证明关闭。删除 RustDesk 诊断迁移后唯一未用的 print_warning import。最终相关联合 49 passed（5.53 秒），包含真实本进程 TCP/UDP 监听、错误归属、非 LISTEN TCP、权限拒绝、PID 查询次数及只读/CLI 回归；Windows venv 启动器不等于实际进程映像，原生用例以实际映像校验。本批未重跑全套，未部署真实 RustDesk Linux。配置/可选 WebSocket/总期限、外部防火墙/公网可达性及产物信任仍需独立验收。
- XUI 报告补服务状态、受验证的 state 端口/路径、实际面板配置比对、面板监听及程序归属、SQLite VLESS 入站启用/REALITY 配置和 loopback CSRF API。数据库用 mode=ro 并显式 close，不创建缺失数据库/写日志；API 固定 127.0.0.1，不使用 state 远端 host，不使用环境代理、不跟随重定向、拒绝压缩/超过 64 KiB 响应，进程预算默认 3 秒。报告只返回布尔检查，不输出凭据/token/数据库内容。联合 53 passed / 1 skipped（9.65 秒），包括真实只读 SQLite、禁用/错误安全配置、实际 loopback API、超大响应和重定向拒绝。随后规范直接 API helper 的路径前缀，最终 XUI 诊断 10 passed（7.23 秒）。本批未重跑全套；CSRF 响应不等于认证 API、REALITY 握手或 TLS 证书验证，Xray 入站监听归属、完整参数匹配、诊断总期限及真实 Linux 安装仍待完成。


## 2026-10-13 目标级版本权威与官方参数计划复核

- 原故障可复现：同一旧运行进程从数据目录A应用较新签名版本，再从目录B应用较低签名版本，旧APP_VERSION判断允许降级。现增加程序目标旁共享版本/摘要记录，在目标锁内校验实际程序摘要和运行build，旧进程必须重启；授权回滚独立记录反向转换。
- Windows延迟helper提交前严格核对绑定prepared事务，过期任务或损坏记录在transaction_validation阶段拒绝；实际替换后写applied。记录是本地私有状态，不是发布签名，也不构成完整断电恢复验收。
- 专项：tests/test_update_target_state.py、test_update_apply.py、test_windows_update_job.py共64 passed（29.00秒），日志cache/update-state-regression-20261013.log。随后补损坏记录的相同摘要拒绝测试。
- 兼容检查：157 passed / 1 failed / 1 skipped（65.79秒），日志cache/update-authority-final-20261013.log。失败T18使用相同字节模拟两个不同版本，新门槛正确拒绝；只改旧程序测试字节，保留真实签名验证。修正后T18单项1 passed（0.83秒），日志cache/marker-distinct-build-20261013.log。第一次全套运行在修正前已收集旧fixture，因此其T18结果仍需据实记录，不能当作修正后全套通过。
- 官方参数计划增加第11节执行清单：新增update download；先定义恢复事务再增加rollback；服务事务优先；明确DNS/monitor组合；插件prepare/fingerprint契约及依赖锁；真实CI和发布门槛。已有参数和目标参数明确分开。
- 尚未验证：真实多账户权限、断电/失联任务恢复、marker恢复凭据统一、任务配额清理、生产签名、原生打包、实际Linux/macOS服务和远端CI。既有提交aa9b035与本次未提交工作区分开，不把本地修改当已推送。

- 第一次全套：973 passed / 1 failed / 11 skipped（186.63秒），日志cache/full-target-authority-20261013.log。唯一失败为运行开始时已收集的旧T18 fixture；修正单项已通过，正在以修正后的源码重新执行全套。保留该失败记录。

- 修正后最终源码Windows/Python 3.12全套：974 passed / 11 skipped（184.05秒），日志cache/full-target-authority-fixed-20261013.log；git diff --check通过。此结果覆盖目标级版本记录、Windows事务绑定和相同摘要拒绝。TEMP/TMP仅在测试进程重定向到F盘私有忽略目录，避免先前C盘空间不足造成的临时输出失败；不删除未知系统临时文件。11项跳过、远端CI、真实服务和发布信任仍未验收，全项目目标仍未完成。


## 2026-10-13 显式签名下载与候选发布

- 新增core/update_download.py和update download --timeout（300秒，0.1..3600），源码模式拒绝；复用既有来源/rollout策略和下载器。后台更新迁移到同一服务，删除旧后台下载/签名/发布的第二套编排。
- 主进程持有固定缓存根的OS暂存区锁；worker在随机私有update-candidates/ID内下载、真实验签、复核摘要和持久化，最后原子发布缓存candidate_id/pending_version。getter解析严格UUID指针，应用/启动/status共用；锁身份不随候选路径变化。Windows启动失败marker继续写固定缓存根。
- 总预算覆盖查询、传输、签名和发布；迟到阶段不发布。提交附近超时/取消可能已有完整候选但响应丢失，明确要求查询status，不谎称必然未提交。签名/checksum响应拒绝压缩、最多4KiB；重定向最多5次且请求目的地前检查HTTPS/无URL凭据。下载worker禁用原下载器原始日志及遥测，错误只含安全阶段。
- 最初10项新回归中1项失败：Windows只读文件句柄fsync不适用，改为可写句柄后10项通过。扩大回归时另一个测试在构造gzip响应时由httpx提前解压失败，改用原始ByteStream验证读取前拒绝，未放宽生产检查。
- 指针/旧更新流程199 passed / 1 skipped（70.32秒），cache/download-pointer-regression-20261013.log。扩展后的真实签名、应用重验、状态读取、进程超时、TLS重定向和旁车限制联合142 passed / 1 skipped（76.86秒），cache/download-integration-final-20261013.log；随后新增实际开始下载后的取消用例，最终全套另记。
- 未验收：原生打包spawn、正式发布签名、真实代理/多平台、候选配额/受控清理、跨调用续传、失联/断电日志和完整marker恢复。保留旧候选/失败暂存供后续受控清理，不擅自删除用户目录；不以本批通过宣布全项目完成。

- 本批最终Windows/Python 3.12全套993 passed / 11 skipped（213.46秒），日志cache/full-signed-download-20261013.log，包含19项新下载测试和真实下载开始后的取消回收。真实main.py --output json update download --timeout 2在源码模式退出2、返回单个JSON错误、stderr为空且未创建隔离数据目录；git diff --check通过。只在测试进程将TEMP/TMP定向到F盘私有忽略目录。全项目目标仍未完成，下一批处理候选/异步任务配额与恢复。


## 2026-10-13 候选缓存配额、worker锁与Windows任务位置

- 上轮有实际竞态：根锁由父进程持有，父进程异常退出而worker可能仍运行；现转为实际下载worker持有固定根OS锁，低层独立暂存锁仍单独使用。worker锁取得/配置/下载/清理均受父进程总预算监督。
- 新增core/update_cache.py。新分配前按有界库存回收平台拥有且未引用的preparing/超额旧候选；最多4条目，总字节2GiB+1MiB，预留最大1GiB产物+128KiB元数据。保护active candidate_id及startup marker引用；只处理普通单链接白名单文件，不递归删除。未知/未拥有内容计入额度并保留，损坏记录/链接/超大库存拒绝清理；不足则拒绝新下载。candidate.json在开始下载前记录preparing，便于超时或崩溃后证明所有权。
- 调用链发现Windows schedule仍按pending.parent创建update-jobs和helper锁；新候选使其嵌套且status找不到任务。现统一cache根update-jobs及根锁。真实应用签名门槛和调度产物生成（只模拟进程启动，不执行字节文件）确认status能读取任务；源候选被清理后独立job快照仍完整。
- 配额/下载37 passed（18.23秒），cache/candidate-quota-20261013.log，含活跃/marker保护、字节/目录上限、重复下载、未知内容、损坏记录、symlink/hardlink拒绝、unlink失败保留和锁竞争。
- 扩展联合113 passed / 1 failed（54.93秒），cache/quota-native-job-integration-20261013.log。唯一失败是新测试遗漏Path导入，补齐后真实孤儿worker与Windows候选调度两项2 passed（4.53秒），cache/real-orphan-and-job-20261013.log。孤儿用例确实由独立启动器os._exit退出、child持续下载持锁；校验PID/创建时间后仅终止自身测试child，再回收preparing，旧pending及缓存未变。
- 未验收：异步job配额/清理、失败启动索引、失联/断电恢复、marker凭据统一、旧嵌套job迁移、真实多账户和原生跨平台发布。未知旧目录保留，未对实际用户数据执行清理。完整目标仍未完成。

- 第一次完整回归1011 passed / 2 failed / 11 skipped（214.62秒），cache/full-candidate-quota-20261013.log。两条旧断言仍查pending.parent/update-jobs或要求脚本引用源pending，均与固定根/独立快照设计冲突。测试迁移为cache根任务位置、快照字节、目标Unicode路径及脚本不引用原pending；未降低签名门槛或删除失败断言。正在以迁移后的测试重新执行全套。

- 本批最终Windows/Python 3.12全套1013 passed / 11 skipped（213.73秒），cache/full-candidate-quota-fixed-20261013.log；git diff --check通过。覆盖候选配额/引用保护、真实孤儿worker锁生命周期、固定根Windows调度、快照独立性及旧流程迁移。检查另确认launch_failed已有独立结果文件，但cache.apply_job只在schedule返回后写入，启动失败索引仍待下一批修复；不把候选配额完成等同于job配额/恢复完成。完整项目、原生发布及跨平台仍未验收。


## 2026-10-13 Windows任务启动索引、阶段日志和健康归属

- schedule在共享缓存根锁内创建preparing所有权记录，快照/脚本完成后写launching及严格原子cache.apply_job索引，再Popen。移除_apply_windows成功后静默_save_check_cache索引的旧路径；_apply_windows本身也持锁覆盖失败marker。
- Popen失败已有launch_failed结果且索引可查。Popen成功后scheduled记录写失败保留已索引launching，不谎称启动失败、不自动再创建重试marker。失去failure结果时也只显示launching意图，不误报scheduled。所有阶段日志不回显传输/启动原始异常。
- job.json新增schema_version/job_id/target/parent_pid及摘要、可用helper_pid，status绑定新记录的job_id并只返回白名单。helper健康记录增加同一job_id，confirm_health保留。健康确认要求整数build、confirmed=true和同job_id，明确失败阶段不确认；旧版本号匹配回退移除。
- 阶段初版联合98 passed（49.04秒），cache/job-launch-index-20261013.log。新增失败矩阵与原生helper/CLI联合46 passed（31.40秒），cache/job-journal-failure-matrix-20261013.log。
- 绑定健康与apply/download联合110 passed（55.20秒），cache/job-journal-bound-health-20261013.log，覆盖真实PowerShell提交后写job_id及confirm保留；prelaunch快照/脚本/意图/索引失败不启动且旧索引不变；postlaunch写失败无重复marker；Popen失败结果可查、结果写失败保持不确定。随后增加旧记录不能凭同版本确认的用例并收紧全部任务健康绑定；最终全套另记。
- 未验收：helper创建时间/强进程身份和存活、失联恢复、job配额/受控清理、完整marker凭据、断电日志、生产签名和原生跨平台。scheduled/launching/helper_pid不作存活或完成证明；全部操作在隔离测试目录，未运行假二进制或用户插件。

本任务阶段/健康归属批次最终Windows/Python 3.12全套1026 passed / 11 skipped（219.74秒），cache/full-job-launch-journal-20261013.log；git diff --check通过。覆盖现代与旧任务健康绑定、启动阶段失败和真实helper行为。异步任务配额、强进程身份、失联/断电及原生跨平台发布仍未验收，全项目目标继续进行。


## 2026-10-13 Helper启动确认与精确进程身份

- helper在实际启动后、parent_wait前写running.json，包含schema/job_id/version/PID、创建FILETIME ticks、PSCommandPath与MainModule.FileName；启动前job.json记录预期helper_executable。未以Popen返回或缓存PID冒充存活。
- 新增core/update_jobs.py只读身份probe，原生OpenProcess/GetProcessTimes获取精确创建ticks；校验ACK任务/版本/类型/绝对路径，实际映像及argv真实-File参数，并在读取后复核创建身份。拒绝权限/缺确认返回unknown，复用或不匹配返回identity_mismatch，不发送进程信号；进程消失返回exited。status在原worker预算内调用并新增helper_status，不暴露PID/创建时间/路径/argv，不修改调度或事务结果。
- ACK初版与已有native/CLI/journal48 passed（32.08秒），cache/helper-ack-regression-20261013.log。扩大身份与原生矩阵68 passed（35.15秒），cache/helper-native-identity-20261013.log。
- 原生用例确实启动本机PowerShell helper、等待自身创建的Python父进程：ACK创建ticks与GetProcessTimes完全相同；真实status观察alive，终止自身helper后观察exited，state仍scheduled（不猜结果），目标未改变、无health/result。测试finally终止/等待自身两个进程，未运行测试字节程序或用户插件。
- 其他用例覆盖一个tick的PID复用、查询中复用、缺ACK、PID不存在、权限拒绝、错映像/脚本/子串argv、布尔/非法ACK及只读不变。全套结果另记。
- 未验收：job额度/受控清理、失联事务恢复、完整marker凭据/断电日志、生产签名与原生跨平台。probe只证明观察时身份；不同平台/无法确认权限不推测存活，更不从exited直接推导失败。完整目标继续进行。

- 第一版全套Windows/Python 3.12：1046 passed / 11 skipped（230.24秒），cache/full-helper-identity-20261013.log。随后复核把ACK/进程路径比较改为纯字符串规范化，不对缓存声明调用Path.resolve；新边界检查与原生存活/退出用例21 passed（7.29秒），cache/helper-lexical-paths-20261013.log。该耗时以实际日志为准，最终源码全套另行记录。

- 本批最终Windows/Python 3.12全套1047 passed / 11 skipped（226.67秒），cache/full-helper-identity-lexical-20261013.log；git diff --check通过。覆盖精确进程身份、真实存活/终止和缓存路径纯字符串比较。路线图已合并过时的重复更新阶段说明，保留完整8类交付/验收要求，历史证据继续保留在本台账。job额度/失联恢复、断电日志、生产信任及原生跨平台仍未验收，整体目标未完成。

## 2026-10-13 Windows任务配额与官方参数最终排期

- 新增update_storage共享有界库存、链接拒绝与身份复核删除，候选模块复用；update_job_cache在根锁内预留8条目/2GiB+1MiB预算，调度在创建任务/启动前预留。schema2记录backup_id/transaction_id，status兼容schema1。
- 只在配额不足时清理无目标事务且明确未启动preparing或无目标事务且确认helper退出的失败记录；活动索引、health/marker及备份引用保护。旧/未知内容计入预算但保留，成功/恢复不完整/无ACK/身份不明/活跃/有事务失败记录保留；空间不足拒绝调度。删除非递归、所有权记录最后，库存验证完成后才清理。
- 基础44 passed（35.10秒），cache/job-quota-foundation-20261013.log；前版联合97 passed（48.17秒），cache/job-quota-matrix-20261013.log。收紧有事务失败记录保留，新增真实schedule配额拒绝检查后专项35 passed（7.57秒），cache/job-quota-final-focused-20261013.log。helper状态分支使用probe替身，真实进程证据仍为前批原生身份测试；未声称已验证真实失败任务退役。
- 官方参数计划新增第13节，明确下一轮6个交付批次；第9节逐参数和第3节18个Recipe表仍为完整覆盖依据。不添加无实现参数。
- 未完成：失联/断电目标事务恢复、成功任务退役、旧任务迁移、apply总预算、多用户/原生发布、正式签名和全部平台验收。当前修改未提交；整体安全与发布未达完整验收标准。

- 最后收紧：preparing即使尚未启动，只要有目标transaction_id也保留恢复证据。最终配额专项36 passed（7.71秒），cache/job-quota-transaction-pins-20261013.log；最终更新联合100 passed（67.35秒），cache/job-quota-final-integrated-20261013.log。全套收集时仍为此前35项版本，因此下方全套结果须与这100项最终源码回归共同阅读，不把全套结果标成最终36项版本。

- 本批全套Windows/Python 3.12：1082 passed / 11 skipped（269.48秒），cache/full-job-quota-20261013.log；最终源码另经上述100项联合回归验证。git diff --check通过。TEMP/TMP仅对测试进程设置到F盘忽略缓存，无用户插件/真实安装操作；本地测试不替代跨平台或生产验收。

## 2026-10-13 索引任务摘要恢复与备份引用保护

- 新增update_reconcile：缓存根/当前目标锁内，只处理索引schema2任务；当前可执行路径是唯一目标来源，不resolve缓存声明。核对同一upgrade事务/build/摘要、普通单链接且有界的备份与侧车，helper须确认exited；明确未启动preparing无ACK/结果只能关闭未提交事务。新字节补applied及health，旧字节关闭aborted；未知/权限不足/存活/复用/坏备份/替代事务拒绝或deferred。target_state增加aborted并拒绝其新摘要版本权威；旧helper严格prepared检查不能通过已关闭事务。
- 恢复凭据独立recovery.json并绑定摘要、事务、任务和版本；status优先使用有效恢复凭据，可解释损坏/丢失helper结果，输出reconciled=true，不输出路径/PID/摘要。已有同任务健康确认和失败次数不重置，同版本其他任务健康不覆盖。重试不会替换程序或删除备份。
- update reconcile --timeout默认30秒/0.1..3600，源码退出2且无运行目录初始化。监督worker自行持锁，取消/超时释放；启动在其他更新写入前以相同30秒核对，未解决则暂停自动回滚、重试和post-update清理。新preparing记录在快照之前持久化两个摘要，便于证明未启动事务。
- 备份清理改为缓存根锁内保护cache/health/marker及所有schema2任务引用；旧/未知任务保留全部，损坏引用拒绝。备份库存256/任务64/任务内文件16有界，删除前复核身份、非递归；JOB_FILES在update_jobs统一维护，候选/任务/备份复用存储检查。
- 初版联合93 passed / 1 skipped（27.21秒），cache/job-reconcile-initial-20261013.log；skip因PATH未列Windows PowerShell，改用现有系统绝对路径后真实ACK用例1 passed（1.65秒），cache/reconcile-native-actual-20261013.log。
- 原生扩展初版1 failed / 164 passed（74.51秒），cache/job-reconcile-native-matrix-20261013.log；测试手动生成脚本但漏建生产schedule会创建的根锁父目录，实际失败在resource_lock，没有到预期提交阶段。补齐隔离测试目录并增加artifact_validation阶段断言，避免错误路径被当作验证成功；故障复核日志cache/reconcile-native-failure-debug-20261013.log保留。
- 扩展矩阵35 passed（19.17秒），cache/reconcile-complete-matrix-20261013.log；含真实PowerShell原子替换后health目录障碍导致applied_recovery_incomplete、移除测试障碍并模拟结果丢失后重建记录；真实坏artifact拒绝后根据旧目标关闭事务。执行的只有已生成平台helper，测试字节程序和用户插件未运行。
- 启动联合301 passed / 3 skipped（137.64秒），cache/reconcile-boot-integrated-20261013.log；后续补损坏结果优先恢复凭据与真实spawn超时释放锁，相关64 passed（39.46秒），cache/reconcile-final-focused-20261013.log。最后收紧恢复凭据schema2归属、备份引用记录阶段/版本/事务类型和目录排除，最终源码全套另记。
- 文档去掉官方参数计划第11/12节累积的过时阶段数字，当前能力与剩余队列集中展示，历史检查只保留此台账。未完成范围：未索引/旧任务与无ACK启动意图、任务退役、marker完整凭据、apply总期限、真实断电、多用户、正式签名和跨平台原生发布；完整目标继续进行，当前修改未提交。

- 最终源码Windows/Python 3.12全套1125 passed / 11 skipped（265.71秒），cache/full-reconciliation-20261013.log；含本批42项新增恢复/引用/CLI/启动/超时检查及真实PowerShell恢复路径。git diff --check及新增更新模块空白检查通过，测试TEMP/TMP仅进程内指向F盘忽略缓存。没有将本地回归或模拟故障替代真实断电、发布信任或多平台验收。
- 下一批还需统一所有健康记录写入口的根锁：当前reconcile/helper已持锁，但confirm_health/_check_health现有读改写未统一锁，需验证跨进程并发不会丢失失败次数/任务归属。此项是调用链复核发现，当前42项验证中的健康保留为串行证据，不宣称并发健康写入已解决。

## 2026-10-13 健康记录并发、重放与恢复证据

- mark_update_applied、confirm_health、启动计数/回滚判断与helper/reconcile统一缓存根锁；不存在健康文件时不初始化运行数据。共用健康记录类型检查，拒绝布尔/字符串build、非法确认/计数和job_id，不回显原始异常/私有内容。reconcile复用检查，既有同版本其他任务用例调整为完整有效健康记录，仍验证不覆盖。
- 修复当前build不一致时直接unlink健康记录：不同运行版本只读保留。回滚成功也保留健康及backup/job引用，避免原子恢复已提交但恢复记录未完成时丢失证据。两项旧测试按新保留契约迁移，不删测试。相同build重复发布保留confirmed/fails/job_id；不同备份归属或更高build记录拒绝覆盖。
- ResourceLock新增ResourceBusyError区分默认RuntimeError锁的真实竞争与路径/文件/其他OS错误；自定义error_type行为保持。Windows字节锁仅EACCES/EAGAIN/EDEADLK作为竞争，其他errno原样失败。后台健康确认移到updater.confirm_health_after，main只调用；健康延迟后默认360秒monotonic重试竞争，每次重读，不重试损坏/不安全数据，窗口到期保留记录。此期限是竞争窗口，不宣称文件I/O硬实时。
- 新test_update_health_lock真实spawn进程暂停在健康读取之后；父进程验证实际OS锁仍被持有、其他确认/计数/发布入口安全拒绝；释放后核对原记录归属/计数与新build发布，旧runtime不更改新health。真实子进程持根锁发布新计数，后台观察真实竞争、释放后重读并确认；另验证竞争超时、非竞争errno不重试、重放保留、坏记录不泄密、回滚决策持锁且证据保留、清理失败不撤销确认。
- 初版联合1 failed / 242 passed / 3 skipped（127.51秒），cache/health-lock-initial-20261013.log；真实check用例初始fails=1已达到项目MAX_HEALTH_FAILS=2回滚阈值，按既有失败回滚契约保持旧计数，测试原先错期望增为2。调为fails=0验证实际普通启动计数至1，保留专门回滚持锁用例；debug日志cache/health-check-competition-debug-20261013.log保留。
- 修正后健康/恢复联合63 passed（46.26秒），cache/health-lock-final-focused-20261013.log；新增坏JSON及非竞争原生errno分类后，最终23项专项passed（6.74秒），cache/health-lock-final-matrix-20261013.log。最终完整源码全套另记。
- 未完成：跨数据目录/多目标健康归属，回滚后的索引与坏候选处理，任务退役、未索引/旧任务及marker完整恢复，apply总期限、真实断电、生产签名和全部平台原生验收。健康持锁不等于全部更新或完整项目已安全验收；当前变更未提交，完整目标继续进行。

- 最终Windows/Python 3.12全套1148 passed / 11 skipped（274.13秒），cache/full-health-lock-20261013.log；git diff --check通过。该全套覆盖最终23项新增健康锁/重放/实际进程竞争及错误分类检查；未执行未知插件或测试二进制。

## 2026-10-13 本地wheel库存与安装入口核对

- 从git当前跟踪与未忽略新文件创建344文件的独立源码快照cache/wheel-health-v47ickfo/source，拒绝源码链接，不复制.git/忽略插件和运行目录。原工作区与真实用户数据未修改。使用python -I -m build --wheel --no-isolation构建opskit-1.0.0-py3-none-any.whl；cache/wheel-health-build-20261013.log成功。-I避免仓库build.py遮蔽PyPA build模块，不是删除平台原生构建脚本；setuptools许可元数据弃用警告仍在日志，尚未迁移。
- wheel共230条目；核对8个官方包全部Python源码、main及core的locale/themes/mirrors YAML，共225项应含文件均存在；console_scripts为opskit=main:app。未含cache/data/logs/plugins/plugin-data/plugin-runtimes/venv/tests/__pycache__/.env或config/common.yaml。inventory.json在上述快照根。初次手工校验误写plugin/sandbox.py这个并不存在的源文件，随后依据实际core/plugin_sandbox.py及完整源码清单重查，没有真实模块缺失；不把手工路径错误当打包缺陷。
- 在快照根verification-env新建无系统site-packages虚拟环境、无依赖/无索引安装该wheel；通过显式pth复用现有.venv依赖目录，不是全新依赖解析/下载验收。实际Scripts/opskit.exe在outside-source目录运行：--help、JSON software list、monitor cpu --once、update status --timeout10均退出0并可解析；源码模式update reconcile退出2。main/core.updater来自安装site-packages，指定runtime-must-remain-absent目录未创建。verification.json和各stdout/stderr/install.log在快照根。
- 此证据只覆盖本地wheel库存与上述只读/拒绝入口，不涵盖全部软件实际安装、插件prepare/run容器、源码之外其他SDK资源、Linux/macOS、干净依赖解析、Nuitka/PyInstaller正式产物或生产签名。CI远端六组合、供应链hash锁/漏洞/SBOM及历史秘密扫描继续待验收，完整发布门槛未完成。
## 2026-10-13 回滚任务退役与持久化升级下限

- 目标记录持久化upgrade_floor；后续升级build须严格高于历史最高回滚build，后续升级准备中止不清除下限。check只读过滤被拒绝build，download传输前拒绝，apply备份前及事务prepare复核。终态重放核对完整事务与实际摘要，同事务ID不能替换其他字段；已恢复字节的回滚重试须有对应反向事务，仅补完成凭据不再次复制。
- 新增update_retirement，在根/目标锁内且helper确证退出、目标/备份/反向事务与健康归属一致后，在job.json先持久化退役证明，再将匹配apply_job转为last_apply_job。status报告rolled_back/active=false/restored_version。删除载荷失败保留最后所有权文件，历史/健康/marker及备份引用继续保护；无证明、其他活动索引、未知内容、旧任务或不确定helper均不清除。
- 初版联合1 failed / 195 passed（92.28秒），cache/rollback-retirement-initial-20261013.log；合成ACK漏了真实身份协议的PID/脚本/映像字段，修复测试fixture，不放宽生产身份验证。原生扩展77 passed（32.00秒），cache/rollback-retirement-native-20261013.log；真实PowerShell提交隔离字节、实际回滚并退役，历史status读取通过。未执行测试字节程序或用户插件。
- 升级下限及退役整合217 passed（110.52秒），cache/rollback-floor-retirement-final-20261013.log。该次收集早于最后check候选过滤及新增只读check用例，不能独立作为最后源码验收；最终源码全套另记。
- 官方参数计划第14节明确逐功能交付模板，第3/9节覆盖18个Recipe和逐参数，第13节保持执行顺序；同步更新路线图、CLI契约和发布安全边界。
- 尚未完成：其他成功/失败终态及未索引/旧任务退役，回滚prepare后仍为新字节的启动恢复，无任务Unix回滚的自动最终化，跨数据目录健康归属、marker完整恢复、apply总期限、真实断电/多用户/正式签名/跨平台发布。前批wheel早于本次源码，须重新构建，不能宣称当前包已验收。全项目目标仍未完成。

- 最终源码Windows/Python 3.12全套1177 passed / 11 skipped（288.20秒），cache/full-retirement-final-20261013.log；包含最后只读check过滤用例、持久化升级下限及真实PowerShell回滚退役。源码在本次收集后未改动；期间仅同步文档。git diff --check通过，TEMP/TMP仅测试进程指向F盘忽略缓存。跳过项及本地平台限制不作为其他平台通过证据；本批后续修改尚未提交。
## 2026-10-13 插件prepare/fingerprint生命周期契约

- 将两个命令从main迁移至plugin/lifecycle_cli，删除旧注册/实现；插件业务run及参数透传未改。prepare返回image/prepared/authorized=false，不授予信任；参数错误2、运行错误1、取消130，拒绝链接/不存在根目录，稳定错误不回显私有Docker/pip异常。fingerprint不boot，生成统一结果，check只读；missing/mismatch返回失败envelope并保留status。
- core/plugin_runtime拒绝选项/换行/带凭据URL等非法镜像引用，校验构建后image ID；原生子进程构建输出送stderr。临时测试平台上下文核对仅Dockerfile/requirements/core.worker，排除私人文件及pytest，FROM固定实际image ID。尚未强制来源digest或hash锁，日志也未完成受控脱敏、构建进程树监督，不能声称供应链已完成。
- 第一条检查命令误引用不存在的tests/test_plugin_cli.py，0 collected且未执行测试，cache/plugin-contract-final-20261013.log保留；改用实际文件后88 passed（20.66秒），cache/plugin-contract-verified-20261013.log。增加运行时边界与原生stderr检查后94 passed（19.54秒），cache/plugin-runtime-contract-20261013.log。
- 注册/输出/原生只读/启动相关35 passed / 2 skipped（5.69秒），cache/plugin-registration-native-20261013.log。最后新增独立Python CLI进程实际生成/校验隔离插件清单，恶意入口只作为不执行哨兵，检查字节不变且无平台运行目录；最终联合结果另记。
- 本批没有运行用户插件、Docker构建或真实容器；Docker接口使用替身，stderr验证实际启动临时Python子进程，不能替代真实Docker日志/取消与恶意插件隔离验收。完整目标与8类路线图继续，后续修改仍未提交。前批1177项全套早于本次修改，应与本批最终源码相关回归区分。

- 最终源码相关联合130 passed / 2 skipped（29.02秒），cache/plugin-final-integrated-20261013.log；覆盖插件生命周期、上下文/镜像边界、原生fingerprint进程、既有插件/服务/主CLI及输出/启动行为。git diff --check通过；测试仅临时数据，无用户数据修改。本批未再宣称最终全套通过，前批全套结论仅属于其源码时点。
## 2026-10-13 插件构建日志与客户端进程树监督

- 复用core.process.capture/stream，增加文件stdin透传（默认保持原有继承行为）。构建上下文放在自有TemporaryFile，通过客户端stdin读取，不引入后台写线程。镜像查询每次30秒/4KiB，构建客户端600秒/4MiB；替换原直接subprocess.run/check_output，包括此前未设期限的最终镜像查询。返回码失败使用固定错误，不附原始日志。
- 公开stderr只输出固定进度。Docker/pip原始输出留在有界私有捕获中并丢弃，不再直接输出；secret原始异常不进入CLI结果。查询/构建/最终验证失败或取消均不发布成功catalog，文件stdin由调用方关闭，监督器不夺取文件所有权。
- 初版相关34 passed（16.00秒），cache/plugin-build-supervision-20261013.log。加入实际创建子进程后的注入KeyboardInterrupt及输出超量回收，最终相关54 passed（18.71秒），cache/plugin-supervision-final-20261013.log，含既有runner、diagnostic_capture和security_boundaries。
- 原生测试实际启动临时Python客户端及其子进程：静默超时、读取ready后的注入取消、超量日志分别回收；核对PID+创建时间（Unix允许已终止zombie）。真实二进制文件stdin以SHA256验证输入正确且源不变；非零错误及凭据日志不进入stdout/stderr；有界上下文内容仍经过验证。
- Docker API用替身，未实际构建容器或运行用户插件。仅证明本地客户端树清理，不证明daemon/BuildKit内部任务、缓存或镜像产物已清理；prepare完整总期限、hash锁、来源digest和真实多平台容器攻击验收仍未完成。当前主机无原生docker命令，仅发现wsl命令，不能据此推断WSL内daemon状态。最终完整源码全套另记。

- 对当前配置选择的Docker客户端只读执行version查询（不构建、不运行容器），经core.process.capture监督10秒/8KiB；返回available=false/error_type=TimeoutExpired。观察结果只证明该次期限内未取到server响应，不能推断daemon不存在、未安装或不安全。真实Docker验收暂无可用响应证据；依赖锁及其他代码任务仍可推进，整体目标不因此标记blocked。

- 最终源码Windows/Python 3.12全套1196 passed / 11 skipped（324.35秒），cache/full-plugin-build-supervision-20261013.log；含本批最终原生超时/注入取消/超量客户端子进程回收、文件stdin及安全输出，覆盖共享process改动的其他调用方。收集后源码未改动，仅同步文档；git diff --check通过。TEMP/TMP仅测试进程指向F盘忽略缓存。全项目发布、实际容器安全和依赖锁仍未完成，当前后续修改未提交。
## 2026-10-13 来源digest、完整wheel哈希锁与SDK依赖分离

- 新增core/plugin_lock：--lock-file必填，版本与SHA256严格解析/规范化，允许注释及续行；拒绝URL/index/include/extras/markers、重复规范化包名、空/未完成项。最多512包、每包256hash、文件1MiB/单项32KiB；OS读取前后核对普通单链接文件/身份，拒绝链接/硬链接/FIFO等特殊文件。SDK四项根依赖必须存在，--dependency只核对锁内版本，不追加未哈希包。来源--base-image必须带真实registry digest，标签不再接受。
- 移除平台requirements和wheel元数据宽泛依赖回退；运行时由SDK根rich/PyYAML/typer/platformdirs及锁内明确包组成。pip使用isolated/ignore-installed/require-hashes/only-binary/固定HTTPS PyPI索引，未用no-deps掩盖传递依赖。旧policy2拒绝；policy3 label及catalog绑定base_reference/规范化锁SHA256/image ID/插件指纹，构建中插件变动拒绝发布。读取catalog有界、普通单链接及类型验证，不从旧记录推测供应链已验证。
- scripts/build_plugin_runtime复用平台CLI，移除第二套参数/直接异常输出；help与必填锁/digest一致。增加输入示例docs/examples/plugin-runtime-requirements.in，示例不是锁，不含虚构生产哈希或镜像digest；操作方须为目标平台生成并审查实际锁。
- 初版47 passed（16.91秒），cache/plugin-hash-lock-initial-20261013.log；多平台hash与OS边界/已有插件联合146 passed / 1 skipped（33.27秒），cache/plugin-hash-lock-integrated-20261013.log。增加catalog类型/硬链接/超量后149 passed / 1 skipped（33.34秒），cache/plugin-hash-lock-final-20261013.log。指纹竞态及source wrapper迁移后150 passed / 1 skipped（34.60秒），cache/plugin-lock-final-source-20261013.log；最后source wrapper原生缺参/秘密拒绝用例由最终全套覆盖。skip是Windows不适用POSIX FIFO。
- 真实pip在独立target、no-index/only-binary下安装临时完整wheel锁成功，错误SHA256及缺少传递依赖锁实际失败；测试wheel入口有不执行哨兵，不运行其业务代码，也不污染宿主venv。Docker接口替身验证上下文、FROM本地image ID、require-hashes、policy3和执行label绑定，不能代替实际容器构建。
- SDK依赖真实下载验证：自有忽略目录cache/sdk-hash-evidence-phxidbr5，pip isolated通过固定HTTPS PyPI下载四项根及传递依赖，共10wheel；未运行sdist。以真实文件SHA256生成完整本地锁，在新dependencies目录no-index/ignore-installed/require-hashes安装，没有使用原venv包目录或系统site。
- 随后Python -I -S在outside-source导入core源码快照与上述private target，实际导入SDK/worker/egress并调用_adapt_sdk；宿主software能力和其他插件namespace拒绝、自身/data/state及RPC proxy成功，未导入cryptography/httpx/sentry_sdk/psutil，也未创建runtime/plugins目录。verification.json、requirements.lock、download/install日志、probe输出及verify.py留在自有目录。实际版本为rich15.0.0/typer0.27.3/PyYAML6.0.3/platformdirs4.13.0，完整10包清单见verification.json；规范化锁SHA256为469fb689e0790634db512664f8b6f0a3672922c71e77804cc242e507215095a9。
- 此原生SDK证据仅Windows/Python3.12的独立依赖/导入/adapter，快照早于最后不涉及adapter的lock/catalog/source wrapper收紧；不证明Linux镜像、所有SDK操作、其他Python/架构或平台完整CLI在这些最新依赖版本通过。哈希不等于发布者签名或无漏洞，来源审查/供应链漏洞/SBOM、最小代码上下文、真实daemon构建/取消及恶意插件隔离仍未完成。整个目标继续，最终全套另记。

- 首次全套1 failed / 1228 passed / 12 skipped，1 warning（308.35秒），cache/full-plugin-hash-policy-20261013.log；新增source wrapper帮助测试未指定读取编码，Windows默认GBK解码实际UTF-8帮助失败，subprocess reader线程异常导致stdout为None。生产来源锁/运行时逻辑未改，修正该测试的PYTHONIOENCODING和读取encoding=utf-8。实际script help/缺参/秘密拒绝重测1 passed（2.34秒），cache/plugin-source-wrapper-encoding-20261013.log；最终全套重新运行，保留首次失败证据。

- 同步仓库提交边界复核：git ls-files -ci --exclude-standard无输出，当前没有“已跟踪但命中现有忽略规则”的文件。真实wheel/独立target/测试缓存均留在忽略cache内；此检查不覆盖历史秘密或未命中规则的凭据，历史扫描仍待完成。

- 最终Windows/Python3.12全套1229 passed / 12 skipped（298.89秒），cache/full-plugin-hash-policy-verified-20261013.log，无首次GBK reader警告。源码与测试在本次收集后未改，仅同步文档；git diff --check通过。12项跳过含新增POSIX FIFO的Windows不适用项，不代表其他平台验证通过。本批新增原生source wrapper和完整lock/catalog/pip验证均纳入此全套；真实Docker/多架构/正式发布与完整项目目标仍待完成，后续修改尚未提交。
## 2026-10-13 执行侧Docker控制与catalog边界

- _control迁移core.process.capture，关闭stdin、每次30秒/16KiB；非零/OS/超时/超量错误统一固定SandboxError，不回显stderr尾部或原始异常，KeyboardInterrupt继续取消。真实临时Python客户端和其子进程覆盖静默超时、读取ready后的注入取消、日志超量回收、stdin关闭及不泄漏合成凭据。
- _network的inspect仅取Networks，不读取含Env的完整container配置。校验IPv4字段类型/地址，拒绝空、URI/凭据、回环/链路本地/保留/多播等，不人为拒绝Docker自定义CGN地址池。无network不创建资源；坏JSON/字段/地址使用固定错误，finally仍尝试仅删除本次自有proxy及network。Docker接口使用替身，清理请求不代表真实daemon清理成功。
- catalog删除未消费的原样dependencies列表，保留完整锁SHA256权威。base_reference上限4096字符且在Docker前拒绝过长值；catalog读写共用plugin_lock的16KiB上限。10000次重复dependency选择不放大输出记录，写出的schema/policy3执行绑定保持。调用链搜索没有发现dependencies消费方，未移除有用的--dependency版本核对行为。
- 插件result.error不再拼接到平台错误；协议坏JSON/非对象固定SandboxError。真实_invoke使用临时清单/指纹授权和已知Python客户端，发送受控error/坏协议；插件入口含不执行哨兵，未导入或运行，失败不写self状态并尝试本次worker清理。此证明本地协议边界，不冒充真实容器执行。
- 基础相关76 passed / 1 skipped（20.23秒），cache/plugin-control-foundation-20261013.log；增加真实_invoke协议边界后最终相关153 passed / 1 skipped（41.81秒），cache/plugin-control-final-20261013.log，覆盖插件/服务/既有安全边界与生命周期。skip为Windows不适用POSIX FIFO。完整源码全套另记。
- 尚未完成：主执行Popen的本地树、完整总预算、daemon清理失败记录/恢复、最小代码上下文、真实Linux/多架构/恶意插件容器验收，以及整体8类路线图。_network/_invoke目前仍可能忽略daemon清理失败；不能把cleanup请求、本地client退出或本批测试当daemon资源消失证明。主机此前只读Docker查询超时，本轮未再次探测或启动真实容器。当前修改尚未提交。

- 最终源码Windows/Python3.12全套1255 passed / 12 skipped（307.47秒），cache/full-plugin-control-20261013.log；包含本批26项新增控制/网络/实际协议边界及过长base-reference检查，源码/测试在收集后未修改，仅同步文档。git diff --check通过；临时数据和测试TEMP/TMP位于F盘忽略缓存，没有运行未知插件或真实Docker容器。当前8类完整目标、真实平台服务/发布及daemon恢复仍未完成，后续修改未提交。

## 2026-10-13 官方参数计划复核与本地执行监督验证

官方CLI计划第15节新增参数组合决策、长写操作timeout前置条件及六批验收顺序；完整库存仍为第3节18个Recipe、第4节管理模块、第9节参数清单。源码确认network dns反查忽略family、monitor processes指定pid忽略limit/sort；本轮仅明确修复计划，尚未修改这两个组合的生产行为。

现有进程监督改动的相关回归：cache/cli-plan-verification-20261013.log，62 passed，14.66秒（插件控制、共享诊断capture、监控CLI、网络CLI）。随后增加实际本地客户端监督测试：静默客户端超时以及RPC客户端不读取stdin、大回复堵塞管道两种情况；使用已知Python客户端及其子进程，不启动Docker、不导入测试插件业务。确认期限错误固定、父子进程回收、stdin/stdout关闭、不提交插件状态。最终插件控制文件27 passed，8.24秒，cache/execution-plan-verification-20261013.log。两个运行存在重叠用例，不相加作唯一测试总数。git diff --check通过（仅换行转换提示）。

边界：本轮未重新执行全套；1255 passed/12 skipped对应先前源码，不能覆盖当前监督改动。上述本地进程测试不证明Docker daemon资源已清除，也不证明同步broker调用可被硬中断；完整操作预算、daemon清理恢复及原生隔离验收仍待完成。工作区后续修改仍未提交。

## 2026-10-13 参数组合实施与共享监督完整回归

实施network dns反查仅接受family=any，ipv4/ipv6在resolver调用前退出2；保留数字IP自动反查及显式reverse。实施monitor processes的pid与显式limit/sort互斥，通过Click参数来源区分显式默认值和省略参数，业务采样之前退出2。无pid时显式默认列表参数继续有效。契约及官方计划第9/15节同步；其余官方功能、整体八类计划仍未全部完成。

证据：先增加IPv4/IPv6/显式reverse/family及pid/列表过滤参数矩阵，修复前13 failed、34 passed，4.86秒（cache/option-before-20261013.log），表明旧行为确有静默忽略；修复后相关联合91 passed，5.81秒（cache/option-after-20261013.log）。错误组合验证无resolver/采样调用、单个失败JSON、退出2，合法组合验证预算与查询参数传递。

共享core.process.stream把读取线程start放入try/finally，线程未成功启动时不join未启动线程，仍终止已启动客户端并关闭输出管道。实际本地Python客户端配合线程启动失败注入验证回收；监督联合41 passed，16.99秒（cache/supervisor-final-20261013.log）。

最后冻结生产源码和测试，执行完整python -m pytest -q：1276 passed、12 skipped，318.79秒，cache/options-supervision-full-20261013.log。测试临时目录位于忽略的cache，OPSKIT_DATA_DIR由fixture隔离。期间仅更新文档。pip check通过，git ls-files -ci --exclude-standard无结果，git diff --check通过。上述不是Git历史秘密扫描、真实Docker daemon清理、全部OS/Python矩阵或实际服务部署验收；跳过项不视为通过。当前工作区后续改动仍未提交。

## 2026-10-13 未提交回滚的原事务重试与启动恢复

修复rollback在复制失败后重试重新生成transaction_id的问题。已有匹配prepared回滚事务时复用原ID、版本、摘要和upgrade_floor；已关闭（aborted/applied）但仍是未恢复字节的记录不复活。只读reconcile在验证任务/已退出helper/健康归属/备份摘要后，以deferred/rollback_pending报告未提交回滚，不替换程序、不清除索引。

启动使用独立reconcile_on_boot，由现有30秒worker监督；在缓存根锁及程序目标锁内重新核对上述权威后重试rollback。实际恢复字节后restart_required优先于post-update清理及其他更新写入。退役记录写失败时仍返回重启要求并保留任务索引；恢复失败保留prepared原事务。未知/alive/identity_mismatch helper不会触发复制。公开CLI仍不提供强制回滚参数，也不启用启动专属写模式。

证据：初始回滚重试/核对/目标权威83 passed，53.39秒，cache/rollback-retry-20261013.log；随后生产源码冻结，更新退休/核对/目标权威/自动更新/验收/CLI/boot联合235 passed、3 skipped，111.68秒，cache/rollback-boot-20261013.log。随后仅扩展真实Windows测试：生成的PowerShell helper提交临时字节文件，用原生helper_status验证已退出，再注入回滚复制中断并启动重试，验证恢复旧字节、原事务ID不变、任务退役/历史状态一致。最终退休测试文件33 passed，24.31秒，cache/rollback-native-final-20261013.log。各批有重叠，不相加作唯一测试总数。git diff --check通过。

限制：真实PowerShell测试替换的是一次性字节文件，并未执行该文件；不证明正在运行的冻结exe能被Windows同步覆盖。若实际原子替换被OS拒绝，当前保留pending并暂停其他自动写入；延迟回滚helper与真正打包进程重启验收仍需完成。无任务索引的Unix回滚、其他任务终态退役、旧/未索引恢复、跨缓存健康归属及真实断电仍未完成。前一轮1276 passed/12 skipped的全套结果早于本轮生产改动，不作为当前源码的完整回归证明。本轮后续修改仍未提交。

## 2026-10-13 当前wheel、独立依赖和发布门槛验收

收紧pyproject的package include为官方包名及其点号子包，避免core_local等前缀包被发现。新增scripts/verify_wheel.py：核对227项Python/YAML源码、232个wheel成员、发行身份/console entry、逐字节源码及完整RECORD哈希/大小；额外文件（插件/缓存/凭据/越界路径）、缺资源、旧源码、错误入口或被篡改RECORD拒绝。新增库存测试11 passed，0.40秒，cache/wheel-inventory-20261013.log。

从Git跟踪及未忽略文件中选择官方源码/资源与构建元数据创建cache/wheel-release-b7hu07we/source独立快照，拒绝链接/多链接源文件。构建不复制.git、运行数据、虚拟环境、克隆插件、测试临时文件。首次wheel/日志保留于failed-original.whl、build.log、inventory.json。新虚拟环境不启用system-site-packages、不添加宿主pth，使用pip --isolated、固定TLS PyPI、only-binary及no-cache独立安装全部依赖；install.log及dependencies-utf8.json记录实际版本。pip check通过。主要版本Typer0.27.3、Click8.5.0、cryptography50.0.2、httpx0.28.1、psutil7.2.2、rich15.0.0。

独立安装首次检测出生产缺陷：typer.core不再暴露click，monitor参数组合与ContractGroup异常边界崩溃（smoke.log、installed-cli/*.stderr）。修复monitor为公开typer.Context注入，使用其参数来源而非跨后端全局上下文；共享异常边界兼容旧Click和新版Typer内置解析异常，Abort使用公开typer.Abort。原开发环境相关72 passed，3.04秒，cache/wheel-compat-20261013.log。重新构建并在上述独立依赖环境重装本项目wheel（无依赖重装，保留先前独立解析版本）。库存SHA256为c229835a277f335f5ac7a1a2903563c54f50640233a377971edc611c9d626c4c，inventory-fixed.json。

scripts/verify_installed_cli.py从源码之外运行实际console entry，验证main/core来自该环境site-packages。最终91个帮助入口+7项只读/拒绝共98 checks通过，installed-cli-final/verification.json与各stdout/stderr保留，未创建runtime-must-remain-absent。plugin run按原合同不提供平台help，单列skipped_help（帮助归插件）；首次遍历误将其当普通平台help导致smoke-fixed.log失败，修正校验器而未更改插件参数路由。pip inspect初次输出受Windows GBK影响失败，改为进程UTF8后成功，失败输出也保留。

用独立audit-environment安装pip-audit，针对独立安装后、测试工具安装前的24项第三方运行依赖完整版本清单查询PyPI已知漏洞；--no-deps/--disable-pip只禁用重复解析，没有忽略任何漏洞或执行自动修复。audit.log退出0、No known vulnerabilities found；runtime-sbom.json为CycloneDX1.4，24组件、0已知漏洞。resolved-runtime.txt是实测确切版本清单，不是wheel哈希锁。该SBOM不包含本项目逻辑、OS/基础镜像、pip/构建/审计工具，不证明没有未知漏洞；未来或其他平台解析结果须重审。

随后在独立依赖环境额外安装pytest/pytest-timeout运行源码全套（不是声称所有源码测试都调用已安装包）。首轮1 failed、1296 passed、12 skipped，365.78秒，cache/independent-deps-full-20261013.log：trickled download的2.5秒窗口没有进度。原测试同环境单独6 passed，13.18秒（independent-http-check-20261013.log），与并发负载下spawn/import消耗窗口相符，不能证明某个单独启动阶段耗时。测试成功场景改5秒以覆盖实际I/O，并新增server started断言；仍检查耗尽窗口、正字节/速率和worker无残留，生产期限和短窗无数据失败行为未改变。相关17 passed，16.03秒，independent-http-final-20261013.log。

冻结运行源码和测试后最终全套1297 passed、12 skipped，320.74秒，cache/independent-deps-full-final-20261013.log。跳过包括Unix/Linux专用检查及未启用真实更新E2E，均不计通过。最后还用真正隔离构建环境从同一快照构建dist-isolated/opskit-1.0.0-py3-none-any.whl（build-isolated.log），库存/RECORD再次通过，227项运行源码/资源与上述实际安装验收产物逐字节相同；隔离构建SHA256为62b51d96db5f21acbccfcbf2ba3c2527478acf38f245999916e6349b376a493c。该隔离构建产物做静态库存/内容等价验证，未重复安装98项；不得把这一范围描述为该产物本身重新运行完整原生测试。

CI六OS/Python组合已增加隔离wheel构建、库存、独立安装/依赖检查、源码外CLI验收，并保存验收JSON；YAML可解析、嵌入安装Python语法可编译，尚未真实远端执行。git diff --check通过。最低依赖版本、Linux/macOS原生、生产签名与密钥、真实服务/容器、实际冻结程序更新/回滚及历史秘密扫描仍未完成。当前Git后续变更仍未提交，完整八类目标不标完成。

## 2026-10-13 静态正确性、存量候选基线与MySQL ZIP安全

在独立工具环境安装固定Ruff0.17.0，不修改宿主运行依赖。初始关键规则扫描21项：F821 8、F811 13（cache/static-critical-before-20261013.json）。MySQL Windows漏subprocess使junction命令执行前NameError并被吞掉；三个版本驱动Path和网络threading类型注解不可解析。修复导入；cmd控制调用增加/d禁用AutoRun；删网络菜单被本地Console覆盖的冗余console导入和两个测试重复导入。跨文件recovery fixture改同名显式导出，不删除其pytest注册用途、不全局忽略F811。

pyproject dev固定ruff==0.17.0、target py310；关键规则E9/F63/F7/F82/F811当前零。F401/F841初扫124项候选（110未使用导入、14未使用变量），只确认并移除core.http.get_json的函数内last_exc声明/赋值，保留日志、异常和重试。剩余123项进入docs/static-debt-baseline.json，不能据此判断全部为垃圾代码或批量删除公开/注册导入。scripts/check_static_debt.py按文件/规则/消息/数量比较，新增失败，修复后残留基线也失败并要求同次缩减；不因行号移动放行额外数量。CI加入正确性和存量候选门槛，尚未远端执行。

真实Ruff的task-owned临时目录实验验证：空基线新增未使用math被阻断；与单条基线匹配通过；删除候选但未删基线被阻断（cache/static-debt-adversarial-20261013.log）。纯比较回归包含新文件、重复次数、不能用清理额度抵消新问题、行号移动和非法计数。工具/基线scope与限制详见docs/static-health-gate.md。最终静态正确性和基线检查均通过，static-critical-latest/static-debt-latest日志保留。

在同一驱动进一步发现手工ZIP循环缺路径和文件类型校验。MySQL改为复用core.archive.extract_zip，新增可选strip_components=1：原始路径校验先于前缀删除，统一root前缀、链接/特殊文件及总配额检查先于任何payload写入；保留官方mysql-VERSION-winx64/bin布局，默认0保持RustDesk等既有调用。复用流式复制，不增加临时完整解压副本；未测量提速，不能声称性能改善。测试五种目录逃逸、正常布局、默认/strip布局、被strip跳过的链接/FIFO、混合root、总大小和非法strip计数；全部使用临时ZIP及惰性字节，不执行数据库程序或写用户注册表。

验证：首个相关pytest命令误用了不存在的test_mysql_recipe.py等名称，0 collected并报文件不存在，cache/static-critical-regression-20261013.log保留；依据实际测试库存修正后212 passed，33.14秒（static-critical-regression-corrected-20261013.log），含实际Windows临时junction创建/切换并证明两个目标数据均保留。后续HTTP局部清理和基线比较联合168 passed，29.91秒（static-health-final-20261013.log）。ZIP迁移初轮94 passed，6.19秒（static-zip-final-20261013.log）；增加共享前缀安全案例后最终104 passed，6.09秒（static-archive-verified-20261013.log）。各批重叠，不相加作独立用例总数。git diff --check通过。

范围限制：本轮未重跑全套/重建wheel，上一轮1297 passed/12 skipped及wheel/SBOM是上一源码时点。本轮相关检查不证明全部Recipe安全；其他驱动的解压统一、Windows cmd路径转义、实际安装事务、生产容器/平台、历史秘密与真实断电仍待验收。关键静态清零不等于全部123候选已清理或完整平台安全达标。后续变更仍未提交，原八类目标保持未完成。

## 2026-10-13 Node/Go/Java/MongoDB Windows ZIP统一安全解压

迁移四个官方Windows驱动的install_tarball到core.archive.extract_zip：Node/Java保留单层官方前缀检测及Node平铺包；Go要求go/根，MongoDB移除统一单层根。移除对应四套手写成员拼接/拷贝循环，4文件10行新增、72行删除；保留shutil用于实际which检测，不按静态候选删掉功能依赖。输入预校验成功前不创建版本目录，不写真实用户配置/注册表，不执行下载二进制。

新增test_windows_archive_drivers.py通过四个真实驱动类（所有目录函数重定向到task-owned tmp），验证官方包布局和bin返回值、../及Windows反斜杠/盘符逃逸、链接与配额在任何payload写入前拒绝，另验证Node无前缀平铺包保留。初轮与已有链接/切换/共享ZIP检查56 passed，2.63秒，cache/windows-archive-20261013.log；最终补充链接/限额/平铺与软件/安全边界联合132 passed，8.71秒，cache/windows-archive-final-20261013.log。两批重叠，不相加作独立数。Ruff关键规则通过、123存量候选无新增/残留基线变化、git diff --check通过。

本轮是解压逻辑和接口布局的实际本地测试，不代表真实Node/Go/Java/MongoDB软件运行、Windows注册表/提权安装或其他平台通过。PostgreSQL/Redis/Python的ZIP入口及Linux/macOS手工tar/包内提取仍需继续梳理和迁移；PostgreSQL固定staging/替换删除也须单独做所有权和数据保留事务审查。当前未重跑全套或重建wheel，上一时点完整回归/产物不覆盖本批改动。完整八类目标继续未完成，后续变更未提交。
# PostgreSQL Windows 安全解压与发布边界（2026-10-13）

本批将WindowsDriver.install_tarball的手写ZIP循环迁为core.archive.extract_zip(strip_components=1)，统一拒绝逃逸、链接、特殊文件、混合前缀和超额包。固定版本.installing目录改为TemporaryDirectory独占工作目录；正常、异常和KeyboardInterrupt仅清理本次目录，不认领旧staging。已存在版本在解压前及发布前拒绝，取消原来的rmtree(dest)，使用Windows不覆盖目标的rename发布。Recipe已有home资源锁；这不等于系统跨用户事务和后续PATH/服务恢复全部完成。

临时目录、不会执行的二进制样例：test_postgresql_recipe、test_windows_archive_drivers、test_zip_prefix_safety联合53 passed / 2.09s，日志cache/pgsql-safe-20261013.log。包括正常布局、逃逸/链接/体积限制、旧版本数据、旧固定staging、发布权限失败、KeyboardInterrupt、解压期间竞争创建目标及本次目录清理。没有实际数据库安装或用户数据操作。Ruff关键正确性通过；静态候选123项，无新增、无过期基线。全套回归和wheel尚未重跑。
# Redis Windows 安全提取与发布（2026-10-13）

WindowsDriver.install_binary改为共享extract_zip默认模式，完整校验原成员路径/类型/配额；TemporaryDirectory独占staging。版本目录存在时拒绝，发布前复核，Windows rename拒绝晚到目标。不再边解压边写最终版本目录。平铺、bin、版本前缀、版本前缀/bin四种包布局支持；恰好一个redis-server.exe，多个入口拒绝。入口同目录文件移入bin，保留DLL和配置且不重复复制，冲突拒绝。其他目录保持包内结构，不擅自删除。失败/KeyboardInterrupt清理仅本次目录。

联合test_redis_windows_archive、test_postgresql_recipe、test_windows_archive_drivers、test_zip_prefix_safety、test_software_mutation、test_version_activation：85 passed / 5.57s，cache/redis-safe-20261013.log。Redis专用16项涵盖四种布局、逃逸、链接、配额、多入口、冲突、旧data.rdb、权限失败、取消和竞争创建目标。测试使用隔离临时目录/惰性二进制，没有实际Redis安装或用户服务操作。全套和新wheel仍待执行。
# Python Windows UV引导安全清理（2026-10-13）

ensure_uv移除irm|iex PowerShell与第三方镜像执行/下载回退，以及三个无消费方的旧Windows源常量；源码/测试/构建引用核查后移除，不删除Linux现有功能。仅官方HTTPS ZIP，curl --fail、HTTPS重定向、64MiB上限和最大120秒；复用capture进程树监督，输出16KiB及DEVNULL。共享安全提取在独占临时目录，唯一准确uv.exe（拒绝not-uv.exe、多入口、链接/逃逸），发布前拒绝既有/竞争目标；目录链接拒绝；异常和KeyboardInterrupt清理本次文件。保留已有私有uv优先及已安装系统uv最后回退，不执行测试包内二进制。

test_uv_windows_bootstrap16项加software、Redis/PostgreSQL/其他Windows ZIP、共享前缀联合134 passed / 7.22s，cache/uv-safe-20261013.log。覆盖平铺/嵌套、路径/链接/多入口、HTTP失败、下载超时、取消、旧路径及竞争保留；下载为替身，不能作为真实网络/发布真实性证据。Ruff及静态候选门槛通过，123项无变化，diff-check通过。

残余：latest x64下载未绑定固定版本/独立可信摘要，私有/系统uv未做版本/签名校验，ARM64、解压阶段总预算、Linux/macOS脚本引导及UV随后下载Python的信任策略未完成；全套/新wheel待重跑。没有把HTTPS或安全解压等同完整供应链验收。
# Go Linux/macOS与共享tar前缀安全（2026-10-13）

core.archive.extract_tar新增strip_components，非负整数且拒绝bool；原成员路径、类型/配额先校验，剥离后校验目的路径和统一顶层前缀，写入时再次检查。默认0保持既有调用布局。新增显式POSIX UTF-8字节路径模式用于Go Unicode测试夹具，resolve/mkdir/open/chmod均使用字节路径，其他调用不改变。Go手写文件提取循环移除，严格go根再调用共享strip1；DarwinDriver继承该实现。Node/npm与Java合法链接暂不迁移，不能用链接全拒绝损坏官方功能。

test_tar_prefix_safety20项（Windows 19通过/1 POSIX跳过），覆盖默认/前缀布局、Unicode名、逃逸/混合根、跳过根的链接/FIFO/设备、参数/配额、实际Go驱动正常和攻击包。与ZIP/software联合78 passed / 1 skipped，cache/tar-prefix-20261013.log；与security_boundaries及Windows安装提取联合115 passed / 1 skipped / 7.81s，cache/tar-related-20261013.log。两组重叠不相加。Ruff及123候选门槛通过，无新增。

局限：Windows上调用LinuxDriver只验证逻辑，不证明Linux/macOS原生安装；UTF-8 byte模式需实际POSIX/ASCII编码环境复核。Go尚未独占staging及无覆盖发布，I/O失败可能留下半成品，系统写事务和完整供应链仍待完成。全套与新wheel尚未执行。
# 安装目录独占发布与Go失败清理（2026-10-13）

新增software._shared.archive_install：仅清理本次mkdtemp，既有/竞争目标不覆盖；检查父目录链接。Windows rename天然拒绝目标，Linux libc renameat2 flags1，Darwin renamex_np flags4；错误errno转OSError，不支持/缺接口明确拒绝，没有检查后普通rename的危险回退。POSIX可用UTF-8字节路径创建/清理。Go Linux/macOS提取至staging，完整性检查在发布前；异常/KeyboardInterrupt后不留半成品版本。PostgreSQL与Redis Windows迁移共享模块，消除重复生命周期，FileExistsError映射明确拒绝覆盖。

test_archive_publication9项覆盖完整发布、异常/取消、旧目录保留、空/非空竞争目标、Linux/macOS排他标志/errno（替身）、缺接口无回退；Go新增缺binary不发布。初始Go/staging/变更激活联合45 passed / 1 skipped，cache/go-publish-20261013.log。共享迁移首轮2 failed /120 passed /1 skipped，cache/shared-publish-20261013.log：竞争数据未被覆盖，但两驱动错误文案变笼统；修正FileExistsError映射后相同范围122 passed /1 skipped /10.36s，cache/shared-publish-final-20261013.log。失败证据保留，重叠组不相加。Ruff和静态候选123门槛通过，无新增。

Windows原生目录竞争实际验证；Linux/macOS排他系统调用只测参数与失败映射，未做原生调用，POSIX UTF-8一项跳过。当前不证明Linux/macOS安装已达标；原生CI/工具链、后续PATH/服务/状态事务、其他解压器、全套/新wheel仍待完成。
# 五种Windows版本软件独占发布（2026-10-13）

Node.js/Go/Java/MongoDB/MySQL Windows install_tarball统一使用archive_install.staged_directory；共享安全ZIP校验后，在staging内以is_file验证必要二进制，再发布。移除发布后的结构检查，避免错误包留最终半成品；既有/晚到目标拒绝并保留用户文件。FileExistsError提供明确拒绝覆盖，InstallError不重新包装以保留bad_structure等具体错误。Node平铺支持保留，官方各包前缀布局与既有返回路径不变。

test_windows_archive_drivers矩阵新增MySQL，六驱动统一检测缺必要binary、旧数据、权限失败、KeyboardInterrupt及真正rename前创建竞争目标（Windows原生拒绝覆盖）；另保留正常/逃逸/链接/配额和Node平铺。联合driver_static_regressions、archive_publication、postgresql_recipe、redis_windows_archive、software_mutation、version_activation132 passed /10.65s，cache/win-staged-20261013.log；随后保留InstallError且缺binary测试明确断言bad_structure，最终同范围结果见cache/win-staged-final-20261013.log。Ruff和123候选门槛通过，无新增。没有执行样例二进制或改真实用户服务。

限定：只验证驱动提取/发布与相关版本服务，安装后的PATH/服务/快照失败仍需完整补偿；Linux/macOS链接和原生调用、来源摘要与全套/新wheel仍待完成。
# 内部tar链接图与Node/Java迁移（2026-10-13）

共享tar提取新增显式allow_symlinks，默认False，硬链接/特殊类型不开放。完整预检成员图，拒绝重复/目录文件冲突、成员写入链接父目录、绝对/越界/驱动器链接、缺目标、链接链环及目录间接递归环；目标必须在包内成员或隐含目录内，不支持通过别的链接父目录寻址。链解析缓存终点，目录关系用拓扑遍历检查；先写普通内容再建链接，无包内链接写穿。合法相对..仅用于链接目标且规范化后仍在根内。原路径/strip/额度检查保持。Windows symlink目标使用本机分隔符。

Node与Java LinuxDriver（Darwin继承）移除两套手写路径/链接循环，共享strip1/allow_symlinks及独占staging；在发布前检查node/npm或java实际文件，拒绝覆盖。Java因此不再用os，静态门槛发现新增F401后移除，不放宽基线。正常npm文件/链及Java legal目录链接样例可读。

链接/前缀/安全边界首轮1 failed /52 passed /1 skipped，cache/tar-links-20261013.log：Windows正斜线相对symlink原生读取失败；改本机分隔符后53 passed /1 skipped /5.10s，cache/tar-links-fixed-20261013.log。两个驱动与软件等联合121 passed /1 skipped /7.84s，cache/linked-drivers-20261013.log；新增缺binary测试和不可用symlink显式skip后最终范围见cache/linked-drivers-final-20261013.log。原生Windows symlink实际创建/读取，不执行包内二进制；POSIX UTF-8专用仍跳过。Ruff及候选门槛123无新增。

局限：测试包模拟已知布局，未验证真实Node/Temurin资产、Linux/macOS原生rename与打包；目录目标通过另一个链接父目录寻址明确拒绝，真实资产若需要须先完善安全图。后续服务/环境/状态恢复、其他提取路径、全套与wheel仍待完成。
# MongoDB tar提取与跨平台故障注入（2026-10-13）

MongoDB LinuxDriver（Darwin继承）取消手写tar路径拼接及直接写最终目录，复用extract_tar(strip1)和staged_directory，默认拒绝所有链接/特殊类型，确认mongod是文件后发布；既有版本拒绝覆盖，保留bad_structure原因。14项MongoDB专用用例涵盖布局、逃逸/重复/混合根、链接/FIFO、缺binary/限额、旧数据库、权限失败/取消。

MongoDB与tar链接/前缀/共享发布/安全边界联合88 passed /1 skipped /7.32s，cache/mongo-tar-20261013.log。检查发现前几批Path.rename故障注入只拦Windows而不拦POSIX专用syscall；PostgreSQL/Redis/Windows矩阵/MongoDB改为共享publish_new_directory入口注入，并在竞争测试里调用原始发布函数。修订后的同组加Windows驱动/PostgreSQL/Redis最终184 passed /1 skipped /12.98s，cache/mongo-publish-final-20261013.log，未执行样例二进制或真实服务。Ruff与静态123候选无新增，重叠组不相加。

Windows测试不证明Linux/macOS原生服务安装、系统调用和工具链；POSIX UTF-8专用仍跳过。完整数据/环境/服务事务、其他提取路径、全套与新wheel待完成。

## 本批追加：WSL Linux真实文件系统验证

有界检查WSL python3返回3.14.6，日志cache/native-posix-check-20261013.log；随后执行scripts/verify_archive_native.py，stdlib直接加载当前archive/archive_install源码，在Linux /tmp的本次TemporaryDirectory验证，不使用Windows挂载盘作为目标。默认UTF-8与LC_ALL=C/PYTHONCOERCECLOCALE=0/PYTHONUTF8=0且--require-ascii两次均exit0；各9项验证：真实renameat2排他发布、相对symlink可读、可执行位、Unicode成员UTF-8 byte路径、空/非空竞争目录保留、OSError/KeyboardInterrupt只清理自有目录、越界链接写前拒绝。脚本记录filesystem_encoding分别utf-8/ascii，确认测试目录删除；日志cache/native-archive-utf8-20261013.log和native-archive-ascii-20261013.log。两种环境分别报告，不当18个独立功能覆盖。

该证据补齐共享提取/发布机制的Linux原生与ASCII字节路径检查；不是完整Linux驱动安装、正式资产/依赖/服务验收，也不证明macOS renamex_np。Windows单测的POSIX skip仍按原结果保留，不回填为单测通过。
# MySQL Linux/macOS tar提取与发布（2026-10-13）

common.extract_tarball删除手写member路径循环，调用共享extract_tar(strip1,allow_symlinks=True)，保留gzip/xz选择与InstallError契约。Linux与Darwin安装在独占staging提取并检查bin/mysql后发布，旧/竞争目标拒绝，失败/KeyboardInterrupt只清理本次文件；具体结构错误不重新包装。Linux _ensure_linux_deps移至校验和结构检查之后，非法包/旧版本不触发依赖安装；包管理本身的失败传播和期限仍未完成，不声明系统写事务完成。

test_mysql_tar_install22项分别Linux/Darwin，覆盖gzip/xz正常布局与lib/private库symlink、逃逸/混合根/重复、缺binary、旧database、权限失败和取消。依赖调用为替身，不安装真实包；正常symlink本机原生创建可读，权限不足环境明确skip。与MongoDB/Node/Java、tar图/前缀/共享发布/静态驱动联合103 passed /1 skipped /4.87s，cache/mysql-tar-20261013.log。Ruff/123候选门槛及diff-check通过，无新增。Linux/macOS真实资产/服务、依赖/状态恢复、全套和新wheel待完成。
# PostgreSQL Darwin ZIP/tar与可执行权限（2026-10-13）

DarwinDriver删除两套手写提取循环，调用共享ZIP strip1或tar strip1/internal symlink，并复用独占staging，确认psql是文件后发布，拒绝覆盖既有/竞争目标。ZIP此前未恢复可执行位，新增extract_zip显式preserve_mode且严格bool，macOS调用True，其他默认False保持；按成员执行位归一755，否则644，setuid/setgid不保留。ZIP链接仍拒绝，tar仅内部验证链接，不虚构真实发行资产支持证据。

test_postgresql_darwin_archive18项覆盖ZIP/gzip布局、路径逃逸/混合根、缺binary、旧database、发布权限失败/取消；与PostgreSQL Windows/ZIP/Windows矩阵/tar图及前缀联合143 passed /1 skipped /7.08s，cache/pg-darwin-20261013.log。Windows不代表macOS原生权限；scripts/verify_archive_native.py追加Linux ZIP chmod检查，分别在UTF-8/ASCII模式运行，日志cache/native-pg-zip-utf8-20261013.log和native-pg-zip-ascii-20261013.log。Ruff/静态123候选门槛通过无新增。

当前为隔离惰性样例，macOS renamex_np/真实资产、ZIP必要链接策略、完整服务/环境恢复、剩余包内提取和全套/新wheel仍未完成。
# ClickHouse包校验、发布与内存（2026-10-13）

extract_clickhouse_tarball取消tf.extractfile(...).read()/write_bytes整文件分配，在自有TemporaryDirectory用共享tar完整校验和流式提取，支持经验证内部链接。选择唯一正规usr/bin/clickhouse候选，否则唯一basename候选；多个候选/缺binary拒绝。移动文件至bin而非再复制。LinuxDriver（Darwin继承）改独占staging，aliases完成后发布且拒绝覆盖。别名symlink使用相对clickhouse；无权限fallback脚本绑定最终dest/bin/clickhouse，避免临时绝对路径在发布后失效。

test_clickhouse_archive_safety13项加原recipe/共享发布/tar图/前缀/安全边界联合79 passed /1 skipped /10.63s，cache/clickhouse-safe-20261013.log。涵盖官方/备用布局、发布后alias可读、路径/重复/候选歧义、未选中越界链接、限额/缺binary、旧database、发布权限失败/取消及无symlink权限fallback。不会执行包内样例。

16MiB同一gzip惰性binary，tracemalloc在fixture构建之后启动：旧提取whole-read峰值33641917字节/0.270891秒，新共享提取4589148字节/0.337077秒，最终大小16777216且SHA256一致。结果cache/clickhouse-memory-20261013/result.json。仅Python分配峰值、一次同机测量，不是RSS/p95或真实大包证明；新路径全包校验/落盘有额外I/O，不宣称耗时提速。真实官方资产/系统服务、代表性负载、全套和新wheel仍需完成。
# Debian外层ar统一与有界提取（2026-10-13）

新增core.debian.extract_data_tar：普通文件容器≤2GiB，最多16成员，data成员≤512MiB；完整成员表验证后才写唯一data.tar/data.tar.gz/xz/bz2/zst。严格magic、header trailer、ASCII精确名称、非负数字size、边界/奇数填充、版本2.0、重复/多个data/缺version/未知成员拒绝。读取固定1MiB块，输出xb不覆盖，失败或取消清理本次输出。只读外层control，不执行maintainer脚本。PostgreSQL/Redis不再执行ar x，MySQL取消外部ar+宽泛整块读取回退及无调用方的旧解析函数。MySQL格式错误转InstallError，保留失败语义，移除迁移后无用subprocess导入。

test_debian_container20项覆盖五种data命名、晚到恶意/重复/截断/trailer/填充/负size、版本缺失、不覆盖、配额及MySQL包装。与MySQL tar/PostgreSQL/software/变更联合106 passed /11.51s，cache/debian-container-20261013.log；错误包装调整后解析20 passed /0.54s，cache/debian-final-20261013.log。Ruff及123候选门槛通过无新增，未安装真实系统包。Linux原生脚本追加精确payload和晚到恶意ar成员写前拒绝，UTF-8/ASCII日志cache/native-debian-utf8-20261013.log及native-debian-ascii-20261013.log，各模式当前13项检查。

局限：只完成外层ar；内部tar原有lstrip路径映射、links/合并、xz/zstd全量解压、HTTP整包读取、PG/Redis最终目录半成品/事务仍有待修问题。未知/GNU/BSD长名称等非标准ar扩展安全拒绝；正式发行资产须验收。全套/新wheel和真实服务/生产来源仍未完成。
# 闲置MySQL APT删除、PostgreSQL Debian选择与限额（2026-10-13）

本批起初计划迁移MySQL APT内部tar，进一步核对发现实际MySQLRecipe._download仅调用download_mysql_tarball，driver仅注册Linux/Darwin/Windows，包__all__仅MySQLRecipe，registry只导入Recipe包，无APT子模块动态入口或文档消费者。唯一引用为上一批新增私有包装测试；此前先硬化未核对生产可达性不够准确。本批删除software/recipes/mysql/apt.py及该测试，不删除现有官方MySQL能力，不新接APT功能。保留真实PostgreSQL/Redis消费的core.debian。

extract_tar_files统一所有原成员路径/重复/体积计数校验，再对被选正规文件的映射目标校验；映射重名、文件/目录冲突、既有目标拒绝，固定1MiB块/xb写入，不对未选链接执行或跟随。PG移除lstrip及手写路径/复制循环，只选版本bin与arch库正规文件，原有库链接按版本文件生成；下载/合并过程在独占staging，确认完整后发布，后包冲突/异常/取消不留最终半成品，旧目录在网络前拒绝。

open_data_tar不再全量lzma/zstd.decompress到内存，有界展开至自有文件后读tar；普通/gzip/xz/bzip2/zstd覆盖，展开限2GiB+64MiB。XZ解码器memlimit128MiB、1MiB返回块、截断/非法尾部拒绝，标准4字节零填充允许；zstd窗口128MiB。zstandard此前环境有但运行依赖未声明，新增>=0.25,<1到pyproject/requirements并用固定PyPI uv lock，39包解析成功，新增zstandard0.25和已声明未入旧锁Ruff0.17，其余版本保留；cache/debian-zstd-lock-20261013.log。新增依赖仍需干净包安装与漏洞/SBOM复验。

选择/压缩test_debian_tar_selection19项、实际PG模拟网络/惰性包test_postgresql_debian_install7项（正常binary/lib、原始路径攻击、旧database无网络、后包冲突、取消），与ar/PG/MySQL/software/发布联合132 passed /9.15s，cache/deb-bounded-final-20261013.log。修正XZ尾部源读取并扩展原生脚本后，选择/PG/wheel库存37 passed /4.72s，cache/deb-final-check-20261013.log。pip check通过。Ruff及静态123候选无新增；迁移后无用PG shutil导入已清理。

Linux /tmp机制脚本UTF-8/ASCII各14项通过，新增bounded_xz_allowlist_selection，日志cache/native-selected-utf8-20261013.log与native-selected-ascii-20261013.log，临时数据确认删除；不是Linux正式PG服务安装证据。库动态加载、跨包合法重叠策略、HTTP整包读取/来源/期限、Redis映射/硬链接/最终事务、全套与新wheel仍未完成。
# Redis Debian alias与源码编译/发布修正（2026-10-13）

Linux install_binary在独占staging合并全部输入后验证redis-server及redis-cli并发布，旧版本拒绝。Debian仅精确usr/bin白名单，原始路径由共享extract_tar_files先校验；正规文件流写不覆盖，alias按原tar软/硬语义验证原目标仍在白名单，全部包完成后统一解析。拒绝越界/绝对/缺目标/循环/重名/冲突；hardlink连接真实正规终点，symlink使用相对终点，不指向待删除staging。不执行未选择链接，不用别的二进制冒充缺失命令，删除这类虚假回退。

Linux源码原实现make后未从真实src产物复制，并在临时源删除后检查错误目录；现在提取必须唯一redis源目录，make用core.process.capture(cwd=src_dir,timeout=1800,max_bytes=1MiB,stdin=DEVNULL)进程树监督，实际src产物在临时源清理前复制，缺server/cli拒绝。Darwin复用该编译路径与独占发布，取消原重复make install/同目录无效复制。capture增加可选cwd并由原生子进程测试证明生效。构建返回非零、超时/超量错误固定报告，不输出原始构建日志。

test_redis_debian_install17项含正常、后包soft/hard目标、危险/缺alias、循环、重复/跨包冲突、缺cli、旧data.rdb、发布失败/取消；test_redis_source_build10项分别Linux/Darwin模拟构建，证明src产物复制、缺产物/非零/超时/取消不发布。首组选择/外层/共享发布/capture共82 passed /13.78s，cache/redis-debian-20261013.log；新增缺cli和真实capture cwd后，Redis/capture/security/network-deadlines/plugin-control/软件锁联合95 passed /26.06s，cache/redis-debian-final-20261013.log。两组重叠不相加。Ruff/123候选无新增。

软件目录重新搜索extractfile/extract/extractall/copyfileobj均无直接调用，剩余为core.archive和updater文件复制；仅为清理范围证据，不证明无其他漏洞。Debian为惰性包、make为替身，无真实编译/服务操作；真实发行包/多架构/源码信任、跨包合法重叠策略、下载总期限、完整环境/服务/状态补偿与全套/新wheel仍需完成。

# 当前源码全套复验、独立wheel与官方参数计划复核（2026-10-13）

本批更新official-cli-completion-plan第16节过时安装提取状态，并新增第17节真实采样缺口：CPU先后窗口、单PID首次基线、网络重置/会话缓存、top-k测量与显式mount错误。未新增占位参数或改变运行采样schema，整体八类计划不缩减。

Windows Python3.12.10全套收集1644项，1629 passed /13 skipped /2 failed，609.51秒；cache/full-current-20261013.log。两失败分别为test_real_supervision_kills_stalled_download_and_retains_old_candidate和test_reconciliation_worker_timeout_releases_owned_lock：总3秒预算已超时，但未生成进入阻塞下载/持锁阶段的标记，不能证明本次达到预定故障注入点。同期独立wheel安装/帮助检查等进程也在运行；不能据此断定产品缺陷或仅是负载原因。未放宽产品期限或删除断言。下载文件独立重跑22 passed /38.35秒，cache/update-download-current-20261013.log；锁测试单独重跑1 passed /4.62秒，cache/reconcile-timeout-current-20261013.log。重跑不改写全套首轮失败，仍需稳定的进程就绪/阶段注入方案以及无竞争全套复验。

干净源码快照cache/wheel-current-i23n6afx/source构建成功；首次复制准备发现仓库没有LICENSE文件，失败快照保留，后续仅复制实际存在的元数据，不编造许可证文本。wheel包含228项运行Python/YAML、233个成员，与当前源码逐字节及RECORD校验通过，SHA256 d70bca31bfbb63bd00f14f17c8c08a6e2baba8a60f0024657c8c4b1f9929b7dd。构建使用python -I -m build，避免同名build.py。新虚拟环境独立从https://pypi.org/simple仅安装wheel依赖，zstandard0.25包含在内；pip check通过。源码目录外91种help和7种查询/拒绝合计98项检查通过，未创建运行存储；inventory.json、build.log、install.log、pip-check.log及installed-cli/verification.json均在该cache目录。

按该独立环境实际版本列出25项第三方运行依赖（排除opskit及安装工具），pip-audit成功，未发现已知漏洞；CycloneDX SBOM包含25组件、0条已知漏洞，audit-requirements.txt/audit.log/sbom.json保留。此检查不是未知漏洞、历史秘密、插件供应链、原生产物或生产签名证明。Ruff关键门槛通过，静态123候选无新增，git diff --check通过。Linux /tmp UTF-8与ASCII各14项机制检查重跑通过，cache/native-current-utf8-20261013.log与native-current-ascii-20261013.log；未替代真实软件/服务安装和macOS验收。

当前源码有新的打包/独立安装证据，但全套未通过；发布门槛保持未完成。后续先稳定真实进程测试的阶段证据，再复验全套；系统事务、更新恢复、最小插件运行时及真实恶意插件/服务、多平台与性能矩阵继续原计划。当前更改未新提交或推送。

# ZIP完整布局、真实进程阶段证据及秘密门槛（2026-10-13）

两项真实进程测试新增expire_worker_after_marker fixture：父监督器monotonic在真实子进程创建下载阻塞/持锁标记后才跳过截止时间。真实启动仍有30秒上限，父子进程/文件/锁/终止清理不替身；产品network.worker时钟及预算未改。测试必须断言标记、注入触发和进程/数据/锁结果；这证明已进入目标阶段后的期限处理，不能当实际3秒阻塞墙钟证据。原有DNS真实2秒监督及其他实际进程期限检查继续保留并在全套通过。

core.archive.extract_zip原有wb允许重名覆盖、布局冲突晚到写入。本批在输出目录创建前检查所有原始与strip后名称：规范化重复、文件/目录及父路径冲突、成员type与目录标记冲突；Windows拒绝大小写别名、尾随点/空格、设备名、非法字符。输出路径内symlink/junction和已有文件拒绝，xb创建使校验后竞争文件仍不覆盖；取消/CRC或权限失败只删除自己创建的当前文件，整体staging生命周期不变。共享路径新增UTF-8≤4096字节、≤128层；这是路径图的资源界限，不是完整解析配额。

test_zip_layout_safety39项覆盖strip0/1重名/规范化/反斜杠、布局/类型、Windows真实大小写和设备名称、既有数据、内部链接、路径额度、取消和校验后竞争创建；合法显式/隐式目录保留。更新/ZIP/六驱动/PG Darwin/UV相关181 passed /52.38秒，cache/zip-worker-stage-20261013.log；首批ZIP61 passed /4.12秒，cache/zip-layout-20261013.log。随后共享路径/tar-links/PG/Debian110 passed /1 skipped /6.34秒，cache/zip-layout-final-corrected-20261013.log；首次该命令误写不存在的test_security.py，exit4且0项收集，cache/zip-layout-final-20261013.log保留，不作为通过证据。最终ZIP竞争与security边界57 passed /8.94秒，cache/zip-race-security-20261013.log，各组重叠不相加。

最新Windows Python3.12全套收集1683项：1670 passed /13 skipped /431.16秒，cache/full-stage-zip-20261013.log；上一轮两失败均在全套通过。未与大批依赖安装/help同时运行，但没有测出具体外部负载根因，不能仅归咎环境。Ruff关键门槛通过，静态123候选无新增；diff检查通过。Linux原生共享脚本增加ZIP模糊布局写前拒绝、既有psql保留，UTF-8及ASCII各16项通过，cache/native-zip-layout-utf8-20261013.log与native-zip-layout-ascii-20261013.log。正常资产/真实服务/macOS仍未替代。

新干净快照cache/wheel-zip-layout-1hmjrfjz构建/源码/RECORD校验228项运行文件、233成员通过，SHA256 2cc3b26ac517e85257ea060b07219ee5d60897c378b7a37718345f06e7b2cfae。此前独立安装并审计过的25项依赖未改变；在该环境用新wheel --force-reinstall --no-deps替换平台包，pip check、源码外91种help+7种查询/拒绝合计98项通过，无运行存储。新产物源码一致，依赖来源为上一轮独立PyPI环境；未声称又新建环境或再解析依赖。inventory/build/install/pip-check及installed-cli/verification.json在新快照目录。

历史秘密扫描采用Gitleaks8.30.1官方HTTPS发行包，固定摘要核对；Windows ZIP SHA256 d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e，Linux tar.gz SHA256 551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb。工具/输入快照/完整脱敏报告均在cache/gitleaks-8.30.1-hq6obsie，不安装到宿主/平台依赖。379项非忽略提交候选扫描0发现；Git --all初次报告280提交，补-m合并差异后工具报告320提交、约4.90MB，0发现。Git可达323提交、41合并提交，文本diff核对322含hunk、1只路径/模式；明确不以工具计数证明所有二进制和规则覆盖。Linux真实工具在/tmp独占目录提取及执行同配置 --all -m --full-history，0发现/320处理提交；临时binary目录已删除，只设置进程内Git safe.directory，没有修改用户Git配置。相关dir/git/git-merges/linux-history报告和覆盖库存均保留。

阳性控制最初连续字符PAT样例被默认规则过滤，验证失败记录positive-scan.log/positive-report.json保留。随机合成高熵PAT重新验证检出github-pat、退出1；报告/日志原值不存在，positive-random-summary.json与独立脱敏断言通过。这不是实际凭据，无远程API验证或上传源码；默认规则仍可能漏报。CI增加secrets job固定Linux版本/SHA、完整历史、合并差异、默认规则、忽略用户allow注释及自定义ignore、redact100报告，YAML/六组合保留验证通过。原生工具命令本地已执行，下载shell/上传artifact及全部CI远端尚未执行，不宣称门槛已远端生效。

新增待修P0证据：cache/archive-parser-probe-20261013/pax-before-extractor.json用64MiB声明的PAX头和拒绝>1MiB读取的BytesIO探针，证明tarfile.open在共享提取器前请求67108864字节。未分配该载荷或运行未知插件，但当前解析前内存风险尚未修复。下一批把tar扩展头/成员/压缩预算、ZIP中央目录限制前移到打开/枚举阶段，再迁移所有真实消费者；继续官方系统依赖错误传播、写事务、更新恢复及沙箱完整生命周期。整体八类目标仍未验收完成；新更改尚未提交/推送。

## Windows 版本路由事务增量（2026-10-14，独立工作树）

此增量仅位于 C:/Users/Administrator/.codex/worktrees/windows-route-transactions/cli，未合入原 develop、未提交或推送。Node/Go/Java 的激活与恢复共用路由协调层；恢复时批量移除 active 和命令目录 PATH，一次注册表发布。目录联接的补偿绑定打开句柄的文件身份与安全描述符；直接删除使用句柄删除，避免关闭句柄后按路径删除的竞争窗口。

新增批量 PATH 测试覆盖大小写/引号/重复项、无关空项及值类型保留、一次写入、部分写入失败、取消、外部替换保留、所有目录预验证，以及新增 PATH 长度上限。四模块测试报告 105 passed /14.41 秒（cache/windows-route-second-20261014.log）；执行会话退出码为 0。静态检查 107 候选、0 新增，git diff --check 通过。

此证据不覆盖全部驱动真实集成、完整安装/卸载事务、持久化崩溃恢复、Python 旧命令路由或 Linux/macOS 验收。原工作区的 286 文件审阅清单保持不变。

联合驱动故障验收补充：Node/Go/Java 实际驱动、原生目录联接及临时 HOME 下真实配置文件，使用隔离替身注册表，在首次激活/切换/恢复三个阶段注入发布后失败或取消，共 18 项；核实注册表原值、命令字节、配置文件存在性与字节、联接目标及安全描述符恢复。AutoRun 迁移明确禁用以避免访问用户实际设置，不能据此声明该迁移联合事务通过。首轮 3 failed /57 passed，原因是测试错误预期 restore 的 PermissionError，而命令事务按既有接口包装为 InstallError；修正为检查准确包装消息与原始 cause，未修改产品实现或放宽恢复断言。四模块重跑 123 passed /19.08 秒、退出 0（cache/windows-route-integration-final-20261014.log）。另在唯一临时 HKCU Software 测试键验证真实批量 PATH 删除、取消恢复、提交及值类型保留，2 passed /0.23 秒、退出 0（cache/windows-route-native-batch-20261014.log）；不读写实际 Environment 键。两项原生测试于 123 项之后新增，不声称一次全套收集 125 项。

## Python uv 安装进程监督增量（2026-10-14，独立工作树）

PythonRecipe._install_with_uv 移除两路无界 pipe.read、虚构百分比后台线程与仅父进程 kill，改用 core.process.capture：沿用 1800 秒总期限、合并输出 1 MiB 上限、DEVNULL 输入、Windows Job/Linux 进程组监督。超时/输出或进程资源失败明确终止安装，不降级继续安装；取消传播；启动 OSError 保留原包管理器回退。非零退出仅显示退出码，不将可能包含代理凭据/私有 URL 的原始输出写日志或界面。进度改为已有安装阶段的 25/75，不再按时间伪造下载进度。

替身约束/失败传播、现有 Windows uv 引导及软件回归联合 71 passed /3.60 秒、退出 0（cache/python-uv-supervision-20261014.log）。新增两项真实进程用当前 Python 作为可控安装器，生成并验证子进程 PID 标记，然后注入静默超时或 2 MiB 输出；实际走 recipe→capture→Job 监督路径，核实父/子进程退出。含前六项测试共 8 passed /10.49 秒、退出 0（cache/python-uv-native-20261014.log）；两组重叠不可相加。无真实 uv 下载或解释器安装，Linux 原生等价行为尚待执行。静态候选 107、0 新增。

Python 精确版本验证、旧 JSON 命令路由及激活/完整安装补偿仍未修复；本记录不证明完整 Python 安装验收，更不证明全部八类目标完成。

## Python 精确版本身份（2026-10-14，独立工作树）

PythonRecipe 原校验只比较主/次版本，3.12.9 可被保存为请求的 3.12.13；原测试也明确掩盖了此错误。现要求退出码为零、完整标准 Python 版本行与请求的稳定三段版本完全相同，通过 core.process.capture 的 5 秒/4096 字节/无 stdin 进程树监督读取。候选版本在路由、shim、PATH、快照修改前校验，之后仍保留发布前复验。包管理器提供其他补丁版本时不冒认请求版本，返回空继续既有源码回退，不卸载或删除系统解释器。

新增10项证据：精确匹配、同 minor 错 patch、数字前缀碰撞、rc版本、非零退出及非标准输出；错 patch 不触发路由/shim/快照；包管理器正确/错误 patch；当前真实解释器匹配其精确版本并拒绝下一个 patch。与uv监督、软件、版本激活、包管理器联合92 passed /12.58秒、退出0（cache/python-version-identity-20261014.log）。静态107候选、0新增，diff通过。旧的成功激活测试已将伪造3.12.9改为实际请求3.12.13，错误minor测试增加禁止所有路由副作用的断言。

这不是完整安装事务：晚于前置验证的联接/shim发布失败和最终保存失败仍需跨资源补偿；Windows Python JSON shim以及Linux/macOS路径覆盖逻辑未迁移。本次未运行真实包管理器或源码编译，未修改宿主解释器。

## Python 目录版本边界（2026-10-14，独立工作树）

梳理旧shim调用时发现 find_uv_python 与 uninstall 使用 cpython-{version} 前缀：请求3.12.1会查找到或删除3.12.13。现匹配完整 cpython-{version}- 边界；查找要求稳定三段版本及候选is_file，卸载明确版本也在读取快照前要求稳定三段版本。新增6项测试在临时目录证明错patch不选择、目录伪装binary拒绝、正确文件可选，真实卸载3.12.1保留3.12.13及其数据，minor/主版本/rc/路径字符串在读状态前拒绝。与身份及软件测试共65 passed /2.01秒、退出0（cache/python-version-directories-20261014.log）。仅操作临时安装目录，无真实用户版本删除。

未将前缀修复称为完整卸载安全：目录/父级链接、竞争替换、删除失败吞噬、先删当前版本再切换及跨资源补偿仍待整改；旧JSON shim迁移仍开放。

## Python 安装目录链接预检查与删除失败传播（2026-10-14，独立工作树）

common.uv_installation_directories 统一以 lstat 检查安装root及祖先目录，拒绝非目录/symlink/Windows重解析点；在返回任何候选前检查所有cpython入口，返回完整目录组。installed_versions、find_uv_python与卸载删除调用复用该边界。Python删除不再ignore_errors=True；权限/IO错误明确InstallError并中止后续快照及环境清理，不冒报卸载成功。合法安装目录中的必要内部symlink未因精简被删除或禁用。

新增真实Windows目录联接三种位置：root、版本入口、祖先目录，均拒绝且外部数据字节保留；删除权限故障验证不调用快照保存/删除及driver恢复，原数据保留。目录/身份/软件联合69 passed /2.28秒、退出0（cache/python-directory-preflight-20261014.log）。这仅证明预先存在链接拒绝和单次故障传播，不证明lstat之后的竞争替换免疫、删除途中损坏可恢复、多版本原子删除或Linux fd-relative删除。先删除活跃版本再切换及持久化卸载恢复仍未修复，旧JSON路由仍需迁移。

## Python 增量 Linux 原生验证（2026-10-14，独立工作树）

使用既有隔离Linux虚拟环境 /mnt/f/ougato/opskit/cli/cache/monitor-linux-venv-20261013/bin/python（Debian Python3.11.2），在当前C盘开发工作树执行Python uv监督、精确身份、版本目录三个完整模块，28 passed /14.12秒、退出0（cache/python-linux-native-20261014.log）。真实子进程故障阶段标记、父/子进程组回收、当前Linux解释器身份及POSIX符号链接root/entry/ancestor拒绝均运行，无平台skip。没有安装依赖、修改系统Python或用户运行数据。驱动并未真实下载uv/编译Python；C盘挂载上的目录测试不替代Linux /tmp其他文件系统与macOS验证。

## uv 官方发布来源复核与当前打包快照（2026-10-14）

只读请求 https://api.github.com/repos/astral-sh/uv/releases/latest（元数据读取上限2MiB、20秒请求超时），确认当时latest为0.13.0、2026-10-09发布。已保存六种Linux GNU/macOS/Windows x86_64/aarch64资产名称、大小、明确版本URL和GitHub API提供的SHA256，cache/uv-upstream-release-20261014.json。未下载或运行产物，未将latest更新策略直接接入代码；API摘要只作为固定完整性与来源复核材料，不是独立签名或生产信任根。Linux musl/其他架构矩阵、现有uv来源及版本策略仍待定，不能据此宣称Linux/macOS引导修复。

当前完整Windows回归保持同一执行会话，尚在运行。为其对应版本准备干净wheel源码快照cache/wheel-route-python-tcpwwlna/source（245文件：运行文件及实际存在元数据），每文件SHA256见source-manifest.json，指针见cache/wheel-route-python-current.json。未复制缓存/测试/插件运行数据；LICENSE缺失不编造文本。此刻尚未build/安装/RECORD验证，不作为新产物验收；等待全套结束后构建以避免同时运行大量子进程干扰故障阶段验证。

## 当前独立工作树全套与wheel源码验证（2026-10-14）

Windows Python3.12.10全套2342项收集，2324 passed /18 skipped /417.19秒，执行会话退出0（cache/full-route-python-20261014.log）。覆盖截至Python目录预检查的全部本地增量；没有与大量安装/help子进程同时运行。18项skip与真实生产/其他平台范围仍须逐项处置，不能以此宣布八类计划完成。

干净源码快照cache/wheel-route-python-tcpwwlna/source的245文件哈希与当前运行源码/元数据一致。python -I -m build成功；wheel库存/源码逐字节/RECORD核验241运行文件、246成员，SHA256 c6dea0094c42e7a6bd74bd4d549a31f6f0364afcefe5a220018eae325f7e1c4b，inventory.json及build.log保留。许可证仍缺独立LICENSE文本，构建的setuptools警告为许可元数据弃用，不编造权利文本。新独立虚拟环境安装正在进行，尚未认定独立CLI验收。

上述新独立环境安装已退出0，直接从PyPI解析安装25项第三方运行依赖，pip check通过（install.log、pip-check.log）。源码目录外CLI验证退出0：94种help及查询/拒绝合计103项通过，未创建运行存储（installed-cli/verification.json、installed-cli.log）。实际安装版本逐项列入audit-requirements.txt，复用既有pip-audit工具对该完整清单执行审查，退出0、未发现已知漏洞，CycloneDX SBOM保存在sbom.json，日志audit.log。不是未知漏洞/生产签名/插件供应链证明。静态107存量、0新增，diff通过。全部来源与产物属于独立工作树，原冻结提交范围未改变；尚未提交或推送。全套通过和独立产物验收没有关闭路线图八类尚未完成的生产/跨平台/写事务门槛。

## Linux/macOS uv 归档引导替换（2026-10-14，独立工作树）

LinuxDriver.ensure_uv（Darwin继承）已删除第三方代理/官方远程脚本下载及sh执行，移除UV_INSTALL_SH/UV_INSTALL_SH_GHPROXY两无消费常量及四项因此无用的导入。新uv_bootstrap选择固定0.13.0官方Linux musl或macOS归档，uv_release保存GitHub指定版本元数据核对的固定SHA256（不是独立签名）；Linux x86_64/aarch64/x86/armv7/armv6与macOS x86_64/aarch64均有固定映射，其他架构明确拒绝而非执行错误架构产物。curl HTTPS/HTTPS重定向、最多10跳、64MiB文件、16KiB输出及120秒预算；校验SHA后共享有界tar（32成员/128MiB），固定顶层结构剥离、规则uv文件、0755和同文件系统exclusive hard-link发布；失败/取消清理私有临时目录，不覆盖竞争文件。现有私有uv拒绝链接/非regular/多hardlink/不可执行，现有父目录拒绝链接；未证明其签名/固定版本。安全归档下载要求curl，无curl时保留已有PATH uv回退，否则明确拒绝并由Recipe保留包管理/源码回退，不再使用无法按下载大小约束的wget脚本路径。

Windows主机联合79 passed /2.50秒（cache/uv-posix-bootstrap-20261014.log），Linux同14项完整测试14 passed /1.17秒（cache/uv-posix-linux-20261014.log），均退出0。WSL实际官方Linux x86_64 musl归档下载、固定摘要验证、共享提取及发布到唯一/tmp目录，运行uv --version输出uv 0.13.0 (x86_64-unknown-linux-musl)，私有复用通过；整个临时安装自动清理，无用户uv修改（cache/uv-posix-native-20261014.log、probe_uv_archive_native_20261014.json）。静态首次发现本次删除导致的4项新unused，已清理，复验107存量/0新增；diff通过。

macOS及其他架构映射仅有替身/固定摘要证据，未原生执行；curl版本、完整来源签名、既有uv来源、多用户权限/路径竞争、提取期间总deadline与持久化事务仍待完善。之前2324全套及c6dea0 wheel早于本批，属于历史里程碑；当前新源码没有再完整回归或重新打包，不能沿用它们宣布当前发布完成。

## Windows uv 固定版本/架构/摘要（2026-10-14，独立工作树）

WindowsDriver.ensure_uv取消浮动latest/x64 URL，复用uv_bootstrap.asset_name与uv_release固定0.13.0清单，按windows x86_64/aarch64/x86映射官方ZIP。新增三项Windows资产固定SHA256，来源同上指定GitHub发布元数据，仍非独立签名。下载10跳HTTPS、64MiB/16KiB、共享120秒剩余预算，归档逐块SHA256通过后才进入共享ZIP提取和发布；摘要失败直接拒绝，不通过PATH备用uv掩盖。删除无消费UV_WIN_ZIP_URL。父级校验改lstat/reparse tag，不依赖Python3.12才有的is_junction；已有uv要求regular、单hardlink、无reparse、可执行，不覆写不安全路径。

Windows uv/POSIX uv/软件联合80 passed /2.81秒（cache/uv-fixed-platforms-20261014.log）；随后增加三种Windows架构映射测试及父目录校验调整，两个引导模块34 passed /0.58秒（cache/uv-fixed-platforms-final-20261014.log）。两批重叠不可相加。当前Windows主机在独占cache临时目录实际下载固定官方ZIP并验证摘要、提取、执行uv --version：uv 0.13.0 (4ecd0eff2 2026-10-09 x86_64-pc-windows-msvc)，重复调用复用同私有文件；整个目录已自动清理，没改宿主uv（cache/uv-windows-native-20261014.log/json，退出0）。该真实进程在随后父级lstat调整前启动，不能用它代替该调整后的多账户/目录竞争验证。静态107/0新增；未再全套、重打包或执行macOS/ARM/x86原生产物；之前2324全套里程碑不适用于本批新源码。

## uv 引导共同职责合并（2026-10-14，独立工作树）

Windows/POSIX引导统一为uv_bootstrap.ensure_uv_archive，两个driver仅调用共享服务；删除Windows重复下载、摘要、目录校验、临时生命周期和异常处理。共享服务保留平台明确分支：ZIP唯一uv.exe及32成员/128MiB提取前限制，tar固定顶层剥离及同额度；Windows非覆盖rename、POSIXexclusive link。下载失败现在统一明确拒绝，不以其他PATH uv掩盖失败；没有curl时仍保留显式既有uv路径回退。清理Windows不再使用的归档/哈希/临时依赖导入，没有保留ensure_posix_uv别名或旧浮动URL常量。

Windows共享引导/软件联合83 passed /2.69秒（cache/uv-shared-bootstrap-20261014.log）；新增hardlink私有uv保留拒绝及未知平台组合测试后，引导两模块38 passed /0.98秒（cache/uv-shared-final-20261014.log）。Linux首轮33 passed /1 failed：原Windows已有uv夹具未设POSIX执行位，共享安全校验正确拒绝；夹具设置0755，产品可执行性要求不放宽。重跑结果见cache/uv-shared-linux-final-20261014.log。静态107存量/0新增，diff通过；原有实际官方Linux/Windows下载证据早于此共享重构，不冒称当前重构产物已全套/原生验收。
Linux修正夹具后的两个引导模块38 passed /2.39秒、执行会话退出0；保留首轮失败日志，不以重跑改写历史。固定架构映射在本轮仅为静态/替身证据，不能证明其他CPU或macOS实际执行。

## Python 活跃版本卸载前切换（2026-10-14，独立工作树）

针对明确卸载当前活跃版本且存在保留版本的路径，将fallback切换与验证移到物理目录删除前；所有候选失败不再恢复原路由/删除快照并继续，而是明确InstallError、保留原安装。取消立即传播。共享activate_version新增ActivationRollbackError（继承InstallError，原错误文本保持），补偿不完整明确使用该类型；Python卸载收到此错误立即停止，不试另一个版本或进行删除，避免不确定状态下继续变更。普通候选可恢复失败仍尝试下一个版本，成功后再删除并保存实际fallback版本。

新增四项在真实临时版本目录验证：全部普通候选失败、取消、回滚不完整时所有目录/数据/状态保留；首候选失败、第二候选成功时原目录在切换期间仍存在、之后删除并保留其他目录。Windows目录/版本激活/软件联合71 passed /2.79秒、退出0（cache/python-uninstall-order-20261014.log）。随后共享激活测试加强为明确要求ActivationRollbackError，Linux针对性验证见cache/python-uninstall-order-linux-20261014.log。静态107/0新增。

仅此特定路径整改，未声明完整卸载事务：--all、最后版本的原路由恢复、删除途中损坏/磁盘满、删除后快照保存失败及崩溃恢复仍开放；切换成功后删除失败保留的状态需完整日志事务进一步处理。当前未全套/打包，不沿用2324旧里程碑。
Linux上述三个模块22 passed /3.33秒、退出0，包含真实临时目录删除/保留、POSIX链接拒绝和真实解释器监督校验。

## POSIX Python 路由与配置恢复整改（2026-10-14，独立工作树）

LinuxDriver（Darwin继承）apply_version_link不再unlink旧命令后创建新链接，改共享shell_path原子链接发布，先要求绝对且存在的解释器文件；已有普通命令文件在发布与安装前快照阶段均拒绝，不再把普通文件路径记录为original_target然后删除该文件。恢复路径必须等于当前平台规定的python3路径，拒绝任意受污染快照路径。父目录lstat拒绝link/reparse/non-directory。保留原始readlink目标表示（含相对或dangling目标），恢复不再吞掉symlink错误；PATH注入/移除复用共享安全profile发布/补偿，删除手写rc拼接/遍历和异常吞噬实现。关联profile失败/取消后补偿旧链接；外部链接替换保持不覆盖、明确ActivationRollbackError，阻止卸载继续破坏性操作。

首轮Windows4 failed /77 passed /5 skipped：Windowsreadlink的扩展路径前缀与发布target字面表示不同，错误触发补偿不完整；生产修复通过共享_command_target作目标语义核对，并保留发布后真实readlink用于精确补偿，不放宽外部替换检查。重跑81 passed /5 skipped /2.44秒、退出0（cache/python-posix-routes-final-20261014.log）。Linux链接/profile联合37 passed /2.99秒、退出0（cache/python-posix-routes-linux-20261014.log）；后追加外部链接变更保留断言，Windows该模块8 passed /0.29秒、退出0（cache/python-posix-external-20261014.log），未声称该新增项在上述Linux37组内。静态107/0新增，diff通过。所有命令/链接/配置/系统锁均使用测试临时路径，不操作真实/usr/local/bin或用户rc。

此改动尚不是完整Python事务：发布前检查到os.replace之间仍有外部竞争窗口，原链接所有权/跨账户批准、profile已成功提交后的其他资源验证失败与持久化崩溃恢复仍开放；未迁移旧JSON shim或remove_shim未知文件删除；当前未全套或重打包，macOS没有原生证据。

## POSIX Python shim 所有权与补偿（2026-10-14，独立工作树）

LinuxDriver（Darwin继承）install_shim/remove_shim取消未知同名文件直接write/unlink；posix_shims在任何修改前对python3/python整组读取、bounded regular/single-link校验及完整模板所有权判断。fallback必须绝对路径，使用shlex.quote；所有权核对完整规范模板，不凭注释，也不接受额外shell指令。通过共享profile原子发布函数的新文件模式参数创建0755脚本，现有模式保留、卸载失败恢复原模式；临时文件、取消/关联PATH失败组补偿，外部变化保持拒绝并报告回滚不完整。未知额外文件保留，不删除非空shim目录。

首轮Windows2 failed /63 passed：POSIX路径/执行位夹具与Windows语义不同，新增PurePosixPath校验分支且把执行位断言限定到POSIX原生；执行位产品要求未放宽。重跑65 passed /6.75秒、退出0（cache/python-posix-shims-final-20261014.log），Linux三个模块25 passed /2.98秒、退出0（cache/python-posix-shims-linux-20261014.log）。随后追加真实POSIX执行测试：fallback文件名含空格/单引号，wrapper直接执行该脚本，two words、semi;literal、$(printf unsafe)均按原参数传递，不发生shell二次展开；结果见cache/python-posix-shims-native-20261014.log。静态107/0新增，diff通过。

本批保留旧JSON解析模板，仅修fallback引用与所有权，不能宣称已完成静态目标路由。关联profile自身事务已有共享补偿，但文件/profile成功提交后的全局验证、权限竞争、原链接身份及持久化事务仍开放；macOS没有原生运行证据，当前没有全套/新wheel。
Linux上述shim模块追加原生执行后8 passed /1.93秒、退出0；该测试晚于25项联合组，不相加冒充一次全套。

## POSIX Python 静态命令路由（2026-10-14，独立工作树）

posix_shims的新producer改为固定平台命令链接（LinuxDriver._symlink_target提供）及明确fallback路径的规范shell wrapper，路径全部shlex.quote，参数仅用"$@"传递；不再在每次命令调用时读取JSON、sed/head或依赖快照发布时序。版本切换更新稳定链接，快照仅记录状态。完整静态模板及精确route参与所有权校验；旧SHIM_SH_TEMPLATE更名LEGACY_SHIM_SH_TEMPLATE，仅作为迁移/删除时的完整历史模板识别，没有旧producer或长期双轨。模板以prefix/suffix拼接，避免路径包含{fallback}文本时被模板替换误改。

Windows四模块74 passed /1 skipped /3.38秒、退出0（cache/python-static-shims-20261014.log）；随后加旧模板迁移与真实切换测试，Linux shim/路由/profile联合28 passed /3.19秒、退出0（cache/python-static-shims-linux-20261014.log）。真实POSIX wrapper执行first、原生链接切换后执行second，wrapper字节不变且python.json未创建；fallback含空格/引号及特殊字符参数原样传递继续通过。迁移可识别原先规范旧模板并替换为无JSON读取的新wrapper。静态107/0新增，diff通过。

Windows Python仍为旧findstr快照路由，不能把POSIX进展称全平台完成。原平台链接路径依赖安装与切换权限一致，跨账户/权限转换及最终卸载恢复需继续验收；完整安装/卸载、当前全套与新产物、macOS原生证据仍开放。

## Python 生命周期联合复验与旧模板换行修复（2026-10-14）

近期Python/static shim/uv/链接/profile/Windows命令及注册表/安装入口共17模块联合首轮308 passed /7 skipped /1 failed，旧模板迁移失败：Windows text-mode writer产生CRLF，而完整所有权模板为LF。生产owned只归一CRLF到LF再比对完整模板及安全quoted target，不按marker放宽；新增LF/CRLF两种真实字节迁移夹具，其他control字符与任意代码仍拒绝。原始旧字节仍用于失败补偿。

Windows重跑310 passed /7 skipped /35.88秒、退出0（cache/python-lifecycle-joint-final-20261014.log），保留首轮失败证据。Linux12模块完整137 passed /19.35秒、退出0（cache/python-lifecycle-joint-linux-20261014.log），含真实shell切换/fallback/权限/进程组回收。静态107存量/0新增，diff通过。本轮联合范围不等于全项目，未生成当前wheel。路线图最新摘要已纠正：远程脚本引导和POSIX JSON producer已迁移；Windows Python JSON路由、完整生命周期、平台/生产信任仍开放，2324全套/wheel历史证据不移作当前发布结论。

后续还需明确处理已有owned shim缺执行位的情形：当前原子publisher保留已有模式，迁移文字本身不证明迁移后一定可执行；应补实际旧文件权限和失败后权限恢复测试，不仅检查内容。此缺口不因本轮137通过而关闭。

## 已有 POSIX shim 执行位与权限回滚（2026-10-14）

平台owned shim原子发布现按原模式加owner执行位，不扩大group/other权限；新文件仍0755。原0600修复为0700，same-content仅权限修复也纳入事务。共享_publish_profile新增可选受限mode_override（仅0..0777），默认profile/Windows命令调用行为不变；在临时文件替换前设置指定模式，不用发布后chmod留下窗口。POSIX shim事务保留原字节/模式，文本及模式共同核对提交/补偿。special permission bits在变更前拒绝，不尝试保存/恢复setuid/setgid；外部权限修改不覆盖，其他自有文件继续补偿，然后明确ActivationRollbackError。

Linux首轮32 passed /3.64秒，证明same-content的0600→0700及失败/取消恢复0600；随后补外部权限变更和special位，最终34 passed /3.65秒、退出0（cache/python-shim-modes-linux-final-20261014.log）。Windows共享publisher/命令/profile回归60 passed /10 skipped /8.70秒、退出0（cache/python-shim-modes-windows-20261014.log）；skip是POSIX原生执行/权限与shell场景，不能视为Windows验证执行位。静态107/0新增，diff通过。事务保护现包含权限，但身份/ACL竞态、多账户、持久化恢复和当前全套/wheel仍未完成。


## POSIX shim 部分发布权限回滚补验（2026-10-14）

新增两个真实POSIX故障注入：已有两份wrapper均为0600、内容不变，第二份文件在原子发布前或发布后抛出PermissionError。检查第一份及第二份字节和权限均恢复原状，确保共享文本事务的same-content快捷判断不会遗漏外层权限补偿。WSL Debian三个模块36 passed /3.79秒、退出0（cache/python-shim-partial-publication-linux-20261014.log）。该结果只覆盖本次shim/profile/链接范围，不代表Windows Python路由、完整卸载事务或全项目验收完成。


## Python 全部与最后活跃版本卸载顺序（2026-10-14）

全部卸载和删除最后活跃版本现在先预检查受管目录，恢复原路由并移除受管shim，成功后才删除解释器。最后版本此前漏掉remove_shim，本次补齐。快照删除仍在目录删除成功后；目录删除错误立即停止，不继续删除其他版本、不删除恢复记录。新增restore/shim普通失败与取消共8场景、两个成功顺序场景、一个删除失败场景；验证失败前解释器文件和快照保留。

Windows三个模块最新74 passed /2.56秒、退出0（cache/python-uninstall-final-order-windows-20261014.log）。Linux对应批次73 passed /9.04秒、退出0（cache/python-uninstall-final-order-linux-20261014.log），它早于追加目录删除失败测试，不能宣称包含后加场景。静态107存量/0新增。这里只修复破坏性删除顺序，restore成功但remove_shim失败可能留下部分路由变更；目录部分删除、全资源补偿、持久化事务、崩溃恢复及Windows旧JSON路由仍开放。不得称完整卸载事务完成。

Linux追加目录删除失败场景单独1 passed /1.95秒、退出0（cache/python-uninstall-delete-failure-linux-20261014.log）；与73项批次分别记录。按仓库CRLF规范检查diff通过；禁用autocrlf的检查会把现有CRLF误报为尾随空白，未据此批量改写文件。


## Python 实际命令身份与有界版本探测（2026-10-14）

新增version_probe共享实际解释器探测，Windows detect不再以快照active_version加路径存在判断成功；Linux/macOS detect不再使用无明确期限的run。系统版本查询和安装精确patch检查复用同一函数，成功退出且输出完整Python X.Y.Z才接受，兼容旧解释器把版本写stderr；单次5秒、合计stdout/stderr最多4096字节、stdin为DEVNULL，使用core.process进程树监督。超时、输出预算和启动错误视为不可用，KeyboardInterrupt传播。PATH命令采用which解析的实际路径执行。

首轮Windows四模块91 passed /2.10秒、退出0（cache/python-version-probe-windows-20261014.log）；Linux87 passed /4 skipped /7.98秒、退出0（cache/python-version-probe-linux-20261014.log）。这些结果早于将_python_bin_matches最后接入共享函数，当前源码完整Windows回归另行运行记录，不提前报通过。静态107/0新增，diff通过。Windows静态路由尚未实施；外部解释器/cross-volume/Unicode/原始参数传递不能用限制到private uv目录的方案冒充完成。


## 当前源码全套首轮与 uv 夹具迁移修复（2026-10-14）

本轮当前源码Windows全套2374 passed /27 skipped /17 errors /375.05秒、退出1（cache/full-python-lifecycle-current-20261014.log）。17 errors均为test_uv_windows_bootstrap的fixture仍从windows.shutil补丁which；版本探测重构移除驱动无用shutil导入后，该旧间接依赖失效。实际uv下载早已属于uv_bootstrap，共享模块已有shutil；本次夹具改在实际调用模块uv_bootstrap.shutil补丁，不为旧夹具恢复生产无用导入，也未执行真实下载。

修复后Windows六模块129 passed /3.69秒、退出0（cache/python-probe-bootstrap-repair-windows-20261014.log），含17项失败初始化场景、共享uv POSIX/Windows、最终_python_bin_matches共享探测、安装入口及软件回归。保留首轮退出1，不把分批复验拼成一次完整绿色全套。静态107存量/0新增，diff通过；当前新wheel和独立安装验收仍待完成。

Linux修复后同六模块125 passed /4 skipped /9.71秒、退出0（cache/python-probe-bootstrap-repair-linux-20261014.log），包含最终共享精确身份探测。四项skip是既有Windows安装专用场景。


## 插件 broker 请求结构与分发授权链（2026-10-14）

宿主broker现在拒绝非对象、未知字段、非call类型、非受限ASCII方法和错误args/kwargs类型；service.call只接受SDK现有四位置参数，describe只接受一个，外层kwargs为空，插件不能提交caller/fingerprint等宿主身份字段。服务业务args/kwargs原样保留，caller仅由manifest绑定。每次分发前重新检查当前及外层授权链，不等下一次轮询或提供方启动。移除broker的paged_select能力：现有guest SDK的分页回调在沙箱中执行，调用已适配的远程select；宿主不接收插件分页回调。

首轮新测试因误用pytest保留参数名request而收集失败（cache/plugin-rpc-contract-windows-20261014.log），改为payload后五模块90 passed /11.02秒、退出0（cache/plugin-rpc-contract-windows-final-20261014.log）。Windows全部test_plugin*.py共313 passed /1 skipped /55.56秒、退出0（cache/plugin-rpc-all-windows-20261014.log），含真实本地客户端进程树/期限/blocked-reply及授权/状态/资源日志测试；不是实际Docker RPC恶意样例验收。

本批只改宿主sandbox与测试/规范，不修改guest SDK代码库存。guest请求结构与现有producer一致；单独的RPC传输协议版本、严格JSON语义、完整字段/业务形状策略、实际容器出口和daemon恢复仍待完成。之前2374/27/17全套及129修复复验早于本批broker修改，不是当前源码完整发布证明。未提交推送，八类目标继续开放。


## Linux 单独插件测试暴露入口导入自举（2026-10-14）

Linux全插件首轮退出1，日志在生命周期CLI处出现F并未输出正常摘要（cache/plugin-rpc-all-linux-20261014.log）。隔离复验和禁用capture诊断保留于cache/plugin-lifecycle-linux-diagnose-20261014.log及cache/plugin-lifecycle-linux-nocapture-20261014.log；后者明确出现另一个解释器执行pytest/__main__.py但无_pytest的ModuleNotFoundError。根因是main模块在被导入时运行ensure_venv并exec；完整测试此前依赖test_boot_sequence在收集时伪造VIRTUAL_ENV而遮蔽这个副作用。

main现仅直接执行__main__、非Windows、非打包时自举。导入CLI不会创建项目venv或切换解释器；移除测试文件收集时设置VIRTUAL_ENV的全局补丁。新增真实子进程：移除VIRTUAL_ENV，bootstrap替换为失败哨兵后import main，确认哨兵未调用、CLI可用且未创建运行数据。Windows入口/启动/只读CLI四模块46 passed /2 skipped /6.74秒、退出0（cache/main-import-boundary-windows-20261014.log）。Linux全插件加入口模块合计362项重新执行，结果单独记录。

该修复不等于整个自举安全完成：直接源码入口仍保留旧自动环境初始化；core/venv_bootstrap.py的系统包安装、有界进程监督、get-pip.py来源验证与显式初始化职责仍待审查。不能将uv固定归档迁移称为移除了项目所有远程脚本执行。现存工作树.venv不作为垃圾自动删除，未更改用户解释器/注册表/真实服务。


Linux移除导入自举后，362项批次正常输出完整摘要：64 failed /296 passed /2 skipped /82.09秒、退出1（cache/plugin-main-boundary-linux-final-20261014.log）。隔离-x诊断确认main导入缺click（cache/plugin-lifecycle-linux-after-import-20261014.log），现有monitor测试venv仅装部分依赖；项目requirements/pyproject均已明确click>=8.1，未将环境缺包误判为删除click代码的理由。已在该cache隔离venv按requirements从固定TLS PyPI补齐click8.5.0、sentry-sdk2.72.0、urllib3及zstandard，未安装到系统解释器，日志cache/plugin-linux-runtime-dependencies-20261014.log。补齐后执行pip check及同362项复验，结果另记。


补齐依赖后的362项复验为358 passed /2 skipped /2 failed /91.27秒、退出1（cache/plugin-main-boundary-linux-deps-final-20261014.log）。第一项native fingerprint显式使用monitor虚拟环境却未导出VIRTUAL_ENV，直接源码自举仍exec到项目venv而缺typer；ensure_venv现按sys.prefix != sys.base_prefix识别实际外部环境并尊重它，增加无VIRTUAL_ENV单测，不靠伪造环境变量修复。第二项旧cross-FS测试抛出无EXDEV errno的OSError并mock旧shutil.copy2；当前生产只对EXDEV走有摘要验证的原子staging copy。夹具改注入真实EXDEV，实际复制并验证新字节、原0750模式、pending消失及无staging残留，不改生产代码吞掉任意OSError。

最终Linux同组363项361 passed /2 skipped /92.00秒、退出0（cache/plugin-main-boundary-linux-accepted-20261014.log），包括全部插件、main导入边界、CLI、启动、原生只读命令；命令显式移除VIRTUAL_ENV，pip check已通过。两个skip为Windows数据路径测试，不能代表Windows验收。当前宿主改动仍无全套绿色/新wheel/完整Docker RPC生产证据。

最终Windows相同363项360 passed /3 skipped /62.24秒、退出0（cache/plugin-main-boundary-windows-accepted-20261014.log）；skip为POSIX锁竞争及两项POSIX更新原生场景。静态107存量/0新增，diff通过。


## 插件线协议严格 JSON 语义（2026-10-14）

新增共享plugin_json编解码，宿主请求/回调解析/回复与worker请求读取/回调/结果均迁移。接收端拒绝任意层级重复字段及转义后同名字段，parse_constant拒绝NaN/Infinity/-Infinity，parse_float拒绝1e999等IEEE溢出；发送端allow_nan=False，非有限业务值不会写入管道。原业务字符串、整数、有限浮点、bool/null与明确Path编码保持。移除worker旧json导入，只有一份编解码行为。

Windows六模块首轮106 passed /17.06秒，随后增加两个实际本地客户端管道场景：重复type以及result value溢出均在宿主状态发布前拒绝，实际进程终止且stdin/stdout关闭；未导入合成插件的MUST_NOT_IMPORT代码，未接触Docker daemon。最终108 passed /17.52秒、退出0（cache/plugin-json-windows-final-20261014.log）；Linux同108 passed /20.10秒、退出0（cache/plugin-json-linux-final-20261014.log）。标准JSON、递归非有限发送、worker回复拒绝与Path编码测试均通过。静态107存量/0新增。

plugin_json.py明确加入RUNTIME_FILES；当前实际库存/SDK摘要记录于cache/plugin-json-sdk-inventory-20261014.json。新增guest文件改变SDK摘要，先前真实容器镜像和wheel证据不作为当前产物证明，旧镜像需重新prepare。传输协议版本、完整字段/状态机器策略、UTF-8与解析资源预算、真实Docker RPC及daemon/出口恢复仍开放。前批363项插件/入口复验早于本批SDK变更，不移作当前完整验收；没有本轮全项目回归或新wheel。


## 插件 RPC 严格 UTF-8 与合法 Unicode 管道（2026-10-14）

宿主原先先decode(errors=replace)再识别协议前缀，会把坏字节替换后作为JSON业务值解析。现在先识别原始ASCII协议前缀，正文严格UTF-8解码，UnicodeDecodeError作为固定invalid plugin protocol拒绝；普通日志仍替换坏字节显示，不改变原显示兼容性。

新增实际本地客户端二进制管道测试：0xff与过长0xc0 0xaf编码均拒绝，broker失败哨兵未调用、状态发布失败哨兵未调用、进程及stdin/stdout回收。随后追加合法原生UTF-8中文callback→实际宿主broker→JSON回复→中文result闭环，业务值逐字保留，普通日志0xff只显示替换字符。该成功路径将状态publisher换成记录哨兵，证明协议结果进入发布回调，不冒充真实持久状态事务验收。复用wire_client临时manifest/进程夹具，不复制准备逻辑，不执行MUST_NOT_IMPORT插件，不使用Docker daemon。

首轮Windows67 passed /10.08秒、Linux67 passed /10.65秒；追加成功路径后最终Windows68 passed /9.94秒、退出0（cache/plugin-utf8-windows-final-20261014.log），Linux68 passed /10.73秒、退出0（cache/plugin-utf8-linux-final-20261014.log）。静态107存量/0新增，diff通过。本批只改宿主解码与测试，guest库存仍为上一批22文件；新SDK实际容器、传输版本、解析资源预算与整体发布验收仍开放。

## RPC 传输版本与当前 SDK 原生验证（2026-10-14）

四个传输方向统一严格 rpc_version=1，仅接受类型为 int 的 1；缺失、bool、浮点、字符串及其他版本均拒绝。worker 在复制状态、适配 SDK 和导入插件之前校验；宿主在 broker 分发及结果发布之前校验。传输版本与 SDK api_version=2、服务业务版本独立，不提供静默降级。Windows 153 passed /19.59秒、Linux 153 passed /20.17秒（cache/plugin-rpc-version-{windows,linux}-20261014.log）。

当前 SDK 库存22文件，digest为3a51e795c0086d8694919d1d6f7ec5f025853704f8092b9fadf85e7ee643b981。WSL Debian 使用已固定官方基础镜像和Linux wheel哈希锁构建，镜像ID为sha256:20d1becd6384d442dd9c3b672a3303bf3abbd4a7a832d96b1b0631aa589b8c24。首次source RPC验证成功并确认20个worker移除；增强原生验证器后第二次验证成功：A1/B1/A2状态分区、未批准边在提供方启动前拒绝、未声明方法拒绝、实际业务8/声明7拒绝、外层及服务边撤权后禁止发布状态、缺失版本和版本2均准确触发invalid plugin protocol且持久状态不变，22个worker确认移除。

第二次证据为cache/plugin-rpc-native-version-negative-20261014/source-rpc-verification.json及对应log。两次各有两个cleanup pending告警；没有直接删除日志或手工假定资源已回收，使用生产reconcile与_cleanup_control核对同一backend/daemon后，两次均recovered=2、active=0、pending=0、invalid=0，无剩余JSON租约。第二次恢复证据为同目录source-rpc-recovery.json。自动清理告警原因仍待定位，恢复成功不能替代自动路径修复。

以上只验证当前源码宿主/guest及已知合成插件，不证明安装CLI/wheel、完整RPC状态机器、解析预算、真实OS取消、出口矩阵和daemon故障恢复。此前363项和全套记录是历史检查点；当前整体绿色回归与新发布产物尚未完成。

## Docker 自动删除竞争：同 daemon 缺失确认（2026-10-14）

隔离原生诊断cache/plugin-rpc-cleanup-trace-20261014确认三个删除命令返回1，耗时0.08–0.10秒，未修改的自动路径保留三个intent；生产恢复器recovered=3/pending=0。诊断使用有界控制结果，不输出环境或插件业务参数。

plugin_resources._cleanup现处理删除RuntimeError/SubprocessError：再次校验daemon身份和原标签/名称资源缺失，仅同daemon确认缺失才继续退休记录；资源仍存在、daemon改变、查询失败仍保留原intent，未直接吞掉删除失败。Windows相关56 passed /12.85秒，Linux同56 passed /12.18秒；新增container/network各四种删除失败后状态场景。

修复后的真实Docker复验cache/plugin-rpc-cleanup-confirmed-20261014.log退出0，无cleanup pending告警；cleanup-controls.jsonl记录四次删除返回1，均明确输出removal of container ... is already in progress，证实--rm与主动删除竞争。其后同daemon查询确认缺失，22个具名RPC worker确认移除，全部原生RPC正负场景继续通过。verification.json、recovery.json显示恢复器recovered=0/active=0/pending=0/invalid=0，平台plugin-resources无JSON记录，证明本轮自动路径已退休，无需事后恢复。本次只改宿主资源恢复模块，不改变guest SDK digest。静态107存量/0新增，diff检查通过。该竞争已验证修复；daemon断联、真实OS取消及全项目发布验收仍开放。

## 当前 RPC/清理源码 wheel 库存核验（2026-10-14）

使用独立cache源码副本构建，没有改动原工作区或全量测试使用的源文件。首次辅助脚本错误假定LICENSE存在而失败；第二次使用不含setuptools的静态审计环境，backend导入失败。保留两次日志，随后改用已有setuptools82.0.1/wheel0.48.0的项目测试解释器，无新增安装，第三次构建退出0。

cache/wheel-rpc-cleanup-current-built-20261014/dist内唯一wheel通过scripts.verify_wheel：246个运行文件、251个成员，SHA256 fe7f09ffcdbf2119fc6b566bc9dd98934ca2fa13ab73219856c6e88a5027e740。库存逐文件对照当前源码，检查额外/缺失成员、重复成员、总大小、唯一分发元数据、opskit=main:app入口、RECORD全部哈希/大小及自引用。verification.json及对应构建log保留。此处只证明wheel内容与当前源码一致，不代表干净安装后的CLI或生产验收。

当前全量Windows回归cache/full-rpc-cleanup-current-20261014.log正在运行，取得终态前不登记通过；安装后CLI检查待回归结束后独立执行。

## 当前源码 Windows 全量回归终态（2026-10-14）

cache/full-rpc-cleanup-current-20261014.log对应当前共享uv/Python生命周期、入口隔离、严格JSON/UTF-8/RPC版本以及Docker竞争清理后的生产源码：2453 passed /27 skipped /411.09秒，进程退出0。构建只在独立缓存副本执行，回归期间没有修改生产源码。该终态替代此前夹具错误批次作为当前Windows完整回归证据，但27个skip和Linux/macOS/生产信任仍不可视作通过。

新wheel已启动独立虚拟环境安装及源码目录外CLI验收，cache/installed-cli-rpc-cleanup-current-20261014.log；取得终态前不登记安装后通过。

## 当前 wheel 干净安装 CLI 验收终态（2026-10-14）

cache/installed-cli-rpc-cleanup-current-20261014.log退出0：独立venv安装上述当前wheel，pip check无依赖冲突，源码目录外94个help入口/合计103项检查全部通过。provenance确认main/core来自installed-environment/Lib/site-packages；显式清理PYTHONPATH/PYTHONHOME，验证结构化envelope/退出码、PID首样本/gone语义、无效参数和只读状态查询；runtime_storage_created=False。报告cache/wheel-rpc-cleanup-current-built-20261014/installed-cli/verification.json。不能以只读CLI检查替代真实安装/卸载及运行中故障验收。

## Windows Python 外部路由方案原生探针（2026-10-14，未接入生产）

核对当前WindowsDriver.apply_version_link/restore_original仍为空；SHIM_CMD_TEMPLATE仍用findstr读取JSON并删除路径空格，在括号块内展开ERRORLEVEL。不能将其他语言的私有目录约束照搬成Python外部解释器限制。

独立cache探针用Windows reparse API将临时.python-current目录junction指向已有外部解释器的整个安装根，只读取已有解释器，未修改解释器、用户注册表、PATH或profile。ASCII批处理通过%~dp0固定相对路径转发；普通解释器采用python.exe，venv采用Scripts/python.exe。实际C盘route→D盘base和C盘route→F盘venv均成功，route目录包含中文/空格/&/!/%；业务参数中文空格、&、%、!、内嵌引号、尾反斜杠逐值断言一致，退出码37正确传递，sys.prefix解析后等于原安装根，venv的base_prefix保留。临时junction在finally仅rmdir入口，没有删除target。

证据cache/python-windows-external-route-probe-final-20261014/verification.json。首次调用错误使用subprocess列表进行cmd二次转义，失败保留；第二次原始cmd调用通过但未断言完整参数，第三次添加逐值断言且修正尾反斜杠输入后通过。后两次均未改变生产源码。

方案可行不等于生产完成：现有共享junction helper强制目标在managed_root内部，必须增加显式外部目标授权边界，保留原Node/Go/Java限制；同时接入junction/命令/PATH/profile联合补偿、完整旧模板迁移与卸载恢复。不允许直接放宽所有junction的根约束，也不以标记字符串认领未知junction。生产旧路由仍待替换，最新已验收wheel仍包含该已知未完成项。

## Junction 显式外部目标事务边界（2026-10-14）

共享windows_junction的set_junction/remove_junction/junction_transaction新增可选authorized_targets，非空tuple且最多8项，规范化为精确目录集合；省略时保持原managed_root子树规则。显式集合替代子树授权，不自动授权其子目录。读取原junction、写入新目标、checkpoint、commit、rollback及删除均经同一边界验证，未授权既有目标拒绝，无目标目录递归删除。调用方仍负责从可信路由状态构造授权集合，不能从未知junction自身认领目标。

新增真实Windows创建/切换/删除后失败或取消的6项补偿、未知原目标/未授权子目录拒绝，以及跨卷普通Python执行。后者实际临时F盘route→D盘base，中文/空格/&/!/%目录和业务参数逐值保持，退出37，finally用生产remove_junction退休入口并确认原python.exe仍在。相关71 passed /7.32秒、exit0，cache/windows-explicit-junction-targets-20261014.log。既有Node/Go/Java私有目录与命令文件检查包含在同组中；静态107候选/0新增、diff通过。

本批只建立Python路由所需的外部目标事务能力，WindowsDriver尚未迁移，旧JSON shim仍存在。2453项全套与上一wheel均早于本批共享junction源码变动；不能移作最新源码整体验收。下一步以快照绑定原/新解释器安装根，接入命令及PATH/profile补偿，再验收生产Python切换/恢复和旧模板迁移。

## Windows Python 固定路由接入与安装补偿（2026-10-14）

WindowsDriver.apply_version_link与restore_original不再为空。新部署python/python3.cmd是ASCII固定相对路径，DisableDelayedExpansion且直接%*转发，独立行返回ERRORLEVEL，不读取JSON/findstr/where。受管shims/.python-current junction指向显式解释器安装根，普通解释器为根/name.exe，Windows venv为根/Scripts/name.exe以保留pyvenv.cfg探测；不限制为uv私有目录，外部跨盘目录允许。原SHIM_CMD_TEMPLATE只用于完整旧模板迁移，LF/CRLF/历史CRCRLF均识别，未知命令文件拒绝。

驱动创建时从快照绑定原解释器，避免activate_version先写新快照丢失原目标授权。apply将junction与两个命令文件置于补偿scope，remove同时补偿junction/命令与PATH操作；restore_original调用同一路由退休流程，不删除解释器。移除时同步清理当前进程PATH。Python安装激活复用activate_version发布/核验及失败回滚，而非先改路由最后写快照；PATH失败恢复原快照并切回已有解释器，首次安装则退休新路由。

Windows相关批次101 passed /9 skipped /19.77秒（cache/python-windows-static-routes-20261014.log），包括Windows原生路由、POSIX事务/身份/卸载/uv监督及共用命令。追加Recipe真实切换后单模块12 passed /3.54秒：重新创建driver绑定旧快照，再保存新快照、执行实际junction切换并核验PATH中的python版本。其余原生场景覆盖普通解释器/venv exact参数与退出37，remove PATH失败/取消恢复，第二命令发布失败保留旧路由，未知junction拒绝，首次/已有安装PATH失败回滚，以及三种旧模板换行迁移。测试PATH/profile写入口使用临时替身，不修改用户注册表或profile；命令执行、junction和解释器版本探测是真实Windows。

本批完整快照/命令/PATH/profile跨进程原子性、断电恢复、真实HKCU及全部卸载错误矩阵仍未验收。此前2453全套与246文件wheel早于本批生产源码，不作为本批整体验收；新全量及安装包需在路由批次稳定后重建。

Linux同批110项94 passed /16 skipped /17.86秒、退出0（cache/python-windows-static-routes-linux-20261014.log），在追加Windows Recipe原生切换测试之前执行；16项为Windows专属场景。该结果验证共享Python安装补偿与POSIX相关行为，没有将Windows跳过项算作Linux成功。静态107候选/0新增、diff通过。

## Python 路由真实 HKCU 临时键联合恢复（2026-10-14）

新增四个Windows原生场景，REG_SZ/REG_EXPAND_SZ各覆盖部分写后PermissionError与KeyboardInterrupt。只创建Software/OpsKitPythonRouteTest-随机UUID临时键，path_transaction定向该键，Path.home定向pytest临时目录，旧AutoRun访问跳过。使用真实Python driver、Windows profile发布、junction与命令事务，未改真实Environment PATH或用户profile。

先真实发布临时profile、命令和临时键PATH，再在删除PATH的SetValueEx完成后注入失败/取消；逐资源核对注册表值及type、全部profile字节、两个命令字节、junction原目标和进程PATH均恢复。随后正常删除成功，临时键PATH恢复原值/类型，profile不再包含平台块，junction退休；finally删除仅本测试临时键。

首轮76 passed /2 failed /14.15秒，失败为测试错误期待原始PermissionError；command_transaction约定将OSError转换为InstallError。修正为要求InstallError.__cause__保留PermissionError，取消仍原样传播；最终78 passed /13.77秒，cache/python-windows-joint-native-accepted-20261014.log退出0。未以改异常断言替代资源验证，所有恢复断言均执行通过。该证据覆盖临时键真实API和故障补偿，不证明多账户/断电/任意第三方并发下全局原子性。

## Python Windows 路由批次当前 wheel 与完整回归启动（2026-10-14）

独立源码副本构建cache/wheel-python-windows-routes-20261014退出0，verify_wheel逐文件与当前生产源码对照通过：246运行文件/251成员，SHA256 4d9c49184cabdea36f749e1279e750b46bf721af58fab1515c77052bdc62a074。运行库存、入口及RECORD验证通过，仅证明本批内容一致，安装CLI与完整发布验收未执行。静态107候选/0新增、diff通过。

cache/full-python-windows-routes-current-20261014.log已启动本批完整Windows回归，当前仍运行，取得终态前不计通过。先前2453全套和103安装CLI是路由接入前的历史里程碑，不作为本批完整通过证据。

## Python 路由完整回归失败与安装包独立验收（2026-10-14）

cache/full-python-windows-routes-current-20261014.log终态exit1：2476 passed /27 skipped /1 failed /422.65秒。失败为test_native_temporary_registry_partial_removal_restores_all_resources[True-1]，取消时junction_transaction的_restore触发rollback incomplete；底层异常原实现隐藏，尚未定位，不将其归为已证实环境抖动或忽略。

隔离记录器包装_restore后独立重复该测试20次，全部通过，cache/python-junction-restore-repeat-actual-20261014.log明确verified_runs=20。首次重复命令虽然collected20但实际只执行1项，另存cache/python-junction-restore-repeat-20261014.log，不计作20次。未捕获新底层失败，原完整回归失败仍开放。

当前wheel独立安装验收cache/installed-cli-python-windows-routes-20261014.log退出0：pip check无冲突，94 help入口/103 CLI检查通过，runtime_storage_created=False。增强scripts/verify_installed_cli.py在已安装环境-I子进程验证Windows路由；用户home/USERPROFILE定向专用临时目录，PATH限定System32，profile/注册表写入口替换为验证回调。真实driver/junction/cmd运行普通D盘base及C盘安装venv，exact中文/空格/&/%/!/引号/尾反斜杠参数、退出37、prefix解析一致，模块来源确认为安装site-packages，resources_removed=True。报告cache/wheel-python-windows-routes-20261014/installed-cli/verification.json含windows_python_routes。该原生验证没有写真实用户配置，也不替代真实临时键联合测试。

随后启动cache/full-python-windows-routes-traced-20261014.log完整重跑，诊断包装仅记录_restore异常类型/消息/winerror与测试nodeid，产物cache/python-junction-full-restore-errors-20261014.jsonl；尚未终态，不计通过。原异常需真实证据定位，不能用安装CLI成功或20次局部成功关闭完整回归失败。

## 诊断启动器 Windows spawn 修正（2026-10-14）

首次诊断全套在Python路由16项全部通过后，update CLI/download出现额外失败。核实ignored诊断启动器顶层调用pytest.main而缺少__main__保护，Windows multiprocessing spawn会重新执行入口并重复启动测试；该诊断运行受到启动器影响，不能作为项目新失败矩阵。确认命令行及父子PID后仅终止本次测试55080/7412进程树，session终态exit1；没有终止用户进程，也没有把未执行完的整套登记通过。

ignored启动器改为__main__保护，子进程只加载定义；通过同一带记录入口单独验证update CLI/download共44 passed /19.16秒，cache/diagnostic-launcher-spawn-guard-20261014.log退出0。生产源码没有为启动器错误修改。原正常入口完整回归的取消恢复失败仍保持开放，20次独立通过及首次诊断Python路由通过均未捕获实际底层错误。

修正后的完整诊断回归使用cache/full-python-windows-routes-guarded-trace-20261014.log，尚未终态。路线计划已精简重复历史数字，八类完成标准及原失败仍保留，逐批证据统一在本台账。

## 主入口保护后的当前 Windows 全量终态（2026-10-14）

cache/full-python-windows-routes-guarded-trace-20261014.log终态exit0：2477 passed /27 skipped /421.14秒。生产源码保持与本批246文件wheel一致；ignored诊断启动器只包装_restore并记录异常，Windows spawn主入口保护已单独44项验证。

诊断文件三条异常分别来自test_checkpoint_rejects_different_external_target、test_checkpoint_rejects_replaced_entry_even_with_matching_target和test_acl_restoration_failure_is_explicit_and_keeps_target_data，为明确故障注入且测试通过。原取消partial registry恢复失败未复现，无该nodeid底层异常记录。该终态证明本次当前Windows完整收集通过，不证明首次偶发失败的根因已修复；仍作为未解释恢复风险保留，不据此增加猜测性重试。

额外小规模RPC分配观察cache/plugin-json-allocation-evidence-20261014.json：1000个小对象/8076 wire bytes对应tracemalloc峰值192906 bytes，10000对象/80076 bytes对应1927826 bytes。宿主先loads_message构造对象再执行broker业务64KiB检查；单行32MiB上限不能等同解析对象预算。仅记录实测规模，不外推成未经测量的最大内存承诺。解析前深度/节点限额继续P0，尚未修改guest编码器。

当前源码Linux完整回归另启cache/full-python-windows-routes-linux-current-20261014.log，终态前不登记通过；Windows、Linux范围分开记录。

## 当前 Linux 完整回归与 ZIP64 标准库回补兼容（2026-10-14）

cache/full-python-windows-routes-linux-current-20261014.log终态exit1：2374 passed /123 skipped /7 failed /371.01秒。失败为ZIP64合法产物一项、依赖解析中文文本五项及更新应用成功模拟一项，不能登记当前Linux整体验收通过。

实际读取两平台zipfile._EndRecData64源码确认语义差异：Windows Python3.12保留普通EOCD位置，Debian Python3.11安全回补设置_ECD_LOCATION为ZIP64记录实际位置。平台原先始终减去ZIP64记录/locator长度，Linux发生重复扣除而拒绝合法产物。修复在声明ZIP64时读取该位置四字节签名，仅普通EOCD减去记录长度、ZIP64 EOCD直接使用，未知签名拒绝；没有按版本号猜测或绕过中央目录预算。增加带前缀ZIP64实际提取与未知位置拒绝验证。

TestResolverDeps只在测试内固定中文目录，不调用持久化switch；生产系统语言规则保持。更新成功模拟的Unix分支原为空操作，新版生产completed必须确认目标摘要，因此测试现在通过_atomic_copy将已签名合成产物写入本测试临时目标，再断言applied；Windows仍只断言scheduled。没有降低生产签名、提交摘要或恢复要求。

相关四模块Windows118 passed /13.87秒，Linux117 passed /1 skipped /10.43秒，日志cache/zip64-linux-backport-{windows,linux}-20261014.log；随后新增未知EOCD位置拒绝测试Windows单项通过，不冒充同一次119项。静态107存量/0新增、diff通过。旧2477 Windows全套与246文件wheel早于本批archive_reader源码，不作最新整体验收。

RPC解析前限额候选暂在ignored cache验证：511合法样例保持、4种超限在调用JSON parser前拒绝、1MB普通字符串预扫描tracemalloc峰值1214 bytes，证据cache/plugin-json-budget-candidate-verification-20261014.json。候选限定深度/值数量/数值token长度，额外内存与深度相关，不复制长字符串；尚未接入生产或发送端，不声明资源预算已完成。

新增未知ZIP64位置拒绝测试Linux亦单项通过/1.17秒（cache/zip64-unknown-location-linux-20261014.log），与上列117项分开记录；该分支两平台均拒绝非EOCD签名，不退回未经证实的位置猜测。

## RPC 解析预算当前源码复验（2026-10-14）

core/plugin_json.py已接入解析前文本、深度、值数量和非字符串token预算，保持重复字段、有限数值、严格UTF-8与传输版本要求。worker回复读取上限同步调整，合法超过1MiB回复不再被旧上限截断。发送侧预算在json.dumps后检查，不能据此宣称序列化分配或所有同步操作已受硬预算控制。

最终优化实现相关Windows137 passed /16.93秒，Linux137 passed /13.56秒，日志cache/plugin-json-budget-optimized-{windows,linux}-20261014.log。本批不以旧全套或旧wheel证明最新源码；新的完整回归、wheel与SDK原生验收仍待执行。静态债务复验107候选/0新增/0消除，diff检查通过；候选数不代表107项都可以删除。

原生容器预算验证session23578已终态，但日志为WSL启动swap.vhdx创建问题，没有生成预定结果目录，不能登记通过。随后wsl --list --verbose确认Debian为WSL2，wsl -d Debian --exec /bin/true退出0；基础启动现可用，不代表Docker/预算验收通过。当前ignored验证启动脚本读取为空，需恢复并复核启动器后再执行，不能盲目重复旧命令。八类目标保持未完成，Windows偶发取消恢复原因仍未解释。

## 当前 SDK 的 RPC 预算原生验收终态（2026-10-14）

恢复ignored启动器后session53560退出0，WSL Debian Docker20.10.24实际运行当前源码。专用输出cache/plugin-rpc-parser-budget-20261014；verification.json与rpc-verification.json记录深度65及超值数量消息均以invalid plugin protocol在持久状态发布前拒绝，旧/未知传输版本亦拒绝。正常业务版本7、调用方计数1/1/2、未审批调用、未声明方法、实际版本8不匹配以及运行中插件/调用边撤权场景通过。

使用固定base digest和既有Linux哈希依赖锁重建当前SDK，镜像sha256:031f6eee0c0024898cb625344f52561d95362ae5623adb8875b9f02df35f64b7。24个精确命名worker经同daemon查询确认不存在，production reconcile结果recovered=0/active=0/pending=0/invalid=0；并非通过手工删除journal获取清洁结果。该结果验证当前源码/guest预算及已列RPC场景，不代表完整信封/状态机、真实OS取消、网络矩阵或daemon故障已验收。新的wheel和最新源码全套仍需完成。

## RPC 结果与回复信封及终态顺序（2026-10-14）

继续审查发现结果后仍可分发call，且接收端接受混合/未知结果和回复字段。本批共用plugin_json增加结果/回复形状校验：成功结果精确为rpc_version/type/value/state且state为字符串；错误结果仅rpc_version/type/error且error为非空字符串。回复仅允许value、非空字符串error或严格True的cancel三种互斥形状。宿主在结果之后拒绝call，不分发能力也不发布状态；原重复result拒绝保留。

Windows相关94 passed /6.36秒，cache/plugin-envelope-windows-20261014.log，覆盖7类非法结果的真实客户端及资源回收、7类混合回复和结果后能力调用拒绝。静态107候选/0新增。最新guest代码再次改变SDK库存，上一批原生预算镜像不能证明此轮信封修改；需重新构建验收。

Linux全套session40168在本批源码修改前已启动，仍运行；将其结果作为启动时回归证据，不作为本批信封代码完整验收。Windows更广插件回归已启动，终态前不计通过。

## 结果信封更广 Windows 回归（2026-10-14）

cache/plugin-envelope-all-windows-20261014.log终态exit0：401 passed /1 skipped /2138 deselected /71.43秒，选择tests -k plugin，含配置并发、归档接入、CLI、插件控制/预算/状态/资源/信任/服务及wheel库存的匹配项。该选择范围不等于整个Windows全套；跳过项保留。静态债务107候选/0新增，diff检查通过。

scripts/verify_plugin_native.py新增缺state、成功错误混合结果与结果后call三个真实容器样例，要求精确失败边界且持久状态不变。当前SDK原生session74066已启动，专用cache/plugin-rpc-envelope-20261014；终态前不登记通过。Linux完整session40168仍运行；其启动前源码与本轮信封变动分开评估，不用混合过程证明最新源码全套。

当前结果信封SDK原生session74066终态exit0：verification.json中invalid_result_envelopes_rejected_before_state_publish、calls_after_terminal_result_rejected及allocation_limits_rejected_before_state_publish均True；已有正常RPC和撤权场景保持通过。27个worker已精确查询确认不存在，recovery.json为recovered=0/active=0/pending=0/invalid=0。此结论覆盖本轮列明的容器样例，不关闭完整初始请求、网络、OS取消和daemon故障矩阵。

## Linux 完整回归终态与最新信封局部复验（2026-10-14）

session40168终态exit0，cache/full-json-budget-linux-20261014.log：2402 passed /123 skipped /386.23秒。此前ZIP64、中文前提和更新模拟七项失败本次不再失败；运行期间存在结果信封源码改动，不能将此批当作冻结最新源码的全量证据。跳过项仍未验收。

随后最新结果信封代码单独Linux验证session93885退出0，cache/plugin-envelope-linux-20261014.log：94 passed /8.33秒，与Windows94项及当前SDK27worker原生结果相互补充，仍不替代最新干净全套和新wheel。

当前JSON预扫描观测首次大转义样例超过32Mi字符额度而拒绝，ignored测量脚本终态exit1，没有生成完整性能报告；这是测量输入超出合法预算，未为此放宽生产额度。修正样例最大输入规模后重新观测，终态前不宣称测量成功或提速。

## 当前预扫描观测及结果信封 wheel（2026-10-14）

当前生产预扫描2000个固定种子、混合Unicode/转义/容器合法JSON样例保持标准解析原值。cache/plugin-json-current-scanner-20261014.json包含6组字符串观测，字符串在追踪前创建，预扫描新增分配峰值152bytes；这是扫描器增量内存，不是原始文本、完整JSON对象或发送编码峰值。tracemalloc开启时大样例耗时约30秒，不能用此值表示普通运行成本。

另以关闭tracemalloc、每组3次运行记录cache/plugin-json-current-scanner-timing-20261014.json；6291458字符与16777218字符样例中位预扫描分别1.12694秒和1.62096秒。环境存在双平台回归竞争，无旧实现同负载对照，因此只报告当前成本，不宣称提速或完整硬期限。完整解析和同步调用预算仍有开放项。

当前源码快照构建session89100退出0，cache/wheel-rpc-envelope-current-20261014/verification.json：246 runtime files/251 wheel members，SHA256=2bf40c93c31088519beca3d676f60943a19860ed6ab3b5fd9604213ff57b0721。verify_wheel核对当前runtime库存、文件字节、成员集合与RECORD。该wheel含ZIP64修复、JSON预算及结果/回复信封规则；独立安装、入口和原生路由尚未执行，不借用旧wheel安装结果。

## 最新结果信封源码 Windows 全量终态（2026-10-14）

session97459退出0，cache/full-envelope-windows-20261014.log：2513 passed /27 skipped /481.07秒。本批启动后源码未改，覆盖JSON预算、ZIP64兼容及结果/回复信封和终态调用拒绝。旧取消junction恢复失败本次未复现，仍无原始底层错误根因证据；不以新全绿结果声称该偶发风险已修复。Linux最新全量session11351继续运行，未登记通过。

当前SDK原生镜像摘要补记sha256:9e35891af4a74dbdaedde04e8c61c63f757fddabaee4c46d9249ac177b083e58，来自cache/plugin-rpc-envelope-20261014/verification.json；不同于预算前镜像。新wheel独立安装脚本已准备，拟新venv且pip --isolated固定HTTPS PyPI解析依赖；为避免与尚在运行的Linux进程故障回归争用，尚未启动，不登记安装验收通过。

## 最新 Linux 全量失败与阶段监督测试修正（2026-10-14）

session11351终态exit1，cache/full-envelope-linux-20261014.log：2416 passed /123 skipped /1 failed /428.71秒。test_check_timeout_reaps_actual_started_worker在5秒总期限后未找到started标记，因此这次没有证明worker到达预定阻塞阶段，不能登记Linux最新全套通过，也不能仅凭缺标记断言真实回收缺陷。

测试现复用既有expire_worker_after_marker：启动有真实30秒上限，只有实际子进程写出标记后才使父监督器时钟到期；明确断言注入已观察标记，子进程及回收代码保持真实。未修改产品期限或增加生产重试。相关Windows44 passed /18.36秒，Linux复验仍运行。wheel运行源码没有因测试修正变化。

当前SDK资源恢复原生session独立退出0，cache/plugin-resource-envelope-recovery-20261014/verification.json七项通过：容器/网络活动租约保留；注入删除失败后资源和记录保留；reconcile随后删除精确归属ID并退役记录；同名而无归属label网络保留。该注入不等于实际daemon停机或身份变化，相关完整故障矩阵仍开放。

阶段修正Linux相关复验session4514终态exit0：43 passed /1 skipped /34.21秒，cache/update-check-phase-linux-20261014.log；Windows44项结果与其分别记录。原Linux全套失败保留，新完整复验待运行。最新wheel独立安装session14094已完成新venv依赖解析及pip check，当前入口/路由检查仍运行，不登记完整安装通过。固定PyPI解析包含Typer0.27.3、cryptography50.0.2等当前依赖；旧SBOM/漏洞扫描不能替代此次实际解析集合，需要更新。

## 当前结果信封 wheel 独立安装与依赖审查（2026-10-14）

session14094终态exit0，cache/installed-cli-rpc-envelope-current-20261014.log：新venv从固定HTTPS PyPI独立解析安装当前wheel，pip check无冲突。installed-cli/verification.json确认94 help入口、103 CLI检查、runtime_storage_created=False，并包含已安装Windows Python base/venv路由原生验证；入口来源、参数/退出码/prefix及临时路由资源清理由验证脚本断言，不写真实用户PATH/注册表。

从该新环境importlib.metadata导出25项运行依赖（排除平台自身、pip/setuptools），installed-runtime-requirements.txt为本次实际版本集合。独立审计环境pip-audit2.10.1按精确版本查询、无再次依赖解析，退出0且No known vulnerabilities found；生成runtime-sbom.json含25components/0vulnerabilities。--no-deps警告保留，不宣称这份精确版本清单有下载哈希或构成供应链来源证明。结果只针对本次已知漏洞库，不替代平台逻辑、未知漏洞、SDK全部架构或生产签名验收。

## 主计划历史状态整理（2026-10-14）

主计划把早期静态/驱动迁移批次及旧更新/发布状态完整移至docs/project-health-history.md，前者16018bytes按原字节保留，后者1192字符按原文保留，并用脚本断言内容未丢失。主计划保留八类执行表、第三方CLI原则、垃圾代码证据标准与全部发布门槛；新增明确历史链接，历史中的“当前”“下一批”不再冒充最新状态。历史风险不会因文档移动自动关闭。最新安装/依赖审查结果同步概览；源码未变，diff检查通过。

## 官方 traceroute 终态调用链复核（2026-10-14）

检查network.commands.traceroute→core.process.stream确认：stream通过StopIteration.value返回实际进程退出码，traceroute使用yield from却丢弃返回值，CLI只输出hop并正常退出。ignored合成验证cache/traceroute-exit-audit-20261014/verification.json：真实子进程输出一跳后退出7，strict=True迭代未抛出异常，退出码7被实际确认；未访问真实网络。官方参数计划的traceroute终态缺口仍存在，不能以帮助入口验收通过关闭。

下一步须保留流式hop，同时区分进程失败、期限、取消、目标未到达和完整结束。目的地址应在同一总预算内解析并固定，使用数字hop地址判断而非依赖本地化“complete”文案；进程退出0也不等于抵达目标。菜单原字符串流兼容保留，CLI提供最终机器可读状态及0/1/130语义。当前Linux全量session20744源码保持未改，待其终态后实施与回归；不将该审查方案登记为已经修复。

## Linux 阶段修正完整终态与 traceroute 终态实现（2026-10-14）

session20744终态exit0，cache/full-envelope-phase-linux-20261014.log：2417 passed /123 skipped /329.43秒。源码运行期间未变；测试阶段修正后此前worker未就绪失败本次不再出现。此全套早于接下来的traceroute改动，不作为新网络源码全套证明。

network.commands.traceroute现固定数值目标，DNS与命令共用总timeout；有限正预算在启动前校验，DNS耗尽不启动命令，IPv6按平台选择程序/参数。保留菜单字符串迭代，使用stream实际退出码，非零明确失败；只有数字hop包含目标且无不可达标记、进程退出0才reached，标题或全部星号不能冒充到达。CLI最后event=complete输出reached/not_reached/failed/timeout/cancelled；退出分别0/1/130，部分输出保留。预期Exit不被通用异常覆盖。

新增真实合成子进程及CLI验证：不同格式目标hop、标题、星号、!H、退出7、超时/取消/缺命令、DNS共享预算/耗尽拒绝、资源回收。首次双平台各3失败37通过捕获预期Exit被通用分支误接，修正后最终Windows40 passed /3.32秒、Linux40 passed /6.91秒，日志cache/traceroute-terminal-{windows,linux}-final-20261014.log。静态107候选/0新增，diff通过。

实际Windows tracert loopback127.0.0.1/max-hops2/timeout30经源码CLI输出完整reached/return_code0记录，cache/traceroute-native-windows-20261014.log；仅回环，不访问外网。未验证Linux/macOS真实命令或所有版本/权限，因此官方参数计划保留原生门槛。该改动使旧wheel不再代表最新源码，后续需要新的发布范围验证。

## traceroute 原生缺命令行为与新wheel（2026-10-14）

WSL Debian command -v未找到traceroute/traceroute6；未安装系统软件。实际源码CLI loopback127.0.0.1调用返回退出1且唯一终态event=complete/status=failed，cache/traceroute-native-linux-missing-20261014.log及.exit为证据。该结果只验收实际缺命令拒绝，不算Linux原生逐跳成功，macOS亦未验证。

包含网络修复的新源码快照wheel构建退出0，cache/wheel-traceroute-terminal-20261014/verification.json：246运行文件/251成员，SHA256=d961b8b4fac2e4fe05899e0bba2aa2accd740740f779a511424daf118cf8c37c；源码字节与RECORD校验通过。README补齐终态和退出码契约。独立安装验证已启动，終态前不登记通过；新源码全套尚未执行，旧wheel和全量结果保留各自范围。

## traceroute 当前 wheel 独立验收终态（2026-10-14）

session64400退出0，cache/installed-cli-traceroute-terminal-20261014.log：新venv安装及pip check通过，94 help/103 CLI检查通过；installed-cli/verification.json确认runtime_storage_created=False并含已安装Windows Python原生路由验证。

另在-I子进程、源码目录外实际调用安装main.app：installed-traceroute/verification.json确认main来自当前venv/site-packages，实际Windows回环tracert退出0，唯一event=complete/status=reached/return_code0，平台和插件运行目录未创建。该证明覆盖实际安装代码，不借用源码入口；Linux/macOS逐跳仍待验收。

新环境25项精确运行依赖导出installed-runtime-requirements.txt，与上一批wheel逐字节相同，故现有25项SBOM/已知漏洞查询适用于同一版本集合；没有重复扫描或宣称来源哈希。最新网络源码Windows全套session91312、Linux全套session70193已启动，日志cache/full-traceroute-terminal-{windows,linux}-20261014.log，终态前不登记通过。冻结本批运行源码，文档更新不改变wheel运行库存。

## 静态候选首批证据复核（2026-10-14）

复核core/config.py的os/APP_NAME、core/constants.py的Path、software/base.py的field：模块内无读取，仓库无对应符号导入/属性使用，未作为显式SDK或Recipe公开契约，也无这些名字的动态注册。四项清理决策及保留边界写入docs/static-health-gate.md；常量本体、dataclass、Recipe及异常接口保留。当前回归期间尚未删除，不冒充107→103已完成。其他候选继续逐项分析，尤其pytest fixture及公开导出不能批量删。

静态规范旧123项文字同步当前107项；基线及运行源码未改。最新Windows全套session91312与Linux全套session70193仍活跃，已核对句柄；观察超时不重启，不登记通过。

## 当前语言资源读取性能观测（2026-10-14）

scripts.bench_locale_load在同一39000byte zh.yaml上各7次，先断言当前读取与yaml.safe_load解析结果一致。Windows Python3.12.10：safe_load原路径中位200.965ms、当前冷读取22.335ms、缓存读取0.987ms；Linux Python3.11.2：201.223/38.496/13.098ms。两环境均有CSafeLoader，结果cache/locale-benchmark-current-{windows,linux}-20261014.json。

此观测存在并发完整回归负载，Linux资源位于WSL挂载Windows工作树；旧路径只解析已读字符串，冷读取包含文件I/O，因此如实给出操作范围，不视为标准化整CLI基准或通用启动加速承诺。源码与缓存实现未因观测修改。端到端冷/热启动p50/p95、RSS和其他负载仍须验收。

## traceroute 双平台全量终态与13项冗余清理（2026-10-14）

源码保持不变的完整回归：session91312 Windows退出0，cache/full-traceroute-terminal-windows-20261014.log为2524 passed /27 skipped /477.38秒；session70193 Linux退出0，cache/full-traceroute-terminal-linux-20261014.log为2428 passed /123 skipped /439.57秒。该范围包含traceroute终态修复，先于以下纯冗余清理，不能把其skip登记原生验收。

已实施docs/static-health-gate.md审核的13项：core/config的os/APP_NAME遗留导入、constants的Path、software/base的field，network/menu六个未用导入及show_dns两处/show_port_scan一处无副作用颜色赋值。保留其他函数实际使用的同名变量、Ctrl-C守卫/进程回收、主题和公开Recipe接口。基线同步移除精确13额度107→94，没有扩大豁免。

Ruff正确性All checks passed；静态94候选/0新增/0未清理已解决额度，diff通过。相关Windows配置/软件/菜单/网络/终态/基线七模块126 passed /9.51秒，cache/static-cleanup-13-windows-20261014.log；Linux同范围仍运行，未登记通过。未新增镜像或业务行为；旧wheel字节因四源码文件导入清理而不再匹配最新库存，新发布包需重新校验。其他94候选仍按证据审核，不要求删除必要接口和fixture。

Linux13项清理相关复验session64086终态exit0，cache/static-cleanup-13-linux-20261014.log：126 passed /16.68秒。双平台同范围保持通过；本次为已证明冗余的删除，不以此缩减完整项目的其余安全、发布与原生门槛。

## worker初始请求副作用前校验（2026-10-14）

core/plugin_worker新增精确初始信封与动作/身份/入口/参数组合校验，在_decode、copytree及SDK/插件加载前拒绝无效请求；服务契约格式抽取为共享校验函数，实际对象版本/方法仍在执行点复核。exec路径按PurePosixPath统一容器语义，避免Windows Path将/bin/sh视作相对路径。未改传输版本，CLI原始字符串参数保持。

新增21类无效组合真实worker.main拒绝哨兵和6类有效请求原值保持；与JSON/worker契约合并。首次Windows100通过/1失败暴露绝对路径宿主语义差异，Linux101通过；修正后最终Windows101 passed /9.94秒、Linux101 passed /6.79秒，cache/plugin-initial-request-{windows,linux}-final-20261014.log。最新guest改动需要重建SDK，不能以之前27worker镜像证明本批；原生当前SDK验证session82809已启动，终态前不登记通过。

13项清理wheel构建退出0，cache/wheel-static-cleanup-13-20261014/verification.json：246运行文件/251成员，SHA256=28b6b390f3d20a0cffc333d8be216f1985ab25ab6b67a55718bbbdea6bbb23e3，早于本批worker改动，后续须重建发布包。完整八类目标仍开放。

## 当前 SDK 初始信封原生负向验收（2026-10-14）

原生RPC session82809退出0，cache/plugin-rpc-initial-request-20261014/verification.json使用当前SDK镜像sha256:dd3482260778db1090cce5a3fc012b0f5f379a5c47c6f56ba5b4538ccb728b54，正常RPC、结果预算/信封、嵌套撤权等场景保持通过；27worker均删除，recovery全部0。

新增可复用scripts/verify_plugin_initial_request.py，只使用固定本地镜像、专用合成plugin/seed/request、nonroot/read-only/cap-drop/no-new-privileges/no-network/限额容器及归属资源lease。实际worker子进程的额外字段、caller逃逸、未知动作、保留入口、绝对exec、混合服务和非法契约7项均退出1，seed和import标记都未出现；合法对照退出0且两标记出现，证明不是测试无法执行插件造成全拒绝。session30642退出0，cache/plugin-initial-envelope-native-20261014/verification.json，8精确worker删除，production reconcile全部0。脚本不访问真实插件/凭据，不作为完整daemon/network故障证明。

Ruff正确性通过，静态94候选/0新增，diff通过。当前更广双平台插件回归及新wheel构建已启动；各自终态前不登记通过。

## 初始请求更广回归与junction补偿异常链（2026-10-14）

session91333 Windows插件选择回归退出0：428 passed /1 skipped /2149 deselected /85.45秒；session50265 Linux退出0：429 passed /2149 deselected /110.62秒。日志cache/plugin-initial-request-all-{windows,linux}-20261014.log；该范围不等同完整项目。initial-request wheel构建session39876退出0，246运行文件/251成员，SHA256=d5c50b901f46f0955fb9f1e8db0189ca19b1fedbaf519c21a7f64d74cdd921de，早于下面junction错误链改动。

审查证明junction_transaction和set_junction补偿失败使用from None丢失底层异常。现保留原rollback_error为__cause__，原操作异常保留在其__context__；普通失败字符串仍为固定rollback incomplete，不增加重试、不改变恢复步骤或授权边界。已有临时ACL恢复注入测试增强断言PermissionError原因和原PATH失败上下文都保留。Windows真实junction/事务/Python路由47 passed /11.74秒，cache/windows-junction-error-chain-20261014.log。该改动只改善可诊断性，旧全量偶发取消恢复失败仍未解释，不登记已修复。

当前包含初始请求、13项清理及错误链的新wheel重新构建中，必须以新库存核对结果记录；不借用旧wheel入口验证。完整八类门槛继续保留。

## 流式指纹与目录发现预算（2026-10-14）

上一批wheel已完成：246运行文件/251成员，SHA256=1f798f3b5e320e2ab27285130c1310ede0e3f1602840b3e0569b13c5e02f8909，cache/wheel-request-recovery-20261014/verification.json。专用环境本地无依赖重解析安装后pip check通过，94个help/103项CLI检查通过；此产物早于以下指纹修改，不能证明最新源码。

文件指纹改为64KiB分块读取，按实际读取字节扣减128MiB总额度；跨块CRLF/单CR规范化和后续NUL二进制选择保持旧算法。CHECKSUMS指纹实际读取上限1MiB。最初Windows测试因参数ID包含巨大字节串超出环境变量长度，49通过/8个setup错误；改为短ID后相关53项通过。Linux相关53项通过，强化stale-stat测试另1项通过。一次Windows单项未设置专用TEMP产生旧临时目录清理警告，未删除不确定目录。

指纹目录发现使用流式scandir，在收集和排序前限制8192条目和64层深度，包括空目录；提前剪枝版本库/缓存目录，拒绝符号链接、junction及特殊文件。有界收集后仍使用旧Path排序，保持正常插件指纹顺序。新增空目录洪泛、忽略目录禁止打开、深度和旧排序兼容测试。本批Windows57 passed /20.03秒，Linux57 passed /6.98秒，均覆盖test_plugin_digest_budget、test_plugin及test_plugin_trust_concurrency；Ruff F/E9与diff检查通过。

范围限制：snapshot复制和worker状态打包仍有独立rglob路径；CHECKSUMS YAML读取/解析预算、发送前编码预算、同步操作硬期限尚未关闭。本批不是完整安全验收；需后续统一遍历策略及真实故障验证，最新源码尚未重建wheel。

## 快照与状态共用有界遍历、校验元数据预算（2026-10-14）

上述目录遍历提取到core/plugin_tree.py，指纹、宿主snapshot与guest状态打包复用，固定SDK库存加入该文件。宿主遍历逐目录/条目调用执行期限检查，树配额错误转SandboxError；guest不再list(rglob)，4096条目限制包括空目录，状态不剪枝缓存同名目录以免丢用户数据。合成空目录洪泛、期限、禁止rglob和缓存同名数据保留测试通过。相关Windows137 passed /39.51秒、Linux137 passed /24.25秒；包含guest请求及SDK库存/构建契约测试。

CHECKSUMS读取最多1MiB+1探测字节，解析构造前限制YAML64层/20000事件并拒绝alias。正常生成清单不使用alias；手写alias清单需重新生成。write_checksums读取版本字段也使用该预算；现有异常降级行为保持。超额字节/深度/alias哨兵证明未进入safe_load，Windows57 passed /18.52秒、Linux57 passed /8.93秒（digest及plugin模块）。这不代表所有插件manifest读取入口已统一预算。Ruff正确性通过，静态94候选/0新增/0已解决；正常Git配置下diff检查退出0。临时关闭autocrlf的诊断将既有CRLF误报为尾空白，不据此批量改写仓库。

真实RPC运行session15416，输出cache/plugin-rpc-bounded-tree-20261014；报告未生成时不登记通过。当前SDK源码已变更，旧SDK结果不作本批证明。snapshot实际复制期间文件增长、符号链接替换竞争和同步I/O硬期限仍需进一步审查，发送侧及daemon/网络故障门槛保持开放；最新wheel尚未重建。

session15416现已退出0，报告与recovery.json已核对：当前SDK镜像sha256:44f21cc99a4c660ddc495cca9b858a1ba1b0fba07dfac5da24bfaa64356e7449，真实RPC授权/撤权、版本/结果信封、终态顺序及分配预算场景保持通过，recovery各项均0。此验证范围不包含新增空目录洪泛在真实容器内的故障注入，相关洪泛证据目前是双平台测试；不据RPC成功扩大其验收范围。

## 快照实际复制预算与身份复核（2026-10-14）

snapshot不再使用事前stat总量加shutil.copyfile：分块最多64KiB、按实际读取量扣减剩余额度，超额探测字节不写目标；每块检查共享执行期限。源必须普通单链接文件，打开句柄前后复核设备/inode、大小、mtime及当前路径，复制变化拒绝；目标以xb独占创建，失败/KeyboardInterrupt删除本次部分文件，不删除已存在目标。POSIX逐级目录fd+O_NOFOLLOW打开祖先和叶子，拒绝父目录链接替换。Windows仍使用身份/路径复核，不把它宣称为内核级无竞态路径授权或可抢占I/O。

状态seed复制取消源码缓存过滤，保留用户的.git等同名目录；源码仍剪枝。合成测试覆盖读取中增长、stat/open间真实文件替换、复制中取消、已有目标保留、硬链接拒绝、POSIX祖先链接拒绝及seed同名用户数据。最终相关Windows72 passed /1 POSIX-only skipped /29.74秒，Linux73 passed /16.42秒；早期71/72项运行对应增强前范围。Ruff F/E9及正常配置diff检查通过。

真实RPC session90191退出0：cache/plugin-rpc-copy-budget-20261014/verification.json，同一guest SDK镜像44f21cc99a4c660ddc495cca9b858a1ba1b0fba07dfac5da24bfaa64356e7449，27worker清除，recovery各项0；执行的是最新宿主复制流程。恶意复制增长/链接注入证据仍来自合成测试，RPC不代替这些故障矩阵。

新wheel构建退出0，cache/wheel-plugin-copy-budget-20261014/verification.json：247运行文件/252成员，SHA256=fc3382e7b0d8733651afe56b41a8554e65eb3d8ea3f2abe92dca7cf8d4d0f1d8，包含plugin_tree和最新复制/元数据实现。专用已有环境--no-index --no-deps安装、pip check通过；session48142入口验收尚运行，更广双平台plugin选择session98129/1419尚运行，终态前不登记通过。同步读取仍不能被期限检查硬抢占，文件内容指纹本身的打开竞态、Windows内核路径约束、完整发送/网络/daemon故障门槛继续开放。

后续终态：session48142退出0，最新wheel94个help/103项CLI检查通过；session98129退出0，Windows453 passed /2 skipped /2149 deselected /101.83秒。Linux日志455 passed /2149 deselected /123.93秒，但session1419外层sh退出包装出现Illegal number（最终命令退出1），不登记整条命令退出0；改用直接wsl/python调用复验，终态另行记录。

新增scripts/verify_plugin_tree_native.py已在固定当前SDK内真实执行：4097空目录、65层含文件树及指向/etc/passwd的链接均拒绝，.git/data合成用户数据保留。无宿主挂载、非root/只读根/无网络/限额，4worker清除、recovery各项0，cache/plugin-tree-native-20261014/verification.json。脚本不是硬I/O/宿主复制竞态证明，Ruff F/E9及diff再次通过。

Linux直接调用最终session92022退出0：455 passed /2149 deselected /88.22秒，确认包装脚本错误不来自pytest失败。最新wheel安装验收报告另核对103项检查、runtime_storage_created=false；94help库存与该wheel入口绑定。此前“尚运行”均为历史批次状态，本批所有已启动验证现已终止；全项目八类验收仍未完成。直接审查另确认core/plugin.py::load_manifest仍向safe_load传递无界文件流，后续需将清单入口统一到有界读取/解析，不能因CHECKSUMS预算修复就声称manifest安全。

## 清单与CHECKSUMS共用读取/解析预算（2026-10-14）

core/plugin_metadata.py统一host清单和校验元数据读取：1MiB实际字节额度及超额探测，YAML64层/20000事件/拒绝alias，在构造文档前拒绝。普通单链接文件要求、打开前后设备/inode复核、实际读取量与fstat大小/mtime复核，符号链接/特殊文件和替换拒绝。错误日志只记录异常类型，不回显YAML解析异常中的敏感内容。guest没有重新读取plugin.yaml，只接收host验证后的请求，因此不增加无用SDK模块，SDK镜像摘要保持上批。

清单、digest、plugin与服务契约Windows81 passed /1 POSIX-only skipped /20.45秒、Linux82 passed /8.36秒。后续增加日志脱敏和fstat后实际增长：metadata最终Windows9 passed /1 skipped /2.22秒、Linux10 passed /2.93秒，证明超额未进入safe_load、真实文件替换拒绝、FIFO未打开、日志无合成敏感标记；正常清单兼容。Ruff F/E9通过，静态94候选/0新增/0已解决，diff退出0。

新wheel248运行文件/253成员，SHA256=9fe25469b9c8a9def8c922a556a309b96ae530a3cfdb2cfe0bca7bc41adf10fd，cache/wheel-plugin-metadata-20261014/verification.json。安装入口session27693、完整Windows71884/Linux76843已启动，终态前不登记通过。清单目录list_manifests仍sorted(root.iterdir())，平台插件库存扫描预算需继续处理；读取的同步I/O硬监督、Windows路径约束、完整网络/daemon恢复及八类发布门槛保持开放。

额外安装版验证发现Windows GetTempPath返回ADMINI~1短路径时，resolve!=absolute错误拒绝正常文件。现改为逐级检查实际symlink/junction，不将合法8.3名称当链接。最终metadata/digest Windows25 passed /1 POSIX skipped /2.57秒，Linux25 passed /1 Windows skipped /6.87秒；新增GetShortPathName实际API检查。Windows安装版独立-I解释器实际ADMINI~1临时根验证普通清单接受、超额/深度/alias/坏YAML拒绝、4条错误日志不含合成敏感标记，installed-metadata.json绑定site-packages模块路径。

初次额外probe未设置专用OPSKIT_DATA_DIR，默认延迟日志可能写入用户日志（未删除用户日志）；修正后所有probe在import core前设置专用临时数据根并关闭logging后清理。此初次probe因短路径误判失败，不登记通过。另一次安装命令在build尚未终止时找不到wheel，仅pip参数失败、未改变安装；待build退出0后正常安装完成。

最终wheel248运行文件/253成员，SHA256=243a66fe3adc44c550fb7cb31462650ca537c1b2907bce0c931f9adebb5592e2，cache/wheel-plugin-metadata-final-20261014/verification.json；--no-index --no-deps安装退出0。上一个9fe25469 wheel的session27693退出0、94help/103入口通过，但不借此替代最终wheel专属入口证据。完整71884/76843对应短路径修正前源码；最终仅改变真实链接判定，相关修正后证据如上，完整回归终态另行记录。

完整回归终态已确认：session71884退出0，Windows2585 passed /29 skipped /560.42秒；session76843退出0，Linux2491 passed /123 skipped /541.35秒，日志cache/full-plugin-metadata-{windows,linux}-20261014.log。两者验证短路径修正前metadata，不冒充修正后的全量；修正后的metadata/digest及安装版真实短路径证据另列。最终wheel专属session61241退出0、94help/103项CLI检查通过，cache/wheel-plugin-metadata-final-20261014/installed-cli/verification.json；pip check、Ruff F/E9、94静态候选/0新增/0差异、diff通过。所有本批已启动测试/构建/安装过程均已终止。

## 插件库存扫描预算与错误传播（2026-10-14）

list_manifests不再sorted(root.iterdir())先无界分配。scandir流式计数最多4096个直接条目，隐藏/非目录计入；只收集普通可见目录并保持旧Path排序。最多256个候选清单，无效清单也计数；完整预检通过后才解析。超限、根链接或OS扫描失败抛固定PluginInventoryError，不以空列表/部分身份掩盖失败。CLI list/run按JSON错误及退出1报告；菜单捕获后报告并返回平台；clone后的身份冲突扫描失败回滚本次目录，不grant，既有目录保留。

相关Windows71 passed /29.64秒、Linux71 passed /41.87秒，涵盖库存额度/顺序、实际CLI list/run JSON、菜单返回、安装clone回滚及官方主入口边界，Ruff F/E9、94静态候选/0新增/0差异及diff通过。遍历O(N)，有界目录排序O(D log D)，N<=4096、候选<=256；不据此宣称正常CLI耗时提速。清单单文件额度仍1MiB，不将本批说成整体同步I/O硬监督。

新增scripts/verify_plugin_inventory.py真实CLI合成4097条目/257清单：Windows最终5项退出码/JSON检查通过，cache/plugin-inventory-native-windows-final-20261014/verification.json；超额plugin list/run退出1、官方software help仍0。Windows首轮subprocess默认GBK读取help发生readerthread解码错误，虽包装退出0，不能登记完整验收；明确UTF-8后复验通过。Linuxsession20776仍在运行，终态后再登记。全量及最新wheel248/253均早于本批，不作为本批发布包证明；后续随稳定安全批次重建，SDK源码未变。

Linuxsession20776现已退出0，同样5项真实CLI检查通过，cache/plugin-inventory-native-linux-20261014/verification.json。Linux使用UTF-8 locale，首轮未发生Windows的GBK问题；生产实现未因验证脚本编码调整变化。当前本批所有启动进程均终止，整体八类目标仍未完成。

## 快照复制的独立进程监督（2026-10-14）

core/plugin_snapshot.py复用network.worker现有spawn/Pipe有界JSON监督，host固定代码执行原有安全复制，不导入第三方业务代码。_invoke两次源码/seed复制改为_copy_supervised，分别使用同一调用剩余预算，准备过期不能继续启动业务容器。worker成功只返回ok，文件系统异常不回显路径/内容；parent超时/KeyboardInterrupt进入现有terminate/join/kill确认退出后再离开监督。复制仍发生在本次TemporaryDirectory内，不发布插件状态。没有新增不可取消的复制线程；冻结程序仍需原生spawn验收，不以main现有freeze_support代替。

测试真实spawn普通/超额/缓存同名状态复制，以及真实os.read管道阻塞后的期限/注入KeyboardInterrupt回收，确认marker PID不再存在。首轮测试未设置parent调用期限，3600秒fallback与仅推进31秒的测试时钟不匹配；确认归属后只终止两个测试阻塞child以释放测试，首轮Windows31通过/1失败/1跳过、Linux32通过/1失败，不登记成功。修正为明确30秒parent期限，最终Windows33 passed /1 POSIX-only skipped /15.03秒、Linux34 passed /14.01秒。取消是注入KeyboardInterrupt，实际OS信号矩阵仍开放。

更广插件回归Windows41796/Linux15060及当前SDK真实RPC33544已启动，终态前不登记通过。RPC缓存launcher增加__main__守卫，避免spawn重执行实验目录创建。当前SDK代码未变；host源码新增模块需重建wheel。Ruff F/E9、94静态候选/0新增/0差异、diff通过。范围限制：授权/指纹/运行时库存/状态提交同步I/O尚未全部进入监督；正常回收最多附加两次1秒等待，内核不可中断I/O及parent崩溃的孤儿进程仍需独立验收，不能宣称整体硬实时期限已达标。

Windows更广session41796退出0：477 passed /3 skipped /2149 deselected /126.22秒。Linux首轮session15060退出1：477 passed /1 skipped /2 failed /2149 deselected /219.87秒；两项test_execution_deadline_reclaims_local_tree_and_blocked_reply未进入客户端阶段，新增真实snapshot启动已消耗2秒总预算，owned为空。生产期限拒绝是正确行为，测试要验证的真实客户端树/阻塞回复回收未被覆盖。现测试允许30秒有界准备，tracked_start接入实际已准备客户端时将parent期限进一步缩小到min(既有deadline,now+2秒)，不放宽生产策略、不重置嵌套预算。修正后control/snapshot/execution-budget Windows51 passed /19.68秒、Linux51 passed /26.64秒；Linux更广最终复验另行记录。

真实RPC33544已退出0，cache/plugin-rpc-supervised-snapshot-20261014/verification.json：当前guest镜像44f21cc99a4c660ddc495cca9b858a1ba1b0fba07dfac5da24bfaa64356e7449，27worker清除，recovery各项0，执行的是新增host复制进程监督。新wheel249运行文件/254成员，SHA256=8f6afdd41120946b5b4b503189164036c50a161fba31f839cff24c2d57e36beb，cache/wheel-plugin-supervised-snapshot-20261014/verification.json。独立环境本地无依赖重解析安装、pip check通过；session67430退出0、94help/103CLI检查通过。独立-I安装版实际spawn源码复制、缓存同名状态保留、超额拒绝3项通过，installed-snapshot.json模块来源绑定site-packages，不读取真实插件/数据。此wheel包含库存预算，旧248文件产物不作为当前源码证明。

Linux最终更广session67716退出0：479 passed /1 skipped /2149 deselected /152.98秒，cache/plugin-snapshot-all-linux-final-20261014.log。Windows477通过/3跳过与Linux479通过/1跳过均为当前生产源码；Windows更广运行早于两个测试的阶段预算修正，之后相关51项亦已通过。所有本批已启动任务现已终止。下一阶段优先授权/指纹同步读取的监督及host worker父进程崩溃回收设计；不能把本批正常超时回收等同于完整OS取消、daemon故障、冻结程序或八类项目验收。

## 通用worker的父进程存活保护（2026-10-14）

network.worker的spawn target包裹存活保护；Windows复用core.windows_job共享Job创建/赋值逻辑，multiprocessing子进程先等待ready，parent成功分配kill-on-close Job后才放行。Job创建/分配失败仍回收child，不执行target。Linux child设置PR_SET_PDEATHSIG=SIGKILL并核对parent PID，补注册竞态；独立parent存活Pipe只有parent持有write端，child内daemon监听线程收到EOF即退出自身，补平台与启动间隙。监听线程只在可被监督回收的child内，不留宿主阻塞I/O线程。正常finally先关闭Job，并在finally中join/kill确认child退出；不会因Job关闭异常跳过process回收。

测试通过普通subprocess.Popen创建独立parent（刻意不使用已有core.process.start的Job，以免掩盖新增保护），child marker记录PID/创建时间后阻塞真实os.read，强制kill parent、不执行parent finally，确认child消失/仅剩Linux zombie。Windows首轮1失败/32通过：is_running与status间进程已消失造成NoSuchProcess，是测试观察竞态；修正后Windows33通过/10.69秒。Linux32通过/1 Windows-only跳过/19.71秒。额外Windows Job分配拒绝/任务marker未出现/无active_children残留，与parent crash两项合并2通过/0.58秒。macOS fallback与冻结程序、启动导入阶段以及内核不可中断I/O仍待原生证据，不将read阻塞测试扩成所有I/O证明。

更广双平台完整回归97569/31008、真实RPC17326已启动，终态前不登记通过。SDK库存未变。当前wheel249运行文件/254成员，SHA256=f975b602c650d6c9a5f23819af5c4eaf9cb43cf3fe98b401d2ecf1a9b4d02d24，cache/wheel-worker-parent-lifetime-20261014/verification.json；安装及入口另行验收。Ruff F/E9、94静态候选/0新增/0差异、diff通过。通用worker当前位于network.worker，core诊断/更新/插件复用同一实现；后续可统一归属核心模块，但不保留长期双实现或复制监督逻辑。

session17326已退出0，当前host的真实RPC27容器worker清除，cache/plugin-rpc-parent-lifetime-20261014/verification.json，recovery各项0，guest SDK镜像仍44f21cc99a4c660ddc495cca9b858a1ba1b0fba07dfac5da24bfaa64356e7449。本批parent crash测试验证的是host复制/查询worker回收，不是Docker daemon或真实容器父崩溃恢复矩阵。强制parent退出也不运行TemporaryDirectory finally，临时快照残留的持久归属/配额/安全清理仍需设计，不删除未经归属确认的系统temp目录。

session27716已退出0：本批f975b602 wheel本地无依赖重解析安装、pip check和94help/103CLI检查通过，cache/wheel-worker-parent-lifetime-20261014/installed-cli/verification.json。完整97569/31008仍运行，未以专项/安装通过替代全量终态。

首轮完整回归已终止且退出1：Windows2599 passed /29 skipped /3 failed /577.12秒，Linux2503 passed /125 skipped /3 failed /597.83秒。三项均为HTTP trickle/cancel的PreparedContext缺Event接口，未进入新监督路径；不是成功全量。修正fixture：真实预热child导入httpx后进入同一_owned_target、存活Pipe及ready门，parent测试上下文供应真实预热进程/两对Pipe/Event，只有监督器在Job分配后放行实际HTTP I/O；真实时钟、总期限、trickle请求及回收断言保留。修正后HTTP/DNS/parent-lifetime Windows18 passed /30.63秒、Linux17 passed /1 Windows-only skipped /39.99秒。Ruff F/E9及diff通过，生产源码未改、f975b602 wheel仍对应当前生产代码；完整最终双平台复验另行记录。

最终完整session59911/45230均退出0：Windows2602 passed /29 skipped /570.32秒，Linux2506 passed /125 skipped /575.86秒，日志full-worker-parent-windows-final-20261014.log和full-worker-parent-linux-final-20261014.log。此时生产代码稳定，本轮新信任存储测试尚未包含在全量收集范围内；下述生产修改后的源码不冒用此全量证明。

## 信任存储读取与失败保留（2026-10-14）

新增回归首先真实复现：损坏的YAML经grant没有抛错，原实现将读取错误转成空记录并继续写入。现_load复用load_plugin_metadata的普通单链接文件、实际1MiB、64层/20000事件、禁别名和读取身份复核；错误只记录类型，修改操作抛出TrustStoreError，原字节保留。只读is_trusted/is_service_authorized及trusted_record由同一只读包装拒绝授权。配置/信任锁顺序与提交时的_live_authorizations复核未更改。_save在atomic_write前验证同一解析预算，不能发布一个下一次读取会拒绝的文件。

解析器只将null变为{}，[]等非映射不再因布尔假值被掩盖。新增测试覆盖损坏、序列根、别名、深度、字节、事件、五种修改操作保留原文件、普通生命周期、目录、软/硬链接和日志脱敏。Windows首轮61通过/1跳过/20错误，原因是自动参数ID含大型样本导致临时路径过长，已改短ID；日志含大量合成样本，未读取用户秘密。Windows修正复验session41786退出0：71 passed /1 skipped /32.96秒；pytest清理历史系统TEMP测试目录报非空警告，未手工删除未知目录。Linux首轮测试71 passed /1 skipped /22.60秒，但bash外层退出变量丢失使脚本退出1，不计脚本通过；直接session91245退出0：71 passed /1 skipped /35.62秒。Ruff F/E9通过。

新增publication超额保留用例后，更广插件/配置回归session21219（Windows，固定隔离TEMP的guarded launcher）和24859（Linux，直接pytest）已启动，终态前不登记通过。Windows首次尝试内联Python启动器因cmd引号失败，尚未执行pytest，改cache内文件启动器；产物均不提交。guest固定代码库存不含plugin_metadata/plugin_trust，故本批不改变SDK摘要；旧wheel未包含本批修复。同步硬期限、默认config读取、原生竞态与完整八类门槛仍开放。

Windows更广session21219已退出0：562 passed /3 skipped /2102 deselected /209.26秒，cache/trust-store-all-windows-final-20261014.log，含publication超额保留用例。Linux24859退出4，命令包装引号导致未收集测试，不能登记更广通过；替换为guarded文件启动器session39800，cache/trust-store-all-linux-final-20261014.log，仍等待终态。此前Windows失败批次的PowerShell日志尾读session8964/88920/52443仍未返回，不能把测试日志的结束行当成包装进程已终止；后续已使用有界字节读取，避免打印大型参数ID。

最新wheel通过pip本地无索引/无依赖重解析构建，session67047退出0；scripts/verify_wheel.py验证249运行文件/254成员及RECORD与当前源码一致，报告cache/wheel-trust-store-budget-20261014/verification.json，SHA256=7f365ff31bfd0a0fb067b6f017f7e6c9599e32e5559ea4d9594a7124d10cc20d。专用installed-environment安装session18525退出0、pip check退出0；安装CLI验收session42686仍运行，不能登记94help/103入口通过。旧installed环境已被本批wheel替换，历史报告仅证明当时版本。静态94候选/0新增/0基线漂移、Ruff F/E9和Git diff --check通过。两次查询build工具失败（源码build.py遮蔽模块/隔离环境无build包），实际改用现有pip wheel成功，不新增工具依赖。

session42686现已退出0：本批wheel的94help/103CLI入口检查通过，installed-cli/verification.json绑定site-packages来源和runtime_storage_created=false。大型测试参数ID造成的三个PowerShell尾读进程已按精确命令、PID及创建时间核实，确认无测试子进程（仅各自conhost），回收PID72180/73756/74348；对应session8964/88920/52443终态15是主动回收日志读取包装进程，不冒作pytest成功或失败结果。日志原件保留，后续只读尾部4096字节避免重复无界日志处理。Linux更广39800仍运行。

Linux更广session39800最终退出0：564 passed /1 skipped /2102 deselected /429.34秒，cache/trust-store-all-linux-final-20261014.log，与Windows562/3范围一致（平台跳过差异）。本批所有已启动验证和日志读取包装进程已终止；当前生产代码的插件/配置相关更广回归、wheel库存和安装入口验证通过。先前完整2602/2506回归仍只对应本批生产修复前代码，不把它称作当前源码完整回归。下一批默认config的读取/解析预算以及指纹/CHECKSUMS独立进程期限监督继续开放；配置/信任原子提交复核必须保留。

## 配置读取/发布预算及共享默认对象（2026-10-14）

元数据读取器迁移为core/bounded_yaml.py，所有清单/CHECKSUMS/trust调用与专项测试同步迁移，旧plugin_metadata.py已移除，无长期双实现或兼容shim。generic loader支持更小但不能超过1MiB的读取预算，保留实际读取探测、普通单链接文件及身份/路径检查；解析只将null转换为空映射，禁别名、64层/20000事件。load_config默认分支也采用这一读取器，拒绝非字符串键映射根并只返回固定错误；原有update调用的65536字节预算不变。save_config在原子写入前验证root和相同解析预算，ensure/单键写入在坏配置时拒绝而保留字节；save_config仍为明确整份替换接口。_deep_merge现在复制默认树，避免读取后修改update.enabled等嵌套字段污染DEFAULT_CONFIG及下一调用。

新增20项配置预算测试覆盖损坏/非映射/非字符串根、alias/depth/bytes/events、三种读取/迁移/单键路径保留原字节、缺文件时非法limit、解析前小额度检查、发布失败保留文件和调用方快照、默认树隔离、软/硬链接/目录与POSIX FIFO拒绝。相关配置/元数据/信任/授权并发/SDK库存/update CLI专项session12313退出0：Windows123 passed /2 skipped /39.08秒；session92864退出0：Linux124 passed /1 skipped /46.27秒。日志config-budget-targeted-{windows,linux}-20261014.log。Ruff F/E9、94静态候选/0新增/0漂移、diff检查通过。没有修改真实运行配置。

配置在guest固定库存内，因此加入bounded_yaml.py并重建当前SDK，不能沿用旧镜像证明。guarded真实RPC验收session37253退出0：provider/a/b均使用sha256:716ccc9a03e847c4742d8dc967f0d9f54dedf79224036f9ec6ef3485dfaddab4，cache/plugin-rpc-config-budget-20261014/verification.json；removed_workers27，state分区/嵌套撤权等既有场景通过，recovery的recovered/active/pending/invalid均0。仍不代表完整daemon故障、全部OS取消、出口或生产供应链门槛完成。

新wheel在独立源码清单副本构建，避免旧build/lib残留被重新带入：cache/wheel-config-budget-20261014/verification.json，249运行文件/254成员、RECORD和当前源码一致，SHA256=4c0e59674002cb62b864fc401b24beeb44a23599dc212e8cf2201b39a3f0a6ca。专用环境本地无索引/无依赖重解析安装和pip check退出0；installed-cli验收77569仍运行。本批全量Windows13859/Linux33618仍运行，终态前不登记通过。旧信任批次wheel已被替换，其报告仅证明当时产物。同步授权/指纹硬期限、锁内原子复核设计、父崩溃文件归属恢复和八类整体目标继续开放。

session77569现已退出0：当前4c0e5967 wheel的94help/103CLI入口验证通过，cache/wheel-config-budget-20261014/installed-cli/verification.json记录site-packages来源和只读入口未创建运行存储。本批双平台全量13859/33618仍运行，尚无终态；生产代码保持稳定。next授权监督批次需要把只读CHECKSUMS/内容指纹放入剩余调用期限监督；授权配置/信任读取的结果不能在锁外被当作提交许可。提交持有configuration→trust锁时可使用受监督的只读查询，但必须在同一次锁区间内完成期限、调用链和服务边复核及状态提交；不得在worker内重新申请父进程已持有的写锁。当前尚未实施此设计，不把预算限额称作I/O硬期限。

配置批次最终全量13859/33618均退出0：Windows2657 passed /30 skipped /613.83秒，Linux2562 passed /125 skipped /628.64秒，full-config-budget-{windows,linux}-20261014.log。此全量发生在下述内容检查监督生产修改前，尚未收集新inspection测试，不作为后续源码的完整回归证明。

## 内容检查独立进程及真实RPC性能回退（2026-10-14）

core/plugin_inspection.py复用network.worker监督器，在独立host进程执行CHECKSUMS和内容指纹，返回固定字段且严格校验64位hex身份；不导入第三方业务，不返回配置/信任许可。sandbox全部compute_fingerprint调用及check_authorization校验均走受监督读取，服务批准末次指纹复核也改为同一入口。单次检查取min(剩余共享期限,300秒)，启动前和返回后核对原期限；超时映射固定错误，KeyboardInterrupt传播并回收worker。配置/trust启用与服务边复核及提交锁未迁移，manifest/catalog/config/trust等其余同步I/O期限仍开放。

首轮相关Windows18397退出0：93 passed /1 skipped /32.10秒，Linux77932退出0：94 passed /65.65秒。随后加入父期限启动前/返回后用例，共11项内容检查专项；更广30315退出0：Windows592 passed /4 skipped /2102 deselected /198.14秒；85352退出0：Linux595 passed /1 skipped /2102 deselected /336.07秒。日志inspection-all-{windows,linux}-20261014.log。测试覆盖真实os.read阻塞、期限/取消回收、CRLF同指纹、checksum不匹配、坏worker结果/脱敏与共享期限。静态94候选/0新增/0漂移、Ruff F/E9通过。

最新wheel55e0b754d1d3576bd5e14a43ca392c7fadd3af2c4d93e0c08f4032cab401d431，250运行文件/255成员，cache/wheel-inspection-20261014/verification.json；独立清单副本构建、当前源码/RECORD核对、专用环境本地安装和pip check通过。44558退出0：94help/103安装入口通过。本批host修改不改变guest库存，SDK仍716ccc9a；旧249文件wheel已被替换，其安装报告仅证明当时版本。

真实RPC54749退出1：正常嵌套count超过原有30秒期限，未登记本批原生通过。cache/plugin-rpc-inspection-build-20261014.log保存错误，finally恢复账本各项0。诊断29475退出0只是成功生成测量报告，不表示RPC通过：原隔离合成lab执行一次count，仍在30.007秒失败（content inspection deadline）。inspection-timing.json记录29次inspect合计16.962秒、平均0.585秒（范围0.346–0.674秒），6次copy合计3.831秒；没有修改原30秒配置，也没有使用真实插件/数据。此测量仅为当前WSL/合成RPC负载，不扩展为发布性能结论。

结论：逐次spawn方案不能按原生验收关闭。下一批需复用一次调用及嵌套调用的受监督只读进程，以去除反复启动成本；每次必须重新读取/计算内容，不能缓存旧指纹或许可，也不能放宽30秒来掩盖回退。进程所有权、父死亡回收、取消和整体期限复用已有共同机制，避免复制一套Job/lifetime逻辑。要验证复用worker的阻塞读仍可回收、没有跨调用泄漏，以及原30秒真实RPC完整矩阵通过。当前生产原型保留待修，不提交或发布；所有本批已启动测试/构建/原生/诊断任务均已终止。

## 调用范围内复用受监督内容读取器（2026-10-14）

network.worker提取owned_process和wait_result，原run与ContentInspector使用同一Job/ready门、Linux父死亡/存活Pipe、回收及期限轮询，没有第二套生命周期实现。资源创建部分失败也关闭已创建Pipe；已有真实HTTP预热夹具/进度/取消语义保留。ContentInspector懒启动，固定256KiB RawArray+长度+Event邮箱提交受限JSON请求，父进程没有请求管道写入的阻塞路径；一次只读请求、线程归属与重入检查，worker每次重新计算内容，不缓存指纹或许可。错误/取消/超时使reader关闭且不能复用，作用域finally回收。execution_budget显式记录inspection_scope，内层共享、外层释放；只有deadline注入但没有作用域的单次检查仍使用同一算法的一次性监督路径，不泄露无人持有的长期worker。

专项17658退出0：Windows45 passed /32.16秒；22072退出0：Linux44 passed /1 Windows-only skipped /50.45秒。包括修改文件后同PID新指纹、嵌套作用域共享及外层回收、新调用新reader、session真实os.read超时/取消且关闭后拒绝复用、过长路径不启动，以及共用所有权下的HTTP trickle/deadline/cancel和共享预算。独立parent原始Popen fixture增加session模式，强制kill parent时实际阻塞读取worker退出；没有借用core.process的Job掩盖新增保护。补充Windows session Job分配拒绝1 passed /0.26秒，拒绝时不执行请求循环、无active_children残留。静态94候选/0新增/0漂移、Ruff F/E9及diff检查通过。

64141退出0完成同一隔离lab、原30秒配置下的诊断：value0、总11.042秒；29次inspect合计1.2598秒、平均0.0434秒，同一PID892，cache/plugin-rpc-inspection-20261014/inspection-session-timing.json。此前逐次spawn的同lab诊断在30.007秒失败、29次16.962秒。两次是不同时间的单次合成负载，不作p50/p95、平台普遍提速或发布性能门槛证明；复用与逐次重读另有真实测试证据，不是通过放宽期限或跳过内容复核。

28299真实RPC退出0：cache/plugin-rpc-inspection-session-20261014/verification.json，SDK仍716ccc9a，既有provider/a/b正常count、调用方状态分区/嵌套撤权等矩阵通过，27worker已清除，recovery各项0；原30秒参数未改。仍不代表完整daemon故障、方法/API/出口、冻结程序和全部OS取消矩阵完成。当前更广全量41978（Windows）/44396（Linux）仍运行，生产源码保持稳定；本批新Job用例已在启动全量前加入。

新wheel779e5456f0fced062846866009509f08a80be706b6bac8bc0307224e2473c402，250运行文件/255成员，cache/wheel-inspection-session-20261014/verification.json验证独立源码清单副本/RECORD与当前源码一致。专用环境本地无索引无依赖重解析安装、pip check通过；安装入口66054仍运行，终态前不登记94help/103通过。旧55e0b754原型wheel已被替换，其报告仅为当时源码。本批仍未关闭manifest/catalog/config/trust同步硬期限、父崩溃文件归属恢复、macOS/冻结程序及八类整体目标。

本批完整终态已核实：41978退出0，Windows2675 passed /30 skipped /624.14秒；44396退出0，Linux2579 passed /126 skipped /721.55秒，full-inspection-session-{windows,linux}-20261014.log。66054亦退出0，当前779e5456 wheel的94help/103CLI入口通过，installed-cli/verification.json绑定site-packages来源和runtime_storage_created=false。本批生产源码保持稳定，完整回归、当前SDK既有真实RPC矩阵和最新wheel证据均有对应终态；所有已启动测试/构建/诊断/原生任务已终止。下一批继续config/trust实时授权的受监督读取及锁内原子复核设计，以及manifest/catalog/其他同步I/O、父崩溃文件账本恢复；不能以本批内容检查监督关闭整体平台安全/性能或八类项目门槛。


## 独立授权读取后的测试夹具复核（2026-10-14）

此前更广Windows回归48459退出1：13 failed、683 passed、4 skipped、2106 deselected，198.79秒，日志standalone-live-broad-windows-20261014.log。13项失败涉及主进程授权模拟未传递到独立读取进程；保留失败记录，不通过恢复生产同步读取来适配测试。

prompt guard改用临时真实信任/配置存储，并避免替换共享time模块时钟；80214/75697均退出0，Windows100 passed /41.93秒，Linux100 passed /57.20秒，日志standalone-live-ui-{windows,linux}-20261014.log。broker contract同样改用实际隔离信任记录；外层撤权用真实grant/revoke验证，保留业务路由断言。随后73829/85298均退出0：Windows72 passed /29.67秒，Linux72 passed /33.34秒，日志broker-live-{windows,linux}-20261014.log。本批只修改测试，不改变运行时wheel；更广回归与当前源码最终全量门槛仍待完成。

已核对67339退出0：当前c81def912717b02f66c83ced5e65616cd03226e8def0ac3a09c28710ae16aef8 wheel的安装、pip check、94help及103入口验证通过。73083本地所有refs历史扫描退出0，覆盖323 commits、0 findings；未fetch，不代表未获取远端历史。此前候选487文件秘密扫描早于本批测试/文档变化，仍需最终提交清单复扫。八类整体目标仍未完成，未暂存、提交或推送。


## SDK运行时目录监督（2026-10-14）

core/plugin_catalog.py集中负责有界普通文件身份读取、六字段执行身份和当前SDK摘要检查；ContentInspector增加catalog操作，_image复用同一调用reader与剩余预算，独立入口拥有完整预算作用域。单次最多30秒，返回后再次复核期限；worker失败固定拒绝并回收。原image标签/指纹检查保持。build.py清理一处无占位符f-string，不改变构建行为。

34858/46862均退出0：Windows70 passed/1 skipped /52.11秒；Linux70 passed/1 skipped /60.12秒，catalog-supervision-{windows,linux}-20261014.log。13项新增用例包括真实新鲜目录、SDK变化拒绝、坏文件不进入Docker、worker阻塞/取消回收及响应验证。静态候选仍94/新增0/漂移0；Ruff F/E9排除已登记F401/F841后通过，diff检查通过。首轮Ruff检出旧build.py F541后已清理复验通过。

27906/18073均退出0：此前授权夹具修复后的更广Windows696 passed/4 skipped/2106 deselected /261.57秒，Linux698 passed/2 skipped/2106 deselected /353.64秒，standalone-live-broad-fixed-{windows,linux}-20261014.log。两者在目录监督源码编辑前启动，不作为本批当前源码完整回归；目录新路径以本批71项收集的专项证明。

新wheel413ce61252b8e70af4232786454328f6959e0055f70321120b48d2b43ed89cdc，252运行文件/257成员，30852构建及源码/RECORD核对退出0，cache/wheel-catalog-supervision-20261014/verification.json。安装/94help/103入口79599和当前完整合成网络容器85176仍待终态，不提前登记通过。SDK guest库存未变；网关catalog、manifest及状态发布监督等八类整体门槛继续开放，尚未提交或推送。


## 网关目录同步缺口一并关闭（2026-10-14）

上述SDK-only批次79599安装退出0，94help/103入口及pip check通过；85176当前SDK目录实际合成网络插件退出0，cache/catalog-supervision-native-20261014/verification.json，恢复账本四项0。随后继续修改网关目录读取，不将这两项旧终态作为最终源码证明。

网关image的有界YAML读取和gateway_source摘要也迁入同一reader，严格四字段/POLICY2/固定base检查。父进程保留真实Docker标签复核，独立image入口包围完整_execution_budget；RuntimeCatalogError区分目录错误与deadline，不能将超时误当有效目录。测试改用正式平台网关源码，避免父进程mock无法传给spawn读取器。新增真实同PID网关变化/撤销、禁止父源码读取及阻塞前不进入Docker用例。11782/4641退出0：两平台均101 passed/1 skipped，Windows42.89秒、Linux61.33秒，catalog-gateway-supervision-{windows,linux}-20261014.log。

最终原生40404退出0，cache/catalog-gateway-supervision-native-20261014/verification.json。真实prepare/invoke使用正式gateway359ebe45与SDK ad31a18c，无解析器覆盖；受控HTTP未授权/授权/撤销，建立CONNECT后的注入取消/身份撤销/私网撤销及网络清理后提交前撤销通过，已提交proof保留、未提交文件未发布，recovery四项0。仅证明当前Docker IPv4、轮询撤销和合成插件，不证明实际Ctrl-C、完整DNS/全部署或daemon故障。

最终wheel f1e957c5299d91780f621d35fe0f04b4d1ccee853a65d477edb7125e6b7460f5，252运行文件/257成员，32998构建/源码/RECORD核对退出0。当前安装入口21245、更广Windows30989/Linux74456仍运行，终态后登记；前一413ce612产物被本批运行时源码替代。静态候选94/新增0/漂移0，相关Ruff及diff检查通过。

计划首页和授权设计已合并过时的“仍运行/同步回退未移除”表述，保留当前架构与未完成边界；历史结果保存在本台账，不再在多个计划重复复制。manifest完整传输、服务声明、状态发布/崩溃恢复及八类门槛仍开放。没有暂存、提交或推送。


21245最终退出0：f1e957c5 wheel本地无索引/无依赖重解析安装、pip check及94help/103入口通过，cache/wheel-catalog-gateway-supervision-20261014/installed-cli/verification.json。另以安装环境python -I在源码导入路径之外执行实际ContentInspector，核对四个模块均来自site-packages，SDK/网关身份、同reader新鲜目录重读及进程回收通过；证据cache/wheel-catalog-gateway-supervision-20261014/installed-reader/verification.json，日志wheel-catalog-gateway-installed-reader-20261014.log。此额外探针只使用新建临时目录，无Docker或真实用户配置写入。当前更广30989/74456已确认仍运行，不标记整体通过。

30989现已退出0：当前目录/网关监督源码的Windows更广回归711 passed/4 skipped/2106 deselected，288.87秒，catalog-gateway-broad-windows-20261014.log。Linux74456仍运行，尚无终态。


## 阶段提交与完整执行清单读取监督（2026-10-15）

用户先后确认347项文件范围及中文提交说明后，创建39fad3d2ab30f6224934acec44f425bd09eb9f5b，标题“重构 平台安全边界与独立插件运行机制”；普通push HEAD:develop成功，远端ls-remote同SHA。原F盘工作区文件及恢复备份保留。提交前当前源码全量终态：Windows67247退出0，2791 passed/30 skipped/834.60秒；Linux12830退出0，2695 passed/126 skipped/802.73秒。对应cache/full-catalog-gateway-{windows,linux}-20261014.log；前述74456更广Linux亦退出0，713 passed/2 skipped/374.81秒。所有结果只证明该阶段源码；提交不代表八类整体验收。

本批把_invoke初始及复制后load_manifest改为同reader受监督读取，单次30秒且不超过父剩余期限。load_manifest与纯_parse_manifest分离，父进程复用规范化谓词检查所有字段并绑定请求root。完整JSON最多8MiB，复用256KiB共享邮箱、精确总长/偏移/块长，所有帧共享deadline；非最后块复制后ack，最后块无ack，防止吞掉下次请求。错误/取消关闭reader；指纹、完整快照比较和实时授权继续保留，无同步回退，也不执行插件业务代码。guest源码库存未变。

首轮测试缺少短parametrize ids，1.1MiB超额样例被pytest作为测试名称传入PYTEST_CURRENT_TEST环境。Linux65909终态1 failed/52 passed/1 skipped，Windows日志53 passed/2 errors（环境变量超长），均不当作生产通过。两个Windows尾部输出包装器65566/33570因打印巨型测试名输出阻塞；确认只剩本批PowerShell和conhost、无测试子进程后只停止这两个输出包装器，工具exit15，原失败日志保留。修复仅为测试添加短名称，未放宽生产边界。

修复后4507/71001均退出0：Windows60 passed/54.60秒，Linux59 passed/1 skipped/62.72秒，manifest-supervision-{windows,linux}-r2-20261015.log。新增28项包含100KiB与Unicode600KiB完整等价、入口/服务字段、同PID混合普通/分块请求、输入超额/错误帧/布尔整数混淆/root注入，以及真实os.read阻塞的初始/复制后清单与后续块超时/取消、确认reader回收且不启动容器。Linux跳过沿用既有Windows Job平台专属项。

此前独立实验两平台各200次同reader请求均完整等价且回收，证据F盘cache/manifest-chunk-reuse-{windows,linux}-20261015/verification.json；仅为实验，不替代本批正式调用链用例。相关Ruff通过，diff检查通过（Git报告既有自动CRLF转换提示）。新wheel e01f71c3162a826251861d93d6cbe07f130e939fe8e887a08a376b615096d0bb，252运行文件/257成员，81600构建及源码/RECORD核对退出0。安装、pip check及python -I真实安装目录的完整清单新鲜读取/混合请求/独立scope释放探针已经通过；完整安装CLI、更广双平台670项选择回归及真实合成网络插件仍在运行，待终态后登记。

服务发现/list_manifests目录I/O、状态发布与崩溃恢复、完整出口/daemon矩阵及其他八类门槛继续开放。本批后续改动尚未提交或推送。


本批终态：42509/93628均退出0，Windows667 passed/3 skipped/2179 deselected/515.19秒，Linux667 passed/3 skipped/2179 deselected/664.89秒，manifest-broad-{windows,linux}-20261015.log。37768安装/依赖检查/真实安装目录探针及94help/103入口退出0，安装包仍为e01f71c3，证据wheel-manifest-supervision-20261015/{installed-reader,installed-cli}/verification.json。静态94候选/新增0/漂移0，Ruff F/E9排除既有F401/F841后通过，diff检查通过。

原生61256退出1：第一份新输出的实际插件invoke在资源清理后、状态发布前_remaining期限复核拒绝，尚未发布proof；recovery四项0，原manifest-supervision-native-20261015.log及其隔离目录保留。不得把失败当作通过或直接归因于并发负载。随后等待双平台相关回归全部终态，使用仅增加父进程阶段计时的cache/trace_manifest_native_20261015.py，未调整生产谓词、未替换读取器、未放宽30秒配置，建立全新独立数据目录复验。76735退出0，manifest-native-traced-20261015/verification.json中passed=true、recovery四项0；正式SDK/网关与完整合成网络插件的未授权/授权/撤销，CONNECT就绪后的取消、身份撤销、私网撤销、清理后提交前撤销均通过，已提交proof保留，未提交状态未发布。7次invoke、14次完整manifest读取；timing.jsonl中清单读取约2.5–65.2毫秒，三次成功invoke约7.5–10.4秒。仅是该轮计时，不是代表性p50/p95或首轮失败原因证明。首轮期限边界仍保留在性能/故障矩阵待分析项，不能以第二次成功抹去失败。

本批正式源码与专项/更广回归、wheel、安装和当前本机原生验证已有对应证据；不宣称全量2849项或远端六组合CI已重新通过。服务发现目录读取等下一批仍开放，八类整体目标未完成。本批7项工作树变更尚未暂存、提交或推送。

## 插件库存与服务选择读取监督（2026-10-15）

list_manifests已转入同scope的_read_inventory；原有4096条目/256清单扫描界限、确定性顺序、坏清单跳过和重复身份保留不变。读取器完成全目录额度检查后再按all/name/service筛选，父进程只解码所选清单；每条最多8MiB、完整列表允许超过8MiB。条目间非末条ACK与分块确认共用绝对期限，精确校验数量/字段/目录名/规范化清单/选择匹配，中途错误不返回部分结果。服务入口共享预算，访问解析改为新鲜受监督身份与授权边复核，无同步信任读取回退，不执行第三方业务。

62453/26126退出0：Windows88 passed/57.33秒、Linux88 passed/62.59秒；额外75763/39923日志均3 passed，Windows16.07秒/Linux15.70秒。证据inventory-supervision-{windows,linux}-r1-20261015.log及inventory-extra-{windows,linux}-20261015.log。覆盖真实阻塞扫描、取消回收、部分结果拒绝、路径/数量注入、混合请求同PID、新鲜变更、重复身份拒绝和服务共享期限；15份大清单验证总量超过8MiB仍完整返回。原始扫描器单测使用私有_list_manifests，CLI额度用例在实际spawn子进程注入测试限制，不允许生产请求覆盖额度。

新wheel 53c6229821434f2f8141afac63c6502f65879bd5ca77b402419dc2c6b4e651aa，252运行文件/257成员，源码/RECORD核对通过。52635安装验证退出0：离线无依赖重解析安装、pip check、94help/103入口通过；python -I实际site-packages探针验证四个模块来源、同reader完整清单/库存all/name/service新鲜读取、公开list_manifests和提供方选择、reader回收与独立scope释放。证据wheel-inventory-supervision-20261015/{verification.json,installed-reader/verification.json,installed-cli/verification.json}及install-inventory-supervision-wheel-20261015.log。

本轮全项目Ruff及静态基线检查退出0：94候选/新增0/已解决未移除0。真实源码CLI库存界限探针已产生5项预期结果：条目超额时plugin list/run明确失败，清单超额时list明确失败，两种情况下官方software help仍成功；证据F盘cache/inventory-cli-supervision-20261015-r1/verification.json，包装器终态另核对。Windows73469/Linux11060完整回归已确认仍运行，尚不记录全量通过。真实Docker RPC安排在重负载回归终态后，不以先前网络插件复验代替新服务路径验收。状态发布I/O、崩溃归属恢复、完整出口/daemon矩阵和八类整体门槛继续开放；本批没有暂存、提交或推送。

库存CLI探针34760终态退出0，前述5项检查全部通过。另完成下一批状态恢复问题的真实子进程探针：cache/probe_plugin_state_crash_20261015.py只对F盘新建目录执行_publish_state，第一步旧root改名成功后子进程os._exit(73)；父进程实际_copy_tree生成空restart-seed，旧值old仍完整位于self.previous，新值new保留在staging。探针退出0，证据F盘cache/plugin-state-crash-boundary-20261015-r1/verification.json。它证明重启准备阶段可能错误地把中断事务当作首次运行，不证明备份被删除，也未触发真实插件或Docker。状态归属与恢复方案和验收矩阵已补入授权监督设计；尚未修改运行时，完整回归仍对应当前库存批次源码。

状态崩溃边界探针在WSL Linux亦退出0，行为与Windows一致：子进程第一步改名后退出73，old备份保留但实际重启seed为空；Linux证据/tmp/opskit-plugin-state-crash-boundary-20261015-r1/verification.json。当前候选提交秘密扫描35169退出0，491文件/0发现；工具发行包与可执行文件摘要已按既有固定校验核对，无仓库allow注释或忽略文件豁免，证据F盘cache/secrets-inventory-tn2_o0fe/{inventory.json,summary.json,report.json}。只证明本批候选文件，不替代远端未知历史或运行配置检查。

验证效率实验：100个4KiB一次性文件的写/读/删，3轮中位数Windows挂载目录1.8206秒，Linux /tmp为0.02034秒；cache/linux-test-filesystem-probe-20261015.json。当前并发负载下的小样本，不推导整体pytest或平台加速倍数。为后续Linux验证把已扫描的491个候选文件复制至/tmp/opskit-inventory-source-20261015-r1，逐文件SHA256核对，库存摘要1800859300323608f04174bfa14d5a12bc71b8e6fe0bf9f0a2ae2aad936e7b18；没有复制Git元数据、cache或用户运行数据。源码仍以工作树为权威，后续改动须重新建立快照。现有11060完整回归继续从原位置运行，没有因观察超时重启或替换；Linux快照仅额外运行两项真实库存新鲜读取/服务共享reader烟雾测试，终态另核对，不能替代完整回归。

Linux文件系统快照烟雾验证已退出0：2 passed/1.65秒，linux-source-snapshot-smoke-20261015.log。实际测试路径来自/tmp快照，覆盖同reader清单变更和服务授权共享reader；没有把挂载位置微基准当作完整回归性能结论。

Windows完整回归73469终态退出0：2847 passed/30 skipped/772.50秒（共2877项），inventory-full-windows-20261015.log。该结果对应库存监督批次运行时，尚无状态恢复修复；不能作为未来源码的证明。Linux11060仍在运行，保留同一handle继续观察。

Linux完整回归11060终态退出0：2751 passed/126 skipped/834.71秒（共2877项），inventory-full-linux-20261015.log。双平台完整回归均已终态，不含尚未实现的状态恢复。随后启动Linux文件系统独立源码快照的真实RPC验证，使用既定审核base与完整哈希锁及新建/tmp输出目录；仅拷入任务拥有的launcher/锁文件，生产策略与读取器无替换，最终资源回收结果单列记录。

真实RPC27461终态退出0，使用491文件逐项哈希验证的Linux文件系统源码快照，无生产读取器/策略替换，SDK仍ad31a18c。合成provider与两个caller计数依次a=1/b=1/a=2，证明认证调用方状态分区；未授权边在提供方启动前拒绝；未声明方法、实际API版本8与声明7不一致、旧/其他传输版本、深度/分配额度、缺失/混合结果信封、终态后调用均拒绝且不发布状态。嵌套身份撤权与捕获服务边撤权异常后均保留原状态。27个拥有容器经逐项查询确认已移除，recovery四项均0，未清理其他资源。证据已从/tmp复制至F盘cache/inventory-rpc-native-20261015-r1/{rpc-verification.json,recovery-verification.json,snapshot-verification.json}；日志inventory-rpc-native-20261015-r1.log。该测试不覆盖完整RPC状态机、实际OS信号、daemon故障或所有网络矩阵。回查当前四个运行时模块SHA与本批验收记录完全一致。

至此本批库存/服务读取监督专项、双平台完整回归、wheel/安装、CLI真实库存额度及本机真实RPC已有对应终态。计划继续状态发布崩溃恢复与I/O监督；八类整体验收仍未完成，未暂存/提交/推送本批新增修改。后续运行时改动必须重新关联验证，不能引用本批结果当作新版本通过。

## macOS验收接入与状态重启拒绝边界（2026-10-15）

用户授权通过ghost-macos验收。SSH首次无记录时使用accept-new记录新主机密钥，未禁用主机密钥检查。环境Darwin22.6.0/macOS13.6.5 Intel，4核/16GiB；SSH默认Python3.9.6，但/usr/local/bin/python3.12已安装3.12.2，无需修改系统Python。未发现Docker命令或Docker.app。仅创建/private/tmp/opskit-acceptance-20261015.Dzybna，使用独立venv及数据/插件/temp目录，没有启动真实服务或改用户项目。

首轮快照491文件，归档SHA 1ec5ff242af385a60862806b3a5201cd375fb93b202500d7761b7fb4de28a1e0，库存SHA f80fe9edd151d3da0467bef33547e1fe1e5ffa0cd166cd97c858d78ccd5256fa。远端先核对两份文件SHA，再逐条验证成员类型/路径/大小/摘要后复制，不传缓存、Git、凭据或克隆插件。依赖安装/pip check/Ruff/静态94候选基线已退出0；完整回归及安装包阶段21859仍运行。该固定快照不包含后续本地状态防护与测试修正，不能声称当前新源码完整通过。

macOS实际回归发现test_verify_binary_valid_elf把所有非Windows平台当作ELF；独立诊断退出1，1 failed/36 passed，远端diag-auto-update.log。生产_verify_binary已正确区分Mach-O，不放宽生产校验。测试改为明确验证PE/ELF在对应平台通过、在其他平台拒绝；新增8种Mach-O/Universal头的本机接受/异平台拒绝用例。首轮失败保留，不用跳过或修改原远端快照隐藏。

本地同时增加_require_resolved_state，在self/caller准备后且资源创建前、状态解码及发布前拒绝.previous任何条目（含悬空链接）。真实子进程在旧root改名后退出73，旧备份和候选仍保留；直接调用_invoke证明两种分区均在网络/容器前拒绝，不生成空seed继续执行。新增5项回归。Windows状态专项14 passed/5.24秒；含更新测试的组合86 passed/8.44秒，macos-prep-windows-20261015-r2.log。Ruff及94/0/0静态门槛通过。此项只关闭错误启动缺口，自动事务恢复、归属、状态I/O监督及断电持久化仍未实现，八类整体验收不关闭。本批没有暂存、提交或推送。

492文件修正快照SHA 5385e6284c83e647db6f51db20f0118a46211c0897f53d4fa27867be112d4ab1、库存SHA 13bcfc212946aae2c4fe4a0e8547da0940569a4612847690c452f53df8b6d8b2，分别逐项核对后放入Linux /tmp/opskit-validation-source-20261015-r2/source及macOS原拥有根的next/source。修正组合在Windows86 passed/8.44秒、Linux86 passed/4.07秒、macOS86 passed/6.14秒，均退出0；证据macos-prep-windows-20261015-r2.log、state-restart-linux-20261015-r2.log及远端next/targeted.log。它们不代表macOS其他首轮失败已解决。

macOS首轮21859终态退出1，完整回归2741 passed/128 skipped/8 failed/403.70秒。8项：ELF平台预期1；真实监听所有权4（普通用户psutil全机枚举AccessDenied，生产返回listeners_unavailable，缺权限不能改判健康）；协议超额client清理1（killpg PermissionError覆盖了原拒绝）；独立guest导入1（待核实editable安装的宿主查找器是否越过captured包）；Tailscale步骤1（当前实现明确Darwin无Linux出口节点步骤，测试原来固定要求出口节点）。完整失败保留，未修改首轮远端源码。安装包阶段全部退出0，wheel SHA 558523e75401a6345e0b833094ad2ca88185a4f3db0c9e7b158a9c70500c7152，252运行文件/257成员；安装依赖检查和94help/103入口通过。原始验证JSON、JUnit、pytest日志、wheel库存及安装CLI结果已复制到F盘cache/macos-acceptance-20261015-r1/remote-results。无Docker，未据此宣称macOS插件真实容器已验收。

macOS进程清理专项独立探针只创建20个本账号短Python子进程/会话。20/20在stdout EOF后、wait前killpg返回PermissionError，wait回收后同一拥有group返回ProcessLookupError；process-reap-probe.json。下一步在不掩盖活进程权限失败、仍回收后代的前提下修复此边界，再增加真实macOS回归。该文件当前只是证据，生产process.py尚未修改；其属于SDK库存，任何改动必须重建并验证新SDK，不能沿用ad31a18c当作新源码产物。

Linux492文件快照完整回归47848已退出0：2764 passed/126 skipped/339.41秒（2890项），state-full-linux-20261015-r2.log。上一批挂载源码834.71秒（2877项）；本轮Linux文件系统快照耗时更短，但用例及并发负载略不同，不推导精确归因比例，也不宣称平台运行性能提升。Windows18704当前新批全量仍运行；不要停止或重启它，也不要在其读取活动源码期间混入下一批process运行时修复。后续Windows回归亦应改用哈希验证的固定快照，以避免开发与验收相互等待。当前新源码仍需新wheel/安装及macOS修正后完整验收，自动恢复、状态I/O监督和八类门槛继续开放。

Windows状态重启批18704终态退出0：2860 passed/30 skipped/673.37秒（2890项），state-full-windows-20261015-r2.log。该运行对应r2采集的测试定义和旧process.py，不作为下一批修复通过证据。

macOS候选目录先复用r2独立源码，再覆盖待验证process修复及三个测试文件；未修改首轮验收目录。首次候选38732退出1，97 passed/17 failed，均在快照manifest阶段失败：独立SSH测试未设置canonical TMPDIR，实际平台临时路径经/var别名被严格清单校验拒绝。保持相同生产策略，仅把该实验TMPDIR设为拥有目录后40074退出0，114 passed/24.01秒，证明进程清理候选及其他测试修正可通过，但不能用改测试环境掩盖默认macOS临时目录问题。

随后Windows既有全量终态后，将候选process修复正式应用工作树：仅当killpg返回PermissionError且poll确认/回收拥有子进程已退出时，重试同一group；活leader及第二次权限拒绝继续抛出。新增真实退出leader/仍活动后代和模拟权限拒绝用例。并将_invoke自己新建临时目录的base规范化为resolve，避免系统/var别名；第三方manifest/source目录的链接拒绝不变。新增真实别名临时目录+实际reader/client用例。远端候选使用默认TMPDIR复验80453退出0，115 passed/24.94秒，targeted-default-temp-fixed.log。无Docker/真实用户服务操作。

Windows相关201项首次6765退出1：196 passed/2 skipped/3 failed；失败均为POSIX模拟测试漏模拟Windows没有的SIGKILL常量，未进入生产killpg。补齐仅测试signal命名空间，单独3 passed/2 skipped退出0，process-mocks-windows-20261015-r3.log；原失败保留，未放宽生产异常或跳过三项断言。两项真实group测试仅POSIX运行。该新process属于SDK库存，后续需重建新SDK及真实Docker验收。

SDK导入干扰已实际确认：macos-editable-import-probe-20261015.json中core来自captured，但core.updater spec被宿主__editable___opskit_1_0_0_finder解析到原source。修正测试使用-I -S及显式纯依赖库目录，保留完整平台模块不存在断言，不执行宿主.pth；真实guest中不安装平台editable包。监听测试先独立复核全局可见性，只有可见才断言真实owner；拒绝枚举时精确断言listeners_unavailable和ok=false，不skip、不改生产健康结果，也不把普通用户下的负向证明当作特权下的真实owner验收。Tailscale步骤分别参数化Linux与Darwin，保留Darwin无出口步骤的已有契约。

修复后的r3固定快照493文件，归档SHA40ac995528e11972e8e69c21b89febd0152d48555dde09542393493b94d93975、库存SHA70c0eacef7e120e7105d53ae9b6d8dc48e6e42746bbd211925c3a8ba1ac3e34f。Windows F盘、Linux /tmp、macOS原拥有根/r3均核对归档及每个成员后使用固定副本；不从开发工作树边改边跑。当前三端完整验收30855/macOS、75570/Linux、82516/Windows均确认仍运行。macOS新环境依赖/pip/Ruff/静态门槛已退出0。Windows首次候选三项SIGKILL模拟缺陷已修复，正式r3含该修正。

12194构建/源码/RECORD核对退出0，使用Windows r3固定快照，wheel dcc060e9baec59f6f5714854b00810bffe4d8862a4c7df35ff366238a2ec3ad0，252运行文件/257成员，证据F盘cache/wheel-macos-fixes-20261015-r3/verification.json。86499正在离线安装并验收真实site-packages读取器、状态拒绝与拥有进程回收，以及94help/103入口，未假定通过。新的process.py改变SDK摘要，Linux r3已准备任务拥有的RPC/网关计时验收helper与既有审核哈希锁；待完整回归资源空闲后重建并验证真实容器，旧ad31a18c不得作为本批新SDK证据。此轮尚未暂存/提交/推送；状态自动恢复及八类整体计划继续开放。

r3终态：Linux75570退出0，2771 passed/126 skipped/378.95秒；Windows82516退出0，2865 passed/32 skipped/772.14秒（各2897项）。macOS30855整套退出0，2769 passed/128 skipped/395.94秒；其独立环境依赖、pip check、Ruff、静态基线、wheel构建/源码/RECORD核对、独立wheel安装/pip及94help/103入口均退出0。macOS wheel 0c58ee519d6079429ae1708c4c8403473439ee2400889c928b5fb7e8850f258a，252运行文件/257成员。首轮8失败及两个候选测试失败批次保留，不能把普通用户全局监听不可见的负向通过当作特权owner正向验收。macOS证据已复制F盘cache/macos-acceptance-20261015-r3/remote-results。

Windows86499退出0：dcc060e9 wheel离线重装、pip check、真实site-packages五模块来源、完整清单与库存重读/reader释放、旧.previous拒绝且数据保留、真实拥有子进程回收及94help/103入口通过。证据F盘cache/wheel-macos-fixes-20261015-r3/{installed-reader,installed-cli}/verification.json。当前代码与493文件r3库存逐项比较，差异仅三份持续更新文档，运行时及测试没有变化。

真实RPC61524退出0，新SDK sha256:b76f2f99ef2b10ca306403dc0b01c5ac2288d5979414735804a4ca18e31d8569，未沿用ad31a18c。实际image标签code-sha256为3b64a5019608b8ada1a83514169223f3ca357e0d6ab3dae525db079881f3991f，与当前SDK源码库存摘要完全相同；policy6/base2b4f19/锁1690a8af保持绑定。两caller状态计数1/1/2、未授权边、未声明方法、真实API版本不符、传输/分配额度/结果信封/终态顺序、嵌套身份撤权及捕获服务边撤权后的状态保留均通过；27个拥有worker确认移除，recovery四项0。

正式网关原生89316退出0，同一新SDK b76f2f99与正式网关359ebe45；实际受控HTTP授权/拒绝/撤销及CONNECT就绪后的注入取消、身份撤权、私网撤权、网络清理后提交前撤权均通过，持久状态保留；recovery四项0。网关catalog与固定标签、无setid/无infocmp/无第三方Python分发/SSL与SQLite/UID65534/无capability及权限屏障检查通过。证据已复制F盘cache/macos-fixes-native-evidence-20261015-r3/{rpc-rpc-verification.json,rpc-recovery-verification.json,network-verification.json,network-timing.jsonl,gateway-catalog.json}。此处是Linux当前Docker的合成插件/轮询撤权，不是macOS Docker或全部部署矩阵、实际OS信号、daemon故障证明。

493个候选文件秘密扫描72443退出0/0发现，F盘cache/secrets-inventory-1pbrspgr。实际新SDK OS复扫46667退出0，镜像ArtifactName与ImageID均为b76f2f99，固定Trivy0.75.0执行文件/发行包摘要重核对，使用2026-10-14T01:19:20Z数据库且不更新，保留同库比较范围。仍Critical0/High44/Medium58/Low61/Unknown3，73个不同编号，High对应8编号；F盘cache/runtime-macos-fixes-os-20261015-r3/{summary.json,os-vulnerabilities.json,scan.log}。退出0仅表示扫描完成，不关闭供应链安全门槛。新快照/新SDK本批已有本机验收终态，状态自动恢复/发布I/O、完整权限与网络/daemon矩阵、远端CI和八类整体计划继续开放。本批仍未暂存/提交/推送。
