# OpsKit 历史实施记录

以下保留原计划中的早期批次原文，仅为历史证据，不表示当前待办或最新源码验收。当前八类目标见 [项目健康计划](project-health-roadmap.md)，逐批实际验收见 [实施验收台账](implementation-acceptance.md)。历史未验证项只有在最新证据覆盖后才能关闭。

## 静态健康与驱动解压的新一批状态

已接入Ruff0.17.0关键正确性零基线及F401/F841存量候选门槛，规范见static-health-gate.md。123项仅是待梳理候选，不能批量判为垃圾代码。MySQL Windows缺subprocess、类型注解缺导入已修复，真实任务临时junction回归通过。MySQL ZIP已复用共享安全解压，原路径校验在strip之前，拒绝逃逸/链接/特殊类型/混合前缀及超额内容；共享默认模式及前缀模式相关104项通过。

上一轮wheel与1297项完整通过早于本轮代码修改，属于历史证据；当前源码尚未重新打包或执行完整回归。后续先统一其余Recipe安全解压与系统写事务，再完成新源码的打包/跨平台及实际服务验收，不能以静态零错误替代完整安全目标。

Node.js/Go/Java/MongoDB的Windows ZIP安装路径已与MySQL共用安全解压，四套冗余解压循环移除；四个驱动的正常布局、逃逸、链接、限额以及Node平铺包联合132项通过。下一批处理PostgreSQL/Redis/Python与Linux/macOS tar及包内提取，并审查固定staging、覆盖删除与用户数据保留。当前源码尚未全套/重新打包，不能将上一轮产物作为本批发布证明。

PostgreSQL Windows已迁移共享安全ZIP提取与独占临时目录，取消删除既有版本，Windows rename发布拒绝覆盖。53项相关回归覆盖旧数据/旧staging保留、发布失败与取消、竞争创建目标及恶意包。继续Redis/Python与其他平台提取；后续PATH/服务事务、系统跨用户边界与完整发布检查仍未完成。

Redis Windows已迁移共享安全ZIP提取、独占staging及拒绝覆盖的Windows发布；四种平铺/嵌套布局保留支持，同目录DLL/配置移动到bin而非重复复制。多个入口和bin文件冲突拒绝，旧版本和竞争创建数据保留。联合85项通过，范围包括Redis/PostgreSQL/其他Windows ZIP、共享前缀和版本变更/激活；不与前批数量相加。下一步Python UV引导、其他平台tar/包内提取及完整事务，仍需全套/重新打包。

Python Windows UV引导已取消远程PowerShell执行与第三方镜像回退，下载走官方HTTPS ZIP、64MiB下载上限及120秒/16KiB进程树监督；共享安全提取、唯一准确uv.exe入口、独占临时目录、路径拒绝覆盖及取消清理。移除无消费方的三个旧Windows源常量。联合134项通过。仍使用latest/x64资产且没有独立可信固定摘要，现有私有/系统uv不做版本/签名证明；完整来源策略、ARM64、提取总预算、Linux/macOS脚本引导及Python工具链下载供应链继续P0审查，不能据此声明供应链达标。

共享tar提取新增strip_components，原路径、全部类型/配额在剥离前验证，拒绝混合顶层目录；默认不剥离保持既有调用。Go Linux/macOS继承驱动已迁移，拒绝非go根成员，保留POSIX UTF-8字节路径以兼容打包时ASCII文件系统编码的Unicode夹具。联合115 passed / 1 skipped；POSIX原生UTF-8路径未在Windows验收。Go仍直接写最终版本目录，失败后半成品、既有版本覆盖及跨系统事务仍需处理。Node/npm和Temurin legal存在必要合法符号链接，必须先实现完整内部链接图校验、拒绝逃逸/环/特殊类型，再迁移；不能以丢弃链接精简功能。

后续Go安装已改为共享archive_install独占staging，确认bin/go后原子发布。Windows原生rename、Linux renameat2(RENAME_NOREPLACE)、macOS renamex_np(RENAME_EXCL)，禁止用可覆盖的普通POSIX rename回退；缺接口明确失败。PostgreSQL/Redis Windows复用同一模块，移除重复目录生命周期代码，保留竞争错误的明确说明。最终122 passed / 1 skipped，含Windows真实空/非空目标竞争、取消/失败清理及接口标志替身测试。Linux/macOS系统调用与POSIX UTF-8原生证据仍缺，完整PATH/服务事务、其他驱动及全套/打包待完成。

Node.js/Go/Java/MongoDB/MySQL Windows也已复用独占staging，结构检查改为发布前is_file，缺binary不发布；重复或竞争安装明确拒绝覆盖，InstallError保留具体结构原因。与PostgreSQL/Redis、共享发布及版本操作联合132项通过，含六驱动统一恶意包/缺binary/旧数据/发布失败/取消/竞争矩阵和Redis专用测试。Windows本批不留失败半成品，不代表后续环境/服务/状态事务全部完成。继续其他平台链接图和提取、生产来源验证以及全套/新wheel。

