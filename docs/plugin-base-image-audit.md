# 插件基础镜像 OS 审计

2026-10-14：当前未通过发布安全验收。SDK Python 包已知漏洞复验无匹配，不代表基础镜像 OS 包安全。

扫描对象是实际policy5 SDK镜像`sha256:7dfd1f1684d32fb37f2e13c0bcc5361a4e12ecaab254f53f44b1c89b858c73ad`，报告中的ArtifactName和ImageID均匹配该摘要。它使用Debian13.6（trixie）。Trivy0.75.0通过Docker读取镜像，只扫描OS漏洞，未运行第三方插件或修改服务。

工具从官方GitHub发布下载，发布校验和匹配，压缩包SHA256为`c6e65abddb348e25f10549df887045629cf28cc72453cd1c63acb717316b3f3f`，可执行文件SHA256为`93f9da8e4ba5e0c1c76d8234ed2494cf9afb0a96fd21953e424bb795f3299b8e`。这不是独立签名验证。数据库UpdatedAt为2026-10-14T01:19:20Z，来自默认aquasec镜像库；部分严重度来自其他厂商，适用性仍需核对。

扫描识别87个OS包，产生235条包漏洞记录、114个不同编号：Critical3、High58、Medium103、Low68、Unknown3。同一编号可对应多个包，不能把235当作235个独立可利用漏洞。进程退出0仅表示扫描完成，不表示安全通过。详细报告保存在工作树cache/trivy-os-audit-20261014/os-vulnerabilities-retry.json，扫描器和数据库存储在F盘独立测试缓存。

| 包 | 当前版本 | 扫描器报告的修复版本 | 后续验证 |
|---|---|---|---|
| perl-base | 5.40.1-6 | 5.40.1-6+deb13u1 | 3条Critical记录，核对32位限定及具体组件可达性；优先使用已修复的来源镜像 |
| gzip | 1.13-1 | 1.13-1+deb13u1 | 新镜像版本及恶意归档回归 |
| libpcre2-8-0 | 10.46-1~deb13u1 | 10.46-1~deb13u3 | 核对全部相关修复与实际依赖 |
| libsqlite3-0 | 3.46.1-7+deb13u1 | 3.46.1-7+deb13u2 | Python sqlite3及SDK导入回归 |
| libssl3t64、openssl、openssl-provider-legacy | 3.5.7-1~deb13u2 | 3.5.7-1~deb13u3 | TLS/证书及获批出口回归，不删除Python所需的SSL库 |

表内版本来自扫描数据库，尚未证明发行仓库实际可下载或新镜像已包含修复。High还包含未给出修复版本的记录，不能通过只升级表中包关闭全部风险。

已取得官方python:3.12-slim最新index元数据并核对响应摘要；linux/amd64候选为docker.io/library/python@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe。已拉取并核对image ID为df18428df8a0f9189c84e7424d214b4c50092af1f9dca5842a00c41c39bda316，同一数据库时点、同样OS扫描参数复验退出0：Debian13.7、87包、166记录/73个不同编号，Critical0/High44/Medium58/Low61/Unknown3；扫描器没有给出可修复Critical/High记录。

该固定来源已构建实际SDK30ce37186ec642e502797c0d38ed6a37515ffed01ba4642ac80d4c8ae0fb5a62，真实RPC矩阵退出0，27个worker清除、撤权后状态保留、恢复账本各项0。实际SDK的同数据库OS复验亦退出0：166记录/73编号，Critical0/High44/Medium58/Low61/Unknown3，与基础候选一致；不是仅扫描基础镜像后推断SDK安全。实际SDK13项Python发行包清单已记录，pip26.2，独立漏洞查询正在执行。

隔离只读、无网络、UID65534探测通过：Python3.12.15、OpenSSL3.5.7、SQLite3.46.1，SDK导入/内存SQL/默认CA载入成功；CapEff全0、NoNewPrivs=1、Seccomp=2，审计容器不存在已确认。此结果证明该探测约束有效，不能泛化为所有生产部署或漏洞不可利用。

44条High按包记录汇总为8个不同编号：util-linux四编号在九个关联包产生36记录；ncurses一编号在四包产生4记录；systemd-homed一编号在两库产生2记录；libacl与perl各1记录。扫描采用源包归属，因此关联库记录未必包含受影响可执行组件。实际镜像包含mount/nsenter，但探测无capability且禁止新增权限；还需验证挂载/用户命名空间和完整运行参数，不能仅凭非root豁免。systemd-homed在指定标准路径不存在、Archive/Tar.pm在指定路径不存在，仍需完整包文件清单/发行版说明确认，不能以单路径探测关闭记录。Python_curses/readline实际链接ncurses/tinfo，因此不可为减少扫描记录盲删这些库；需核对恶意终端数据入口、上游修复及复现实验。libacl仍需核对消费者与路径权限。所有记录继续保留，未添加宽泛allowlist。

