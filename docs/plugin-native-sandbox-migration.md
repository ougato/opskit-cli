> 当前版本已升级 SDK 3 并完成业务接口分离；当前接口和迁移步骤见 [SDK 3 迁移](sdk3-business-separation.md)。下文保留历史阶段记录，其中旧 SDK、菜单和业务命令不作为当前操作说明。

# 插件原生沙盒迁移

本文记录当前实施状态，不将原型测试解释为完整插件迁移完成。

最终要求与证据逐项核对见 [交付验收清单](native-sandbox-acceptance.md)。

## 当前验收摘要（2026-11-03，优先于下方历史记录）

本期 Windows 原生沙盒改造已完成本地交付验证：当前源码全量 4463 passed、129 skipped，
源码未变；97c666 同包 Creator/上传/恢复中英文与原生、生命周期、权限及 CLI 验证汇总
见 `native-sandbox-acceptance.md`。14 份当前包报告已核对完整 SHA256。
Linux/macOS 运行后端按约定留待后续；磁盘硬配额与真实 S3/生产联调未实施，边界继续保留。
以下全部过程摘要保留历史原貌，不覆盖上述最终状态；本次未提交或推送 Git。

最新源码快照全量回归已完成：4463 passed、129 skipped、39 warnings，2281.32 秒；
`cache/native-snapshot-regression-20261103-ssh-url.result.json` 确认源码未变、无变化路径。
当前 Windows 包 SHA256 `97c6665bcbb39f85519088e6893a8f0ddcc2eb6182b2b742a5786825062fd47e`；
SSH 中英文、HTTPS 中文、英文 native、32 项 CLI 及中英文永久授权真实 TTY 已通过。
同包 Creator、上传和恢复中英文验收正在运行，结果见当前验收清单。
以下“当前/运行中”摘要按历史记录阅读，最终状态以 `native-sandbox-acceptance.md` 为准。

最新补验以 `native-sandbox-acceptance.md` 顶部为准：b0cb44 冻结包中英文 Creator、
中英文上传/恢复、TTY Ctrl+C 和英文原生/本地 Git 生命周期均已有报告。
快照回归 4462 passed、129 skipped，但运行中源码有变，仍须按变更补验。
HTTPS 冻结生命周期首轮正在本机合成服务上运行；整体尚未最终验收。
下方构建号与“运行中”条目保留为历史记录，不代表当前最新构建。

Creator 新产物检查已落地：每次显式输出到独立的 `build/opskit-平台-随机ID`，
候选成功后检查新产物再返回 artifact.directory/files/bytes；不再用旧输出证明成功。
授权增加输出策略，原工具授权须重新确认。中英文错误与规范同步；相关专项 65 通过，
补充根目录替换后产物模块 9 通过。中文真实 SDK 官方模板构建已验证 69 文件、
15601168 字节及状态提交。新冻结包 SHA256
`e384bb6958a7e8a936ddc5f504de19dca6453d19fbda51ccd314b4af6b798fd7`
的中文 Creator 和英文 native 验收正在运行。产物上限是完成后检查，不是磁盘硬配额。

最新更新：任务控制构建 SHA256
`61e03d2f946c743c2f1657fa3f9cdbee0d4e0126bfe648004e2041ca77f5936b`
的 native/upload 中英文和 32 项 CLI 均已通过，中文真实 Creator 构建亦通过。
对应报告见 `docs/native-sandbox-acceptance.md`。英文 Creator 同版复验运行中。
本次发现的 Creator 参数、路径、程序变化和日志上限硬编码错误已补齐中英文，
相关专项 51 passed；这些后续修改尚未进入上述冻结包，新构建正在制作。
第二轮全量回归已完成：4419 passed、129 skipped、2390.52 秒；
日志为 `cache/native-regression-20261103-console.txt`。该轮不能覆盖采集后新增的测试
或运行期间发生的源码修改，尚不是最终源码快照验收。
以下旧摘要中“运行中”是历史状态，以本段和逐项验收清单为准。

- 完整回归终态：4412 passed、129 skipped、2 failed；证据
  `cache/native-regression-20261103-after-upload.txt`。两个失败均为本机 TLS 上传测试，
  单独复验上传模块 14 项通过，尚不宣称完整回归绿色。
  Windows 接收器已改独占绑定，避免并行验收复用 443 端口导致连接到错误证书端点。
- 冻结产物 `7269de78fdbd6dcc79d09f54c749d93ebbfbf8e45977a40593a311a3eb233826`
  已通过中英文真实原生 prepare/run/rollback/revoke 和 32 项 CLI 检查。
  英文 Creator 构建、非交互未授权拒绝、能力撤权后拒绝均通过：
  `cache/frozen-creator-20261103-en2/verification.json`。
  英文冻结上传完整字节/MD5/SHA256及撤权验证通过：
  `cache/frozen-upload-20261103-en2/verification.json`，2,640,000 字节。
- 后续修复 Windows NUL 字符设备误判终端：能力授权额外查询 GetConsoleMode，
  无控制台立即拒绝；原生运行错误进入统一 CLI 错误处理，避免预期拒绝打印冻结 traceback。
  权限与多语言专项 21 项通过，新构建位于
  `cache/native-pyinstaller-build-20261103-console`，中文工具链验收运行中。
- 新版中文 Creator 验收已通过：
  `cache/frozen-creator-20261103-console-zh/verification.json`。
  同版中英文冻结 SDK 上传均通过，报告分别为
  `cache/frozen-upload-20261103-console-zh/verification.json` 和
  `cache/frozen-upload-20261103-console-en/verification.json`。
  这些自动化测试使用精确的测试目录持久授权，不证明人工选择授权界面。
  新版英文 Creator 与原生回滚复验在运行，全量回归日志为
  `cache/native-regression-20261103-console.txt`。
- [旧插件升级指南](plugin-native-upgrade-guide.md) 已补齐双语操作步骤、SDK 替换、
  Creator 别名配置、信任与能力授权的区别、上传及状态边界。
  产物摘要接口已接入（见下），不能要求隔离插件任意读取大宿主文件。
- Docker 源码审计仅剩独立镜像配置类别和 SDK 软件配方文档示例；
  插件架构专项 14 项通过，确认旧容器模块不可加载、宿主权限实现不进入 guest bundle，
  独立 Docker 软件管理仍可导入。
- 未完成：新版冻结双语交互完整验收、磁盘硬预算、准备恢复的最终冻结验收、
  取消界面与恶意负载矩阵、最终 SDK 迁移交付说明及需求逐项审计。
  真实 S3/生产发布没有凭据与验收证据；本机合成 TLS 不等于真实 S3。
  Linux/macOS 仅预留后端契约，未实现时拒绝执行，不回退无隔离宿主运行。

### 准备阶段恢复更新

运行时 prepare 和 Git 工具 prepare 现使用 `native-preparations/objects` 下的
身份凭据目录，复用 catalog 所有权与活动租约机制；不再新增无凭据 staging 临时目录。
正常退出及准备失败走有界监督清理，监督进程崩溃则保留凭据，操作者可使用
插件管理 → 清理闲置原生缓存 → 中断的运行时或工具准备，或：

```text
opskit plugin inspect-native-cache prepare
opskit plugin clean-native-cache prepare OBJECT_ID --record-sha256 FULL_RECORD_SHA256
```

只有目录和父目录身份一致、摘要匹配且没有活动租约时才清理。
创建目录到写凭据之间若崩溃，目录保持未知，不根据名称接管。
旧版本遗留的无凭据 `native-runtime-staging` / fetch `staging` 不自动删除。
最多保留 8 个准备对象（未知对象也计数）；清理上限为 8GiB/262144 条目，
用于容纳多份解释器/Git/候选快照，不是磁盘硬配额或空间预留。
真实 os._exit、错误摘要、替换目录、活动租约和容量拒绝等准备/清理专项 19 项通过；
运行时、Git 工具、双语菜单和帮助接入专项 33 项通过。
后续仍需新版冻结 prepare 验证、恶意容量矩阵与最终全量回归。

