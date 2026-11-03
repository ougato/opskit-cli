# 原生沙盒交付验收 / Native sandbox delivery acceptance

## 当前补验状态 / Current supplemental verification

97c666 新包中文 SSH 完整生命周期和英文 native 复验均已通过，报告分别为
`cache/frozen-lifecycle-20261103-ssh-url-zh/verification.json`、
`cache/frozen-native-20261103-ssh-url-en/verification.json`。
另以真实冻结 TTY 中英文各选择 `(2)` 永久授权（会话 8194、28836），正式权限列表
确认精确目录授权已持久化；故意传错误摘要令插件在上传前失败，原状态仍为 en/zh，
正式撤销后列表均为空。汇总 `cache/frozen-always-consent-20261103-ssh-url.json`。
这是终端注入按键、测试专用资源，不是物理键盘；未发送上传内容。
The new binary passed Chinese SSH and English native checks. Real frozen TTY
Always-allow selection passed in both languages: formal commands listed the persisted
grant and revoked it; intentional pre-upload failure preserved the previous state.
Keys were terminal-injected, resources were test-owned, and no upload was sent.

SSH URL 修复相关插件及生命周期 CLI 专项终态：62 passed，572.44 秒。
97c666 新包英文 native 隔离/状态/回滚复验启动，目录
`cache/frozen-native-20261103-ssh-url-en`；以最终 verification.json 为准。
The plugin and lifecycle CLI regression passed 62 tests. New-binary English
native isolation/state/rollback verification is running in the directory above.

97c666 新包中文 HTTPS 完整生命周期已通过：
`cache/frozen-lifecycle-20261103-https-url-zh/verification.json`，包含实际中文拒绝提示、
认证安装与更新、更新后重新信任及移除保留状态，服务端清理后生成报告。
构建输入对比 `cache/native-ssh-url-build-delta-20261103.json` 确认，与 b0cb44 包
仅 `plugin/commands.py` 不同；不把输入相同误写成新包所有运行路径都已复验。
The 97c666 binary passed the complete Chinese HTTPS lifecycle, including localized
refusals and server cleanup. The recorded build-input comparison finds only
`plugin/commands.py` changed from b0cb44. Equal inputs do not prove every runtime
path on the new binary has been exercised.

最新终态（优先于下方过程记录）：97c666 新包英文 SSH 生命周期通过，证据
`cache/frozen-lifecycle-20261103-ssh-url-en/verification.json`；覆盖私网默认拒绝、
错误主机密钥拒绝且不发送私钥认证、错误口令拒绝、安装/运行/更新重新信任、
移除保留状态。新包 32 项 CLI 通过：`cache/frozen-cli-20261103-ssh-url/verification.json`。
同包中文 HTTPS/SSH 与新一轮全量回归仍在运行，不能把英文结果当成中文终态。
源码搜索 `core`、`plugin`、构建与依赖，Docker/WSL 剩余引用属于独立软件管理、
镜像配置及软件 SDK 说明，没有发现插件容器执行依赖。
Latest terminal results: the 97c666 binary passed English SSH lifecycle and 32 CLI
checks. Its Chinese HTTPS/SSH and full regression checks remain in progress.
Current Docker/WSL references in the inspected production boundaries belong to
standalone software management and its configuration, not plugin container execution.

快照回归终态：4462 passed、129 skipped、39 warnings，2524.20 秒。
`cache/native-snapshot-regression-20261103.result.json` 记录 pytest exit_code=0，
但 source_unchanged=false；运行期间变化的授权、语言和辅助进程取消处理须补验，
不能将此结果表述为最终不变源码快照通过。
同一 b0cb44 冻结包英文 Creator 也已通过，报告
`cache/frozen-creator-20261103-console-signal-en/verification.json`；与中文同包均验证
独立网页产物、语言状态、未授权及撤权拒绝。这不证明原生安装包构建或人工授权。
The regression finished with 4462 passed and 129 skipped, but its source manifest
changed during execution. It is not an unchanged final-snapshot pass. The b0cb44
English Creator check also passed, matching the Chinese artifact/language/revocation
checks on the same binary. Native installers and human consent are not established.