候选不代表所有漏洞修复或完整生产安全通过。其他架构未验收，不能把本次linux/amd64结果泛化；生产配置/catalog未改。

补充实际dpkg-query文件清单：17个相关包已在同一实际SDK、只读/无网络/非root容器中逐个列出，包含743个perl-base包路径；这些相关包清单未含Archive/Tar.pm或systemd-homed，但这仍不是整幅镜像的不存在证明或发行版适用性裁定。mount/umount、su、newgrp/sg存在setuid位（04755），当前NoNewPrivs阻止其通过exec获得额外权限；不能删除运行参数中的该保护。后续最小SDK候选可评估去除不需要的可执行工具/setuid位，必须固定构建库存、更新策略身份并复验，不原地修改当前镜像。初次清单探测因诊断capture对超过64KiB单行切块插入换行而JSON解析失败，容器清理确认；改为有界多行JSON后重试退出0，失败日志保留。此为探测格式修正，不是生产读取协议修复。

下一步补齐上述八编号的上游适用性、受影响组件及权限路径，验证完整出口/取消矩阵和其他声明架构，再决定是否采用候选或更小的独立SDK基础镜像。避免在插件prepare中临时执行无版本的系统升级，否则相同base digest与依赖锁也不能重现镜像内容。删除未使用工具前先核对安装、Python扩展、SDK/出口代理及包依赖；不要按CVE数量盲删系统库或官方功能。

发布门槛包括可修复Critical/High处置、未修复记录的组件/架构/调用路径评估、完整RPC与出口隔离、SDK及解释器/SSL正常运行、取消回收及新镜像供应链身份验证。未修复项必须明确列出，不能用ignore-unfixed或宽泛allowlist隐藏。此OS扫描不覆盖源码逻辑、Docker daemon/宿主内核、编译到/usr/local的Python解释器或未知漏洞。

## policy6 与独立代理实际镜像复验（2026-10-14）

实际SDK ad31a18c8c752822ea855cf3495c2f7355be5984afd27d7914ae2492b1370664 与平台代理 c692074f5280c0f677fb9b195b23b99ab26dca1d35fb79537a210ba7f4e94532 均已单独扫描，报告分别为 cache/trivy-os-audit-20261014/policy6-sdk-os.json、independent-gateway-os.json。两次均退出0，各Critical0/High44/Medium58/Low61/Unknown3，与前述候选一致；八个High编号处置仍开放，没有新增豁免。

SDK实际13个Python包（包含pip26.2）重新捕获后使用 pip-audit --no-deps --disable-pip 查询，退出0、No known vulnerabilities found，CycloneDX报告在 cache/sdk-policy6-audit-20261014/sbom.json。输入是实际完整分发清单，未重新求解依赖；该结果仅表示查询时已知Python包漏洞无匹配。代理以显式site-packages路径检查物理分发元数据为空，移除第三方Python包不移除OS风险。完整插件网络生命周期、其他架构与实际部署安全仍未验收。

实际整树组件盘点补充：cache/remaining-os-components-20261014/verification.json，14408退出0，SDK遍历7645项、gateway5034项；两者均发现 /usr/bin/infocmp，不能以Python只链接ncurses库为由将CVE-2025-69720判为组件缺失。各发现11个setuid/setgid普通文件，包含mount、su、passwd等，no-new-privileges仍是必要执行约束。两者未在已遍历路径发现systemd-homed或Tar.pm，但两个APT partial目录因权限拒绝未遍历，不能宣称完整文件系统不存在或关闭对应编号。探测使用只读/无网络/无capability/禁止新增权限及-I -S，仅查询平台固定实际镜像；具名容器删除并确认缺失。下一步核对上游影响函数及发布组件，评估gateway移除无用命令和清除setid位的固定构建策略；不原地改现有镜像或删Python需要的共享库。

最小硬化候选已做隔离实验：从固定实际gateway c692074f派生新只读候选cbde413966bc023a09b20226633a7e61b847d34f349b64554b2f9fd0f002840d，只移除infocmp命令、清除/usr/bin、/usr/sbin、/usr/local/bin普通文件的setuid/setgid位，保留Python需要的共享库。非root探测相应命令不存在/setid列表为空，SSL/SQLite/策略导入正常；实际_network的自有HTTP peer正反向权限与直连隔离通过、恢复账本四项0，证据gateway-hardening-candidate-20261014。为单独检验传输，实验明确覆盖了镜像解析器，不作为批准catalog/生产标签验收；当前生产POLICY仍1。删除文件不必然改变dpkg源包扫描记录，更不等于八个High全部关闭。采用前需在正式固定构建中实施、更新独立策略身份，并复验实际镜像/冻结包/插件生命周期和漏洞适用性。