新增准备恢复的冻结产物 SHA256 为
`e4d960a272789eff7499d2f9b2b9302fa78f8e1910d883c8622a6ba6e7282b5d`，
已通过中英文正式 prepare/run/rollback/revoke：
`cache/frozen-native-20261103-preparation-zh/verification.json`、
`cache/frozen-native-20261103-preparation-en/verification.json`。
英文报告另核对 prepare 后 `inspect-native-cache prepare` 为空。
8300 普通文件专项首轮超出 60 秒清理期限，证据保留在
`cache/native-preparation-final-20261103.txt`（19 passed、1 failed）；
准备清理及手动 prepare collection 默认独立期限已调整为 180 秒，普通 runtime/fetch
collection 仍为 30 秒。超时保留凭据，后续可重试；复验日志
`cache/native-preparation-large-20261103.txt`。该期限调整尚未重建冻结产物。

8300 文件复验已通过，92.26 秒完整清理并移除凭据；180 秒仍为独立、有界的
准备清理期限，不改变普通缓存清理期限或执行授权。

Runtime and fetch preparation now use receipt-backed preparation objects with active
leases. Inspect `prepare` using the native cache CLI and confirm the exact receipt digest
before collection. Active or identity-changed objects are refused; unrecorded directories,
including legacy staging directories, are never adopted. Cleanup limits are not disk quotas.

## 最近回归与待办

### 统一任务取消（2026-11-03）

最新版冻结程序 SHA256
`61e03d2f946c743c2f1657fa3f9cdbee0d4e0126bfe648004e2041ca77f5936b`
已通过中文 native 准备/清理/运行/回滚/撤权和英文 artifact/upload/撤权。
报告在 `cache/frozen-native-20261103-task-control-zh` 与
`cache/frozen-upload-20261103-task-control-en` 的 verification.json。
英文真实 SDK 取消回滚也已通过：`cache/native-task-cancel-20261103-en/verification.json`。
这些自动化取消不证明物理键盘/人工界面验收完成；最新版 CLI/中文上传复验中。

Creator、产物摘要和上传在真实交互控制台显示当前语言的 Esc/Ctrl+C 取消提示。
Windows checkpoint 非阻塞读取按键，每轮最多处理 16 个事件，扩展方向键不会误当取消；
非交互模式和 NUL 输入不读取按键、不显示无效操作提示。
取消从宿主抛出 KeyboardInterrupt，直接退出调用并回收进程，不能作为可捕获的
普通 RPC 错误交给插件继续提交状态。已产生的宿主构建文件和已传输字节不自动撤回，
取消提示明确说明这一区别。
控制按键、双语反馈、非交互、输入洪泛、Creator 回收/失败摘要、产物和语言专项 55 项通过。
真实 SDK 取消后状态回滚验收运行中：`cache/native-task-cancel-20261103-zh`。
该脚本注入宿主取消 checkpoint，不等于物理键盘或冻结界面人工操作已验收。

Host tasks show localized cancellation hints on interactive consoles. Windows Esc and
Ctrl+C abort the invocation and prevent plugin state commit; existing host output or
transmitted bytes are not rolled back. Noninteractive input never polls the console.
The real SDK cancellation verifier injects a checkpoint interruption, not physical keyboard input.

中文真实 SDK 取消验收已通过，报告
`cache/native-task-cancel-20261103-zh/verification.json`：第一次成功上传提交状态；
第二次 worker 先写入临时状态，再由宿主 checkpoint 取消，已提交状态保持原值，
TLS 接收器没有收到第二次上传。英文同流程复验中。
摘要/上传的上一冻结产物英文复验也已通过：
`cache/frozen-artifact-upload-20261103-en/verification.json`。
加入任务控制后的新冻结包正在构建，不能用上一产物证明新增取消界面已交付。

### 产物摘要接口（2026-11-03）

新增 SDK `artifact_info(directory, relative)`，由宿主监督进程在精确目录读取授权下
计算普通文件大小和 SHA256，最多 512MiB，不把文件内容送入插件或 RPC。
与 upload 共用路径校验、文件句柄身份检查及大小上限；拒绝链接/硬链接/重解析路径、
读取期间变化。静默 worker 仍由父进程检查执行期限及授权，撤权后回收，结果不返回。
同次调用取摘要和上传复用一次目录权限，上传快照重新校验摘要，变化则拒绝。

产物/上传/句柄访问/架构专项 44 项通过；摘要与宿主权限排除复验 24 项通过。
真实中文 AppContainer SDK 取摘要→上传→接收端字节/MD5/SHA256 校验→状态提交通过，
只询问一次目录授权，报告 `cache/native-artifact-upload-20261103-zh/verification.json`。
冻结包 `cache/native-pyinstaller-build-20261103-artifact` 已构建，中文完整流程验收中；
该包之后源代码仅又合并了摘要/上传共用的 512MiB 常量，最终发行包仍需重建。
SDK 文件变更后，现有插件运行时必须重新 prepare，不能沿用旧 SDK 摘要。

The host computes artifact size/SHA256 under the same directory grant as upload.
No contents are returned. Upload rechecks the digest against its snapshot. The real
Chinese AppContainer flow passed with one consent prompt; frozen verification is pending.

中文冻结完整摘要/上传验收随后通过：
`cache/frozen-artifact-upload-20261103-zh/verification.json`，产物 SHA256
`63e8ab69ec35ff4d63b03f912dcbb3490016e9c92164c6b6ca5e707e9284cefb`。
验证取摘要、完整接收字节、未授权拒绝、能力撤权后拒绝及语言状态提交；
英文同产物复验已启动，仍不证明真实 S3 或人工授权界面。

- 新增更新中断记录与回滚入口：正式更新在撤权和两次目录移动前写入旧版本/候选/父目录/工作目录身份及内容摘要，保留原安装来源、版本、分支和提交基线。`plugin inspect-updates` 只读检查，`plugin recover-update ID --record-sha256 SHA256` 需精确记录确认；“插件管理 → 恢复中断更新”提供同一流程的中英文详情和确认。恢复排除活动调用/安装发布，拒绝替换后的工作目录、变更备份及独立写入者，不重授执行或服务权限，保留候选和记录供检查。
- 真实子进程在旧版本移走、新版本发布及新安装凭据写入后分别 `os._exit`，重启后均可恢复旧内容和旧安装基线；元数据/目录/摘要变更拒绝、重复恢复与双语菜单/帮助也有回归。阶段专项 47 通过，最终增加记录复核窗口后复验中；没有扩展为未知目录清理，也不宣称断电持久性或 AppContainer profile 崩溃恢复已完成。
- 更新恢复最终专项与双语失败提示 23 项通过；架构、安装发布、恢复菜单和扩展到 24 项的冻结命令门禁共 64 项通过。真实原生采集的正常更新/撤权、降级确认和指定分支更新另 3 项通过，保留成功更新与信任失效语义。冻结命令门禁是脚本测试，仍不代表已生成可用冻结程序。

- 第二轮广泛回归为 4160 通过、118 跳过、19 失败；包含开跑后已修复的旧容器测试，以及缺少符号链接权限和 pip 的环境依赖。锁文件集成测试现改验正式原生离线 wheel 安装，不再以宿主 pip 为证据；专项 66 通过、6 跳过。双语测试隔离前序 JSON 输出上下文后 9 项通过，尚未再次全量通过。
- 宿主 Creator 进程树增加 Job 主动终止与活动数归零确认；SDK 进程监督同步携带该纯清理模块。真实已退出父进程/存活后代测试通过。Creator 支持实际安装的 CocosCreator.exe；真实启动发现缺少 PATH 导致 Node fork ENOENT，改为仅获准工具目录和 Windows 系统目录的固定 PATH，并提供系统 ComSpec，仍不继承用户 PATH 或凭据环境。相关专项 19 通过、3 跳过；真实模板构建复验进行中。
- 旧 Docker 资源一次性人工清理说明见 [退役资源清理](plugin-docker-retirement.md)，同时提供中英文；仅逐项确认完整 ID 与旧记录，不自动删除未知资源或工作目录，保留独立 Docker 软件管理。
- 正式 SDK 原生运行/状态、语言、执行期限、源码清单和发行包命令门禁复验 51 项通过。宿主进程监督补充“父进程已退出但后代持有 stdout”窗口：有界等待期间回收整棵树，再读至 EOF，避免 Creator 已结束仍等满任务期限；相关专项 40 通过、3 跳过。
- 真实 Creator 3.8.7 模板工程产出 `build/web-mobile/index.html`，退出码 36；核对官方命令行文档确认 36 是成功、32/34 是失败。宿主工具返回新增 `success`，保留原始 `returncode`；中英文开始/完成/失败/中断摘要及 0/36 成功分类共 26 项通过。最终直接 broker 复验进行中；这不证明正式插件 RPC 中 Creator 任务、完整产物校验或上传链路已完成。
- 实际 Nuitka 发行构建已使用独立 Python 3.12 和哈希锁依赖；固定摘要的 Dependency Walker 已准备到私有 cache。构建进入 C 后端后因缺少其支持的 C 编译器失败；保留非交互拒绝未审核下载策略，没有冻结产物通过的结论。日志：`cache/native-release-build-second.txt`。不得用只读 CLI 门禁代替实际冻结程序运行。
- Creator 最终直接 broker 验证通过：`cache/creator-host-acceptance-20261103-fourth/verification.json`，真实退出码 36、success=true、生成网页。随后生成已知插件，使用审核离线运行时走正式 AppContainer → SDK RPC → 本次授权 → 真实 Creator → 状态提交，中文构建摘要、成功返回及持久状态均通过；报告 `cache/creator-sdk-acceptance-20261103-second/verification.json`。只授权验收脚本生成的插件及自有模板工程，未授权外部插件或改动游戏工程。此结果仍不证明冻结 CLI、真实授权弹窗操作、恶意构建脚本约束或 S3。
- 最终源码版本的真实 SDK/清理后提交专项 12 通过；源码库存/架构/通用语言回归 48 通过、2 跳过。完整全量回归尚未重新通过。

