# 插件 Git 获取边界

最新终态更正（2026-10-16）：642 的最低 Python 3.10 回归已结束，60 通过、1 项 setup error，耗时 1125.28 秒；`test_withdraw_cleanup_retire_and_idempotent_absence` 在 `workspace.allocate(timeout=30)` 超时。失败根因尚未定位，642 四环境验收未通过。原始报告：`cache/plugin-install-abort-20261016-r1` 对应 Linux 根目录 `/var/tmp/opskit-plugin-install-abort-linux310-20261016-r1/related/stdout` 与 `result.xml`。下文“仍在执行/待终态”描述为此前观察。

## 642 prepared 显式中止（最低 Python 验收进行中）

最低Python本批运行已观察到撤销/退役模块1项pytest错误，进程仍在执行；原始XML尚未生成，原因待原报告确认。受限容器观察时无OOM、内存约68MiB、4个PID；不据此推断错误根因或替换为三端结论。本批四环境验收尚未通过，具体活动句柄记录于对应cache目录的continuation-checkpoint.json。

快照 `8ca6e8173938782ba79299adc075aeae1487dc252795c23ed2f2128a11aefbd7` 增加 `abort_publication`：只接受仍在原绑定工作区、完整内容与目录身份未变且目标未发布的prepared事务；持插件→安装→信任锁撤权，再以不可变 `.aborted.json` 绑定原意图摘要。接口不移动或删除目录，通用恢复不会自行作出中止决定。中止后的清理仍由工作区API显式执行；工作区不存在、目标不存在、记录一致时，复用既有墓碑协议退役记录。已发布内容必须走撤销，不能改标为中止；未知对象、硬链接、损坏标记和矛盾完成记录均保留并拒绝。旧641撤销墓碑继续可读。

受影响发布34项、撤销/退役15项及新增中止12项组成61项冻结回归，Windows/Linux3.14/macOS各61通过、零跳过，642源码与原XML已逐一复核；最低Python3.10矩阵仍在执行。未改动的获取/准备/审查链路沿用641的205项矩阵，不重复把未运行项目记成本批测试。相关运行模块与测试ruff F通过，无fixture例外。开发首轮61项断言通过但硬链接夹具清理报1项错误，原XML保留；修正仅移除测试自身外部硬链接，未放宽运行时代码，已绑定的遗留测试工作区单独核对并清理。修正后冻结三端全部执行通过。

真实Linux复用此前受限HTTPS响应，注入发布前信任写失败后保留prepared意图；通用恢复不发布，显式中止撤权且候选inode/内容不变。工作区显式清理后，新解释器退役记录，再以相同别名成功发布同一原始内容且未获信任；两个工作区已清理。独立审计还重新读取保留的无害发布树，纠正测试工具将观察文件名复用于意图文件的证据命名问题；未把意图文件当作内容观察。归档 `cache/plugin-install-abort-20261016-r1/native-audit.tar.gz` SHA256 `e6439888ef187d4225cf545f0e359d0c3cfdc08a4d682e8eabebab998b0a3341`。这不是新的远端连接或掉电验收；完整维护准入、install/update迁移、全链路I/O及匹配发布产物仍开放。

## 641 撤销与记录退役候选（局部验收完成）

快照 `83320749b15ad3580c0c8df390280dbf0697b42ded2ad0e5cf01eddec8f9282e` 在639发布协议上增加显式 `withdraw_publication` 和 `retire_publication`。撤销先绑定一个不同的空工作区，撤权后不覆盖移动，重新核对完整内容和目录身份，再记录完成；不直接删除插件树。通用恢复只确认已移动的精确候选，不执行prepared撤销。两工作区均已显式清理、发布目标不存在后，记录退役才生成包含精确文件版本和摘要的不可变墓碑，逐项核对后删除固定记录；支持主意图已删除后的继续退役。未知、新增或变化记录拒绝并保留；`absent`仅证明当前记录不存在，不证明历史安装成功。

Windows开发验证原发布34项、新增撤销/退役15项全部通过。冻结候选Windows196项通过9项POSIX相关跳过，Linux3.14与macOS各205项通过零跳过，原XML与641源码逐一核验；最低3.10亦205项通过零跳过，固定镜像禁网/只读/1CPU/2GiB/pids128/caps0/nnp、退出0及无OOM已独立核验；本批汇总见 `cache/plugin-withdrawal-20261016-r1/audit.json`，不引用639矩阵作为本批证明。Linux真实调用进程在撤销rename后、完成标记前被SIGKILL，helper随父退出；新解释器从withdrawn-unconfirmed确认到withdrawn，独立读取核对原始文件摘要与inode，然后显式清理两个工作区并退役记录。原始响应来自已验收HTTPS合成源；本次不重复声称完成远端获取。追加Linux真实SIGKILL发生在主意图unlink之后，helper退出；只读检查与通用恢复返回retiring且剩余记录摘要不变，新的显式退役成功继续，另两个已知工作区已清理。两个强杀场景独立审计合并归档 `native-audit-with-retirement.tar.gz` SHA256 `8ccaced1887297328f8645f15a290d3ab3f08a721ff27dff0ffec6a5d1470579`，证据位于 `cache/plugin-withdrawal-20261016-r1/`。

此处没有实现prepared发布意图的显式中止、完整维护准入或install/update接入，宿主Git缺口仍开放；也不提供掉电、多账户或其他系统父进程强杀的证明。静态F检查对测试导入fixture产生的F811显式例外，其余相关F规则通过。

