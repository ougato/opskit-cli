# OpsKit 当前验收状态

最新终态更正（2026-10-16）：642 的最低 Python 3.10 回归已结束，60 通过、1 项 setup error，耗时 1125.28 秒；`test_withdraw_cleanup_retire_and_idempotent_absence` 在 `workspace.allocate(timeout=30)` 超时。失败根因尚未定位，642 四环境验收未通过。原始报告：`cache/plugin-install-abort-20261016-r1` 对应 Linux 根目录 `/var/tmp/opskit-plugin-install-abort-linux310-20261016-r1/related/stdout` 与 `result.xml`。下文“仍在执行/待终态”描述为此前观察。

642中止增量：冻结Windows/Linux3.14/macOS各61项通过零跳过，源码与XML独立复核，最低Python3.10仍在执行。Linux原始候选中止保留、明确清理后新进程退役、同别名重新发布且未获信任已独立审计；证据 `cache/plugin-install-abort-20261016-r1/native-audit.tar.gz`。641撤销/退役矩阵现已四环境全部独立核对：Windows196通过9跳过，其余各205通过。第3类及八类整体仍未完成，详见[获取边界](plugin-git-acquisition-security.md)。

## 当前验收（2026-10-16）

639发布快照`62ccd582`：四环境各190项，Windows181通过9项POSIX检查跳过，Linux/macOS/最低Python3.10.22各190通过；34项新增全部执行，源码、原XML和最低镜像约束已逐端独立复核。复用完整内容审查的事务发布，意图/完成记录绑定根、目录及内容，撤权先于不覆盖移动；三个实际HTTPS发布与独立进程读回、真实Linux调用进程SIGKILL及新解释器确认恢复、两项独立运行锁/安装锁竞争通过。16测试资源消失、6工作区清理、profile与TLS测试私钥已删除，不授信。证据`cache/plugin-publication-20261016-r1/audit.json`。仅发布原语；完整维护准入、安装/更新宿主Git迁移、拒绝信任撤回与记录退役、全链路I/O及匹配安装/SDK/整仓/冻结未完成。单点Linux故障不能代替全平台崩溃、断电或全项目验收。

637私有profile快照`9e2c761e`：四环境各133项，Windows127通过6项平台跳过、Linux/最低3.10各130通过3跳过、macOS131通过2跳过；新profile29项仅Windows跳过一项POSIX权限夹具。严格JSON对象、来源绑定及私有文件监督读取接入实际HTTPS五项、SSH绝对URL六项及SCP相对路径六项平台API，输出/资源回收/测试进程及私钥、profile删除均独立核对。SCP在独立网络命名空间使用22端口，现有宿主SSH监听原始记录前后一致。证据`cache/plugin-fetch-profile-20261016-r1/audit.json`。原null开发失败和Windows测试ID路径失败保留；完整I/O期限、事务发布、宿主Git安装/更新迁移及匹配安装/SDK/整仓/冻结未完成。后续旧段落只覆盖对应快照。

635校验监督快照`7e298a05`：140项矩阵Windows139通过1项POSIX检查跳过，Linux/macOS/最低3.10各140通过；17项新用例全部执行，含两个阻塞阶段的实际worker回收。实际HTTPS host API五项回归、16资源清理及reconcile全0已核对。证据`cache/plugin-fetch-supervision-20261016-r1/audit.json`。仅捕获后归档校验监督，不代表全链路I/O、SSH平台联调、安装/更新迁移或匹配安装/SDK/整仓/冻结验收。

633 remote-r2快照`810d7564`：四环境各218通过0跳过，实际HTTPS平台远端API 5项通过，16个测试资源不存在、reconcile全0，原源码/XML及原生证据独立复核。证据`cache/plugin-fetch-remote-20261016-r2/audit.json`。首轮租约命名前缀错误已修复并重新冻结；SSH平台调用、用户profile读取、安装/更新迁移及匹配安装/整仓/冻结尚未验收。

633 SSH guest：Windows/Linux/macOS/最低Python3.10各178通过0跳过；实际SSH8项、HTTPS6项回归及7文件镜像字节已独立复核。23个已知容器/网络不存在。证据`cache/plugin-fetch-ssh-20261016-r1/audit.json`，范围及首次失败详见[获取边界](plugin-git-acquisition-security.md)。尚无平台远端API、安装/更新迁移及匹配安装/SDK/整仓/冻结验收。