- 首轮全量回归在 15 个失败上限处停止：1214 通过、37 跳过。失败包括 8 个 Windows 长路径解压用例、6 个旧安装/更新或 Git 错误映射用例、1 个已删除的容器控制接口用例；不是全量通过。
- 通用 Python/Git 测试输入已抽为共享辅助模块；安装/更新测试改用原生采集及安装收据，不恢复宿主 clone/pull。
- 安装/更新旧测试现已改接真实原生采集，所在模块 43 项通过。发布竞争/取消/回滚改在候选交付边界注入已知测试数据，仍使用真实进程和发布锁验证竞争，27 项通过；生命周期互斥和采集失败保留原内容/信任 6 项通过。hooks 已改接真实原生工具，待本轮结果；不能将候选交付桩当作下载隔离证据。
- 已移除旧容器控制预算用例，保留嵌套调用共享期限、准备过期不启动及状态锁等待预算；新增原生静默进程不能延长期限的实际管道验证，专项 18 项通过。
- 容器控制测试已移除，其标准输入关闭、取消、超时、输出限额及子进程回收由通用进程捕获专项承接，6 项通过。prepare 参数帮助已补齐中英文，语言与生命周期专项 28 项通过；废弃容器命令语言键移除。
- Windows tar 解压改用扩展文件路径执行磁盘写入，不要求用户修改长路径注册表；测试用同样支持长路径的方式独立读取实际产物。原先 8 个长路径失败及归档前缀、内部链接、布局限额回归共 104 通过、2 跳过。
- 后续仍需原生崩溃恢复、资源硬预算、Creator 与上传工具链、取消界面、冻结发行包及完整需求验收；下文完成门槛不变。
- hooks 真实原生采集 4 项通过。新增公共 `NativeLimits`，Windows 在恢复挂起 worker 前设置内存、进程数和 CPU 速率硬上限，默认 256MiB/16 进程/整机 CPU 25%。真实内核查询确认限额已设置，注入设置失败时验证插件代码未执行；原生边界专项 34 项通过。该证据不覆盖磁盘硬配额，也不代替完整恶意负载及发行包验收。
- 原生退出新增 Job 主动终止与活动进程数归零确认，确认失败继续释放 Job/进程/线程句柄并向上报错，不把关闭 Job 句柄等同于进程树已退出。实际 AppContainer 后代进程回收及注入确认失败专项纳入原生边界，36 项通过；正式状态提交链另行复验中。

## 当前正式入口（2026-11-05）

- `plugin prepare` 已使用原生运行时和离线 wheel。
- `plugin prepare-fetch --python-runtime <独立Python目录>` 只准备隔离 Python，不再接受 `--git-runtime` 或 `--packages`，不下载 Git、不构建 Docker 镜像。对象读取与 pack 索引使用随 OpsKit 打包的 Dulwich 纯 Python 源码，在无网络 AppContainer 内完成。准备先复制 Python，再读取平台生成的已知 Git 对象，探测通过后才原子发布工具指针。自动安装优先从当前解释器、PATH 和注册表发现本机 Python，复制必要运行文件并排除第三方包；有效工具缓存直接复用。准备不授信插件。
- 工具 catalog 绑定完整文件 SHA256、平台和采集代码版本；源工具变化不影响已发布快照，执行快照仍使用准备时的文件身份，损坏或过期则拒绝。准备失败保留旧指针。
- 正式 `plugin install`、`plugin update` 已移除宿主 Git clone/pull 调用。当前支持本地 Git 仓库已提交内容：AppContainer 采集 → 独立归档校验 → 私有工作目录写入 → 清单/入口/校验和复核 → 原子发布；不导入插件模块，不发布 `.git`、未提交或未跟踪内容。
- 安装在宿主信任记录保存 `installation_receipt` 与 `source_commit`，不授予执行权限；重新确认信任保留原安装凭据，不把本地修改变成可覆盖的基线。
- 更新拒绝本地修改和缺少原生安装凭据的旧安装。请先备份再重新安装；不静默删除开发者文件。更新发布前重新核对源配置、安装凭据与内容，先撤销旧信任；发布失败恢复旧树。独立写入者不被覆盖，不能恢复的旧备份留在 `.opskit-update-*` 中供恢复。崩溃恢复操作入口仍待完成。
- HTTPS 原生采集已接入正式安装、更新入口：宿主监督进程只传输有界 Git smart protocol v0 pack 字节，Git 对象解析及解压在无网络 AppContainer 内完成。验证精确仓库、DNS 全部地址、数字 IP 连接及 TLS；不读取环境代理、Git 配置或凭据助手，不跟随重定向。`--fetch-profile` 显式读取绑定完整仓库地址的私有凭据文件；`--allow-private` 仅本次允许该仓库目标的私网地址，更新需重新指定。凭据及私网选择不写入插件信任记录。
- SSH 原生采集已接入同一正式入口：使用 Paramiko，先核对主机密钥再发送身份认证，只执行固定且路径经 POSIX 引号处理的 `git-upload-pack`。显式凭据文件绑定完整来源并可提供加密私钥口令；未指定文件时，只读取本机 known_hosts 的精确主机条目、SSH agent 及标准身份文件，不调用本机 SSH 程序或执行 SSH 配置命令。DNS、私网、下载限额、取消与后续 AppContainer 对象解析沿用同一监督边界。
- 已删除旧 Docker 采集入口、镜像构建器、包锁、Linux guest 及容器 HTTPS/SSH 配置助手，对应容器专用测试移除；路径/条目/归档保护抽为 `plugin_git_archive.py` 并保留 40 项校验测试，正式采集仅包含固定原生 worker 与纯归档模块。旧 gateway、容器资源账本和监听式出口代理也已移除；安装发布事务需要的通用工作区身份记录继续保留。全量测试及构建、文档引用审计未完成，不能宣称插件系统已完全清理 Docker。
- 新增帮助、工具目录说明、准备失败、缺少工具、远程待迁移、更新保护提示均有 zh/en 文案。CLI 仅公开固定平台错误，不公开工具日志、仓库凭据或底层路径异常。
- 多语言补查：插件生命周期命令中原先写死英文的状态/锁/工作目录检查帮助、校验与授信错误、移除确认等已接入 zh/en。新增两种语言的实际 CLI 帮助和失败提示测试；本轮语言、权限菜单及生命周期 CLI 回归共 31 项通过。此结果不代表原生沙盒整体迁移完成。

真实 Windows CLI 已验证准备、安装、指纹核对、授信、启停、无变化更新、新版本更新后撤权、本地修改保留、撤权和移除；运行中断言 Docker 入口不可调用，合成插件入口含主动异常以证明安装审核没有导入插件。另以合成 TLS Git 服务验证 HTTPS 私有凭据读取、安装、版本更新，以及更新不复用私网授权；合成 SSH 服务验证固定主机密钥、加密 Ed25519 私钥、错误主机/口令拒绝、隔离导出、正式安装/版本更新及更新不复用凭据。没有调用真实用户仓库或上传用户数据。此验证不证明冻结二进制或 Creator/S3。