## 当前证据与缺口（2026-10-16）

当前安装与更新在宿主执行 Git，仅通过命令行覆盖 `core.hooksPath=/dev/null`。这关闭了已验证的钩子路径，不能保证所有获取行为都在插件沙箱内。

Windows 合成仓库复现：仓库提交 `.gitattributes`，将 `demo.py` 标记为 `filter=opskit-probe`；隔离的宿主全局配置为同名过滤器定义 smudge 命令。平台安装成功，但检出时过滤器在插件目录外写入无害标记。命令仅写标记并透传输入，没有执行插件业务代码。证据 `cache/plugin-git-hooks-20261016-r1/git-filter-probe-r2/audit.json` 核对了源码、配置、属性、安装内容和标记。首次探针缺少 Windows spawn 入口保护而失败，保留于 r1；r2 修正探针，未修改平台代码。

这个反例需要宿主已有或被注入的同名过滤器配置；不证明仅凭远端仓库就能任意执行命令，也不证明其他平台已经验收。它证明插件可以选择触发宿主过滤器，违反第三方获取过程与宿主执行能力隔离的目标。

## 639 候选事务发布与确认恢复

快照`62ccd582d094644692ffee6fb66e807c9892787c56adc4dd1c42d3b29ec132fa`新增`core/plugin_install_transaction.py`，将原响应、完整候选树、manifest摘要、指纹、内容凭据及目录身份在发布阶段重新绑定；审查与发布复用`plugin_fetch_review._review_tree`。worker依次取得既有按插件锁、安装锁及信任事务锁，独占写入不可变意图，先撤销旧授权，再不覆盖移动候选；复核实际目标与意图后，同步目录并独占发布绑定意图摘要的完成记录。失败不删除候选、竞争目标或事务记录；没有自动授信。意图绑定插件根、数据根及工作区身份，每条最多16KiB，最多256事务；未知记录、损坏或容量不足均拒绝，记录退役尚未实现。

`inspect_publication`只观察既有记录与拓扑，不重建锁或改权限；`recover_publication`只对精确匹配的已移动候选补齐完成记录，prepared原样保留，不自动发布。根身份、内容、额外硬链接、结果记录或原始树发生变化均拒绝；超时/通信中断携带事务token，调用者必须保留工作区。协议与锁序详见[维护恢复设计](plugin-maintenance-recovery-design.md)。当前POSIX根须为所有者私有，Windows目录拒绝外部写/删除权限；外部可写根不会经chmod后放行。发布/恢复复用的ResourceLock仍可能收紧受管根的只读ACL，不保证写操作完全不改目录权限。

四环境各190项相关矩阵已独立复核：Windows181通过9项POSIX执行位/fsync/FIFO/描述符检查跳过，Linux3.14.6、macOS3.12.15及最低Python3.10.22各190通过零跳过；新发布34项全部执行。覆盖审查后内容/缓存/空目录变化、伪造凭据、旧同指纹信任失效、竞争者保留、完成记录缺失恢复、损坏记录/额外硬链接/跨数据根重放拒绝、只读检查不重建锁、三个真实阻塞阶段回收及worker突然退出。每端639源码、原XML及最低镜像禁网/只读/1CPU/2GiB/pids128/caps0/nnp/退出0已核对，相关模块ruff F通过。最低矩阵耗时838秒，仅为受限测试环境耗时，不代表程序性能提升。

真实Linux HTTPS profile-file平台链路五项，三项成功均完成获取→准备→审查→发布，并由独立解释器读回原始文件摘要及目录身份；默认私网/错凭据继续拒绝。15租约资源及1测试origin消失、reconcile全0、私有profile与TLS测试私钥均删除。追加实际SIGKILL：在rename后、完成记录前杀死调用进程，helper随父退出；新解释器观察published-unconfirmed并显式恢复为published，原意图不变、内容读回一致、不授信。另有两个独立OS锁持有进程分别阻止发布，释放后同一候选成功。6个已知工作区已按身份清理，合成已发布目录与私有事务记录作为测试证据保留。

证据`cache/plugin-publication-20261016-r1/audit.json`；含真实组合、SIGKILL及锁竞争的归档SHA256`9d5002b19c12bf1f3874e8f8669ac97d5e7d74412175892d8c5acaa367cc90da`。本批仅候选发布原语；**install/update仍在宿主执行Git**。完整维护准入与所有写入者、prepared意图撤销、拒绝信任后的撤回、来源/分支持久记录、更新/删除切换及记录退役仍须接入，不能发布成长期双轨。没有639匹配安装包、SDK重建、整仓或冻结验收；单点Linux SIGKILL不证明其他OS父退出、全崩溃矩阵、断电或不可中断I/O故障安全。第3类及八类完整目标仍开放。以下为各自历史快照证据。

## 637 私有 profile 与远端平台接口验收

快照`9e2c761e502393de82a328fd8c1cab8e636f57518abefb76ec2da9f097ad18b1`新增`core/plugin_fetch_profile.py`，显式文件输入复用敏感输入的描述符、单链接普通文件、所有者、权限/ACL及前后身份检查，在owned worker的一期限定额内读取至多64KiB。仅接受严格JSON对象及完整来源绑定，拒绝null、重复键和非有限数；分页传回有界私有数据，父进程再次核对来源、顺序、长度和摘要。没有环境、agent或宿主配置凭据回退，不修改用户文件权限。`remote_candidate`的profile/profile_file互斥，文件在daemon操作前、同一执行预算内读取。