630 HTTPS guest快照`0f3a3055727a805c4c95003ac57144b86a4cef4d883751a4192ebf388e7bd80c`：Windows3.12.10、Linux3.14.6、macOS3.12.15、最低Python3.10.22各157通过0跳过，原源码/XML及最低容器约束逐端复核。实际policy-3获取镜像完成6项TLS获取、7项Git URL认证头范围及本地获取回归；镜像内5源文件逐字节复核，17个已知容器/网络不存在。初次origin所有权夹具失败保留，修正测试端固定safe.directory后在相同源码通过。证据`cache/plugin-fetch-https-20261016-r1/audit.json`；仅HTTPS guest组合，SSH、平台远端API、宿主Git迁移及630匹配安装/SDK/整仓/冻结验收均未完成。

八类计划及第三类插件安全均未完成。下表只记录各固定快照的实际证据；测试通过不代表生产发布获批。旧版状态页已原文归档到[历史记录](project-health-history.md#2026-10-16-验收状态页原文归档)，其中“当前/在途”不代表现在状态。

626运行审查快照`775afcccd02b498fdd4c59c3c0327c6492eab9e634c9717d9c82b6ed5ac0856b`。129项矩阵Windows128通过1项POSIX执行位跳过，Linux3.14.6/macOS3.12.15/最低Linux3.10.22各129通过，原源码/XML逐端复核；29项新增审查用例除该平台跳过均执行。实际Linux受限获取4文件547字节，prepare/review成功且不执行入口、不写信任，篡改后review拒绝；独立观察进程在清理前核对并保留4个候选文件。1容器/1工作区最终消失，reconcile全0。证据`cache/plugin-fetch-review-20261016-r1/audit.json`，原生归档SHA256`11feae182158215aa7c02e24c3c6544f66aafe47ac7c216f262a77d2fe0fe8b3`。只验收候选审查组件及本地组合；没有来源认证、事务发布、安装/更新迁移或626匹配wheel/SDK重建/整仓/冻结证明。原主进程归档校验I/O期限仍开放，详见[获取边界](plugin-git-acquisition-security.md)。

624受监督候选准备快照`7771d3c3a0fec403e012ec75eeb1112b1a7aeee7c248cc57695a35a0fb8f5d8f`新增`core/plugin_fetch_prepare.py::prepare_candidate`：通过spawn时复制的文件描述符/句柄传入响应，不按名称重开或序列化80MiB响应；完整验证后仅在已有身份绑定、空的私有工作区创建candidate。POSIX使用目录描述符与NOFOLLOW/EXCL写入，Windows固定工作区根句柄、拒绝外部写权限并收紧继承ACL；逐文件核对摘要/身份、保留可执行位的私有权限。校验及写入共用监督期限；失败保留工作区，所有回收复用现有归属清理。四环境各100项相关矩阵99通过1项平台跳过，新增24项全部执行，包含两阶段真实阻塞worker终止、目录重定向保留外部内容、已有证据/候选拒绝及畸形结果。实际Linux Docker组合获取与落盘，3文件90字节原始内容读回一致，重复候选/追加响应均拒绝；1容器及2工作区消失，reconcile全0。源码/原XML/guest响应/原始观察及最终不存在已核对，证据`cache/plugin-fetch-prepare-20261016-r1/audit.json`；候选已删除，磁盘读回内容与权限以删除前原生控制脚本记录为证。不发布、不授信；工作区必须由平台独占且不可挂载给插件写入。仍缺独立manifest/来源校验、HTTPS/SSH认证、维护准入与安装/更新事务、所有宿主Git调用迁移、崩溃持久归属及匹配安装/整仓/冻结验收；不证明敌对祖先替换或同账户攻击隔离。

622私有临时文件候选`c2ee660835b7d099cc9ec9bdcd33ecb1a1364a25e0cd097a7b8b7f7355da217f`修复Windows临时文件继承ACL的问题：新增`core/private_file.py::temporary_private_file`，Windows创建时显式设置受保护DACL、独占打开并最后句柄关闭删除，POSIX保留匿名临时文件并核对所有者/权限。二进制请求、输出及归档验证快照均接入，权限校验失败则不交出文件。实际Windows前后对照确认这三处由旧621的私有权限策略拒绝变为622接受；ACL拒绝本身不等于已证明跨用户读取。新增原生专项核对受保护DACL、另行打开被拒绝及关闭后删除；POSIX核对匿名inode和owner-only权限。124项相关矩阵：Windows110通过14跳过、Linux及最低Python3.10.22各112通过12跳过、macOS115通过9跳过，零失败；每端新增6项中5通过1项平台跳过，源码/原XML及前后观察已独立复核。证据`cache/plugin-fetch-private-temp-20261016-r1/audit.json`。因此早先621对Windows临时文件“私密”的描述不能作为跨身份权限证明。受监督落盘、来源认证、维护事务、宿主Git迁移、匹配安装/整仓/冻结及完整安全验收继续开放。

621本地获取候选`4f0512dcf755568d61cf1659785be0e0f4bfa9225a442717ba868963b08da231`新增正式`local_candidate`接口：固定本地Docker端点，核对镜像/worker身份、来源祖先身份，在现有预算与资源租约内运行受限容器并独立复核输出；容器清理后才交出结果。65项相关矩阵Windows64通过1项POSIX跳过，Linux/macOS和最低Python3.10.22各65通过。实际Linux API正常、非仓库、错误镜像和远程端点4项检查完成；两已知容器消失，reconcile全0。源码、原XML和原生报告已复核，证据`cache/plugin-fetch-local-20261016-r1/audit.json`。不证明本地socket未转发、同账户rename/restore抵抗或完整阶段I/O监督；安装/更新仍使用宿主Git。

随后621字节保留候选`d5257c77ac2e366f28ccac8d5df39754839dd08bd5ead323724e00a7967c0d03`增加`validated_archive`上下文：完整验证后保留同一临时归档，供可信写入阶段消费；原响应被改写或关闭不会改变该快照。原`validate_output`复用该实现，保持清单接口。Windows/Linux/macOS及最低Python3.10.22各64项相关回归通过，零跳过，逐端源码和原XML复核，证据`cache/plugin-fetch-binding-20261016-r1/audit.json`。临时文件只供平台可信调用方；此接口本身不提供安全落盘、I/O期限、发布或授权。尚未接入受监督候选写入、维护事务、HTTPS/SSH认证及安装/更新；无匹配wheel、SDK重建、整仓或冻结验收。

此前敏感导出运行增量为609文件候选`685d8556301b5b8c400eab11001141d35e02b6f81bb292cba08b01ab51f5afd0`：补齐导出期间父目录权限/ACL重查、最终同步后目录身份重查及Windows原始文件名验证。相同21项导出测试的修复前后对照，Windows从3失败到13通过8跳过，Linux从2失败到13通过8跳过，macOS从3失败到15通过6跳过；每端修复前后609源码和原XML均独立复核，只有`core/private_export.py`运行字节不同。最低Python3.10.22为13通过8跳过，固定a38955镜像禁网、只读、1CPU/2GiB/pids128及正常退出已核对。证据`cache/secret-export-races-20261016-r1/{windows,linux,macos,linux310}-audit.tar.gz`。

Linux原生跨用户增量：平台进程euid0在root拥有的0755目录内写入0600暂存文件时，通过setpriv切换到uid/gid65534且清空附加组的独立进程，实际读取、替换及创建均被内核拒绝；最终导出可由平台读回，目录仅保留目标文件。609源码、原始子进程命令/退出码/输出及实际owner/mode已独立复核，见`linux-crossuser-audit/audit.json`。这只证明该Linux场景，不覆盖Windows/macOS其他账户、多租户或管理员威胁。

POSIX若目录在导出期间失去保护，按名称清理也不再安全：拒绝发布，保留0600的私密暂存对象，不依据名称猜测删除；Windows继续通过原句柄删除。本次测试明确验证保留行为。完整暂存恢复/断电/网络文件系统仍开放。609尚未构建匹配wheel或执行安装/整仓/冻结验收；以下608安装结果及603整仓/产物结果不能替代609。

最近完成全量和产物验收的候选为603文件快照 `21e9afd3d1ad3cf5fc0b92f1f549d3b66cc73365be5706ddd383793fed768865`，修复管道关闭异常跳过资源回收；与599相比仅`network/worker.py`运行文件字节改变。新wheel为`0957ff6e8864326ed9a18031df739253b8a9cc46087bec511adbfff1ee37e938`，269运行文件/274成员、603源码及RECORD独立复核完成。三端各113CLI、20生命周期及1项真实阻塞子进程关闭异常检查已完成且独立复核269已安装字节/RECORD、验证脚本及依赖未变。macOS整仓4064项3930通过134跳过，603源码/XML独立复核（macos-full-audit-r1）；7个新增回归均执行。Linux实际容器/RPC的3×21基础、9生命周期、完整RPC已完成并独立核对603源码/269已安装运行文件/23实际镜像guest字节，38已知测试容器消失且最终reconcile四项0（linux-native-r1/audit.json）。Linux整仓4064项3934通过130跳过、603源码/XML及Python3.10.22固定镜像禁网1CPU/2GiB终态已独立复核；Windows整仓4064项4010通过54跳过、603源码/XML独立复核完成，证据根为`cache/worker-pipe-retirement-20261016-r1`；三端各143项必需相关用例均在本轮全量中实际执行通过，未重复运行独立专项。603冻结Linux产物已完成构建、功能验收及独立审计：20CLI、2钩子、3×21插件基础、4信号取消、2强制退出恢复通过，7个数据根最终reconcile全0，8个已知测试容器不存在。另113项冻结CLI矩阵通过，实际二进制及340份原始报告/输出重新收集核对，见`frozen-full-cli-matrix-audit-r1/audit.json`。产物SHA256为`eeb5ceccd2ea66ec2182a18663c8a0981cb701ed8da7d2aa7f75ca1014609803`，只包含明确的3文件测试密钥变体，不能用于生产信任或发布批准。原历史失败根因及安全门槛仍开放。复用专用依赖环境，不是全新依赖安装。

下表为先前599文件快照 `fcf030adfe35c1cc6e23acfd0db899cc9458873747e1c6f8c18cc75f8a335e84` 的证据，主要增加插件安装/更新时禁用Git钩子，保留先前生命周期互斥及内容绑定回滚。不能覆盖603修复。

| 验收项 | 最新已确认结果 | 证据及限制 |
|---|---|---|
| 三端相关回归 | 各143通过，零跳过；599源码和XML独立复核 | `cache/plugin-git-hooks-20261016-r1/audit.json`；4钩子、6生命周期及27安装用例均执行 |
| wheel | 269运行文件、274成员，源码与RECORD核对通过 | SHA256 `3b3703f7cb1aea4b8d1ba1b370b2c4c9751b8246dfbea07b8ac31df771b95f32` |
| Linux/macOS实际安装 | 每端113CLI及20生命周期，已独立复核 | `remote-installed-audit.json`；复用专用锁定依赖环境，不是全新依赖安装 |
| Windows实际安装 | 113CLI通过；20生命周期修正测试夹具后通过并独立复核 | 原失败为更新后源CRLF/检出LF的字节比较；仅将夹具write_text改为明确LF的write_bytes。原失败保留于windows-installed，增量lifecycle-r2。`windows-installed-audit-r1/audit.json`重核269运行文件/RECORD；未重跑113CLI或重装依赖 |
| macOS整仓 | 4057项，3923通过134跳过，795.70秒；599源码/XML独立复核 | `macos-full-audit-r1/audit.json`；XML `7688ae24658b07578492d09ab94eb0a3e8e21c177714c1c8d14b987a6621266d` |
| Windows整仓 | 4057项，4002通过54跳过1失败；599源码和原XML已独立归档核对 | `windows-full-failure-audit-r1/audit.json`；取消record_sync worker后PID存在断言失败，原PID/创建时间保留；稍后不存在不证明当时回收或PID复用。保留原PID断言并增加出生时间观察的20次定向诊断均未复现，20份XML独立核对；不关闭原失败，未重跑全量 |
| Linux整仓 | 4057项，3927通过130跳过，1063.03秒；599源码/XML独立复核 | `linux-full-audit-r1/audit.json`；实际Python3.10.22、固定a38955镜像、禁网、1CPU/2GiB及容器退出0已核对；XML `0f1729a7027b3816649ab189f6298f3d9790dfe024b6129fdac88b3a968de003` |
| 实际容器与RPC | 3×21基础、9生命周期、完整RPC通过，独立审计完成 | `linux-native-r1/audit.json`；599源码、269已安装文件、23guest和SDK身份已核对；A1/B1/A2计数、合同/版本/协议/撤权限制通过；38已知测试容器消失，最终reconcile四项0 |
| 冻结产物 | Windows全量失败，门槛未满足，未启动构建 | 同快照三端全量、专项、安装和原生审计是前置门槛；新增实际冻结CLI安装/更新钩子检查，探针仅在已安装CLI验证通过，不能算冻结结果；测试密钥3文件变体单列；旧冻结结果不覆盖599 |

上述证据均在 `cache/plugin-git-hooks-20261016-r1`，该目录被Git忽略。已安装环境当前已升级为603 wheel；本节599报告只对应当时快照，603证据见页首。

Windows新增诊断：`preack-native-exit-r1/audit.json`独立核对599源文件，其中仅worker退出记录和测试观察两文件为诊断变体；52项相邻模块测试通过，4个目标worker的原生句柄已退出且断言时PID不存在。相同变体整仓诊断4003通过54跳过，4个目标均有唯一原生已退出记录且PID不存在；原599文件与两文件变体、原XML/日志已独立复核，证据为`preack-full-diagnostic-r1/audit.json`。原断言保留，原失败未复现，原因仍开放；诊断不替代未修改候选的整仓验收。

另有确定的故障注入缺口：`owned_process`关闭管道时抛错会跳过剩余管道、Job和进程回收。原实现6个管道反例及1个真实阻塞子进程反例失败；cache内修复用ExitStack尝试全部管道关闭，并在finally回收Job和进程，保留关闭异常。Windows40通过2跳过加真实子进程1通过；Linux/macOS同601文件诊断候选各41通过2跳过、7个新增反例均执行，源码/XML独立复核。Linux专项使用Python3.14.6，不替代3.10整仓门槛。证据在`pipe-cleanup-fault-r1/audit.json`、`linux-audit.json`及`macos-audit.json`。已确认整仓诊断使用独立固定副本且599源码未变，随后将修复和两个回归文件接入生产工作树；运行代码AST与601诊断候选相同，仅增加解释注释和保留空行。工作树专项41通过2跳过、7个新增反例均执行，三文件摘要/XML已独立复核；随后仅删除真实子进程测试的未用json导入，AST对照及单项增量通过另存production-test-cleanup-audit.json。接入后603打包、三端安装和整仓验收已完成，结果见页首；匹配Linux冻结产物已完成上述功能验收与独立审计；其他平台产物和剩余安全门槛继续开放。没有证据将此缺口归因到原整仓失败。

## 619二进制捕获前置

619快照`c2ef7167c4255cb7b29f9cc8c7df3befbe1cfedfe3e2891416fe6c1270c79f8e`新增有界二进制捕获和13项真实进程检查。90项相关矩阵Windows87通过3项POSIX跳过；Linux3.14.6、macOS3.12.15、最低Python3.10.22各90通过，源码/原XML独立核对。受控Docker脚本组合已有资源租约、捕获、候选校验：正常获取、guest失败和宿主超时预期通过，三名称确认消失，reconcile四项0；删除前状态未保留，不能证明超时容器在期限前running。原报告摘要与三名称再检查独立完成，证据`cache/plugin-fetch-binary-20261016-r1/audit.json`。Ruff F/diff通过；尚无生产fetch入口、619 SDK重建或匹配安装/整仓/冻结验收。process.py是SDK库存成员，新摘要使旧准备镜像失效；不沿用旧SDK验收结论。

## 617宿主候选只读校验

617文件快照`5ba4ead177979eb7531c4a1afae9b40739f6e552e63fd51b1453077c6f2661a3`新增宿主响应/归档独立校验及不可变逐文件清单。Windows3.12.10、Linux3.14.6、macOS3.12.15、最低Python3.10.22各45项专项通过，无跳过；源码/原XML独立核对。核对guest字节未变后消费615实际容器原始stdout成功，追加字节、截断、计数不符、重算摘要的隐藏尾部4种修改拒绝；未重建镜像，不作为617原生获取验收。Ruff F和diff检查通过。证据`cache/plugin-fetch-candidate-20261016-r1/audit.json`。仅只读校验，无解压/发布、输入I/O期限监督或617匹配安装/整仓/冻结验收，Git及其余安全门槛保持开放。

## 615本地获取guest组件

615快照`8df2047cd4b2eac12c2e8afc47e01375a14e80334d5ff722bc34ab213517d109`增加独立本地获取worker、镜像内源码摘要及显式源码bundle。Windows3.12.10、Linux3.14.6、macOS3.12.15及最低Python3.10.22各144项相关回归通过，无跳过；源码、原XML及报告哈希独立核对。实际镜像`sha256:d21f1c57e5433dd2ed868903583b3db3c83786dde00e20b2bb449b3dfe8f4add`完成8项本地受限容器检查并独立复核挂载/限制/原字节输出及拒绝行为，8容器消失，嵌入worker字节重新核对。r2/r3所有权配置失败及r4夹具失败保留，详情见[Git获取边界](plugin-git-acquisition-security.md)。证据`cache/plugin-fetch-worker-20261016-r4/audit.json`；Ruff F和diff检查通过。未接入宿主执行器、网络认证或事务发布，无615匹配安装/整仓/冻结证明，宿主Git缺口不关闭。

## 613插件获取工具链前置

613文件快照`7586be50a97db95528d118da47537148f1f26cd8e83e06d99208706147e96858`新增固定Git/SSH包锁和平台禁网构建器。104项相关用例在Windows3.12.10、Linux3.14.6、macOS3.12.15及固定容器内Python3.10.22均通过，无跳过；独立核对各端源码及原XML。实际平台构建得到`sha256:22290694c570efb92463db975add3e26cba8df7b3732327e8891938f34dee80c`，独立核对122包清单哈希、身份/标签、Git/SSH版本及受限运行属性，探针容器消失。证据`cache/plugin-fetch-image-20261016-r1`。环境选错导致的未运行/导入失败保留；后续未改变运行代码，仅切换测试环境。Ruff F检查与diff检查通过。仍无不可变fetch worker、来源认证、候选发布及613匹配安装/整仓/冻结验收，不能关闭宿主Git漏洞或其他安全门槛。

## 610插件获取出口组件

快照610文件`160e6d9c45e902d7e5e333c2a7c5c4b63e84177f212d2837a47620db2f7cf592`：Windows3.12.15、Linux3.14.6、macOS3.12.15及固定镜像内Linux3.10.22各90项相关用例通过，无跳过，源码和原XML独立复核。正式gateway镜像`sha256:9d266edddc78ac5fd8b2026d51934e144350fa386513e66ded782c2968ecb449`已核对目录、标签、代码及现有加固检查；实际双网络7项行为检查通过，两个服务计数2/0。控制器清理后观察断言失败，原即时输出缺失；保留失败，补充按名称/标签/ID处理，独立确认6个已知资源均不存在。证据`cache/plugin-fetch-egress-20261016-r1`。仅为TCP目标限制组件，无Git/SSH生命周期、TLS应用来源/路径/认证范围验收，也无610匹配安装/整仓/冻结结果。

## 603之后的待验收增量

最新608候选`5f914fbb14b4451da6017fc1e62be8eed3b95f9e2fbc183f1d7cd7b956fc7bcb`改进敏感导出：先完成JSON序列化；`core/private_export.py`要求受保护父目录，在私密暂存文件写入/同步后不覆盖发布。POSIX以目录描述符绑定操作位置，原子link后移除暂存名，失败按对象身份清理；Windows创建即指定受保护ACL，持有不共享删除的目录句柄，通过NtSetInformationFile相对目录发布，失败按打开的文件句柄删除。移除旧导出中的路径权限修改和无条件unlink；`core/private_file.py`仅增加目录权限查询分支，输入文件规则保持。旧606源码加同一导出测试的对照为6失败3通过3跳过，这些是契约反例，不是六个独立漏洞；额外合成路径替换证明旧清理会删除无关替换文件。源码和原始XML已核对。

87项相关回归：Windows3.12.15为77通过10跳过，Linux3.14.6为81通过6跳过，macOS3.12.15为83通过4跳过，608源码及原始报告独立复核。最低Python3.10.22为81通过6跳过，固定a38955镜像禁网/只读/1CPU/2GiB/pids128/正常退出已核对。Windows首轮重命名使用Win32接口的非空RootDirectory参数被拒绝，改用支持相对目录的原生接口后通过；原XML保留。Ruff F检查通过。

新wheel SHA256 `66f4707ff26e8f7435b899e2f8e7419cf6054e0ff716e267858d8852eb6ed2cb`含271运行文件/276成员。三端专用Python3.12.15环境离线升级安装各113CLI+16敏感输入/导出检查通过；608源码、271安装字节和RECORD独立重读。Windows首次导出夹具目录继承外部写授权，被正确拒绝；保留原失败，仅对新夹具目录设置私密权限后补跑16检查，再运行113CLI；依赖版本与前606安装验收及当前状态比较一致。Linux/macOS依赖版本升级前后一致；均复用环境，不是全新依赖安装。Windows278份、Linux/macOS各260份原报告收集于`cache/secret-export-20261016-r1/*-installed-audit.tar.gz`；相关/最低版本和旧实现对照证据位于同目录。

608尚未执行匹配整仓/冻结验收；父目录/祖先并发变更、真实跨用户、网络文件系统、断电后的暂存回收及完整安装/服务事务仍开放。以下606报告为前候选，只保留其实际覆盖范围。

当前606文件候选`2cd734a2a990869c33bdbd84bc8433be5c3fbe303cb3c799f7364593fdfe8c6d`增强宿主敏感文件输入：`core/cli_secrets.py`绑定单个描述符核验普通单硬链接、大小、身份和变更；`core/private_file.py`在读取前后检查权限，不修改输入。Linux要求当前euid所有及owner-only权限位，macOS还拒绝全部存在的扩展ACL，Windows仅接受当前用户/SYSTEM/Administrators所有及授权（经验证owner的OWNER RIGHTS亦可）。未知ACE、无法核实权限时拒绝；不声称隔离管理员或同账户恶意进程。

58项相关矩阵的三端606源码/原XML独立核对完成：Windows3.12.15为53通过5跳过，Linux3.14.6为55通过3跳过，macOS3.12.15为56通过2跳过。跳过是其他系统的专属用例，实际Windows Everyone授权和读取中新增授权、macOS额外扩展ACL及POSIX共享权限位拒绝均执行。Ruff F检查通过。证据`cache/secret-input-permissions-20261016-r2/audit.json`。r1实际macOS测试暴露acl_get_entry返回0为成功的语义差异，原606源码及失败XML先独立收集保留，再简化为任何存在的ACL都拒绝；不能用后续通过抹去原失败。

前604候选`71fc1746`的52项相关回归及旧实现接受硬链接反例保留在`cache/secret-input-identity-20261016-r1`；Windows超长pytest参数ID及legacy ctime差异的原失败也保留。它们仅对应前候选。

606最低Python3.10.22相关验收已独立核对：55通过3跳过，606源码/原XML一致；固定a38955镜像禁网、只读、1CPU/2GiB/pids128且退出0，证据`cache/secret-input-permissions-20261016-r2/linux310-audit-r1/audit.json`。新wheel SHA256 `635d763398b66bfc98943994becf2eac6e410019c9d367483fa4c045266dc7d2`包含270运行文件/275成员，新增private_file模块已打包。

三端Python3.12.15专用环境离线无依赖升级安装后，各113项CLI和12项敏感输入检查通过；独立重读606源码、270已安装运行文件和RECORD，并收集每端259份原始报告。私密输入API成功；硬链接、空值、无效UTF-8、超限、原生共享ACL/mode拒绝，6项实际CLI负例退出2且JSON固定提示，不回显合成秘密、不创建平台数据。依赖版本前后一致；复用既有环境，不是全新依赖安装。证据为同目录`{windows,linux,macos}-installed-audit-r1.tar.gz`内`independent-audit.json`。独立审计脚本首轮POSIX环境根推算少一层，拒绝合法bin入口，修正审计边界后重读原结果通过；未重跑或修改产品以绕过断言。

606增量尚无全量或冻结产物证明，不能沿用603结果；父目录保护、网络文件系统、ACL变化竞态的完整威胁边界及敏感导出事务仍开放。之后仅文档更新，运行字节仍绑定该606候选。

## 原失败与历史结果的使用边界

| 快照/问题 | 已有证据 | 仍开放的事项 |
|---|---|---|
| 598 / 49794fde | Windows4050项3996通过54跳过，598源码/XML独立审计；三端专项及113CLI+18生命周期通过 | 不能替代599钩子修复的整仓或冻结验收 |
| 595 / 5798adb7 | Linux4034项3904通过130跳过；macOS3900通过134跳过 | Windows3979通过54跳过1失败：辅助PowerShell在原15秒内未退出。原失败已独立归档；保留现场仅有apply.ps1/job.json，无running.json确认，阶段观察另存windows-full-failure-stage-r1，不推断具体阻塞位置；同源码两参数诊断通过不是根因关闭 |
| 593 / 1bef | 原生RPC中提供方已提交、调用方清理后超时；旧现场和摘要保留 | 未恢复为原子事务；599正常完整RPC通过不证明旧超时原因或持久幂等已解决 |
| 591 / 404e | 冻结CLI20、插件3×21、信号4、kill2及7根终态审计通过 | 产物f81476a4仅覆盖该旧源码及明确测试密钥变体，不覆盖599 |
| 原Windows/Darwin及582 reader偶发失败 | 原日志及后续专项/全量保留在历史台账 | 后续绿色结果不代表失败机制查明 |

## 尚未满足的关键门槛

- 完整维护准入、未知归属及损坏头恢复；当前设计[尚未实现](plugin-maintenance-recovery-design.md)。
- 安装/更新/删除的崩溃事务、凭据生命周期及外部写入者边界。更新报错但源码已变化时，现有内容检查拒绝执行；旧信任记录可能保留，不等于生命周期治理已完成。
- [RPC持久请求键与回执](plugin-rpc-transactions.md)：选择独立事务，状态/回执同次提交；结果不明禁止自动重试。协议、宿主回执隔离及故障矩阵仍待实现。
- [Git获取隔离](plugin-git-acquisition-security.md)：Windows合成仓库已复现`.gitattributes`触发宿主已配置的smudge过滤器，在插件目录外写入无害标记；独立审计完成。禁用hooks不足以关闭该路径，受限获取与事务发布待实现。
- SDK镜像 `sha256:2dbc499a808c904a0083cb4c6d1cc148977c0280208a44e63a550dcc39fe8598` 的High44条包记录/8个漏洞编号仍开放；稳定签名源未发现相关已安装包升级，没有豁免或发布批准。
- 物理磁盘、镜像/构建缓存、跨数据根配额；daemon失联、完整网络、跨用户、多租户、ARM、断电持久性及未知进程清理证明。
- 全部官方CLI真实业务、官方写事务、原生系统服务、远端CI/历史秘密扫描/生产签名、代表性性能测量。完整范围以[八类路线图](project-health-roadmap.md)及[官方CLI计划](official-cli-completion-plan.md)为准。

## 执行与提交边界

生产改动仅在独立工作树，原F盘项目只使用忽略cache保存证据，`core/paths.py`保持不变。阶段提交e0e6a8d已保存224个文件，未推送；本次后续验收状态更新尚未提交。测试使用隔离数据、已知合成插件及对应固定源码，不修改真实插件或用户服务。

保留原进程句柄至终态，不因观察超时、断开SSH或新文档修改重启任务。终态后重读源码/安装字节、原报告和摘要；源码变化后的旧证据不能拼接成新候选通过。当前没有充分依据给出整个项目完成百分比或准确工期。

628来源/认证策略快照`4e3a5c79a9a1eb3691f0b40f8a85d8d43b2b7f68f6681a9167f98a52769d04a5`：Windows3.12.10、Linux3.14.6、macOS3.12.15及最低Python3.10.22各60通过、0跳过；逐端源码/原XML与最低镜像约束独立复核。4项实际OpenSSH pin查找通过，合成私钥移除；ruff F通过。证据`cache/plugin-fetch-source-20261016-r1/audit.json`。仅来源/认证配置策略及pin语法，不是实时来源认证、安装/更新迁移或匹配wheel/SDK/整仓/冻结验收。
