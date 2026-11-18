# Python依赖安全基线

## 原生 TLS 公告核对（2026-10-16）

后续Linux专属解释器已落地 `/var/tmp/opskit-python-linux-20261016-r1`：发行资产SHA256 `5d546fc004fc2435fa142eb8f42ed8de5eb4725963dc22f7e6671014a80eb560` 与发行元数据、已核摘要的SHA256SUMS一致；原生目录安全解包后实测Python3.12.15/TLS3.5.9。不改变系统Python；r3全新wheel安装已启动。此结果不改变旧3.5.7宿主证据，也不代表最终冻结产物已使用该解释器。

已下载[OpenSSL 官方漏洞索引](https://openssl-library.org/news/vulnerabilities/index.html)，按明确的数值 from/before 区间核对实际后端：3.0.16命中37项，3.5.7命中23项；3.5.9及4.0.3在本次可解析区间内无匹配。此方法未覆盖供应商回补、非数值区间和所有原生依赖，不是完整安全证明。原索引、解析结果及20260127/20260609/20260929公告原文、摘要保存于 `cache/openssl-advisories-20261016-r1/`。

3.0.16命中的High包括CVE-2026-84782（DTLS重传，修复3.0.23/3.5.9）、CVE-2026-45447（PKCS7_verify，修复3.0.21）及CVE-2025-15467（CMS AuthEnvelopedData，修复3.0.19）。3.5.7也命中前述DTLS公告。当前平台HTTP入口使用HTTPX/TLS；发布签名为Ed25519，源码搜索未发现直接DTLS、PKCS7或CMS调用。这只能说明所查直接入口，不能排除依赖、动态调用或其他命中公告的可达性，不能把这些High统一判为不受影响。

已提供任务专属Windows Python3.12.15/TLS3.5.9（发行资产与SHA256SUMS双重摘要核对），不更改系统解释器。基于它的r2 wheel独立安装二次审计已通过113项CLI及两项实际期限，cryptography后端仍为独立的4.0.3。新r3 Windows CI使用这一解释器。Linux旧宿主安装TLS3.5.7仍须替换或确认发行方补丁；manylinux冻结构建解释器与宿主不是同一运行时，不互相推断漏洞状态。

开放发布门槛：明确各正式构建解释器/原生库来源及补丁状态，审核全部命中公告的实际路径，为正式产物记录实际TLS与加密后端；增加与供应商回补兼容的构建策略并在最终产物复测。当前没有新增生产TLS门槛，也没有据此宣布整个供应链安全达标。


2026-10-16最新585兼容候选的独立安装审计重新核对包版本及原生后端：Windows Python3.12.10的TLS为OpenSSL3.0.16、cryptography后端4.0.3；Linux Python TLS为3.5.7、cryptography后端4.0.3；专属macOS解释器与私有加密wheel均3.5.9。Python发行包升级不会同步升级解释器TLS，不能用cryptography新后端证明Windows/Python标准库TLS已更新。旧解释器TLS的来源、支持状态及精确公告仍需核对；此处只记录实际版本，不宣称所有原生组件已安全达标。证据为`cache/compatibility-source-20261016-r2/{windows,linux,macos}-install-r2/audit-r1/audit.json`。

2026-10-16复核全新macOS安装的25个外部Python发行包，按精确版本查询并保存PyPI公开漏洞元数据。发现应用锁中的AnyIO4.13.0、idna3.13及解释器自带pip25.0.1有公告；本次查询不是原生库、Rust依赖或操作系统的完整漏洞扫描。公开元数据没有公告也不能证明不存在漏洞。

| 依赖 | 已发现的边界 | 本次处理 |
|---|---|---|
| AnyIO4.13.0 | CVE-2026-63374涉及国际化域名TLS证书校验；CVE-2026-64847涉及进程池stderr未排空导致阻塞，公告修复版4.14.2 | 项目声明安全下限`>=4.14.2,<5`，锁定4.15.1；不能据公告直接推断当前同步HTTPX路径可利用 |
| idna3.13 | CVE-2026-45409：部分超长输入在拒绝长度之前执行高成本上下文校验，完整修复版3.15 | 项目声明`>=3.15,<4`，锁定3.20；调用方仍须保留输入长度和整体预算边界 |
| typing-extensions4.15.0 | 不是本次发现的漏洞项；新AnyIO在Python<3.15要求至少4.16.0 | 按新依赖闭包升级为4.16.0，不保留不满足元数据的旧版本 |
| 验收环境pip25.0.1 | 包含多个归档/路径及索引相关公告；触发条件各不相同，不能把只安装哈希wheel视作版本已修复 | 新验收环境使用单独完整哈希锁的pip26.2.1；旧环境保持为历史证据，不在正在运行的测试环境中升级 |

`pyproject.toml`显式声明两个传递依赖的最低安全版本，使普通pip安装同样受到约束；仅更新uv锁无法约束重新解析依赖的使用者。未增加新的业务组件。`uv.lock`只改变上述三个应用包的版本，运行时、CI和构建依赖导出一并更新；签名专用子集及构建引导子集不受影响。pip是独立验收工具，不加入应用运行依赖。SDK的独立pip26.2已有自己的锁与镜像身份，本批不为宿主依赖升级而无条件重建SDK。

精确版本的官方PyPI包摘要与uv锁中全部wheel/sdist摘要重新核对；新AnyIO/idna/typing-extensions公开元数据无未撤回公告，`uv lock --check --offline`通过。这些是解析与来源证据，不能替代新依赖下的实际行为、独立安装、整仓及原生产物验收。

原始发现位于忽略目录`cache/macos-intel-install-20261016-r2/pypi-vulnerability-r1`；旧锁副本、升级后元数据和逐包差异为`cache/dependency-security-20261016-r1/{before,lock-audit.json}`。当前在途Windows诊断全量和macOS完整回归使用升级前快照，必须保留其原始结果，不能记为本次依赖安全修复的验证。

后续验收：新依赖环境先执行HTTP/下载/网络期限、插件授权及锁导出检查，再构建新wheel核对元数据与RECORD，在声明平台进行全新安装。源码稳定后补整仓与冻结门槛；正式发布仍受镜像OS漏洞、生产签名、跨平台和其他路线图门槛约束，不以本批依赖更新关闭整个供应链要求。

本批583文件快照`95d38c69209e319b4b3ccfa7071cae941b63f0494e1a62069c32377c1f2cf6e5`：Windows全新完整哈希CI依赖环境的上述九组107项全部通过（69.78秒）。无隔离构建的新wheel为266运行文件、271成员，源码/资源库存与RECORD核对通过，SHA256 `bc9ed6198a55a66a66a1ed4aeb48093334518e9b3c15fc87dc992aa1682aa16c`，METADATA实际包含两个安全下限。新wheel独立安装正在验收，尚无本批跨平台整仓或冻结结果。

随后已把固定pip接入正式入口：`installer`工具依赖组锁定pip26.2.1，由uv导出`requirements-installer.txt`；CI/发布前测试用独立步骤先安装该wheel，防止Windows中后续成功命令掩盖引导失败。独立wheel环境、签名前环境及`install_release_build.py`也先安装此锁。所有入口保留完整哈希、仅wheel、禁止解析依赖和版本自检；最初ensurepip仅用于安装这一已审核工具制品，不能宣称完全不使用解释器捆绑pip。

相关Windows28项中25通过、3项POSIX专项跳过（最终r3，3.06秒），Linux及macOS各28项全部通过，包含新工具锁与uv闭包一致、引导/后端哈希失败即停止、工作流次序和签名秘密边界。修改时发现的YAML标量解析失败已修复，原r2失败XML保留。实际Windows全新环境使用生产构建安装器成功：先安装pip26.2.1，再锁定后端及允许的Nuitka源码包，pip check通过，实际版本逐项核对；不是已经编译冻结程序。证据为`cache/dependency-security-20261016-r1/windows-build-installer-r1/audit.json`。

包含引导流程的584文件快照为`aa67ed29fd5b480c96b78210874e8503cccba525775dc8a6432c65d405221a6a`；Linux/macOS已各启动全新哈希环境的133项组合回归，尚未终态。macOS继续使用显式私有Intel加密库制品锁。583快照新wheel的Windows独立113项CLI及两项实际5秒期限通过，完整收集审计为该快照`windows-install-r1/audit-r1/audit.json`。远端workflow、签名实际执行、新依赖三端完整回归和新冻结构建仍待验收。