四环境各133项：Windows127通过6项平台跳过，Linux3.14.6及最低3.10.22各130通过3跳过，macOS3.12.15为131通过2跳过；新profile的29项Windows28通过1项POSIX跳过，其余全部执行。源码、原XML、明确跳过名及最低镜像禁网/只读/资源约束逐端独立核对；相关模块ruff F通过。开发中发现null误作匿名输入已修复；Windows过长测试ID仅缩短夹具名称，原失败XML保留。

真实平台API经profile_file完成HTTPS五项（Basic/Bearer/匿名成功、默认私网与错凭据拒绝）和SSH六项（普通/加密密钥成功、错口令/错pin/默认私网/未许可仓库拒绝）。成功输出与合成源文件逐字节摘要一致，全部租约资源在交出结果前消失；HTTPS含origin共16资源、SSH18资源最终不存在，reconcile全0，SSH测试进程已退出，4私钥及所有profile已删除。证据`cache/plugin-fetch-profile-20261016-r1/audit.json`；原生归档SHA256：HTTPS`ab85df80ae0b8f5be8692ef62e6513aceba5582260b09089046189bff3c893c4`、SSH`21634e9ccfd942aeb6acd15882ee47385760304d29bcf714b0921423f96e6f70`。HTTPS原始scope沿用旧脚本文字，实际调用源码使用profile_file；审计中明确纠正而不改原记录。SSH仅绝对URL，本批不证明SCP相对路径。

同一637源码追加SCP相对路径平台实测6项：隔离的合成网络命名空间监听22端口，不映射宿主端口、不修改宿主sshd配置或authorized_keys。普通/加密密钥成功，错口令/错pin/默认私网/未许可仓库拒绝；origin原始命令分别为两次`git-upload-pack repo.git`及一次拒绝的`git-upload-pack unapproved.git`，相对路径语义没有转换为绝对URL。输出摘要、18租约资源及1命名空间容器消失、sshd进程退出、4私钥/profile删除和reconcile全0均独立复核；宿主22端口监听原始记录前后一致。归档`cache/plugin-fetch-profile-20261016-r1/native-scp-audit.tar.gz`，SHA256`2d2c1b5d65c157970ccabec44e0e9bfc7c10a5c1fd7ba603012646141e1ec323`。合成sshd仅fixture使用StrictModes no，客户端仍严格核对主机pin；不代表生产SSH服务器加固验收。

不证明同账户/管理员攻击隔离；完整I/O监督、事务发布、维护准入、安装/更新宿主Git迁移及匹配安装/SDK/整仓/冻结验收继续开放，不能关闭第3类或发布安全门槛。以下为前快照的历史证据范围。

## 635 捕获后候选校验监督

快照`7e298a05168b5b843402ba205555fb6ec1fc2383c49a3524261057ca2400dbca`将local_candidate/remote_candidate的完整归档读取与校验迁入owned spawn。复用并提取`core/process_file.py`中的私有描述符传递，准备/审查也共用该实现，不按名称重开、不复制80MiB响应进程参数。子进程校验全部字节后按16项分页发送清单，主进程以同一期限等待并核对页序、请求绑定、布局、预算和完成摘要；每帧仍受既有64KiB限制，最多8192文件，不把大清单强塞单帧。成功时子进程重置共享描述符位置，父进程保留文件所有权；只有完整完成记录才返回inventory。

140项相关矩阵：Windows139通过1项POSIX执行位检查跳过，Linux3.14.6/macOS3.12.15/最低Python3.10.22各140通过。17项新用例全部执行，包括超过单帧的1000文件清单、畸形分页/完成结果，以及读取和分页阶段真实阻塞后worker终止、无部分结果返回。源码/原XML及最低镜像约束逐端复核，相关模块ruff F通过。实际Linux正式HTTPS host API五项回归通过，16个测试资源消失，reconcile全0。证据`cache/plugin-fetch-supervision-20261016-r1/audit.json`，原生归档SHA256`8a7013f11ff293b9a95cb56f3c300846af46f338652f9edf0911c804807e65b7`。聚合审计首次在最低版本归档生成前启动而缺文件，归档完成后重跑成功；运行测试本身未重启。

仅关闭捕获后的候选归档校验主进程读取缺口，不宣称全链路I/O期限达标；capture中的私有文件写入/seek/close、源码/镜像库存读取等仍需完整边界复核。SSH平台API/SCP相对路径实测、profile文件监督读取、事务发布与宿主Git安装/更新迁移仍未完成；635无匹配安装/SDK重建/整仓/冻结验收。

## 平台远端API接入（633 remote-r2）

快照`810d7564b6752b8f1676b89517d40008815096e01c4261281a776711e87daa21`新增`core/plugin_fetch.py::remote_candidate`：平台先验证来源/profile并快照请求，校验获取镜像和当前gateway源摘要，复用现有ResourceLease创建固定目标代理与内部网络。默认拒绝私网；显式allow_private只授予当前来源的主机/端口，不能由profile增加目标。远端凭据仅在私有stdin中传输；process_binary默认4KiB不变，仅显式请求可提升至128KiB。获取容器不挂载宿主目录，成功也要完成容器、代理、网络清理才交出私有响应；失败保留既有恢复机制，无宿主Git回退。

