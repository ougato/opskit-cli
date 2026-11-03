# OpsKit 插件规范（SDK API2，原生沙盒）

## 平台与边界

插件独立开发，放在 `plugins/<repository>`；平台的 `plugin/` 是官方生命周期模块，不是第三方插件目录。插件不得要求修改平台入口、注册表或共享依赖。

Windows 使用 AppContainer 与 Job Object。插件在隔离验证完成后才执行，无直接宿主网络能力。Linux/macOS 共用后端契约，当前未实现，明确拒绝执行；不得降级为普通宿主进程。插件系统不再使用 Docker 或 WSL，独立 Docker 软件管理不受影响。

当前迁移状态和未完成验收见 [原生沙盒迁移](plugin-native-sandbox-migration.md)。本文描述当前接口，不代表冻结包、崩溃恢复、硬资源预算、Creator 或 S3 发布链路已全部验收。

旧插件迁移步骤和中英文 SDK 示例见 [原生升级指南](plugin-native-upgrade-guide.md)。
For migration steps and bilingual SDK examples, see the native upgrade guide.

## 清单与入口

```yaml
name: hello
version: 1.0.0
api_version: 2
kind: python
entry: hello_pkg
permissions: []
```

身份以小写字母开头，后续为小写字母、数字或下划线；核心身份保留。入口必须属于插件自身，拒绝链接和越界路径。清单可声明 `filesystem`、`network`，这些标签不会自动开放宿主目录、工具或 root 权限。

Python 插件导出 `cli(argv)`，返回 0..255 的整数或 None（0）；适配 argparse 的 SystemExit。菜单入口导出 `register() -> core.sdk.ModuleInfo`。exec 入口可为插件相对路径，或以 windows/linux/darwin 为键的平台映射；Windows 选择 windows。路径使用正斜杠，拒绝绝对路径、父目录与反斜杠，目标须能在对应平台隔离执行。

```python
# hello_pkg/__init__.py
import argparse

def cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="opskit plugin run hello --")
    parser.add_argument("--name", default="world")
    args = parser.parse_args(argv)
    print(f"Hello {args.name}")
    return 0
```

`opskit plugin run hello -- --name Alice` 中，`--` 后的参数、帮助和业务退出码由插件定义。平台不导入插件来生成全局帮助，不保存第三方 CLI schema。

## 采集、审核与更新

先准备独立采集工具：

```text
opskit plugin prepare-fetch --python-runtime C:\tools\python --git-runtime C:\tools\git
opskit plugin install https://git.example.com/team/hello.git --branch main
```

工具先复制并在 AppContainer 中探测，通过后才发布 catalog。采集不执行宿主 Git，不运行插件导入、checkout hooks 或任意宿主命令。HTTPS/SSH 下载使用平台受限传输，pack 在隔离 Git 中校验与导出，随后核对候选内容再发布。

私有仓库使用 `--fetch-profile` 指向当前用户私有凭据文件，绑定完整来源；内网另需本次命令的 `--allow-private`。更新不继承前次凭据或私网许可。SSH 必须预先核实主机公钥，不自动接受首次主机，不使用宿主 SSH agent/config。凭据格式见迁移文档。

`opskit plugin fingerprint PATH` 生成 CHECKSUMS.yaml；它是内容校验，不是发布者签名。安装后核对来源、权限、指纹并授信。内容更新撤销旧信任与相关授权，不自动继承。更新必须保留用户本地修改，无法安全替换时拒绝；不能强行覆盖以完成更新。

## 准备运行时

```text
opskit plugin prepare plugins/hello --python-runtime C:\tools\python --wheels C:\reviewed\wheels --lock-file C:\reviewed\requirements.lock
```

源码等价入口是 `python scripts/build_plugin_runtime.py`，其余参数相同。旧 `--base-image` 和 WSL 设置不再适用。

准备使用独立解释器快照、离线 wheel 和完整哈希锁。可用 `--dependency package==version` 核对明确选择；它不会追加未审核依赖。拒绝源码包、任意依赖 URL 和依赖覆盖平台 SDK。解释器兼容标签在原生隔离中探测。准备不授信插件。

catalog 绑定插件指纹、锁、SDK 与全量文件内容；执行前重新验证。SDK 文件和语言资源由 `core/plugin_runtime_inventory.py` 明确列出，不递归复制整个平台 core。SDK 内容变化后须重新 prepare。冻结包通过同一明确库存提供运行时源码，其实际发行产物仍需独立验收。

## 宿主能力与多语言