冻结生命周期验证脚本现增加 `--transport https`，使用本机独立端口、临时证书及
合成令牌的真实 Git smart-HTTPS 服务，不改系统信任、不连接生产仓库。
首轮验收运行中；本地私网地址不等于公网/全部网络路径验收。
The lifecycle verifier now supports a disposable authenticated smart-HTTPS server.
It neither changes system trust nor contacts production.

HTTPS 首轮已通过：`cache/frozen-lifecycle-20261103-https-en/verification.json`，
涵盖私网默认拒绝、不可信 CA 拒绝、重定向不跟随、认证安装/更新、重新信任及移除保留状态。
随后脚本增加 finally 清理服务端后才写成功报告；首轮运行的是较早脚本，不将后续修改
追溯为已验证。SSH 冻结首轮安装失败，源码使用同类服务/密钥实际采集成功，正在定位
冻结差异；`cache/frozen-lifecycle-20261103-ssh-en` 保留失败日志，尚无 SSH 冻结成功证据。
运行期间变更的授权、语言和辅助进程相关补验：60 passed、2 skipped。
The first HTTPS run passed its listed lifecycle and rejection cases. A later verifier
change writes success only after server cleanup; that later code was not exercised
by the first run. Frozen SSH installation failed while source acquisition succeeded
against the same type of fixture. Its frozen packaging difference remains under
investigation. Changed authorization/language/helper checks: 60 passed, 2 skipped.

SSH 失败根因已定位：安装目录推导把完整 `ssh://git@host:port/path` 当成 SCP
地址，生成含端口的无效目录名，尚未进入采集。源码诊断直接调用 populate 成功，
先前 SSH 生命周期测试用 `--as` 避开了此问题。`dir_name_from_url` 现仅对无
`://` 地址匹配 SCP；新增带端口、默认端口与 SCP 三种目录推导断言。
无别名 SSH 冻结重验需使用新构建；旧 b0cb44 报告不证明该修复。
The SSH failure came from installation-name parsing before acquisition: full SSH
URLs were mistaken for SCP notation. Explicit aliases hid this in earlier tests.
SCP matching now excludes URLs with a scheme, with three regression assertions.
The fix requires a new frozen build; b0cb44 evidence does not establish this change.

SSH URL 修复新包 SHA256：
`97c6665bcbb39f85519088e6893a8f0ddcc2eb6182b2b742a5786825062fd47e`，
路径 `cache/native-pyinstaller-build-20261103-ssh-url/source/dist/opskit-windows-x64.exe`。
SSH 冻结重验已通过初次安装/运行及更新阶段，完整终态仍在运行，报告目录
`cache/frozen-lifecycle-20261103-ssh-url-en`。同期新包 CLI 门禁与插件专项在运行。
另启动唯一命名前缀 `cache/native-snapshot-regression-20261103-ssh-url` 的全量回归，
保留前轮报告；需以对应 result.json 确认终态及 source_unchanged。
The SSH URL fix has a new binary with the digest above. Its frozen SSH run has
reached installation, execution and update; the full outcome is still pending.
New-binary CLI checks, plugin regression and a separately named full source
regression are running. Retain the previous reports and inspect terminal results.

多语言是交付必选项，沿用 OpsKit 当前语言设置；Windows 与未来 Linux/macOS
共用中英文语言目录，不另设沙盒语言。菜单、授权、帮助、进度、错误、取消与恢复
均须本地化；错误码和协议字段保持稳定，第三方原始日志保留原文。
本轮语言、权限菜单、工具配置及工作区恢复菜单专项：28 passed。
Bilingual delivery is mandatory, using the existing OpsKit language setting across
all native backends. UI, consent, help, progress, errors, cancellation and recovery
must be localized. Stable identifiers and third-party raw logs are not translated.
The focused language and menu regression passed 28 tests.