四环境各218项相关测试通过，无跳过，源码和原XML独立复核。实际Linux使用正式host API完成Basic/Bearer/匿名成功、默认私网拒绝和错误凭据拒绝共5项；成功用例在yield前分别确认3资源已消失，退出后响应关闭。15租约资源与1测试origin最终不存在，reconcile全0。使用既有policy-4镜像（guest源码未改变），没有以新host源码冒称重建镜像。证据`cache/plugin-fetch-remote-20261016-r2/audit.json`，原生归档SHA256`162b71019e1cf94c5cdb99ec41b9c87d73362b5798936045daa223ee3a59b4d5`。

首轮实际调用发现新网络前缀不符合既有租约规则，在创建前拒绝；修复为现有opskit-net命名后重新冻结r2并完成验证，r1源码/失败记录保留。仍缺SSH host API和SCP相对路径实测、监督读取用户profile文件、主进程归档I/O监督、事务发布及安装/更新迁移；633 remote-r2没有匹配安装/整仓/冻结验收。第3类及八类完整目标继续开放。

## 633 SSH guest 实际获取验收

633 SSH guest快照`184f1c7d6f4771d99fa15a90264efa09179826b6df0d20d58fea6cd99ddaab82`新增固定操作SSH launcher、不可变askpass及有界CONNECT双向转发。私钥、口令及代理token仅保留guest私有临时文件，客户端禁用宿主配置/agent、密码与交互认证、转发和连接复用，严格使用显式known_hosts。远端命令固定git-upload-pack及来源绑定路径，忽略Git附加操作参数；协议固定为v0以避免未审核环境转发。获取镜像策略4，显式7文件库存与SDK独立；HTTPS复用相同代理校验和私密文件基础设施。

四环境各178项相关测试通过无跳过；新增21项覆盖固定操作、私有配置、口令提示与路径shell引用。实际Linux合成SSH源8项：普通/加密私钥成功，错误密钥/口令/主机pin、未允许路径、错误代理token及目标端口拒绝；实际HTTPS6项回归通过。镜像`sha256:496dcbc8532844cffe6e84e81e5be490192e524726abfb2ec1a92245a787653c`内7源文件字节独立复核，已知23个容器/网络名称最终不存在，10份合成SSH私钥与TLS私钥已移除，测试sshd及观察到的子进程已停止。新模块ruff F通过。开发首轮仅测试UTF8读取断言失败，显式编码后通过；实际r1因测试sshd检查/var/tmp祖先权限而拒绝客户端，r2仅在合成sshd关闭StrictModes，平台客户端严格主机校验未放宽。原失败保留，不作为平台缺陷修复。证据`cache/plugin-fetch-ssh-20261016-r1/audit.json`，原生归档SHA256`7b9c97e86cb33c28705d7267d4aea1c8660b871a1394f3a81070108b7379ae77`。

此批实际SSH仅验收绝对URL路径，SCP相对路径尚缺实测；尚未完成平台敏感profile读取、128KiB宿主请求通道、远端资源租约API、全链路I/O监督、事务发布及宿主Git安装/更新迁移。没有633匹配安装/SDK重建/整仓/冻结证明，第3类及八类计划均未完成。

## 630 HTTPS guest 实际获取验收

快照`0f3a3055727a805c4c95003ac57144b86a4cef4d883751a4192ebf388e7bd80c`新增`core/plugin_fetch_https.py`并接入独立worker。远端来源和显式profile在guest重新校验；请求限128KiB，认证仅写入guest私有临时配置，不进入Git参数或响应。仅允许HTTPS协议，清空凭据助手与环境继承，强制TLS证书校验、关闭重定向，认证头按规范化仓库URL匹配；代理地址/端口/token由受信调用方显式绑定。网络命名空间仍须由调用方限制，本模块不能把宿主Git变成沙箱。现有宿主二进制capture仍限4KiB，平台侧远端API尚未接入。

本地与HTTPS复用裸克隆、对象批处理和有界原字节归档，不增加检出或宿主回退。获取镜像策略升为3，锁、worker及显式5文件源码库存分别核对；guest只加入不可变`/runtime`导入路径，获取模块仍不进入插件SDK。实际离线构建镜像`sha256:2554806e4126b792097a617fb5e13abfa1ee93f5af380b7e6171d6f0af122fef`，镜像内5文件字节独立复核。

四环境各157项相关用例通过、无跳过，18项新配置用例全部执行；新改模块ruff F通过。实际Linux隔离的合成TLS origin与既有固定目标gateway组合完成6项：Basic/Bearer/匿名成功取得相同3文件原字节，错误凭据、未受信CA及重定向均失败且不输出候选；origin未收到重定向目标请求，响应未含测试认证材料。实际Git另完成7项URL认证头匹配，拒绝相似前缀、兄弟路径、其他主机/协议/端口；新镜像本地API回归通过，required失败过滤器未改变原字节，最终reconcile全0。成功组合、scope探针、本地回归及首次失败共17个已知容器/网络名称最终不存在。