Node/Java Linux及继承的Darwin驱动已迁移共享tar与独占发布。tar默认仍拒绝链接；仅显式allow_symlinks允许经完整成员图校验的内部相对symlink，硬链接/特殊类型仍拒绝。检查重复成员、文件/目录冲突、越界/缺目标、链接链和目录递归环；禁止成员写入链接目录，普通文件写完后创建链接。npm/npx、JDK legal链接样例在本机原生symlink验证可读；Linux/macOS发布/官方真实资产仍待验收。相关结果见台账；后续MongoDB/MySQL/PostgreSQL/ClickHouse及包内tar、源码编译路径继续审查，全套/新wheel尚未执行。

MongoDB Linux及Darwin继承驱动也已迁移共享strip1和独占发布，默认不开放tar链接，发布前确认mongod。路径逃逸/重复/混合根/链接/特殊文件/限额、旧数据库、缺binary、发布失败及取消有临时数据回归。共同发布故障注入改为共享publish_new_directory入口，避免POSIX syscall绕过Path.rename注入；实际目标竞争仍调用原生发布函数。最终184 passed /1 skipped，静态123候选无新增。继续MySQL/PostgreSQL/ClickHouse及包内tar、完整服务事务和原生/发布验收。

本批WSL Python3.14.6真实Linux /tmp追加验证共享archive/archive_install：UTF-8及强制ASCII编码分别9项全部通过，含renameat2排他发布、空/非空竞争、合法链接、权限、Unicode成员、取消清理及越界拒绝。可复用stdlib脚本scripts/verify_archive_native.py；当前仅Linux机制烟测，macOS接口、真实驱动/官方资产和全套仍需完成，未将既有skip改写成通过。

MySQL common.extract_tarball已复用共享strip1与受验证内部symlink；Linux/Darwin安装用独占staging、检查mysql后发布，拒绝覆盖。Linux系统依赖调用移至包校验/结构检查之后，非法包/旧版本不先执行包管理。联合103 passed /1 skipped；gzip/xz及包内私有库链接样例覆盖。依赖检测/安装仍有吞错及无总期限问题，真实官方资产、系统依赖与数据/服务/环境恢复待验收。剩余手写提取集中于PostgreSQL Darwin及Debian、MySQL Debian、Redis Debian、ClickHouse单binary；继续按调用链迁移，不把updater文件复制当解压垃圾。

PostgreSQL Darwin ZIP/tar已统一共享strip1与独占staging，确认psql后发布并拒绝覆盖；tar允许经过图校验的内部链接，ZIP仍拒绝链接。ZIP新增显式preserve_mode用于macOS包，执行位归一755/非执行644，去除setid；默认调用不变。联合143 passed /1 skipped。Linux原生验证脚本补ZIP权限检查；macOS接口与真实EDB/theseus资产仍待验收，若真实ZIP必要链接出现，须先设计受验证支持，不允许不安全回退。剩余Debian内提取/ClickHouse及系统事务继续处理。

ClickHouse单binary提取已改为完整包共享校验/有界流写，唯一官方usr/bin候选优先，模糊/重复候选拒绝；独占staging完成alias后发布，拒绝覆盖。alias改相对链接，fallback脚本引用最终路径，避免发布后指向已删除临时目录。联合79 passed /1 skipped；16MiB样例Python分配峰值约32.1→4.4MiB，耗时0.27→0.34秒，摘要相同，不声称提速。全包校验有额外磁盘I/O，真实资产和代表性负载仍待测。剩余Debian内提取及完整系统事务/全套/打包继续处理。

三个Debian路径的外层ar已统一core.debian.extract_data_tar，取消ar x与MySQL整块读取/吞错回退；先验证完整成员表，只流写唯一精确data.tar.*，检查版本、名称、重复、截断/填充、2GiB容器/512MiB数据/16成员上限及输出不覆盖。联合106项、修订包装后解析20项通过；Linux原生脚本追加正常payload与晚到恶意名称写前拒绝。内部tar映射、zstd/xz全量解压、链接/重叠合并、下载期限/来源及PG/Redis最终目录事务仍未完成，不能将外层ar安全当整个deb链路达标。

后续调用链核对证明MySQL apt.py无生产消费者、公开导出/注册/构建入口，仅有上一批新增包装测试；实际MySQLRecipe走tarball下载。该闲置模块及包装测试已删除，不增加新APT功能，也不继续维护其脆弱索引/提取。PostgreSQL真实Debian路径已改共享原始/映射路径双校验、规则文件白名单和不覆盖流写；整个包组在独占staging合并后发布，后包冲突/取消清理自有文件，旧database在网络前拒绝。原有库链接仍按版本文件生成，真实libpq加载待验收。