本轮远程协议及 HTTPS 边界测试 36 项通过，涵盖畸形帧、包大小、精确 ref、重定向/错误类型/编码拒绝、数字地址与 SNI、取消静默下载进程、固定安全错误分类等；真实 HTTPS CLI 完整用例在错误分类完善后再次通过，SHA1/SHA256 pack 均已真实隔离解包导出。下载 pack 上限 64MiB，沙盒工作树扫描限 256MiB/16384 条目；扫描配额不是硬盘硬配额，仍需补足平台预算验收。

HTTPS 使用示例（凭据文件必须是当前用户私有的普通文件，拒绝链接及共享权限，不自动修复权限）：

```text
opskit plugin install https://git.example.com/team/tool.git --branch main
opskit plugin install https://git.example.com/team/private-tool.git --fetch-profile C:\private\repository.json
opskit plugin update private_tool --fetch-profile C:\private\repository.json
```

凭据 JSON 结构如下，`source` 必须绑定完整仓库；令牌不放在 URL、命令参数、插件配置或环境变量中。私有 CA 可在 `authentication` 中另加 `ca_pem`，不提供跳过证书验证选项。

```json
{
  "schema_version": 1,
  "source": "https://git.example.com/team/private-tool.git",
  "authentication": {"kind": "bearer", "token": "REPLACE_IN_PRIVATE_FILE"}
}
```

匿名仓库无需凭据文件；需要自定义 CA 时可使用 `kind: anonymous` 加 `ca_pem`。内网仓库每次命令另加 `--allow-private`，只对本次精确目标生效。平台错误区分凭据输入、私网拒绝、TLS、协议/限额和网络传输，并使用程序当前中英文设置；原始服务器响应和凭据不进入错误文本。

SSH 同样使用 `--fetch-profile`，凭据对象的 `authentication` 为 `kind: ssh_key`、`private_key`（PEM/OpenSSH 私钥内容）、`host_keys`（已向管理员核实的 `算法 base64` 公钥数组）和可选 `passphrase`。不支持密码、键盘交互或自动接受首次主机密钥。支持 `ssh://git@host:port/team/repo.git` 与 `git@host:team/repo.git`；相对与绝对仓库路径分别绑定，不互相替换。SSH 依赖已加入 pyproject/uv.lock，并重新导出运行时、CI 与构建哈希清单。

交互菜单的安装、单个更新与批量更新均新增中英文“仓库访问方式”：展示本次完整来源，选择公网/允许内网及是否使用私有凭据文件；SSH 仅提供有凭据的方式。本地仓库不新增网络提示，取消不执行采集；批量更新逐仓库重新选择，不把一个仓库的私网许可或凭据复用给下一个。

本阶段协议/传输策略、双语、依赖导出闭包、归档/源码清单/原生工具 catalog 共 130 通过、2 跳过；正式生命周期及纯归档另一组 74 通过。归档抽离后，SHA1/SHA256 两个真实隔离 pack 用例及 SSH 完整 CLI 安装/更新再次通过。此处仍不是全量回归结论。

本阶段 catalog/原生入口/信任凭据/中英文专项 52 项通过，候选审核及更新事务另一组 35 通过、1 跳过；旧插件模块中与安装更新无关的回归 30 项通过。正式 CLI 生命周期在最后一次安全修正后再次真实通过。旧 `test_plugin.py` 的 clone/pull 与 stderr 映射测试，以及 `test_plugin_install_publication.py`、`test_plugin_git_hooks.py`、`test_plugin_lifecycle_exclusion.py` 中直接挂钩 `commands.run` 或依赖安装后 `.git` 的测试仍需迁移到新边界；不保留宿主执行回退来满足旧测试。当前没有全量测试通过的结论。

## 目标

- 插件系统最终完全移除 Docker/WSL 运行依赖；保留独立的 Docker 软件管理功能。
- 共用 SDK、权限申请、指纹绑定、RPC、任务与状态事务；隔离机制由平台后端实现。
- 首期 Windows 使用 AppContainer 加 Job Object；Linux/macOS 后端待实现，禁止无隔离回退。
- 用户权限选择为本次授权、永久授权或返回；返回取消当前请求，不保存拒绝记录。永久授权绑定插件内容身份，可撤销。
- 新增菜单、权限弹窗、CLI 帮助和错误提示沿用现有 zh/en 语言机制；契约标识/JSON 字段保持稳定，不随显示语言变化。新增文案必须同时补齐两份语言目录与参数一致性测试。
- 多语言是本次迁移的必验项：菜单、授权、进度、成功/失败提示、帮助及平台未实现提示均遵循程序当前语言；用户可见文案不得散落硬编码。中英文必须同时交付，语言键与插值参数一致；机器错误码、配置键、命令和协议字段保持稳定。第三方工具原始输出作为诊断保留，由本地化摘要说明结果，不承诺翻译原始日志。Windows 与后续 Linux/macOS 后端共用此规则。
- Creator 等宿主工具通过受限操作代理调用，授权绑定工具、项目与输出范围。明确提示构建脚本在宿主执行。

## 前期阶段记录（当前入口以上节为准）

- 已新增平台选择接口、三种授权选择和宿主能力请求类型；授权存储已实现精确范围、指纹绑定、一次/永久授权、撤销及提示期间撤销保护，尚未接入正式插件执行入口。
- 已增加 `plugin permissions list` 和 `plugin permissions revoke ID` 官方命令；单次授权随调用对象结束释放，永久授权进入私有有界存储。非交互无授权明确拒绝。
- “插件管理 → 插件权限”已接入同一授权存储，可查看范围、指纹及完整 ID，并确认撤销；空列表、成功及损坏存储提示支持中英文。列表不执行插件，损坏记录保留原证据。
- Windows 已实现真实 AppContainer profile 生命周期、无网络 capability 的探测进程启动、执行前 token 验证与 Job 回收。
- 已实现专属 staging 树的 AppContainer SID ACL，保留并恢复原 DACL；真实进程验证只读可读但不可写、可写目录可输出。只适用于平台独占的临时树，不直接修改用户工程或共享 Python 安装。
- 专属最小 Python 运行时已在真实 AppContainer 中启动；标准流通过进程属性白名单传递，未开放其他宿主句柄。这仍是探测路径，尚未接入正式插件 RPC。
- 已将原生启动器抽为上下文接口，加入 256MiB Job 总内存和 16 个进程限制；退出上下文回收进程树。Linux/macOS 共享同一后端接口，不提供无隔离回退。
- 已加入有界双向通信与消息循环，覆盖真实 AppContainer RPC 往返、非法消息、终结后调用拒绝、输入等待期间退出回收。插件 worker 路径可由宿主提供，状态路径在线路上仍使用 /data 相对约定。
- 专项测试覆盖原生隔离、通信、中英文一致性及原 worker 请求/服务契约回归；不代表全量回归、宿主工具任务或冻结包完成。
- 正式插件入口已切换到原生 catalog 与 AppContainer worker，保留原有内容授权、服务边、期限、快照和状态事务。当前环境不继承凭据；完整冻结包和宿主工具仍需验收。
- 已增加原生运行时文件清单和私有快照：绑定全量文件集合及 SHA256，拒绝硬链接/重解析点、超额和变更内容；执行快照再次校验。依赖安装、锁文件及正式目录发布尚待接入，不以复制本机 Python 代替交付方案。
- 已加入离线 wheel 解包模块：核对锁定名称/版本/哈希，拒绝启动 .pth、越界路径、Windows 保留名、成员冲突和超额；宿主不运行包安装脚本。解释器兼容性、传递依赖验证及 prepare CLI 尚待接入，不能将此模块单独视为完成运行时准备。
- wheel 模块已补充目标解释器 tag、Requires-Python、依赖版本与 marker 验证；拒绝 URL/extra 依赖，未知或缺少依赖不能发布。packaging 已列入正式依赖及哈希运行依赖清单；目标解释器身份探测与 prepare CLI 仍待装配。
- 已实现运行时候选装配与独立对象 catalog：解释器快照、离线依赖与平台 SDK 合并；清单绑定插件指纹、依赖锁、SDK 和全量文件哈希，完整对象验证后原子切换指针。拒绝依赖覆盖 SDK，执行前校验身份与内容。目标解释器 tag/marker 在 AppContainer 探测；正式 prepare 命令使用原生输入，不再构建 Docker 镜像。准备不等于授信。
- 原 Docker 链路暂未删除：迁移完成后整体移除，不将其保留为最终可选后端。
- 已删除插件运行器废弃的 `_image` 镜像选择和 `_network` 容器网络启动分支，以及旧插件网关验收脚本；旧镜像身份和网络测试随对应入口移除，原生替代验证见 runtime catalog、AppContainer、HTTPS RPC 和清理后状态提交测试。Docker 控制/资源账本与采集网关仍被旧 Git/SSH 采集使用，尚不能删除。
- 原生调用装配已运行真实 SDK worker：校验并复制运行时、插件与状态快照，受限管道完成交互 RPC 和状态归档；中英文插件注册文案及宿主语言继承已在真实 AppContainer 验证。返回的状态由原有授权与事务层验证提交。