首次r1正向失败源于测试origin的非root进程拒绝异主合成仓库，独立诊断复现dubious ownership；仅测试origin增加固定仓库safe.directory后，r2在相同630运行字节上完成矩阵，原失败保留。源码/原XML/镜像及原生报告独立复核，证据`cache/plugin-fetch-https-20261016-r1/audit.json`，原生归档SHA256`41302ef95844367ac193eb86b67e7eb4f9a3d47d90ef831902b3c0652707dc10`。测试TLS私钥已移除。

此批只完成HTTPS guest及受控网络组合，未完成SSH解锁/主机pin/CONNECT适配器、平台侧敏感profile加载与资源租约API、全阶段I/O监督、事务发布或安装/更新迁移。没有630匹配wheel/SDK重建/整仓/冻结证明；宿主Git风险和第3类P0继续开放。

## 本地获取、归档字节绑定与候选准备

626候选审查快照`775afcccd02b498fdd4c59c3c0327c6492eab9e634c9717d9c82b6ed5ac0856b`新增`core/plugin_fetch_review.py::review_candidate`：受监督worker重新验证原响应与准备结果的绑定，对照原始逐文件摘要检查完整落盘树（不跳过缓存目录或空目录），再复用现有manifest、官方名称冲突、CHECKSUMS、执行指纹及完整内容凭据规则。复用实际guest的纯请求校验器检查入口语法，检查入口存在及exec原始执行位，不导入插件模块。返回规范化清单摘要、指纹、私有树凭据及校验和状态，不返回授权；missing继续要求后续显式TOFU决定。原始树在元数据检查前后核对，发布时仍必须重新绑定凭据和清单摘要。

同快照129项相关矩阵：Windows128通过1项POSIX执行位检查跳过，Linux/macOS/最低Python3.10.22各129通过；新增29项Windows28通过1跳过，其余环境29通过，涵盖字节/忽略目录/空目录/缺失项、官方/保留/缺失入口、错误校验和、准备绑定变化、三个真实阻塞I/O阶段的worker终止和畸形结果。实际Linux Docker来源配置required且smudge=false的合成过滤器，获取原字节后完成4文件547字节候选准备与审查；入口标记和信任文件均未创建，篡改候选被拒绝。独立观察进程在清理前读回并保留4个实际文件，核对权限、归档摘要、规范化清单、指纹及凭据；1容器和1工作区最终消失，reconcile全0。源码/原XML及原生证据已独立核对，见`cache/plugin-fetch-review-20261016-r1/audit.json`。

此审查仅证明候选与捕获输出一致，提交ID仍是guest报告值；不认证远端来源，不授信、不发布。安装/更新仍调用宿主Git。HTTPS/SSH认证与受限网络执行、维护准入/持久归属和事务切换、发布时最终身份/清单/凭据复核、匹配安装/整仓/冻结门槛均开放；local_candidate现有主进程validate_output的文件I/O也尚未迁入监督，不能据此关闭全调用链期限门槛。

624受监督候选准备快照`7771d3c3a0fec403e012ec75eeb1112b1a7aeee7c248cc57695a35a0fb8f5d8f`新增`core/plugin_fetch_prepare.py::prepare_candidate`：通过spawn时复制的文件描述符/句柄传入响应，不按名称重开或序列化80MiB响应；完整验证后仅在已有身份绑定、空的私有工作区创建candidate。POSIX使用目录描述符与NOFOLLOW/EXCL写入，Windows固定工作区根句柄、拒绝外部写权限并收紧继承ACL；逐文件核对摘要/身份、保留可执行位的私有权限。校验及写入共用监督期限；失败保留工作区，所有回收复用现有归属清理。四环境各100项相关矩阵99通过1项平台跳过，新增24项全部执行，包含两阶段真实阻塞worker终止、目录重定向保留外部内容、已有证据/候选拒绝及畸形结果。实际Linux Docker组合获取与落盘，3文件90字节原始内容读回一致，重复候选/追加响应均拒绝；1容器及2工作区消失，reconcile全0。源码/原XML/guest响应/原始观察及最终不存在已核对，证据`cache/plugin-fetch-prepare-20261016-r1/audit.json`；候选已删除，磁盘读回内容与权限以删除前原生控制脚本记录为证。不发布、不授信；工作区必须由平台独占且不可挂载给插件写入。仍缺独立manifest/来源校验、HTTPS/SSH认证、维护准入与安装/更新事务、所有宿主Git调用迁移、崩溃持久归属及匹配安装/整仓/冻结验收；不证明敌对祖先替换或同账户攻击隔离。

622私有临时文件候选`c2ee660835b7d099cc9ec9bdcd33ecb1a1364a25e0cd097a7b8b7f7355da217f`修复Windows临时文件继承ACL的问题：新增`core/private_file.py::temporary_private_file`，Windows创建时显式设置受保护DACL、独占打开并最后句柄关闭删除，POSIX保留匿名临时文件并核对所有者/权限。二进制请求、输出及归档验证快照均接入，权限校验失败则不交出文件。实际Windows前后对照确认这三处由旧621的私有权限策略拒绝变为622接受；ACL拒绝本身不等于已证明跨用户读取。新增原生专项核对受保护DACL、另行打开被拒绝及关闭后删除；POSIX核对匿名inode和owner-only权限。124项相关矩阵：Windows110通过14跳过、Linux及最低Python3.10.22各112通过12跳过、macOS115通过9跳过，零失败；每端新增6项中5通过1项平台跳过，源码/原XML及前后观察已独立复核。证据`cache/plugin-fetch-private-temp-20261016-r1/audit.json`。因此早先621对Windows临时文件“私密”的描述不能作为跨身份权限证明。受监督落盘、来源认证、维护事务、宿主Git迁移、匹配安装/整仓/冻结及完整安全验收继续开放。