本地冻结生命周期重试已通过：
`cache/frozen-lifecycle-20261103-console-signal-en2/verification.json`，
b0cb44 同包完成安装、准备、信任、运行、更新后拒绝旧信任、重新信任运行、
移除并保留状态。首轮失败为验证脚本误将业务 CLI 空输出解析为 JSON；旧记录保留。
这不证明远程 HTTPS/SSH 生命周期，整体交付仍有未完成验收项。
The retry passed the frozen local Git lifecycle, including update retrust and state
preservation on removal. The first verifier incorrectly parsed empty business CLI
output as management JSON. Its failure logs remain preserved. Remote HTTPS/SSH
lifecycle and overall delivery acceptance remain incomplete.

最新同包补验：b0cb44 中文 Creator 成功构建流程已通过
（`cache/frozen-creator-20261103-console-signal-zh/verification.json`，会话 25774），
独立产物、状态提交、未授权及撤权拒绝均通过。
新增 `scripts/verify_frozen_lifecycle.py` 在私有目录准备 Git/Python，正式冻结 CLI
安装、准备、信任、运行、更新后拒绝旧授权、重新准备信任及移除保留状态。
英文首轮 `cache/frozen-lifecycle-20261103-console-signal-en`（会话 66185）
已通过 prepare-fetch/install，后续尚未完成，不能宣称生命周期全通过。
脚本已区分业务 CLI 输出与正式管理 JSON envelope；运行中的首轮载入较早版本，
须核对实际终态，不能只凭脚本当前源码推定已覆盖修正。
The b0cb44 Chinese Creator success path passed. New frozen local Git lifecycle checks
are in progress: tool preparation and installation passed; later steps remain unverified.
Local lifecycle evidence does not establish remote HTTPS/SSH lifecycle coverage.

## 最新修复：Ctrl+C 辅助进程输出 / Latest Ctrl+C helper fix

fb0d3b 冻结程序真实 TTY 会话 71127，选择 1 本次授权后发送 Ctrl+C，
任务退出码 1、旧状态保留、空持久授权、对应 Creator 进程已回收；
但检查辅助进程打印 multiprocessing KeyboardInterrupt 堆栈，故体验验收未通过。
`network/worker.py` 现由辅助进程忽略 SIGINT，宿主仍负责取消、Job 和期限回收。
新增真实 spawn 子进程 SIGINT 用例已通过；父进程消失、管道异常及资源回收专项
终态 37 passed、2 skipped，日志 `cache/native-worker-console-cancel-20261103.txt`。
此修复已进入下述 b0cb44 冻结包，并完成中英文 Ctrl+C 复验；旧包失败记录保留。

Frozen TTY Ctrl+C preserved state and reaped Creator, but an inspection helper
printed a multiprocessing KeyboardInterrupt traceback. Helpers now ignore SIGINT;
the supervisor retains cancellation and process cleanup ownership. A real spawned
helper interrupt check passed; cleanup tests finished with 37 passed and 2 skipped.
The subsequent b0cb44 frozen checks below verify this repair in both languages.

修复冻结包 SHA256 `b0cb447b5d2e86fe57ea879258b63a25ebe70186e57ec2f5703e0e2129113934`
已通过英文真实 TTY Ctrl+C：会话 31135，本次授权后取消，退出码 1；
未打印辅助进程堆栈，显示本地化中断摘要，Creator 回收、旧状态和空持久授权已复核。
汇总 `cache/frozen-console-signal-20261103.json`，终端注入而非物理键盘。
该同包 32 项 CLI 与英文 native/越权验收均已通过。
The signal-fix binary passed real English TTY Ctrl+C without the helper traceback,
preserving prior state and leaving no persistent grant or matching Creator process.
Keys were terminal-injected. Same-binary 32 CLI and English native checks passed.