原生准备命令：`opskit plugin prepare <插件目录> --python-runtime <独立Python目录> --wheels <离线wheel目录> --lock-file <哈希依赖锁>`。依赖锁需覆盖 SDK 依赖及 Windows 子进程监督所需的 psutil；不再接受 `--base-image`。Windows 解释器目录须含 python.exe、运行 DLL 与标准库，目标兼容性由隔离探测决定，不能按宿主 Python 版本猜测。

已通过正式 `plugin_sandbox.invoke` 的真实 Windows 集成测试：已授信插件从原生 catalog 启动，worker 输出通过原有状态事务写入持久目录，过程中调用 Docker 会令测试失败。另验证清理失败、执行失败、撤权和期限届满均不提交状态。Windows staging 使用扩展路径，避免依赖用户机器的长路径注册表设置。尚未完成全量旧 Docker 测试替换和完整发行包验收。

`scripts/verify_plugin_native.py` 已替换原先名为 native 实际使用 Docker 的旧实现：接收 `--output`（必须全新）、`--python-runtime`、`--wheels`、`--lock-file`、`--language zh|en`，生成已知合成插件，走正式 prepare/invoke 和状态提交，检查宿主秘密不可读、插件源不可写，输出 verification.json。2026-11-03 已使用独立 Python 与 13 个真实离线 wheel 分别通过 zh/en 验收，均确认原生隔离与状态提交；报告分别位于 `cache/native-offline-acceptance-20261103/run-zh2` 和 `run-en`。准备过程中修复了误拒绝未启用开发 extra 依赖的问题，仍拒绝活动的 URL/extra 依赖；相关依赖与多语言专项共 29 项通过。此脚本明确不证明冻结 CLI、真实 Creator 或完整发布链路。

## 后续实施及删除清单

本地 Git 采集使用 `core/native_fetch.py` 与固定 `native_fetch_worker.py`：Python、Git 工具和含 `.git` 的源仓库先复制为已校验私有快照，随后在无网络 AppContainer 中只读取提交对象，不 checkout、不执行插件、不调用宿主 Git。默认 HEAD 和显式分支已真实验证；未提交及未跟踪文件不导入，源仓库不修改，硬链接输入拒绝。输出在进程树回收后交给现有独立候选校验器。二进制管道按期限/撤权/输出上限监督。现已由原生工具 catalog 及正式安装/更新入口消费，远程采集仍未完成。此前底层相关专项 92 通过、1 跳过；冻结包及远程网络尚未验收。

随后补充恶意仓库配置/checkout hook 与无效分支拒绝测试；禁止 Git fsmonitor、自动维护与自动 gc。锁定依赖通过 `uv sync --locked --extra dev --no-install-project` 安装到项目独立 `.venv`，不修改系统 Python。该环境原生采集、管道、候选规则及多语言专项 53 项通过，最后增加的分支/恶意配置专项另验；完整测试收集曾通过 4682 项（之后新增 5 个分支用例），仅代表收集成功，不是完整测试通过。

宿主文件读取已接入 SDK `read_host_file(directory, relative) -> bytes` 和正式 broker：宿主身份绑定、目录规范检查、显式授权、前后撤权检查、单文件 1MiB 上限；拒绝链接、硬链接、越界及 ADS 路径。单次授权对象由调用入口创建并在退出时关闭。目录写入仍待实现；Creator 初步接入情况见下段，整体工具链尚未验收完成。

Creator 固定操作已新增 SDK `creator_build(executable, project, options)` 与正式 RPC：当前 Windows Creator.exe，选项仅 platform/debug，禁止任意 shell 和任意参数；授权绑定可执行文件 SHA256、项目、平台及 debug。宿主环境使用白名单，日志有界，静默期间仍检查授权和期限，撤权或取消异常回收 Job 进程树。中英文授权明确说明：宿主工具及项目脚本以用户权限运行，不构成目录隔离。真实 Creator、产物配额、任务取消 UI 与平台 SDK 版本文档仍需完成验收；单元测试不代表真实构建成功。

原生网络新增 SDK `https_request(url, method, headers, body)` 与正式 RPC：要求清单 network 权限，HTTPS/443、受限请求头、32KiB 请求与响应、无重定向和环境代理。宿主监督进程解析并校验全部 DNS 地址，连接已检查数字地址，保留 TLS SNI/证书验证；私网仅现有精确 private_targets 授权允许，执行及状态提交保留撤权检查。该有界 API 尚不能替代大文件/S3 上传与 Git/SSH 采集链路，后者仍需迁移，不能声称 Docker 已完全清理。

1. Windows：专属运行时、文件 ACL、受限继承管道、进程和内存预算、profile 崩溃恢复；真实读写/网络拒绝和进程回收验证。
2. 共用授权：宿主规范化资源、可信 UI、一次/永久授权存储与撤销、非交互拒绝、指纹更新失效；不让插件自行授予权限。
3. 宿主任务：Creator 固定操作、目录范围、环境白名单、输出限额、取消、任务归属；不提供任意 shell。
4. 将通用期限、授权链、RPC、状态事务从 plugin_sandbox 拆出；迁移入口及下载器。
5. 原生受控网络出口：保持网络/私网限制与撤权语义；不可只开放网络 capability 代替目标限制。
6. 替换运行时准备及锁文件校验，迁移 prepare CLI、SDK 版本、源码及冻结包资源。
7. 删除 Docker 专用 runtime/image/gateway/network/resource 代码、镜像加固、旧参数、测试和打包脚本；按引用逐项核对通用机制，不按文件名前缀盲删。
8. 旧容器、网络和镜像提供一次性归属清理说明，不操作未知资源；迁移后移除旧执行入口。

## AppContainer 异常退出恢复 / Crash recovery

插件管理菜单提供“恢复原生沙盒配置”，支持中英文。命令行先运行
`plugin inspect-native-profiles`，核对返回的配置名、事务 ID 和完整摘要，再运行
`plugin recover-native-profile TRANSACTION --record-sha256 SHA256`。
不自动扫描或删除 Windows 配置目录。

The bilingual plugin menu provides native profile recovery. Run
`plugin inspect-native-profiles`, review the profile name, transaction and full digest,
then use `plugin recover-native-profile TRANSACTION --record-sha256 SHA256`.
Windows profile directories are never scanned for automatic deletion.

每次创建先记录意图，并在整个生命周期持有独占锁。只有已确认创建、SID、目录身份及记录摘要均匹配的非活动配置允许恢复。
创建确认丢失、记录变化或归属未知的对象保留人工检查。删除成功后先持久化 `removed` 状态，再移除记录；此状态可重试，仅移除记录，不再删除配置。删除 API 成功到状态持久化之间的窄窗口仍保留人工检查，不能当作完整自动恢复已完成。

Each creation records its intent and holds an exclusive lifetime lease. Recovery requires
a confirmed creation, matching SID, directory identity and journal digest, and an inactive lease.
Unconfirmed or changed resources are retained. Successful deletion is persisted as `removed`
before retiring the journal. Retrying that phase only retires the record, without deleting a profile.
The narrow window between API success and persisting this phase still requires manual inspection;
full automatic recovery is not yet proven.

2026-11-03：`tests/test_windows_profile_recovery.py` 的 8 项实测通过。
其中真实宿主进程启动 AppContainer 中持续运行的 cmd 及其子进程，确认二者仍活动后
调用 `os._exit(72)`；测试通过预先打开的进程句柄确认二者均已终止，然后恢复精确记录的配置。
这证明该场景的 Job 关闭回收，不代表断电、磁盘配额或完整发布验收。