621本地获取候选`4f0512dcf755568d61cf1659785be0e0f4bfa9225a442717ba868963b08da231`新增正式`local_candidate`接口：固定本地Docker端点，核对镜像/worker身份、来源祖先身份，在现有预算与资源租约内运行受限容器并独立复核输出；容器清理后才交出结果。65项相关矩阵Windows64通过1项POSIX跳过，Linux/macOS和最低Python3.10.22各65通过。实际Linux API正常、非仓库、错误镜像和远程端点4项检查完成；两已知容器消失，reconcile全0。源码、原XML和原生报告已复核，证据`cache/plugin-fetch-local-20261016-r1/audit.json`。不证明本地socket未转发、同账户rename/restore抵抗或完整阶段I/O监督；安装/更新仍使用宿主Git。

随后621字节保留候选`d5257c77ac2e366f28ccac8d5df39754839dd08bd5ead323724e00a7967c0d03`增加`validated_archive`上下文：完整验证后保留同一临时归档，供可信写入阶段消费；原响应被改写或关闭不会改变该快照。原`validate_output`复用该实现，保持清单接口。Windows/Linux/macOS及最低Python3.10.22各64项相关回归通过，零跳过，逐端源码和原XML复核，证据`cache/plugin-fetch-binding-20261016-r1/audit.json`。临时文件只供平台可信调用方；此接口本身不提供安全落盘、I/O期限、发布或授权。尚未接入受监督候选写入、维护事务、HTTPS/SSH认证及安装/更新；无匹配wheel、SDK重建、整仓或冻结验收。

## 目标受限出口组件（610候选）

`core/plugin_egress.py`增加仅由平台设置的`OPSKIT_FETCH_TARGET=host:port`。启动时固定一个规范化TCP目标，解析前拒绝其他主机或端口；私网授权不能扩大目标范围。HTTP请求还要求唯一且一致的Host，CONNECT仅约束TCP目的地。该内部环境配置不是插件业务CLI参数。

610文件快照`160e6d9c45e902d7e5e333c2a7c5c4b63e84177f212d2837a47620db2f7cf592`的90项相关用例在Windows、Linux、macOS及最低Python3.10.22均通过且无跳过。通过正式gateway构建路径生成镜像`sha256:9d266edddc78ac5fd8b2026d51934e144350fa386513e66ded782c2968ecb449`，独立核对嵌入源码与非root、无capabilities、no-new-privileges及seccomp设置。

实际双网络容器矩阵7项行为检查通过：允许目标的HTTP/CONNECT成功，其他目标、错误Host、重复Host、错误认证及客户端直连均拒绝；两个合成服务请求数为2和0。控制脚本随后在清理后的文本匹配断言失败，原即时观察输出未保存；保留失败，按确切名称、标签和ID补充清理并独立确认6个测试资源均不存在。原始报告位于`cache/plugin-fetch-egress-20261016-r1`。

这只验证TCP出口组件，不验证TLS应用来源、仓库路径、重定向或凭据范围。现有安装/更新仍调用宿主Git，不能据此关闭smudge风险。固定Python基础镜像的只读非root探针未在PATH找到git、ssh或git-upload-pack；必须提供独立的Git/SSH工具镜像，不能假设SDK带有工具。610没有匹配安装包、整仓或冻结验收。

## 独立工具镜像候选

固定基础镜像的Debian13/amd64上新增35个包：独立用基础镜像携带的Debian归档密钥核验3份InRelease签名，核对签名内Packages内容哈希，再逐包核对名称、版本、架构、大小和SHA256。锁文件SHA256为`488262e5cdde6b18d3559a740e17a169a7b6b484c2b91742fa4dada34a6c6445`；原索引、签名、包和基础包清单均保留。首次下载因临时容器目录权限失败；首轮离线构建遇到apt本地包路径错误，保留两次失败，改为dpkg安装全部锁定包后执行apt依赖检查。

禁网构建候选镜像`sha256:3486081d7d4e2b51f5febffcbb99e38540c8ed7d5636372af799e09a5418c38a`，随后在只读、禁网、uid65534、cap-drop ALL和no-new-privileges容器验证Git2.47.3、OpenSSH客户端、git-upload-pack及临时仓库初始化。独立重读122项已安装包，等于87项未变基础包加35项锁定新增包。两个已退出获取容器按核验后的完整ID移除并确认消失。证据归档`cache/plugin-fetch-egress-20261016-r1/toolchain-evidence.tar.gz`，SHA256`6092e15b332df117814be4b2eabafb62ad57485f47a421bb9a3f897884d9d754`。

后续613候选将锁定数据纳入`core/plugin_fetch_lock.py`，并新增平台构建器`core/plugin_fetch_image.py`。构建器按锁定名称只读取有界普通单硬链接文件，校验大小与SHA256后生成固定tar；拒绝链接、缺包及变化内容，不复制目录内额外文件。固定基础镜像必须已存在且身份与amd64架构匹配；构建禁网，使用dpkg安装锁定包并检查依赖及完整122包清单哈希，最终核对镜像ID、架构及策略/锁标签。平台模块随正常Python源码打包；软件包二进制仍在外部准备目录，不进入仓库。