同包中文 Ctrl+C 亦通过，会话 45584：中文中断摘要、无辅助进程堆栈、退出 1，
旧状态保留、空持久授权、对应 Creator 已回收。测试语言配置恢复 en。
中英文汇总均在 `cache/frozen-console-signal-20261103.json`。
同包英文 native/越权验收已通过：
`cache/frozen-native-20261103-console-signal-en/verification.json`，
包含准备清理、状态提交/回滚、撤权及宿主文件/环境/本机 TCP/UDP 边界。
CLI 报告 `cache/frozen-cli-20261103-console-signal/verification.json`。
同包英文上传通过：`cache/frozen-upload-20261103-console-signal-en/verification.json`，
完整接收字节/MD5/SHA256、未授权及撤权拒绝均通过（本机 TLS，非 S3）。
同包中文上传也已通过：`cache/frozen-upload-20261103-console-signal-zh/verification.json`。

新增恢复脚本 `scripts/verify_frozen_recovery.py`：源码 Python 创建真实 os._exit(27)
准备凭据，正式冻结 CLI 检查与清理；中文报告
`cache/frozen-recovery-20261103-console-signal-zh/verification.json` 已通过，
覆盖活动租约、错误摘要、替换目录拒绝、准确凭据清理及未知目录保留。
这不等于冻结准备进程本身在任意崩溃点的恢复矩阵；英文同版复验正在运行。
The frozen CLI recovered a source-created os._exit(27) preparation fixture and
refused active, digest-mismatched and replaced objects while preserving unknown data.
Chinese passed; English is running. This does not cover every frozen preparation crash point.

英文恢复终态也已通过：
`cache/frozen-recovery-20261103-console-signal-en/verification.json`，同一 b0cb44 构建，
覆盖与中文相同的活动/摘要/身份拒绝和准确凭据清理。
English recovery also passed on the same b0cb44 binary, with the same refusal
and exact-receipt collection checks as Chinese.

旧源码验收脚本 `verify_plugin_native.py` 已修正为 CLI 返回 0、从提交的语言状态
文件验证语言；原先返回 dict 已不符合当前入口契约。新目录真实复验进行中，
不回写历史失败日志或报告。

该脚本英文真实复验已通过：`cache/native-final-sdk-20261103-en/verification.json`，
使用正式 prepare/SDK/隔离调用验证宿主读取拒绝、源码写入拒绝、语言及状态提交。
这份是源码宿主报告，不是冻结包报告。

同类源码上传验收 `verify_upload_native.py` 也已统一为整数退出码，
上传结果写入插件事务状态 result.json 后由宿主核对；取消用例仍要求旧状态不提交。
英文真实复验已通过：`cache/native-upload-final-sdk-20261103-en/verification.json`，
完整接收 2560000 字节、摘要与状态正确；第二次调用注入取消后旧状态保留且未第二次上传。
该取消是注入 checkpoint，不能替代上述冻结 TTY 按键证据。
The same binary also passed Chinese TTY Ctrl+C with no helper traceback,
preserved prior state and no matching Creator process. Test language was restored.

fb0d3b 英文摘要/上传/撤权验收通过：
`cache/frozen-upload-20261103-consent-label-en/verification.json`（本机合成 TLS）。

最新控制台验收：e384bb 中文冻结程序通过真实 TTY 按键注入完成选择 3 拒绝、
选择 1 本次授权后 Esc 取消；退出码均为 1，取消后旧 result.json 与 zh 状态保留，
持久授权为空，未发现属于该测试项目的 Creator 进程。证据汇总
`cache/frozen-console-20261103-zh.json`，对应工具会话 40071、94231。
这证明真实终端路径，不等于人工物理键盘操作。发现 `(0)` 空标签，已补中英文
“拒绝并返回”；授权/语言/权限菜单专项 26 passed，尚未进入此冻结包。

Real frozen Chinese TTY checks exercised denial and once-grant followed by Escape,
preserving prior state and leaving no persistent grant or matching Creator process.
Keys were injected through the terminal, not a physical keyboard. The empty zero/back
label was found and fixed bilingually in subsequent source (26 focused checks passed).