On 2026-11-03, all eight Windows profile recovery tests passed. The abrupt-owner-exit
test launches a live AppContainer worker and descendant, holds their actual process handles,
exits the owner with `os._exit(72)`, confirms both terminate, then recovers the recorded profile.
This verifies Job cleanup in that scenario, not power-loss recovery, disk quotas or release acceptance.

补充：新增正常退出清理、显式恢复清理两条路径在 `removed` 持久化后强制退出的实测。
恢复时确认目录已不存在，并禁止再次触达 Windows 删除接口；相关恢复、中英文菜单、隔离、CLI 及架构专项共 100 项通过。

Additional abrupt-exit tests cover both normal cleanup and explicit recovery after persisting
`removed`. They confirm the directory is absent and that retry never calls the Windows API again.
The related recovery, bilingual menu, isolation, CLI and architecture group passed 100 tests.

## 临时目录崩溃恢复 / Workspace crash recovery

正式调用和 Git 采集的工作目录在分配确认后保存父目录、目录文件身份及私有记录，
整个使用期持有占用锁。清理 worker 另持互斥锁，防止宿主退出时与后续恢复交错删除。
分配确认前或记录持久化前退出的目录不自动接管，按未知归属保留。

Invocation and acquisition workspaces receive a private identity record after allocation
acknowledgement, with a lifetime lease and a separate cleanup-worker lease. Directories left
before acknowledgement or journal persistence remain unknown and are never adopted by name.

菜单“恢复原生沙盒临时目录”及以下 CLI 均支持中英文：
`plugin inspect-native-workspaces` 只读查看记录，核对目录、事务及完整摘要后，
运行 `plugin recover-native-workspace TRANSACTION --record-sha256 SHA256`。
只有记录匹配、无活动占用、父目录与目录身份匹配的目标允许清理；完整树先通过
既有有界检查，链接、硬链接、替换目录、超限内容均拒绝。已清理但记录未删除的情况可重试。

Use the bilingual recovery menu or `plugin inspect-native-workspaces`, review the exact
directory, transaction and digest, then call
`plugin recover-native-workspace TRANSACTION --record-sha256 SHA256`.
Recovery requires matching records and filesystem identities and inactive leases. The existing
bounded tree validator rejects links, hard links, replacements and over-budget content before
deletion. An already-removed tree can complete journal retirement on retry.

此功能不提供目录硬配额，不清理运行时 catalog 对象，也不把目录观察结果当作所有权记录。
This does not provide disk hard quotas, catalog object collection or ownership from directory observations.

2026-11-03 验证：恢复专项 11 项通过（真实 `os._exit`、活动占用、清理 worker 互斥、
摘要/记录/目录/父目录变化、已删除目标重试、未知目录保留）；既有目录与清理提交专项
38 通过、1 跳过；菜单、语言、CLI、冻结门禁定义及架构相关 107 通过、2 跳过。
真实 AppContainer SDK 调用专项 4 项通过，覆盖正式入口状态提交及 zh/en RPC；真实原生 Git 安装/未变化更新/变更更新及旧授权失效用例 1 项通过。
这些结果不代表冻结可执行文件或完整构建上传链路验收。

Validation on 2026-11-03: 11 recovery tests passed, including abrupt exit, active leases,
cleanup-worker contention, changed identities/records, absent-tree retries and unknown-directory retention.
Existing workspace/cleanup checks passed 38 tests with one skip; related bilingual menu, CLI,
gate-definition and architecture checks passed 107 with two skips. Four real AppContainer SDK
tests passed, including public state publication and zh/en RPC. The real native Git install,
unchanged update, changed update and trust-invalidation integration test also passed. This is not frozen executable
or end-to-end build/upload acceptance.

## 运行时对象占用 / Runtime object leases

正式插件调用与 Git 工具采集在 catalog 锁内选择并校验对象，然后取得该对象的独立 reader lease；
退出 catalog 锁后仍持有 lease，直到调用/采集结束。允许同一对象多个 reader 并行；
发布新指针不会解除旧对象占用。崩溃遗留的空 lease 文件只在确认 OS 锁已释放后才能退休，
未知文件、硬链接和非空 lease 阻止后续清理。底层占用机制已接入，清理入口见下节。

Public invocation and Git acquisition validate and pin the selected object under the catalog lock,
then retain a per-reader lease until consumption ends. Multiple readers may run concurrently;
publishing a new pointer never unpins the old object. Abandoned ordinary empty lease files can
only be retired after their OS locks are released. Unknown, linked or nonempty entries prevent
collection. This establishes the reader boundary; the collection entrypoint is described below.

2026-11-03：占用及关联回归 131 通过、1 跳过；采集 catalog 专项 9 通过；
正式 AppContainer 调用并提交状态的真实验收 1 通过。新增占用测试包含真实进程
`os._exit(76)` 释放、双 reader 并发、指针更新后旧版本仍占用及未知 lease 拒绝。

Validation: 131 related tests passed with one skip, nine fetch catalog tests passed,
and the real public AppContainer invocation/state-publication test passed. New lease tests cover
actual abrupt process exit, concurrent readers, old-object pinning after pointer replacement,
and rejection of unknown lease entries.

补充验证：新增跨进程活动 reader 阻止清理、正常退出释放，以及崩溃空锁不会永久耗尽
reader 容量的检查。进入新 reader 时仅回收 OS 锁已释放的普通空锁；活动锁保留，
活动 reader 上限仍执行。占用、运行时与采集 catalog 专项共 28 项通过。

Additional validation covers an active reader in another process preventing collection,
normal exit releasing it, and abandoned empty locks not permanently exhausting reader capacity.
Reader entry retires only ordinary empty leases whose OS locks are released, retains active
leases, and enforces the active-reader limit. All 28 lease/runtime/fetch catalog tests passed.

## Catalog 对象创建凭据 / Object creation receipts

运行时和 Git 工具 catalog 在成功独占创建对象目录后、复制内容前，立即记录对象 UUID、
父目录及对象目录文件身份。即使复制中断，也有独立于目录名的归属证据。
验证会拒绝目录替换、父目录身份变化、无效或硬链接凭据。旧对象没有凭据时不自动接管。
此步骤建立清理凭据；reader 占用和当前指针也必须在清理时核对。

Runtime and Git tool catalogs persist the object UUID and parent/directory filesystem identities
immediately after exclusive directory creation, before copying content. Interrupted copies therefore
retain explicit ownership evidence. Replacements, changed parents, malformed or hard-linked receipts
are rejected. Legacy objects without receipts are never adopted automatically. This establishes
ownership evidence only; collection still requires reader and current-pointer checks, and the
object deletion entrypoint is described below.

2026-11-03：归属凭据、并发占用、运行时/采集 catalog 和架构专项 49 项通过，包含发布复制中断后凭据保留。
Validation: all 49 ownership, reader, runtime/fetch catalog and architecture tests passed,
including receipt retention when publication copying fails.

## 闲置缓存清理 / Unused cache collection

插件管理菜单“清理闲置原生缓存”支持中英文，可选择插件运行时或 Git 采集工具。
先检查 `plugin inspect-native-cache runtime`（或 `fetch`），核对对象及完整凭据摘要，
再运行 `plugin clean-native-cache runtime OBJECT --record-sha256 SHA256`。
这是精确单对象操作，没有自动全量 prune，也不清理未知旧对象或准备临时目录。

The bilingual plugin management menu offers unused native cache collection for plugin runtimes
and Git tools. Inspect with `plugin inspect-native-cache runtime` (or `fetch`), review the exact
object and full receipt digest, then call
`plugin clean-native-cache runtime OBJECT --record-sha256 SHA256`.
Collection targets one explicit object; there is no broad prune or adoption of legacy objects.
Preparation staging directories are outside this collector's scope.

清理在有期限的独立 worker 内执行，该 worker 全程持有 catalog 锁，阻止发布或新增 reader
穿插。任何指针格式异常都会拒绝清理；当前引用、活动 reader、未知 lease、凭据/身份变化
均拒绝。目录内容完整通过有界普通文件检查后才开始删除，拒绝链接和硬链接。
凭据最后移除，部分删除或目录已删除但凭据仍在时可以重试。该机制不提供磁盘硬配额。