613快照`7586be50a97db95528d118da47537148f1f26cd8e83e06d99208706147e96858`的104项相关用例在Windows3.12.10、Linux3.14.6、macOS3.12.15及最低Python3.10.22均通过且无跳过，源码与原XML独立复核。实际调用平台构建器禁网生成`sha256:22290694c570efb92463db975add3e26cba8df7b3732327e8891938f34dee80c`；独立只读禁网探针重核包清单、Git/SSH版本、uid65534、零capabilities、no-new-privileges、seccomp与可执行根目录无setid，测试容器已消失。证据`cache/plugin-fetch-image-20261016-r1`。首轮误选缺依赖环境，保留未运行/导入失败记录，切换既有专用测试环境后完成；未向已安装CLI环境加入测试依赖。

这仍是获取工具链前置模块，不是安装/更新已接入的fetch执行器。尚缺不可变worker、获取执行器、来源认证、恶意仓库矩阵、漏洞门槛和其他架构验收；613也没有匹配wheel安装、整仓或冻结证明。旧候选镜像与613镜像分别保留身份，不混用验收结论。

## 实施决策

### 615本地获取worker增量

619候选新增`core/process_binary.py`文件缓冲捕获；复用现有Job/进程组启动及回收，精确保留stdout字节，stderr单独限64KiB并丢弃，内存队列最多8个64KiB块。请求≤4096字节、响应≤80MiB+1024；静默和继承管道均受总期限约束，仅进程成功退出且清理完成后交出私密临时文件，失败不展示子进程输出。终止失败时不在仍阻塞的BufferedReader上调用close，传播错误，守护进程资源须由调用方租约恢复。

619快照`c2ef7167c4255cb7b29f9cc8c7df3befbe1cfedfe3e2891416fe6c1270c79f8e`的90项相关矩阵：Windows87通过3项POSIX检查跳过，Linux/macOS及最低Python3.10.22各90通过；13项新增二进制进程检查均执行。源码和原XML独立复核。控制脚本组合现有资源租约、二进制捕获及候选校验，正常获取、guest失败和宿主超时三项通过；三个已知名称均消失，最终reconcile四项0。未保存删除前容器状态，不能证明超时用例在期限前已进入running；不作为完整daemon取消/崩溃验收。证据`cache/plugin-fetch-binary-20261016-r1/audit.json`。

该619批次仍是宿主执行器的前置接口；后续621已增加本地API，尚未迁移安装/更新。`core/process.py`只增加可选stderr分离参数，原默认合并行为保留；它属于SDK源码库存，因此SDK摘要变化，旧准备镜像不能覆盖此增量。尚无619 SDK重建、匹配安装/整仓/冻结证明；既有615独立worker镜像在受控探针复用，不代表SDK验收。

后续617候选新增`core/plugin_fetch_candidate.py`宿主只读校验器：限长且无重复字段的响应头、严格整数预算、精确长度及整体摘要先验证，再通过既有有界tar读取器复核普通文件、权限/所有者元数据、目录节点预算、跨平台路径/碰撞及逐文件SHA256。拒绝链接、额外PAX字段、隐藏尾部归档和清单计数/大小不符；返回不可变清单，不解压、不发布、不授信。提交ID明确为guest报告值，不是来源签名。后续解压必须消费同一受保护字节或重新验证，不能把清单当作任意同名归档的授权。

617快照`5ba4ead177979eb7531c4a1afae9b40739f6e552e63fd51b1453077c6f2661a3`在Windows3.12.10、Linux3.14.6、macOS3.12.15和最低Python3.10.22各45项专项通过，无跳过，原源码/XML独立复核。额外复用615实际容器原输出验证成功并得到3文件90字节清单；追加、截断、伪造计数和重算摘要后的隐藏尾部4种修改均拒绝。先核对guest源码未变及原输出摘要；这是617宿主对615原输出的消费验证，没有重建镜像或宣称617原生获取验收。证据`cache/plugin-fetch-candidate-20261016-r1/audit.json`。宿主I/O期限、候选解压/发布、来源认证、维护事务及617匹配安装/整仓/冻结结果仍缺失。

`core/plugin_fetch_worker.py`为独立stdlib Linux guest，只处理固定只读`/source`本地仓库及有界JSON请求。入口检查非root固定uid/gid、零capabilities、no-new-privileges、seccomp和根/来源只读；私密工作区生成仅含固定safe.directory的配置，不继承宿主配置、环境注入或凭据。裸克隆后通过ls-tree和cat-file读取提交对象，不检出工作区，也不应用export-ignore/export-subst。输出为有界元数据和候选tar，不写已发布插件，不决定信任。

条目预算8192包含目录节点，单文件16MiB、提交内容64MiB、索引2MiB、归档80MiB；仅接受普通文件，拒绝子模块、符号链接、路径逃逸、Windows特殊名字及大小写/Unicode规范化碰撞。读取对象使用批处理及文件缓冲，不为每个文件启动Git；目录冲突使用前缀索引，避免逐文件扫描全部既有路径。Git子进程有剩余期限、文件输出限制和进程组回收，入口还有300秒计时器；宿主整体监督和实际超时/取消故障验收仍待接入。

