# 插件损坏事务记录的显式恢复

平台生命周期命令，不注册任何第三方业务参数：

```console
opskit --output json plugin inspect-state provider
opskit --output json plugin repair-state provider --record-sha256 <检查返回的64位小写摘要>
opskit --output json plugin inspect-state provider --caller consumer
```

省略caller选择plugin-data/provider/self，绑定调用方platform；显式caller选择plugin-data/provider/callers/CALLER。因此显式`--caller platform`与省略caller不是同一分区。命令不依赖插件manifest，插件移除后仍可检查保留的数据。输出不包含日志内容或绝对路径。

检查不创建目录和锁，只读取上限4096字节的事务元数据及目录身份，不读取业务文件。clean只表示未发现目标事务日志及previous目录，不是整个平台健康结论，也不是允许删除未知候选的凭据。valid表示可解析记录；repairable-tail表示完整绑定头、可确认目录归属及损坏尾部。观察不是后续修改的许可。

修复要求精确匹配当前摘要。父进程持插件调用锁，受监督worker持实际事务锁；损坏头、绑定不匹配、替换目录、矛盾的完整结果均拒绝。先排他保存原始evidence并同步，再写入有效pending并同步；复核文件身份/版本、内容及目录归属后原子替换日志，随后复用既有恢复流程。未提交事务恢复旧状态，已发布事务保留新状态；此操作不能授权新的发布。

证据文件名为TARGET.repair-随机标识.evidence，位于目标父目录，权限请求0600；Windows实际访问控制仍由宿主账户与目录ACL约束。残留pending和未知同前缀文件均保留并计入每目标16文件上限；父目录扫描上限4096条目。没有实现自动删除或全局物理容量管理。

失败返回1且不泄漏底层路径；参数错误2，取消130，成功0。取消或通信失败可能发生在替换后，必须重新检查，不把失败当作未修改。原始证据不自动清除。文件fsync不保证目录在断电后的持久性；未知归属或损坏头需要另外的人工恢复设计，不猜测删除。

当前验收：Windows真实worker/CLI及修复核心42项通过，Ruff通过，JUnit位于cache/state-operator-r2-20261015.xml。先前记录/恢复回归另有证据，不合并宣称最终整仓完成。三系统、进程强杀、故障矩阵和安装包验收仍待完成，此工具尚未完成发布验收。

### 2026-10-15 显式修复同快照三端验收

冻结549文件源码，archive SHA256为427248941bd5cfa4ea659181b9e68dee7e0348bc2041160d83f23bef92f012ac。九组161项，Windows161通过66.09秒、Linux161通过34.86秒、macOS161通过46.77秒，无跳过。包括证据保存后/pending写入后/原子替换后真实os._exit退出与重试/正常恢复；这不是外部强杀worker或父进程验收，也不是断电验收。全部任务句柄已终态，结果归档cache/state-operator-evidence-20261015-r1。

wheel构建及源码清单/RECORD核对通过：263运行文件、268成员，SHA256为5e261203b1ad4131fcf64f88206fc8889b644dddef7cbdfa160dfb159aaedaa3。尚未完成该wheel安装后真实命令、外部强杀及完整故障矩阵，不作为最终整仓或第三类完成结论。

### 2026-10-15 wheel实际安装命令验收

上述同一wheel已分别在Windows/Linux/macOS隔离target安装，各执行11次真实CLI命令，全部符合预期：缺失home检查不创建目录；self/显式platform/consumer三个分区分别检查、错误SHA拒绝、正确SHA恢复并保留原始证据；非法SHA退出2。关键四模块实际加载路径来自安装target，文件字节与wheel一致，未借用源码模块。三份verification.json已归档cache/state-operator-evidence-20261015-r1/installed-{windows,linux,macos}.json，全部进程终态。安装依赖复用既有验收环境，不声称全新操作系统零依赖安装。外部强杀、更多实际文件系统故障、最终整仓和第三类其余门槛仍开放。

### 2026-10-15 外部强杀与修复证据故障增量