An independently supervised worker holds the catalog lock throughout collection, preventing
publication and new readers from interleaving. Malformed pointers, current references, active
readers, unknown leases and changed ownership/identities refuse collection. The entire bounded
ordinary-file tree is validated before deletion; links and hard links are rejected. The receipt
is retired last, allowing retries after partial deletion or an absent tree. This is not a disk hard quota.

2026-11-03：入口、菜单、语言、CLI、门禁定义和架构专项 75 项通过；补充真实删除中途
`os._exit(77)` 的恢复验证后，清理专项 11 项通过。未运行真实发行可执行文件门禁。

Validation: 75 entrypoint/menu/locale/CLI/gate-definition/architecture tests passed. All 11
collection tests passed after adding an actual `os._exit(77)` during deletion and successful retry.
The real frozen executable release gate has not been run successfully.

并发与取消补充：真实清理 worker 暂停在删除前时，运行时和采集 catalog 的 OS 锁均不可获取；
超时或 Ctrl+C 取消后 worker 已回收，旧对象及凭据保留，解除注入后重试成功。
清理专项扩展为 14 项通过；此验证未引入真实用户缓存删除。

Additional concurrency/cancellation checks suspend the actual collector before deletion and
confirm neither runtime nor fetch catalog can acquire its OS lock. On timeout or Ctrl+C the
worker is reaped, ownership and object data remain, and retry succeeds. All 14 collector tests
passed using synthetic test caches, without deleting user cache objects.

## Creator 本机配置 / Local Creator configuration

新增中英文配置菜单及 `creator-tools`、`configure-creator`、`remove-creator` 命令。
数据位于 `config/common.yaml` 的 `host_tools.creators`，SDK 现有 `creator_build`
支持 `@creator/工具名`，仍由 broker 解析，不向 guest 暴露本机配置文件。
配置不授信；执行期间重新解析并核对原选定路径，变更会阻止启动或继续。
具体使用规范见 `plugin-spec.md` 的本机 Creator 配置章节。

Bilingual menu and `creator-tools`, `configure-creator`, `remove-creator` commands now manage
`host_tools.creators` in `config/common.yaml`. The existing SDK call supports `@creator/name`,
resolved only by the host broker. Saving is not authorization. Calls recheck the configured path
and refuse changes. See the local Creator configuration section in `plugin-spec.md`.

验证：配置、菜单、构建 broker、语言及冻结门禁定义 62 项通过；追加授权期间路径改变
不得启动的专项 1 项通过。这些测试使用已知测试文件和注入构建输出，不是新一轮真实 Creator 构建。

Validation: 62 configuration/menu/broker/locale/gate-definition tests passed; an additional
path-change-during-authorization test passed. These use known test files and injected build
output and do not constitute another real Creator build.

## 最新综合回归与发行工具进展（2026-11-03）

完整回归以 4508 项测试启动，日志 `cache/native-regression-20261103-latest.txt`。
已在 10 个失败上限处结束：3972 通过、112 跳过、10 失败，不能宣称全量通过。
10 个失败均为 Windows 符号链接输入创建所需权限不足（WinError 1314）。相关用例已
增加明确的能力前置条件，仅缺少该权限时跳过，不修改生产检查或系统设置；失败模块、
上传与语言复验合计 66 通过、28 跳过。输出长时间停留时，已确认同一进程仍存活，
通过 Python 3.14 的只读堆栈诊断确认它正在真实采集工具准备的文件校验路径，未重启测试。
Creator 配置移除菜单已改为从现有名称选择，中英文菜单专项 8 项通过。

The 4508-test full regression stopped at ten failures: 3972 passed, 112 skipped, ten failed.
The log is `cache/native-regression-20261103-latest.txt`; this is not a passing full suite.
All ten failures were WinError 1314 while creating symbolic-link test inputs. Link-dependent
cases now explicitly require that capability, skipping only when it is unavailable without
changing production checks or OS settings. The affected modules plus upload/language tests
passed 66 tests with 28 skips.
A stack-only diagnostic confirmed the same live process was validating files during actual
fetch-tool preparation when console output paused; the run was not restarted. Creator removal
now selects an existing configured name, with eight bilingual menu tests passing.

为冻结构建新增固定摘要 GCC 提供器及 8 项测试；真实下载因网络失败尚未完成，详见
`release-pipeline-security.md`。冻结可执行文件验收仍未通过。

A pinned-digest GCC provisioner and eight tests were added. Actual downloading remains
incomplete due to network failures; see `release-pipeline-security.md`. Frozen executable
acceptance is still unproven.

## 可重复的命名 Creator 验收

`scripts/verify_creator_native.py` 使用全新的 `--output` 隔离配置、授权和插件状态，
复制 `--template` 指定的官方模板，通过 `@creator/acceptance` 名称走正式 SDK/RPC，
仅对生成的插件、精确工具和模板副本自动授予一次权限。需要提供 `--creator`、
`--offline`（含 python、wheels、requirements.lock）和 `--language zh|en`。
成功条件包括构建成功码、非空 web-mobile/index.html、语言继承和插件状态提交。
脚本不会验证真实用户授权界面、冻结程序或上传链路；应分别使用全新输出目录验证中英文。

`scripts/verify_creator_native.py` isolates configuration, grants and state in a new
`--output` directory. It copies the official `--template`, resolves `@creator/acceptance`
through the real SDK/RPC and approves a single exact generated-plugin/tool/project scope.
Supply `--creator`, `--offline` (python, wheels and requirements.lock) and
`--language zh|en`. Success requires the build success code, a nonempty web-mobile index,
language inheritance and committed plugin state. This does not verify interactive consent,
the frozen executable or uploads. Use a separate new output directory for each language.

2026-11-03 首次命名调用真实进入 Creator，但验收脚本未像正式 main.py 一样设置
UTF-8，重定向输出发生 UnicodeEncodeError，构建被回收。脚本已补齐相同的输出编码
设置，并在失败时只保存异常类型和栈位置（不保存异常值）。英文重跑真实通过，报告为
`cache/creator-named-acceptance-20261103-en3/verification.json`：退出码 36，正式 SDK、
命名配置、一次授权、英文继承、非空页面产物及状态提交均已核对。中文同样真实通过，
报告为 `cache/creator-named-acceptance-20261103-zh/verification.json`，上述条件及中文继承
均已核对。两次运行均正常退出（0）；Creator 子进程原始退出码均为 36。

The first named-call run on 2026-11-03 reached Creator but failed with UnicodeEncodeError
in the verifier's redirected console output. Production main.py already configures UTF-8;
the verifier now matches it and retains only exception types and frame locations on failure.
The English rerun passed: `cache/creator-named-acceptance-20261103-en3/verification.json`
records exit 36, real SDK, named configuration, one-time authorization, inherited English,
a nonempty page artifact and committed state. The Chinese run also passed with inherited
Chinese at `cache/creator-named-acceptance-20261103-zh/verification.json`.
Both verifier processes exited 0; both Creator processes returned success code 36.

源码补查：`core` 与 `plugin` 的 Python 中剩余 Docker 字样仅为镜像设置分类，以及
SDK 文档中的独立软件配方示例；未发现插件 Docker/WSL 执行入口。依赖清单与锁文件
未发现 Docker 依赖字样。这项检索不替代冻结产物检查。固定摘要编译器第三次下载仍以
RemoteProtocolError 结束，尚无可用编译器缓存，不运行不完整下载。

Source audit found only the standalone mirror category and SDK software-recipe example
mentioning Docker in core/plugin Python, with no plugin Docker/WSL execution entry point.
Dependency manifests and the lockfile contain no Docker reference. This is not a frozen
artifact audit. The third pinned-compiler download failed with RemoteProtocolError;
there is no usable compiler cache, and incomplete downloads are not executed.

## 受限文件上传（2026-11-03）

新增 SDK `upload_file(directory, relative, url, sha256, headers=None)` 与宿主 RPC。
network 权限、目录读取授权、运行中撤权/期限复核共同生效；先复制普通文件并核对
预期 SHA256，再对预签名 HTTPS PUT 分块发送，最多 512MiB，不走大 JSON。
自动 Content-Length/Content-MD5，不跟随重定向，不泄露查询串/响应正文；只返回状态、
字节数和本地内容摘要。上传快照持有实际 worker 租约，回收后才报告成功。
工作目录逻辑预留相应调整为每项 512MiB、8 项共 4GiB；仍不提供磁盘硬配额。
双语摘要和真实本地 TLS 接收端逐字节/MD5 核对均通过；上传、目录预算、恢复和
guest 隔离清单专项共 47 通过、1 跳过。实际 S3、冻结程序和目录批量发布仍未验收。
SDK 源码摘要已改变，旧 runtime 须重新 prepare。