英文冻结 TTY 同样通过：会话 38251，选择 1 本次授权后 Esc，英文授权和取消提示，
退出码 1；旧 result.json、en 状态和空持久授权均保留，该测试项目无 Creator 进程。
证据 `cache/frozen-console-20261103-en.json`。未将终端注入描述为物理键盘验收。
“拒绝并返回”文案已由下述 fb0d3b 新包完成中英文 TTY 复验。
English frozen TTY once-consent/Escape checks also passed with preserved state,
empty persistent grants and no matching Creator process (terminal-injected keys).
The subsequent fb0d3b build verified the localized zero/back label in both languages.

返回项文案修复的新冻结包已生成：
`cache/native-pyinstaller-build-20261103-consent-label/source/dist/opskit-windows-x64.exe`，
SHA256 `fb0d3bde7b1fd1d5c95d9bab551be6bc55a0aa08d9e89395ac94b808d6ee127a`。
英文 Creator 与中文原生/越权验收分别在会话 87713、65971 成功完成。
报告为 `cache/frozen-creator-20261103-consent-label-en/verification.json` 和
`cache/frozen-native-20261103-consent-label-zh/verification.json`，均绑定上述 fb0d3b 摘要。
前者验证独立产物、语言状态及未授权/撤权拒绝；后者额外验证宿主读写、源码写入、
环境秘密及本机 TCP/UDP 接收端边界。UDP 发送返回成功但接收端没有收到数据。
旧包控制台报告不自动证明此新包；待按最终摘要逐一核对。

fb0d3b 新包 32 项 CLI 已通过：`cache/frozen-cli-20261103-consent-label/verification.json`。
同包真实 TTY 核对 `(0) Deny and return`、`(0) 拒绝并返回`，中英文按 0 均拒绝，
旧状态与空持久授权保留；仅修改测试目录语言配置并恢复 en。
按键注入会话 82569、63951，汇总 `cache/frozen-consent-label-20261103.json`。
This binary passed 32 CLI checks and actual zh/en TTY zero-label/refusal checks.
The test-owned language configuration was restored; keys were terminal-injected.

运行中源码快照核对发现 5 个已修改文件：`core/locale/en.yaml`、
`core/locale/zh.yaml`、`core/plugin_capabilities.py`、`scripts/verify_frozen_native.py`、
`tests/test_native_sandbox_i18n.py`。前四项功能/脚本按对应新包和专项验收，
全量回归不因观察等待重启；终态须保留 changed_paths，不能报告源码完全未变。

The consent-label build is available with the SHA256 above. English Creator and
Chinese native/adversarial checks passed. Five files changed after the full
regression snapshot; retain that distinction instead of claiming an unchanged snapshot.

当前结论：**尚未完成最终交付**。本表核对已约定的迁移目标及实施方案完成门槛，
不将专项通过或旧构建报告当作最终源码与最终发行包的全部证明。

Status: **final delivery is not yet verified**. Focused tests and earlier binary reports
are not evidence that every requirement passes on the final source and release artifact.

## 要求与证据 / Requirements and evidence