r2快照549文件，SHA256 d25a561a5d4b6762bb2b65266be4e5e223e70597d2eae43c536933d703931b1e，相比r1仅修改修复测试与两份文档，运行代码未变，既有wheel与安装验证仍适用于同运行代码。两组51项三端全通过：Windows14.16秒、Linux4.05秒、macOS7.27秒。新增外部kill修复进程在证据保存后/pending写入后/替换后退出，确认锁可重新取得、证据保留、可重试或正常恢复；新增证据硬链接、同内容替换与原子替换错误，均拒绝并保留原始日志。Ruff通过。结果cache/state-operator-evidence-20261015-r2，句柄全部终态。

这批强杀直接执行修复的进程，不等同CLI父进程死亡后的受监督worker生命周期；后者专项和实际文件系统故障、物理配额、断电及剩余第三类门槛仍开放。

### 2026-10-15 CLI父进程死亡与两级锁验收

r3冻结549文件，SHA256 3d981294d5c7f61e43f0578d80ae214dfc11b9b02e6c987c13f75e4eab498aba；运行代码与已安装验收wheel一致，差异仅测试/文档，比较证据已存档。三组74项：Windows73通过1平台跳过48.79秒，Linux73通过1平台跳过32.52秒，macOS72通过2平台跳过39.50秒。三个新修复场景在三端均执行通过，无跳过。

实际CLI父进程持插件调用锁，真实worker持事务锁，在证据保存后、pending写入后、替换后阻塞；其他调用取得两级锁均被拒绝。强杀CLI后5秒内确认worker退出，重新取得调用锁及事务锁，保留原始证据且可重试或正常恢复旧状态。测试复用Linux仍活动task检查，不以zombie主线程判断整个worker退出。Ruff通过；全部句柄终态，证据cache/state-operator-evidence-20261015-r3。

此批关闭损坏尾修复的已测父进程死亡路径，不关闭损坏头/未知归属、实际磁盘满/ACL/fsync/网络文件系统、断电、全局物理配额、供应链和完整部署矩阵。完整八类目标继续开放。

### 2026-10-15 Linux真实容量错误（独立tmpfs/ext4）

使用r3运行代码，在新建临时目录分别挂载1MiB tmpfs及新建16MiB loopback ext4文件镜像；不填满宿主盘、不修改既有分区。真实写入填充文件直至errno28，修复也收到真实ENOSPC；原日志、旧状态和候选保持不变，失败证据/pending保留。释放本次填充文件后重试成功恢复旧状态，完整原始损坏日志保存在新evidence中。另在证据同步后填满空间，验证pending写入失败后相同恢复路径。共四份通过报告（tmpfs两份、ext4两份），六次挂载均以findmnt确认已卸载，测试镜像保留在专属临时目录。

两份ext4失败记录是验收脚本错误假设：先假设失败证据一定为空，再假设只会创建一个文件。ext4实际分配/同步时序可能允许证据完整写入后pending失败。修正为核对一至两个保留文件均为其预期内容前缀，仍要求修复必须报ENOSPC、原始日志/旧候选不变及释放空间后恢复成功，没有修改生产代码或放宽其安全断言。最终ext4观测为完整309字节evidence及0字节pending。原脚本、失败目录索引及四份报告归档cache/state-repair-real-enospc-evidence-20261015-r1。

这证明所测Linux文件系统的真实容量错误路径，不代表物理介质I/O故障、断电持久性、Windows/macOS磁盘满、全局/物理配额、网络文件系统或完整安全门槛已完成。完整八类目标仍开放。

### 2026-10-15 普通用户实际访问拒绝验收

Linux使用已核验wheel的实际安装模块，setpriv切换uid/gid65534并清除附加组；三种真实POSIX权限故障分别为日志mode000、父目录mode000、父目录0555。日志不可读/父目录不可遍历时inspect和repair退出1；父目录只读时inspect成功、repair退出1。原日志、旧状态及候选保持不变，无修复文件误创建；恢复权限后实际CLI修复成功，证据归属65534且mode0600。共九次真实命令，错误结构化且无私有路径泄漏，报告cache/real-eacces-state-repair-20261015-r3.json。

前两次验收启动失败是测试环境root目录0700使普通用户Python无法读取pyvenv.cfg，CLI进入源启动自举，尚未触及事务操作；已保留脚本与输出，后续为此次测试创建普通用户可读的独立虚拟环境并复制验收依赖，未修改原环境权限。该结果是Linux POSIX mode权限验收，不代替ACL、Windows/macOS或跨用户生产部署验收。