SDK `artifact_info(directory, relative) -> {bytes, sha256}` 在精确目录读取授权下
由宿主监督进程计算普通文件摘要；最大 512MiB，拒绝链接、重解析路径、硬链接及
读取期间变化，支持期限/撤权。结果不包含文件内容。
将该 SHA256 传给 `upload_file`，上传快照会再次核对；文件变化不静默接受新摘要。
同一调用、同一精确目录的一次授权可复用，不重复询问。

`artifact_info` returns only host-computed file size and SHA256 under explicit directory
consent (ordinary files, up to 512MiB). Upload verifies its snapshot against that digest.
Both operations share the same scoped grant; once consent is reused within the invocation.

插件通过 SDK 请求能力，不直接扩大隔离目录：

- `read_host_file(directory, relative)`：显式目录读取授权，单文件最多 1MiB，拒绝越界与链接。
- `creator_build(executable, project, options)`：固定 Creator 构建操作。Windows 工具须为 Creator.exe 或 CocosCreator.exe；当前 options 仅支持 platform 和 debug。授权绑定插件指纹、工具内容、项目和构建参数。
  每次显式输出到 `build/opskit-平台-随机ID`，授权另绑定输出策略；成功退出后检查新产物，返回 `artifact: {directory, files, bytes}`。
  后续摘要/上传使用返回目录。产物检查上限 2 GiB/65536 条目/64 层，拒绝链接；网页须有非空 index.html。完成后检查不是磁盘硬配额，也不是原生包编译/安装验收。
  返回 `success`、原始 `returncode` 和 `platform`；调用者检查 `success`，不要假定所有成功退出码为零。[Creator 3.8 官方文档](https://docs.cocos.com/creator/3.8/manual/en/editor/publish/publish-in-command-line.html)规定 36 为构建成功、32/34 为失败；兼容返回 0 的启动器。平台摘要随当前语言切换，第三方原始日志保留原文。
- `https_request(url, method, headers, body)`：受限 HTTPS RPC，需要 network 权限。
- `upload_file(directory, relative, url, sha256, headers=None)`：单文件 HTTPS PUT，最多 512MiB；
  同时要求 network 权限和宿主目录读取授权，必须给出待上传内容的 SHA256。

Creator 在宿主执行，可能执行项目构建脚本；授权界面必须说明此边界。它不是让插件取得任意 shell。通用目录写入、大文件传输与 S3 工具链不能因能力类型已预留就视为可用。

授权选项为本次、永久、拒绝；永久授权绑定内容身份与精确范围，可通过菜单或 `plugin permissions list` / `plugin permissions revoke ID` 查看和撤销。非交互模式没有授权时明确拒绝。

中英文是必验项。菜单、授权、进度、结果、错误、帮助及平台未实现提示遵循程序当前语言；新增文案同时加入 zh/en，键和插值参数一致。插件使用 SDK 的 t/current_lang/register_locale 接入自身语言资源。机器错误码、命令、配置和协议字段不随语言改变。第三方工具原始日志可保留原文，但平台提供本地化结果摘要。

## 状态、期限与清理

`plugin_data_dir` 只能访问自身隔离状态。自身状态分区为 `<data>/plugin-data/<name>/self`，服务分区为 `<data>/plugin-data/<provider>/callers/<authenticated-caller>`；调用方由平台绑定，不接受插件伪造。插件不能读取其他分区。

执行使用私有快照和工作区。成功返回不立即提交：先确认进程及工作区清理，再重新检查期限、内容指纹和授权链，最后提交状态。执行、清理、撤权或期限检查失败均不提交。状态上限 16MiB、4096 条目、64 层；扫描额度不等于硬磁盘配额。

嵌套调用共享父期限，不能重新获得完整 timeout。并发状态事务冲突明确失败。`plugin inspect-workspaces` 仅观察，不根据目录名称推断所有权或授权删除。旧容器 reconcile/锁退休命令已经移除；原生崩溃恢复仍需实施，不能用删除任意遗留目录替代。

Windows 原生进程树默认 Job 预算为 256MiB 内存、16 个进程、整机 CPU 总容量的 25% 硬上限。宿主通过公共 `NativeLimits` 契约传入预算，插件不能修改；后续平台后端沿用此契约。所有限制必须在挂起进程恢复前成功设置，设置失败不执行插件。CPU 上限控制速率，整体执行期限另行限制持续时间；目录扫描仍不能代替磁盘硬配额。

## SDK 服务契约

只依赖 `core.sdk` 稳定接口。SDK api_version=2、RPC 传输版本和业务服务版本相互独立。

提供方声明 provides 及 service_contracts，每个契约包含严格整数 api_version（1..65535）及 1..64 个不重复公开 methods，并导出 provide_service(name)。调用方 uses 必须明确 service/provider/api_version/methods；拒绝通配、私有、重复方法和未知授权字段。旧 uses 不自动取得全部方法权限。

操作方核对 `plugin show CALLER` 和 `plugin show PROVIDER` 后，使用：

```text
opskit plugin authorize-service CALLER SERVICE --caller-fingerprint CALLER_SHA256 --provider-fingerprint PROVIDER_SHA256
opskit plugin revoke-service CALLER SERVICE
```

SHA256 必须换为核实后的实际指纹。批准精确绑定调用方、提供方、服务版本与方法集，不执行插件。独立运行授信不等于批准调用边；提供方替换、双方更新或撤权不会继承旧边。调用和状态提交检查整条授权链，嵌套返回后仍保留其授权依赖，避免提交依赖已撤销服务的结果。

get_service 返回 JSON RPC 代理，参数与结果必须可序列化；函数、回调和共享对象不跨边界。循环调用及超过四层嵌套拒绝。宿主先校验授权，worker 在执行点核对快照契约、实际业务版本和公开方法，不在宿主导入提供方。

RPC 版本严格为整数 1。身份由平台绑定，信封未知字段、重复 JSON 键、非有限数字、超额结构或结果后继续调用均拒绝。业务版本不能覆盖传输版本。协议预算见实现与测试，不以合法 JSON 等同于获批能力。

## 网络

AppContainer 不开放直接网络；第三方 HTTP(S)_PROXY 与容器出口代理已移除。插件必须调用受限 SDK HTTPS API，不能假定 requests、socket 或 Git 自动取得网络。

当前 HTTPS RPC 仅 HTTPS/443，受限请求头，32KiB 请求和响应，无重定向、无环境代理。平台检查全部 DNS 地址，连接已检查的数字地址并保留 TLS SNI 与证书验证。默认拒绝私有、回环、链路本地与保留目标。精确私网设置为 `plugin.sandbox.private_targets.<plugin>`，不接受通配；具体端口仍受该 API 策略限制，配置许可不能扩大 API。

运行中私网撤权进入调用授权复核，提交前继续复查；新增许可不扩大正在执行的作用域。网络权限意味着插件可向批准网络发送自身已获得的数据，不能把隔离当作数据不会外发的承诺。S3 工具链仍需独立真实验收，不能把普通 HTTPS 成功当作 S3 已验收。

### 文件上传 / File uploads

`upload_file` 面向预签名 HTTPS PUT。调用示例：

```python
from core.sdk import upload_file
result = upload_file(project, "build/web-mobile/index.html", presigned_url, expected_sha256)
```

宿主先在独立工作目录复制快照并核对完整 SHA256，再分块发送，不把文件塞进 JSON RPC。
使用 HTTPS/443、检查后的数字地址、TLS 证书校验和原查询参数；不重定向、不使用环境代理。
自动发送 Content-Length 和 Content-MD5；签发预签名 URL 时须兼容这两个实际请求头。
只返回 status、bytes、sha256，不回传服务器正文、签名 URL 或凭据。
权限在等待、发送、清理后继续复核，撤权/超时/取消回收本次 worker；已经抵达服务器的
字节不能撤回，失败后应通过对象存储状态核查再决定重试，不自动重试 PUT。
HTTP 2xx 不等同于独立验证了远端存储的 SHA256；返回摘要表示经本地核对的发送内容。
目前不支持分片上传、跨文件事务、自动签发 URL 或上传整个目录；插件逐个调用固定接口。
开始和完成摘要支持中英文，只展示目标主机，不输出签名查询参数。

`upload_file` targets presigned HTTPS PUT. The host copies a private snapshot and validates
the complete expected SHA256 before streaming chunks; payloads never enter JSON RPC.
HTTPS/443, checked numeric addresses, TLS verification and the original query are preserved;
redirects and ambient proxies are refused. Content-Length and Content-MD5 are supplied by
the host, so the presigner must allow those exact request headers. Results contain only
status, bytes and sha256, never response bodies or signed URLs. Revocation, cancellation
and deadlines stop the owned worker; bytes already delivered cannot be recalled. Inspect
remote object state before retrying an ambiguous failure; PUT is not automatically retried.
A 2xx result is not independent verification of the stored object's SHA256. The returned
digest describes locally verified outgoing content. Multipart uploads, cross-file transactions,
URL signing and directory uploads are not included. Plugins invoke the bounded file API
per object. Bilingual progress summaries display only the destination hostname.

SDK 源码摘要已因新增接口改变，已有插件须重新 prepare；API2 的旧接口保持兼容。
工作目录逻辑预留同步调整为每项 512MiB，最多 8 项，总预留上限 4GiB；包含保留目录，
仍不是文件系统磁盘硬配额。上传快照持有实际 worker 租约，清理与恢复使用同一身份记录。

The SDK source digest changed; existing plugins must prepare their runtimes again. Existing
API2 calls remain compatible. Workspace logical reservation is now 512MiB per workspace,
up to eight workspaces / 4GiB, including retained workspaces. This is not a filesystem hard
quota. Upload snapshots hold a worker lease and share identity-bound cleanup/recovery.

## 迁移与验收

更新中断恢复：在“插件管理 → 恢复中断更新”选择记录并确认，或执行：

```text
opskit plugin inspect-updates
opskit plugin recover-update TRANSACTION --record-sha256 REVIEWED_RECORD_SHA256
```

检查命令不修改文件，也不创建不存在的插件目录。使用检查输出的完整事务 ID 和记录 SHA256；恢复会排除活动插件与安装发布，核对父目录、工作目录、旧版本及候选目录身份和内容摘要。仅恢复记录中的旧版本，独立写入者或已修改的备份不会被覆盖。原安装来源/版本/提交基线恢复，但执行信任和服务授权不恢复，需要重新审核授信。所有恢复证据与撤下的候选保留；重复恢复是幂等的。未知、损坏或没有身份记录的历史目录需人工核查，不按名称自动删除。该协议针对进程退出中断，不宣称 Windows 目录元数据在断电后的持久性。

Interrupted updates can be reviewed under **Plugin management → Recover interrupted update**, or with the commands above. Review the exact transaction and record SHA256 first. Recovery restores only the identity- and content-bound previous version, retains all evidence, and never overwrites an independent writer. It restores acquisition metadata but leaves execution trust and service grants revoked. Unknown or damaged directories require manual inspection. This is process-crash recovery, not a power-loss durability guarantee.

旧插件将 CLI 放回自身包；数据改用自身状态目录；共享 pip 改为离线运行时准备；对象共享改为获批 JSON RPC；宿主文件和命令改为固定能力请求。备份后人工迁移自己的必要状态，不自动注入旧宿主数据，不把凭据和运行数据放入 Git。

Windows 验收需覆盖无 Docker 的准备/安装/运行、拒绝与撤权、更新、取消/超时、状态回滚、宿主访问拒绝、真实构建与上传。Linux/macOS 后端未实现期间必须明确拒绝。源码测试、冻结包、实际 Creator 和 S3 是不同证据，不能互相替代。完整未完成清单见迁移文档。

### 本机 Creator 配置 / Local Creator configuration

在“插件管理 → 配置 Creator 工具”保存本机软件路径，或使用：

```text
opskit plugin configure-creator creator-3-8 "D:\cocos\creator\editors\Creator\3.8.7\CocosCreator.exe"
opskit plugin creator-tools
opskit plugin remove-creator creator-3-8
```

路径保存于当前 OpsKit 数据目录的 `config/common.yaml` 中 `host_tools.creators`，不属于游戏工程或插件清单，不应提交其他机器的绝对路径。
支持多个命名工具，名称以小写字母开头，可含数字、下划线、连字符，最长 64 字符，最多 32 项。
插件可调用 `creator_build("@creator/creator-3-8", project, {"platform": "web-mobile"})`；原绝对路径调用仍支持。
保存配置只做路径校验，不启动软件、不授予执行权限。实际调用仍核对软件内容摘要、项目和选项并请求授权；执行期间工具名指向变化会拒绝继续。
移除配置不会卸载软件或撤销历史授权；授权记录在权限管理中独立检查和撤销。

Save local paths using **Plugin management → Configure Creator tools** or the commands above.
Paths live in `host_tools.creators` in the active OpsKit data directory's `config/common.yaml`,
separate from game and plugin configuration. Do not distribute machine-specific absolute paths.
Up to 32 named tools are supported. Names start with a lowercase letter and may contain lowercase
letters, digits, underscores and hyphens, up to 64 characters. Plugins use
`creator_build("@creator/creator-3-8", project, {"platform": "web-mobile"})` or an explicit path.
Saving validates the path without executing the application or granting permission. Calls still
bind authorization to the executable digest, project and options; changing the named path during
execution is refused. Removing a configuration neither uninstalls software nor revokes historical
grants; inspect and revoke those separately in permission management.