| 要求 / Requirement | 当前证据 / Evidence | 剩余 / Remaining |
| --- | --- | --- |
| 删除插件 Docker/WSL 依赖，保留独立 Docker 软件管理 | `test_plugin_native_architecture.py` 14 项通过；旧容器模块不可导入，源码搜索剩独立镜像类别与 SDK 软件示例 | 最终包完整运行及依赖审计；旧用户 Docker 资源按独立说明人工核对，不自动删 |
| Windows 原生隔离，无不受限回退 | `windows_appcontainer.py` / `windows_job*.py`；真实冻结 native 报告验证宿主秘密不可读、插件源不可写、状态提交及回滚 | 最终构建的完整恶意负载矩阵 |
| Linux/macOS 可扩展契约 | `native_sandbox.py` 定义 Backend/Process/Limits，未实现平台返回本地化拒绝 | 两平台具体后端留待后续，不能宣称已支持 |
| 本次/永久/拒绝；指纹绑定；查看与撤销 | `test_plugin_capabilities.py`、权限菜单及双语测试；冻结 Creator/upload 未授权与撤权拒绝报告 | 最终冻结程序的实际交互选择验收；自动化精确测试授权不等于人机界面验收 |
| 沿用原程序中英文 | 两份语言目录键/插值检查；真实 worker 语言继承及 zh/en Creator/upload 状态报告 | 最终程序逐入口实际切换语言检查，包含新增取消提示和准备恢复 |
| Creator 宿主代理及便捷配置 | 别名配置与授权分离；e384bb 新包 zh/en 真实 Creator 独立输出、产物检查、状态提交与撤权均通过；产物/身份专项见下 | 原生平台完整编译/安装包验收未完成。Creator 项目脚本以用户权限运行，不承诺宿主目录隔离 |
| 上传可用、无需插件自行读取大宿主文件 | `artifact_info` 与 `upload_file` 共用目录授权；真实 SDK 和冻结 zh/en 全字节/MD5/SHA256 验证 | 最新构建复验；真实 S3、签名链接签发及生产发布没有凭据/目标证据 |
| 超时、取消、进程树回收与状态回滚 | 原生进程/协议期限专项；任务控制相关 55 项通过；真实 SDK zh/en 第二次调用取消后状态不变且无第二次上传 | 物理键盘/最终冻结界面操作；不能宣称外部构建文件或已发网络字节可撤回 |
| 崩溃恢复与明确归属 | 更新、profile、工作区和 catalog 凭据/活动租约；新 preparation 真实 os._exit 恢复与替换拒绝测试 | 最终包恢复操作；预凭据崩溃目录及旧 staging 保持未知，不按名称删除 |
| 准备清理支持真实目录规模 | 8300 文件复验 92.26 秒完成；prepare 清理独立期限 180 秒，失败保留凭据 | 最终构建确认；清理大小上限不是磁盘硬配额 |
| 资源预算与产物配额 | 内核内存/CPU/进程数限额；状态/传输/目录扫描及逻辑保留上限 | 磁盘硬预算尚未实现，不能把逻辑扫描、空间估算或事后检测写成硬配额 |
| 安装、更新、远程下载 | 真实原生 Git 工具采集与 HTTPS/SSH 传输专项、固定源码库存和生命周期回归 | 最终冻结程序的完整远程生命周期与网络拒绝矩阵 |
| 最终源码/发行包回归 | 第二轮全量 4419 passed /129 skipped，2390.52 秒；首轮两个 TLS 失败已不再出现 | `cache/native-regression-20261103-console.txt` 已成功，但运行期间有后续源码和测试变更，不能作为最终快照绿色结论 |
| SDK 升级交付说明 | `plugin-native-upgrade-guide.md` 提供双语迁移、配置、授权、摘要上传及取消说明 | 按最后实现同步，核对全部命令和边界 |

## 已检查的真实报告 / Inspected real reports

- `cache/frozen-creator-20261103-console-zh/verification.json`
- `cache/frozen-creator-20261103-console-en/verification.json`
- `cache/frozen-native-20261103-preparation-en/verification.json`
- `cache/frozen-native-20261103-preparation-zh/verification.json`
- `cache/frozen-artifact-upload-20261103-zh/verification.json`
- `cache/frozen-artifact-upload-20261103-en/verification.json`
- `cache/native-task-cancel-20261103-zh/verification.json`
- `cache/native-task-cancel-20261103-en/verification.json`

这些报告的 binary_sha256（若适用）对应各自构建，不可互换为最终构建通过。
`native-pyinstaller-build-20261103-task-control` 是目前新增取消控制后的构建，
其 native/upload 中英文复验均已通过，尚不能据此标记全部完成。

Each report identifies its own binary where applicable. Do not transfer its result to
a later build. The task-control build has passed native/upload checks in both languages; final completion
requires closing the remaining items against the final source and binary.