SDK `upload_file` and host RPC now combine network permission, explicit directory read
consent and live revocation/deadline checks. A private snapshot is SHA256-verified before
chunked presigned HTTPS PUT, up to 512MiB. Content-Length/Content-MD5 are supplied; redirects,
query logging and response-body disclosure are refused. Results contain status, byte count
and the local content digest. Snapshot workers hold a cleanup lease, and success follows
cleanup. Logical reservation is now 512MiB per workspace / eight workspaces / 4GiB, not a
disk hard quota. Real loopback TLS byte/MD5 verification and bilingual summaries passed;
upload/budget/recovery/guest-boundary checks passed 47 tests with one skip. Actual S3,
frozen binaries and directory publishing remain unverified.
The changed SDK digest requires runtime preparation again.

后续完整原生链路已通过：`scripts/verify_upload_native.py` 分别以 zh/en 生成已知插件，
使用正式离线 prepare、AppContainer、SDK/RPC、一次目录授权和 HTTPS/443 接收端。
两次均完成 2,560,000 字节传输，接收端核对完整内容、MD5 与 SHA256，语言状态提交成功；
报告位于 `cache/native-upload-acceptance-20261103-en/verification.json` 和
`cache/native-upload-acceptance-20261103-zh/verification.json`。脚本需全新 `--output`、
审核离线资源目录 `--offline` 和 `--language zh|en`；只自动授权生成插件的精确目录，
不触达用户项目。该证据是本地合成 TLS 接收端，不证明真实 S3、冻结包或手动授权界面。

Subsequent full native acceptance passed in zh/en with `scripts/verify_upload_native.py`:
generated known plugin → official offline prepare → AppContainer → SDK/RPC → one-time
directory grant → HTTPS/443 receiver. Each transmitted 2,560,000 bytes; the receiver verified
the full content, MD5 and SHA256, and language state committed successfully. Reports are
`cache/native-upload-acceptance-20261103-en/verification.json` and
`cache/native-upload-acceptance-20261103-zh/verification.json`. Supply a new `--output`,
reviewed `--offline` directory and `--language zh|en`. Automatic consent covers only the
generated plugin's exact synthetic directory. This loopback TLS evidence does not establish
actual S3, frozen-binary or manual-consent UI acceptance.

官方编译器 API 下载在 600 秒有界期限处结束，未取得完整归档；不能使用部分下载。
The official compiler API download hit its bounded 600-second deadline without a complete
archive. Partial downloads are not usable.

## 宿主文件读取复核（2026-11-03）

`core/host_file_access.py` 为宿主 broker 提供有界普通文件读取，未进入 guest 源码清单。
Windows 打开后先用 GetFinalPathNameByHandleW 检查真实目标，再读取字节；POSIX 使用
逐级目录句柄与 O_NOFOLLOW。拒绝链接、硬链接、超限、读取期间的身份/大小/时间变化。
目录授权前后同时绑定目录身份，替换目录不读取。相关 15 项通过，包括真实 Windows
junction 在预检与打开之间替换父目录（保持文件 inode 不变）的拒绝用例。这是宿主文件
读取补强，不是大文件上传完成，也不提供磁盘硬配额。Windows 8.3 路径先展开拼写，
不解析 junction 目标；系统临时目录短路径另做 200 次真实读取通过。复验发现本机
CPython 的 fstat/lstat ctime 存在差异，因此 ctime 只在同一句柄 API 前后比较，路径
仍复核 inode、长度、mtime 和链接数，避免普通读取被误判为文件改变。

`core/host_file_access.py` provides bounded ordinary-file reads for the host broker and is
excluded from the guest inventory. Windows verifies the opened handle's final path before
reading; POSIX opens ancestors through directory descriptors with O_NOFOLLOW. Links,
hardlinks, oversize files and changed identities/content metadata are rejected. Directory
identity is checked across authorization. Fifteen related tests passed, including a real
Windows parent-junction swap between inspection and open while retaining the file inode.
This strengthens host reads; it does not complete large uploads or enforce disk hard quotas.
Windows 8.3 spelling is expanded without resolving junction targets; 200 real reads through
the system temporary short path passed. Local CPython fstat/lstat ctime values differed;
ctime is therefore compared within the handle API, while published-path checks still verify
inode, length, mtime and link count. A regression test covers the differing ctime semantics.

## 后端契约待完成

共用层持有身份/期限和权限决策，平台层负责隔离创建、目录映射或 ACL、受限 IPC、进程预算、取消和回收。
Linux 后端需选定并验证 namespace/文件系统/网络隔离；macOS 后端需验证适用系统版本的可交付隔离机制，不能假定复制 Windows 实现即可。
两平台未实现前明确返回 unsupported，不执行插件。

## 完成门槛

多语言为必验项：沿用程序当前语言设置，覆盖菜单、授权选择、帮助、进度、成功及错误提示；宿主向 worker 显式传递语言，沙盒内不另设一套默认语言。平台生成的提示支持中英文，第三方工具原始输出保留原文。错误码、命令、配置键及 JSON 字段不翻译；两份语言目录的键和插值参数必须一致，并验证语言切换后的实际交互。

无 Docker/WSL 的 Windows 上真实安装、运行、RPC、状态提交、下载、Creator 构建及上传链路通过；授权拒绝、撤销、更新、超时、崩溃、网络拒绝与产物配额通过。
源码与实际冻结程序分别验收。当前已有底层及本地正式入口验证，但完整远程采集、发布链路和冻结包仍未完成，不满足完成门槛。

## 冻结宿主 Creator / 上传验收入口（2026-11-03）

`scripts/verify_frozen_creator.py` 接收 `--binary`、全新 `--output`、审核的
`--offline`、`--creator`、官方 `--template`、`--language zh|en`。
仅复制示例项目，使用正式冻结 configure/prepare/trust/run/permissions revoke
命令；验证未授权拒绝、精确测试授权后的真实构建及产物、语言状态与撤权拒绝。
测试授权仅写入独立数据目录，不代表交互授权界面已验收。

`scripts/verify_frozen_upload.py` 接收 `--binary`、全新 `--output`、审核的
`--offline`、`--language zh|en`。临时 TLS 接收器只监听本机 443，使用临时证书，
精确测试目录授权及本机目标配置均局限于独立测试数据。
验证冻结 SDK 上传完整字节/MD5/SHA256、未授权不联网、撤权后不再上传及语言状态。
该脚本不使用 S3 凭据，不证明真实 S3 上传或交互授权界面。
两脚本仅在全部检查成功后写 verification.json；失败日志仍保留。

修复版 32 项冻结 CLI 检查已完成：
`cache/native-pyinstaller-cli-acceptance-20261103-probe/verification.json`。
Creator / 上传英文冻结验收已启动，结果待核对；中文必须另用新目录执行。

首轮冻结上传已实际完成 2,640,000 字节并返回成功摘要，但生成测试插件错误地
返回 dict（正式 CLI 必须返回 0..255），因此整体验收失败且状态不提交。
生成的 Creator / upload 测试插件已改为验证结果后返回 0。
Windows 冻结环境下 DEVNULL 被视为终端，首轮未授权检查进入提示并等待期限；
脚本现显式使用正式 `--non-interactive`，不依赖设备 TTY 推断。
这些是测试夹具修正，不等于交互授权界面通过；原失败证据保留。
修正版 Creator 英文验收目录为 `cache/frozen-creator-20261103-en2`，仍在运行。

新版冻结产物 SHA256
`c03ee95b551d53fd4251ae44cbb038118fc300935d69496868aa767415eb5f48`
已通过中文 SDK 上传、完整字节及撤权检查，报告：
`cache/frozen-upload-20261103-console-zh/verification.json`。
未授权拒绝为中文单行错误，stderr 不再输出预期失败 traceback。
新增正式 CLI 原生错误边界的 zh/en 测试通过，双语模块共 11 项通过。
更新后全量回归已启动，输出 `cache/native-regression-20261103-console.txt`；
新版英文上传及中文 Creator 验收仍在进行。