open_data_tar提供普通tar/gzip/xz/bzip2/zstd的有界展开，原始tar限2GiB+64MiB，XZ字典128MiB及严格单流/标准填充，zstd窗口128MiB；zstandard>=0.25,<1补入两份运行依赖并重锁（保留既有版本，新增zstandard0.25和之前未入锁Ruff0.17）。相关132项通过，修正末尾读取后的选择/实际PG/wheel库存37项通过，pip check通过；Linux UTF-8/ASCII机制各14项通过。新增依赖的干净安装/漏洞与SBOM需重做。Redis内部映射/硬链接/独占发布、下载来源/预算、真实资产和全套/新wheel仍未完成。

Redis Linux Debian安装已复用有界外层/展开/选择与独占发布，仅精确usr/bin白名单；合法soft/hard alias在全部包合并后统一验证、终点解析和相对链接/硬链接创建，拒绝越界/缺目标/循环/重名，不再拿redis-server或check-rdb冒充缺失命令。server与cli均为必需完整性。Linux源码修正未复制实际src产物的缺陷，Linux/macOS复用同一有界进程树监督编译（1800秒/1MiB输出/无stdin），产物在源临时目录删除前复制并完成发布；取消/缺产物/失败不留最终目录。联合95项通过，静态无新增；编译为替身、包为惰性样例，不证明真实服务/编译和正式来源。软件手写提取调用已迁完，共享实现、真实资产、跨平台以及完整事务/全套/新wheel仍需验收。

最新源码复验：全套1644项收集，1629通过/13跳过/2失败；两个3秒真实进程测试未到达预定故障阶段，单独重跑通过，但不能改写全套失败。下一批需稳定就绪与阶段注入证据并做无竞争全套复验，保持发布门槛未通过。新干净快照wheel228项运行文件/233成员与源码/RECORD一致，独立安装pip check及91种help+7查询/拒绝通过；25项依赖已生成SBOM，未发现已知漏洞。Linux UTF-8/ASCII各14项机制复验通过。详情见implementation-acceptance最新记录，八类计划仍全部保留。官方参数计划第17节新增具体采样/算法任务；另需公开发布前核对缺失LICENSE与MIT元数据的法律文本一致性，不能自行假定权利人。

随后真实进程阶段测试改为在标记实际下载阻塞/持锁后，仅对父监督器时钟注入截止耗尽；启动仍有真实30秒上限，进程回收、文件和锁为原生，产品时钟/期限不变。ZIP新增完整原始/剥离后布局检查、Windows别名/设备名拒绝、既有文件不覆盖、竞争创建与取消清理、4096字节/128层路径限额。全套1670通过/13跳过，431.16秒；Linux UTF-8/ASCII各16项通过。新wheel228项运行文件/233成员与源码一致，已有独立审计依赖环境替换为新平台wheel后98项入口复验通过；依赖未变化，未声称再次全新解析。

Gitleaks8.30.1固定发行摘要校验后，379项提交候选文件及Git所有引用/合并差异扫描均未发现规则匹配；Windows/Linux结果一致，报告全量脱敏，随机合成PAT阳性及脱敏检查通过。CI新增完整历史及合并差异秘密门槛，远端仍未运行；这是已知规则扫描，不证明不存在所有秘密。Git有323个可达提交，工具报告320个处理提交，不用工具计数替代全部二进制/规则范围审查。下一批优先修归档解析前配额：64MiB PAX头会让tarfile.open在共享提取器之前请求64MiB读取，安全探针已拦截并保留证据，当前尚未修复；同时继续系统事务/更新恢复及完整插件沙箱原生验收，八类目标不关闭。

2026-10-14 独立开发工作树进展：Node/Go/Java Windows路由协调与批量PATH恢复已完成针对性回滚验证（四模块123项及追加真实临时注册表2项）；Python uv安装转统一进程树监督，输出1MiB/总1800秒/无stdin，真实超时及超量子进程回收通过；Python请求版本改精确patch校验且前置于路由副作用，包管理器错patch继续源码回退，联合92项通过。全部是工作树增量，原develop待提交清单未改变，未提交/推送。完整安装/卸载及崩溃恢复、Python旧JSON路由、原生Linux/macOS和远程CI仍未完成；八类目标保持开放。详见implementation-acceptance对应2026-10-14记录，历史章节的“尚未修复”只代表当时状态。

## 下一批 Python 生命周期的实际调用链决策（2026-10-14）

当前工作树只读复核确认三项未完成边界，按下面顺序实施，不能以当前Windows/WSL的版本测试替代验收：