最新版已核对报告（同一 binary_sha256
`61e03d2f946c743c2f1657fa3f9cdbee0d4e0126bfe648004e2041ca77f5936b`）：

- `cache/frozen-native-20261103-task-control-zh/verification.json`：准备后临时目录凭据为空、
  原生隔离、状态提交/回滚、信任撤销后拒绝。
- `cache/frozen-native-20261103-task-control-en/verification.json`：同版英文准备清理、
  原生隔离、状态提交/回滚和撤权拒绝均通过。
- `cache/frozen-creator-20261103-task-control-zh/verification.json`：同版中文真实
  Creator 3.8.7 网页构建、状态提交、未授权拒绝和撤权拒绝通过。
- `cache/frozen-upload-20261103-task-control-en/verification.json`：SDK 取摘要、完整上传、
  未授权拒绝及撤权拒绝，英文状态提交。
- `cache/native-task-cancel-20261103-en/verification.json`：英文真实 SDK 注入取消后
  临时状态不提交、没有第二次上传；与中文报告一致。此项宿主是源码，不是冻结界面操作。

最新版 32 项 CLI 与中文冻结上传验收均已通过，报告分别为：

- `cache/frozen-cli-20261103-task-control/verification.json`
- `cache/frozen-upload-20261103-task-control-zh/verification.json`

### 多语言交付门槛 / Localization delivery gate

沿用程序现有语言配置和 `core/locale/zh.yaml`、`core/locale/en.yaml`，
新增功能不单独维护语言开关。菜单、授权选择与撤销、错误、命令帮助、进度、
取消提示、恢复与清理入口都必须提供中英文；语言键和格式参数必须一致。
Linux/macOS 后续实现也遵守同一要求。第三方工具原始日志保留原文，
OpsKit 自身的操作说明与结果提示按当前语言显示。

Reuse the application's existing language configuration and zh/en catalogues.
All new menus, consent and revocation flows, errors, command help, progress,
cancellation, recovery and cleanup prompts require both languages with matching
keys and interpolation parameters. Future Linux/macOS implementations follow
the same contract. Preserve third-party tool logs verbatim while localizing
OpsKit's own instructions and result messages.

本次重新运行语言、取消及新增管理菜单专项：64 passed。
该结果不替代最终冻结程序的人工交互及全量回归验收。
Localization, cancellation and new management-menu checks: 64 passed.
This does not replace final frozen interactive acceptance or full regression.

Creator 参数、路径、程序变化和日志上限错误已接入同一中英文目录；
补充无效参数在授权前拒绝且按当前语言返回错误的测试。
相关 Creator/语言/取消专项 51 passed，证据：
`cache/native-creator-localization-20261103.txt`。
此后续源码修改尚未进入上述 SHA256 的冻结包。
Creator validation and resource-change errors now use the shared bilingual catalogues.
Related Creator/localization/cancellation checks: 51 passed. This subsequent source
change is not included in the frozen binary identified above.

补齐 Creator 错误文案的新冻结包已构建：
`cache/native-pyinstaller-build-20261103-localized-errors/source/dist/opskit-windows-x64.exe`，
SHA256 `9a916ea1f60b6433f35af9f10419a3da3aeb775f50fef66ea9f4d3d00f6dbb5f`。
新包中文 native 验收已通过：
`cache/frozen-native-20261103-localized-errors-zh/verification.json`，
覆盖隔离、语言、准备清理、状态提交/回滚及撤权拒绝。
新包 32 项 CLI 与英文 native 验收均已通过，报告分别为
`cache/frozen-cli-20261103-localized-errors/verification.json` 和
`cache/frozen-native-20261103-localized-errors-en/verification.json`。
The localized-error build passed Chinese native checks covering isolation,
language, preparation cleanup, state commit/rollback and revocation. Its 32 CLI and
English native checks also passed. Earlier reports remain bound to their own artifacts.

## Creator 新产物检查更新 / Fresh Creator output inspection