## 发行版上游逐项核对

2026-10-14已直接读取Debian Security Tracker八个编号页面，全部HTTP200、最终URL仍为官方站点，响应各不超过256KiB；原始HTML与SHA256清单保存在F盘任务缓存debian-cve-evidence-20261014/provenance.json。上游Trixie均仍标为vulnerable；ncurses、systemd及四项util-linux列为no-dsa/Minor issue，acl记录等待point release且新增ABI不适合单补丁回移，Perl记录postponed等待上游回归处理。这些是发行版处置状态，不是本项目豁免，也不能替代实际组件/权限路径分析。不要混入sid包或prepare时临时无版本升级来消除扫描数字。

| 编号 | 受影响路径与当前判断 | 剩余处置 |
|---|---|---|
| CVE-2025-69720 | infocmp的analyze_string，真实命令存在；平台代理不调用它，插件本已获准运行容器内代码，但仍保留攻击面 | 候选移除命令已验证；正式固定构建/策略身份及适用性复验尚未完成 |
| CVE-2026-76642 | 失败mount helper后仍执行特权post-hook，涉及X-mount.owner/idmap | 非root/cap-drop/no-new-privileges必须保留；验证实际mount权限拒绝及完整部署约束 |
| CVE-2026-78408 | 特权nsenter --join-cgroup跨凭据保留root打开的cgroup.procs | 平台不以root进入未知guest命名空间；验证实际namespace操作拒绝，宿主运维不可绕过该边界 |
| CVE-2026-78409 | Linux6.15+、fstab授权X-mount.subdir及中间符号链接 | 当前无挂载能力、不提供宿主fstab授权；验证挂载/用户namespace约束，不按内核版本猜测豁免 |
| CVE-2026-78410 | SUID mount、可替换fstab源、X-mount.owner/group/mode | no-new-privileges阻断SUID提权；候选清除setid位，保留正式构建/原生验收 |
| CVE-2026-54369 | 特权调用libacl pathname API时路径替换 | 平台core/plugin源码未发现这些API或setfacl调用，普通worker不能获得宿主权限；文本扫描不是所有动态调用的证明 |
| CVE-2026-16742 | systemd-homed管理已登录用户、缺失home-record签名校验 | 已遍历镜像/相关包清单未发现homed，未运行该daemon；两个APT partial路径未遍历，组件/部署适用性不作宽泛结论 |
| CVE-2026-9538 | Perl Archive::Tar解析恶意size导致内存耗尽 | 已遍历镜像/相关perl-base清单无Tar.pm，平台使用Python有界归档路径；保留组件适用性记录及worker内存限额 |

官方来源分别为 https://security-tracker.debian.org/tracker/ 后接表内编号。风险降低措施和受限路径证据不构成所有CVE已修复或整个平台达标；这些记录无宽泛allowlist，正式采用瘦身方案后仍需发布时重新扫描与复核。

当前实际SDK和gateway的权限屏障已分别复验：runtime-privilege-barriers-20261014/verification.json，UID65534、CapEff=0、NoNewPrivs=1、Seccomp=2；容器内自有/mnt上的tmpfs mount返回32，unshare用户namespace与nsenter当前容器mount namespace均返回1。两容器均删除并确认不存在，无宿主目录挂载、无外部网络或真实服务写入。此结果证明当前daemon默认seccomp与固定运行参数下相关特权操作被拒绝，不是漏洞利用复现、内核无漏洞或所有rootless/远程部署保证；仍不能移除cap-drop/no-new-privileges/只读根等约束。

正式网关策略2已落地并原生验收：实际image359ebe4520dd3b516b0894ef81fcb9a3784eec7390a2498d37b546dc03b6daa2，批准catalog/schema2及实际标签核对通过；移除infocmp、清除三个可执行目录普通文件setid位，保留共享库，无实验解析器覆盖。scripts/verify_plugin_gateway.py已检查物理清单、无第三方Python分发、SSL/SQLite/策略导入和实际权限，并复验完整合成插件HTTP/CONNECT/撤权/清理后提交，98809退出0，全部资源恢复计数0。正式OS复验gateway-policy2-os.json的ArtifactName匹配实际镜像，12844退出0，仍Critical0/High44/Medium58/Low61/Unknown3；保留原包元数据，不按扫描数字删库。CVE-2025-69720的受影响命令在正式网关已移除，但SDK仍含该命令，不能把此结论跨镜像复用。