1. LinuxDriver.ensure_uv 优先 UV_INSTALL_SH_GHPROXY，再取官方脚本；curl/wget输出无界capture_output，执行sh脚本的异常路径未确保临时文件删除，DarwinDriver直接继承。移除这条远程脚本执行路径和无消费的代理常量，替换官方明确OS/架构release归档、HTTPS重定向策略、下载总预算、下载/解析/提取上限及独占发布；来源可信摘要必须独立于同来源未签名下载获得，不能以随包SHA冒充签名信任。支持声明中的架构需实测，未知架构明确拒绝；用户已有uv是否可以执行必须有明确来源策略。验收包含恶意布局、超量/取消/下载失败、竞争目标、旧文件保留、临时清理和真实Linux/macOS产物，避免把Windows latest/x64设计直接宣称全平台完成。

2. Python命令路由现Windows findstr读取JSON并删空格/引号，POSIX sed读取JSON后exec、fallback模板不做shell转义；Windows apply_version_link及restore_original为空。采用受控、明确目标的独立路由，快照只保存状态，不再让shell解析JSON；安装/切换/卸载走统一事务边界，平台驱动公开协议需保留官方包管理器和源码安装产生的外部目标，不能只支持uv私有目录而删原能力。Windows普通路径包含空格/Unicode/元字符和跨盘目标均必须有证据。所有权判定只接受完整规范路由及精确历史模板，不能仅匹配注释；旧模板仅为迁移证据。

3. PythonRecipe.uninstall当前先rmtree活跃版本再switch，switch失败吞错再删除快照，属于破坏性错误顺序。必须先完整预检/锁定版本组和路由快照；在可恢复阶段切换到验证通过的保留版本或恢复原命令，失败保留旧安装；待删除目录需私有隔离、身份校验和持久化阶段记录，再提交状态及清理。失败恢复不得覆盖外部修改，删除阶段失败须留可识别恢复记录；不能把删除成功但快照失败当可回滚。多账户系统路径锁、崩溃恢复和删除过程中路径替换需平台相应句柄/dirfd策略，lstat预检查不足。

本节为明确实现与验收队列，三项均未完成；保持八类门槛和原用户范围。













## 早期更新与发布批次原文

当前更新链路已接入：签名候选及原子指针发布、候选配额和受控清理、跨数据目录程序目标锁、目标级版本/摘要记录、备份身份/摘要回滚、Windows独立快照和原子替换、启动前任务索引及阶段日志、任务健康归属，以及helper实际启动确认与精确进程身份。实测证据集中在implementation-acceptance.md；这些是本地实现和测试结论，不能代替生产或跨平台验收。

下一批优先完成未索引/旧任务恢复、其他终态退役、marker凭据和断电日志。job配额及当前索引任务恢复已接入，仍不能凭PID或exited删除任务、杀进程或判定替换失败。存活查询只描述身份快照；恢复必须在锁内核对实际目标摘要、事务和备份。真实多账户权限、旧嵌套任务迁移和原生产物继续待验收。

当前已接入索引schema2任务的update reconcile及启动核对：在根/目标锁内以实际摘要和备份/事务证明应用或未提交，独立恢复凭据供status读取；未解决任务暂停自动更新写入。备份清理已保护任务和marker引用，旧/未知任务保留全部。下一步先做未索引/旧任务、其他终态退役和marker完整凭据，再验收真实断电；索引任务的本地故障恢复不能代表完整更新恢复完成。

健康记录所有读改写入口已统一缓存根锁；旧进程不再删除其他build记录，回滚后仍保留恢复引用。同build重放不重置确认/计数，后台在健康延迟后以360秒竞争窗口重读并确认；非竞争错误不重试。已索引现代任务现支持有证据的回滚退役及活动索引转历史，目标持久化升级下限拒绝最高回滚build及以下候选。仍需成功/其他失败任务退役、未索引/旧任务、无索引回滚启动恢复、真实运行程序替换及跨数据目录健康归属；不得为释放配额删除不确定记录。

当前本地wheel已重新在干净源码快照构建，包含本轮回滚恢复及CLI兼容修改；227项源码/YAML、232个wheel成员逐字节和RECORD校验通过，无运行目录/克隆插件。新虚拟环境独立从固定TLS PyPI安装全部wheel依赖（未复用宿主site-packages），91个帮助入口及7种查询/拒绝检查通过，源码来源为安装目录且未创建运行数据。首次独立安装暴露Typer0.27解析后端差异，已修复并保留失败产物。构建命令用python -I -m build避免本仓build.py同名遮蔽。

24项第三方运行依赖已查询已知漏洞并生成CycloneDX SBOM，本次未发现已知漏洞；这不证明没有未知漏洞或平台逻辑漏洞。CI增加wheel库存与独立安装CLI验收，但尚未远端执行。跨平台、最低依赖版本、原生程序、真实服务/容器和生产签名仍需完成。测试与artifact明细见实施台账，官方参数完成门槛见official-cli-completion-plan第3、9、13、14节。