安装长期回归脚本新增self/显式platform缺失状态检查与非法SHA三项；Linux普通用户实际安装命令三项通过且Ruff通过，报告cache/state-operator-added-smoke-20261015-r1.json。本次没有重跑整个安装verifier，不把这三项结果作为完整脚本通过。

### 2026-10-15 Windows真实DACL拒绝验收

已验收wheel的Windows安装模块，实际icacls对专属临时夹具设置当前账户拒绝规则：事务文件拒绝R，父目录拒绝WD（创建文件）。六次真实CLI命令符合预期；日志不可读时inspect/repair均退出1，目录不可写时inspect退出0而repair退出1，输出结构化并脱敏。拒绝期间原日志、旧状态、候选保持不变且无修复产物误创建；恢复原DACL后实际修复成功并保留原始证据。结果cache/real-windows-acl-state-repair-20261015-r2.json。

首轮清理脚本使用icacls remove失败：拒绝R同时拒绝READ_CONTROL，普通清理无法读取文件安全描述符。仅对该已知测试夹具通过短生命周期恢复helper还原其父目录DACL，再icacls reset恢复继承，已确认无拒绝ACE。后续正式验收在施加拒绝前保存目标原始安全描述符及WRITE_DAC句柄，finally用SetKernelObjectSecurity从保留句柄还原，避免清理再次请求被拒绝的读权限；两目标均已恢复。初次失败与恢复脚本保留在F盘cache。未修改项目源码/真实用户数据或既有权限；辅助进程已退出，无持久权限设置。

此结果是当前账户在Windows DACL下的实际拒绝与重试，不代替跨用户服务部署、所有权迁移、继承攻击、macOS ACL或网络文件系统验收。第三类和全部八类目标仍开放。

### 2026-10-15 macOS实际权限与ACL拒绝验收

Intel macOS13.6.5 uid501，使用已核验wheel的安装模块及既有Python3.12验收环境。日志mode000、父目录mode000、父目录0555、日志ACL deny read、父目录ACL deny add_file五种故障，共15次真实CLI命令通过。日志不可读/父目录不可遍历时检查和修复均退出1；仅禁止写入时检查成功、修复退出1。错误结构化且不泄漏测试路径，原日志、旧状态及候选不变，无修复产物误创建；原mode/临时ACL恢复后修复成功，原始证据保留且权限0600。五目标测试权限均已恢复，任务终态。报告cache/real-macos-permission-state-repair-20261015-r2.json。

首轮夹具在prepare之前失败：macOS临时路径含/var符号链接，被已有父目录检查拒绝。验收脚本将临时目录规范化后通过，没有放宽生产路径检查。旧脚本保留。此结果不代替其他macOS版本/Apple Silicon/跨用户服务部署或网络文件系统。完整八类目标仍开放。

### 2026-10-15 macOS真实HFS+/APFS容量错误

使用已验收安装wheel，在新建32MiB HFS+及128MiB APFS磁盘镜像内执行；宿主盘不填满。HFS+数据填充产生ENOSPC后修复也明确ENOSPC，原日志/旧状态/候选不变，删除本次填充文件后重试恢复旧状态且保留原始证据。报告cache/real-macos-hfs-enospc-state-repair-20261015-r3.json。

APFS初次验收假设不成立：填充文件4KiB写入ENOSPC后，小型修复仍成功，不能将一次数据分配失败当作后续元数据操作必失败。保留该失败脚本；强化夹具，在专门pressure子目录继续创建并同步单字节文件，1772个后真实ENOSPC，修复随后也ENOSPC。仍要求原日志/旧状态/候选不变、保留失败产物并在释放填充文件后成功恢复；已通过，376字节失败证据保留。没有修改生产代码或降低恢复安全断言。报告cache/real-macos-apfs-enospc-state-repair-20261015-r2.json。

hdiutil前两次建镜像的脚本参数错误（type UDRW及无source时format UDRW）已修正，失败脚本保留；未挂载成功即停止，没有涉及既有磁盘。五次专属测试目录的镜像均通过hdiutil info确认已卸载，证明cache/macos-enospc-detach-verification.json。镜像仍保留在测试临时目录供复核。此结果仅覆盖macOS13 Intel的两种镜像文件系统，不代表物理介质I/O故障、断电、其他macOS/架构、Windows容量故障或全局物理配额完成。完整八类目标仍开放。