平台镜像策略升为2，按worker字节摘要构建并核对标签；冻结源码bundle显式加入worker，SDK源码与摘要仍独立。615快照`8df2047cd4b2eac12c2e8afc47e01375a14e80334d5ff722bc34ab213517d109`的144项相关回归在Windows3.12.10、Linux3.14.6、macOS3.12.15、最低Python3.10.22均通过且无跳过，源码和原XML独立复核。实际镜像`sha256:d21f1c57e5433dd2ed868903583b3db3c83786dde00e20b2bb449b3dfe8f4add`完成8项本地容器检查：普通提交原字节成功，链接/子模块/大小写碰撞/超额文件/超额条目/非法分支/重复请求拒绝；只挂载合成来源，禁网、只读根、非root、无capabilities和受限tmpfs，8个容器确认消失，镜像内worker字节独立核对。

原r2/r3正常获取失败：上传进程不继承clone的命令行safe.directory，改为guest私密配置后成功。r4控制夹具过早启用required过滤器，导致负面仓库尚未创建就失败；保留原记录，仅将夹具配置移到构造结束后，在相同615运行字节上完成矩阵。原失败、诊断及最终证据分开归档于`cache/plugin-fetch-worker-20261016-r4/native-audit.tar.gz`，SHA256`9e6c9d9e10730320de3b2a0c826c820663914b76d0858e7d74cbb081fafc1c61`。

此增量只支持本地来源的guest组件。安装/更新仍调用宿主Git；HTTPS/SSH与凭据适配器、受限出口接入、宿主资源租约/候选独立复核和事务发布尚未实现，不能关闭smudge缺口。615没有匹配wheel安装、整仓或冻结验收；增加源码bundle条目不是冻结验收证明。

获取、内容检查、发布分成三个阶段。最终采用受限获取执行环境：不挂载平台源码、用户目录、其他插件、Docker socket或宿主Git配置；只输出有界候选内容及来源摘要。宿主复核输出后，使用已有发布与内容凭据路径。获取进程自身不得决定信任或授权。

不能仅追加几个 `-c` 参数就宣称隔离完成：过滤器名称可变，仓库本地配置与 include、SSH、凭据助手和远程助手也能引入执行能力。清空 global/system 配置也不能覆盖更新时已有 `.git/config` 的执行路径。

实施时需要明确保留本地仓库、HTTPS私有仓库和SSH获取能力。认证由平台显式选择的认证适配器提供，凭据只授权给指定源和本次获取，不继承整个宿主环境。SSH配置不能由插件仓库指定；需要自定义认证的用户须能明确配置。网络出口按远端来源控制，不能因克隆需要网络就开放全部宿主访问。

更新不得直接在已发布目录中 `pull`。从已确认的来源在干净的受限获取环境生成新候选，校验清单、目录预算及来源后，在完整维护准入和生命周期事务保护下切换。失败保留原版本或可恢复事务记录；内容变化必须撤销原授权。此变更要与崩溃恢复设计一起实施，不能用新目录替换掩盖现有维护屏障缺口。

## 必须通过的验收

- 合成仓库触发已配置的 smudge/process/clean、自定义 merge、hooks、include、SSH/凭据/远程助手时，不能获得宿主文件写入或命令执行能力。
- 安装与更新都覆盖正常公开源、本地源、显式私有源认证，以及认证失败、取消、超时、错误输出上限；凭据不进入日志或候选内容。
- 插件目录与平台目录身份发生变化、候选包含链接或超量内容时拒绝发布；获取失败不污染已发布插件。
- 更新与执行、删除竞争及获取/发布期间崩溃均有确定的恢复终态；未知结果不能自动重试产生重复副作用。
- Windows、Linux、macOS分别验证源码、已安装CLI和发布产物；无受限获取环境时明确拒绝不安全获取，不能静默回退到宿主Git。

以上是待实现设计和验收要求，当前平台尚不满足，不作为安全完成证明。

## 628远端来源与显式认证策略

快照`4e3a5c79a9a1eb3691f0b40f8a85d8d43b2b7f68f6681a9167f98a52769d04a5`新增`core/plugin_fetch_source.py`，纯函数规范化HTTPS、SSH及SCP来源；凭据绑定协议、主机、端口、用户名、完整路径和相对路径语义。拒绝URL内嵌凭据、查询/片段、路径逃逸及控制字符；HTTPS仅允许显式anonymous/basic/bearer及可选CA，SSH要求显式私钥和固定主机公钥，不读取宿主配置、agent或凭据助手。敏感payload有64KiB上限，不进入repr；PEM只检查有界封装，不证明密码学有效。

四环境（Windows3.12.10、Linux3.14.6、macOS3.12.15、最低Python3.10.22）各60项通过且无跳过，冻结源码逐端重核，原XML及归档摘要独立复核；最低版本固定镜像禁网、只读根及资源约束已核对。另以实际Windows ssh-keygen核对默认/非默认端口及DNS/IPv6共4项known_hosts查找，错误主机均不匹配，合成私钥已移除。开发期测试发现并修复非默认DNS端口缺方括号的问题。证据`cache/plugin-fetch-source-20261016-r1/audit.json`；新模块/测试ruff F通过。

这仅验收来源和认证配置策略及OpenSSH pin语法，不证明实时TLS、SSH认证或私钥解锁。实际guest适配器、敏感请求传输、受限网络执行、安装/更新迁移及匹配wheel/SDK/整仓/冻结验收仍未完成，宿主Git风险继续开放。