后续源码已新增显式 `buildPath=project://build` 和每次随机 outputName，
不再沿用可能含旧产物的默认输出。工具授权绑定 `project-build-unique-v1` 策略。
成功候选退出后检查普通目录/文件、单链接、大小/条目/深度上限及网页 index.html，
成功结果提供 artifact.directory/files/bytes；失败保留已有构建文件并显示双语摘要。
相关专项 65 passed，证据 `cache/native-creator-output-final-20261103.txt`。
中文真实官方模板 SDK 构建已通过：
`cache/native-creator-output-20261103-zh/verification.json`，69 文件、15601168 字节，
独立输出目录、语言与状态提交均已核对。该进程启动后补充的目录身份复核由
65 项专项覆盖；另新增根目录替换测试，产物模块 9 passed。
新冻结包 SHA256 为 `e384bb6958a7e8a936ddc5f504de19dca6453d19fbda51ccd314b4af6b798fd7`，
中文 Creator 和英文 native 验收运行中；上述旧构建证据不能证明此更新。

New source uses an explicit project build path and a unique output name per run,
bound to the tool grant's output policy. After a successful candidate exit, inspection
checks ordinary single-link files, size/entry/depth limits and web index.html before
returning artifact metadata. Related checks: 65 passed, plus 9 output tests including
root replacement. The real Chinese SDK template build produced 69 files (15601168
bytes) and committed state. Subsequent directory identity checks have focused evidence;
new frozen Creator/native checks remain in progress. This is not a disk hard quota.

最新 e384bb 构建报告更新：

- `cache/frozen-creator-20261103-output-zh/verification.json`、
  `cache/frozen-creator-20261103-output-en/verification.json`：真实独立输出及产物检查、
  语言状态、未授权和撤权拒绝均通过。
- `cache/frozen-native-20261103-output-en/verification.json`：英文原生准备清理、
  隔离、状态提交/回滚及撤权通过。
- `cache/frozen-upload-20261103-output-zh/verification.json`：中文摘要上传、
  接收字节/MD5/SHA256、未授权与撤权拒绝通过；仍是本机 TLS，不是真实 S3。
- 同版英文上传与 32 项 CLI 复验均通过：
  `cache/frozen-upload-20261103-output-en/verification.json`、
  `cache/frozen-cli-20261103-output/verification.json`。

冻结越权测试新增宿主写入、环境秘密、真实本机 TCP/UDP 接收端检查。
首轮未通过，第二轮诊断显示网络异常未符合原先仅接受 EACCES 的断言；
补充记录 timeout 与 access_denied，并由宿主独立核对接收端没有连接/数据。
第三轮进一步发现 UDP sendto 可先返回成功，因此不能单凭该返回判定实际出网。
第四轮 `cache/frozen-native-20261103-adversarial-en4` 记录调用结果，再独立检查宿主
TCP/UDP 接收端是否有连接/数据；第四轮已通过。报告明确记录 TCP timeout、
UDP send_accepted，但宿主 TCP 没有连接、UDP 没有收到数据；不将 UDP 返回值
解释为立即拒绝，也不把本机环回证据扩展成所有公网/私网路径已验证。
此外宿主文件读写、插件源写入被拒绝，宿主秘密环境变量未继承，状态回滚和撤权均通过。
本机端点用普通宿主连接/数据先验证可达，避免把没有监听服务误当隔离成功。

The e384bb build passed real Creator fresh-output checks in both languages,
English native checks and Chinese upload checks. Expanded frozen adversarial
checks passed for these local cases. Earlier failed attempts are retained; network timeout and
access-denied observations are distinguished and host receivers are checked
independently. This does not establish a complete external-network rejection matrix.

源码快照全量回归已启动，进程日志 `cache/native-snapshot-regression-20261103.log`。
运行器记录开始/结束的源码摘要和变更路径；运行中仍修订了上述验收脚本，
因此即使测试通过也须核对 result.json，不能直接称为完全未变的最终源码快照。

更细历史与命令见 [迁移实施记录](plugin-native-sandbox-migration.md)。
See the migration record for detailed history and commands.
