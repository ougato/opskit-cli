# 旧插件升级到原生沙盒 / Native plugin upgrade guide

接口以 [plugin-spec.md](plugin-spec.md) 为准；验收进展见
[plugin-native-sandbox-migration.md](plugin-native-sandbox-migration.md)。
当前 Windows 后端可运行；Linux/macOS 尚未实现，会明确拒绝，不直接在宿主运行插件。

The specification defines the API. Windows is implemented; Linux/macOS reject execution
until their isolation backends are implemented. There is no unsandboxed fallback.

## 迁移顺序 / Upgrade steps

1. 备份插件自有配置及状态，保留旧源码。检查清单 `api_version: 2`，仅依赖 `core.sdk`。
   Back up plugin configuration/state and source. Use API version 2 and only the public SDK.
2. 删除 Docker 镜像、WSL 路径、宿主 Git/SSH、直接宿主目录读写及任意 shell 的依赖。
   Remove assumptions about Docker, WSL, host Git/SSH, host filesystem access and arbitrary shell commands.
3. 准备审核过的独立 Python、普通 wheel 文件和完整 SHA256 依赖锁。
   Prepare a reviewed standalone Python, ordinary wheel files and a complete SHA256 dependency lock.
4. 使用正式 `prepare`；核对插件来源与内容指纹后另行授信。准备成功不代表允许执行。
   Prepare first, then independently review and trust the exact plugin fingerprint.
5. 分别在中文、英文运行成功/失败/取消流程，检查状态提交、失败回滚及撤权。
   Verify both languages, success/failure/cancellation, transactional state and revocation.

```powershell
opskit plugin prepare C:\plugins\game_release --python-runtime C:\reviewed\python --wheels C:\reviewed\wheels --lock-file C:\reviewed\requirements.lock
opskit plugin show game_release
# 从上一步详情核对完整指纹，不要把下面占位符当成真实摘要。
# Review the full fingerprint from show; the next value is a placeholder.
opskit plugin trust game_release --fingerprint REVIEWED_FULL_SHA256
opskit plugin run game_release -- --help
```

如需通过 Git 安装插件，先按规范执行 `prepare-fetch`；不要在插件内部调用宿主 Git。
For Git acquisition, prepare the separate fetch toolchain described in the specification.

SSH URL 支持 `ssh://git@host:2222/team/repo.git` 和 `git@host:team/repo.git`。
安装目录默认取仓库路径（此例为 `team-repo`），可用 `--as` 指定；插件命令仍按
清单中的 `name` 查找。私网默认拒绝，需要明确 `--allow-private`；私钥、口令、
固定主机公钥由私有 `--fetch-profile` 文件提供，更新时也须显式提供，不复用环境凭据。
Both full SSH URLs and SCP notation are supported. The default installation folder
comes from the repository path; `--as` overrides it. Commands address the manifest
name. Private endpoints require `--allow-private`. Supply an explicit private fetch
profile with the key, passphrase and pinned host public key for installation and
updates; ambient credentials are not reused.

## API 替换 / API replacements

| 旧方式 / Previous assumption | 原生方式 / Native replacement |
| --- | --- |
| 在插件中启动宿主 Creator / Launch host Creator directly | `creator_build("@creator/creator387", project, options)` |
| 读取任意宿主文件 / Read arbitrary host files | `read_host_file(directory, relative)`，先获得精确目录授权 / exact directory consent required |
| requests 直连 / Direct network access | `https_request(...)`，清单声明 `network` / manifest network permission required |
| 整文件塞进 JSON 上传 / Upload bytes in JSON | `upload_file(directory, relative, url, sha256)`，宿主流式快照上传 / host snapshot streaming |
| 插件自行读取大宿主文件计算摘要 / Read a large host file to hash it | `artifact_info(directory, relative)`，只返回大小及 SHA256 / size and SHA256 only |
| 写公共平台配置 / Write platform configuration | `plugin_data_dir(plugin_name)` 下的插件状态 / plugin-owned transactional state |
| 执行时 pip install / Install dependencies at runtime | 更新离线锁及 wheel 后重新 `prepare` / reviewed offline dependencies and prepare |

`run` 等接口只运行于隔离环境，不因此取得宿主程序访问权。
`ensure_software` / `software_bin` 不授予插件宿主软件安装或执行能力。
宿主目录写入尚未提供通用 SDK，不要用 shell、链接或其他 API 绕过。

Process helpers stay inside isolation. Software helpers do not grant host installation
or execution. General host-directory writes are not implemented; do not emulate them
through shell commands, links or another API.

## Creator 配置和授权 / Creator configuration and consent

Linux/macOS 后端实施边界与验收要求见 [原生后端接入](native-backend-porting.md)。
See [native backend porting](native-backend-porting.md) for Linux/macOS extension contracts.

```powershell
opskit plugin configure-creator creator387 "D:\cocos\creator\editors\Creator\3.8.7\CocosCreator.exe"
opskit plugin creator-tools
```

工具名称与路径保存到宿主 `config/common.yaml` 的 `host_tools.creators`；配置不是授权。
插件使用别名避免硬编码每台机器路径。首次调用由宿主询问本次、永久或拒绝。
授权绑定插件指纹、工具内容 SHA256、项目、平台及 debug。永久授权可在插件权限菜单
或 `plugin permissions list` / `plugin permissions revoke ID` 撤销。

Tool aliases live in host configuration, separately from capability grants. Consent binds
the plugin fingerprint, tool hash, project and build options. Inspect/revoke persistent
grants in the permission menu or CLI. Once grants expire with the invocation.

Creator 及项目构建脚本以当前用户权限运行，可访问项目之外的文件；工具代理不是
Creator 的目录沙盒。只对已审核项目授权。当前 options 支持 platform/debug；
成功候选退出码为 0 或 36；还必须通过新产物检查，插件检查 `success`，
不要将所有非零值解释成失败。每次输出到工程的 `build/opskit-平台-随机ID`，
不覆盖旧输出；成功结果的 `artifact` 返回 directory、files、bytes，后续摘要与上传
使用这个 directory，不要再硬编码 `build/web-mobile`。
授权增加输出策略绑定，已有工具授权须重新确认。检查上限为 2 GiB、65536 条目、
64 层目录；拒绝链接，网页构建还要求非空 index.html。这是完成后检查，
不限制 Creator 写入期间的磁盘占用，也不证明原生平台已产出可安装包。

Creator and project scripts run with user privileges and can access files outside the
project. Review the project before granting access. Check `success`; return codes 0 and
36 are success candidates; fresh output must also pass inspection. Options currently
support platform and debug. Each run writes to `build/opskit-PLATFORM-RANDOM_ID` and
returns `artifact` with directory, files and bytes. Use that directory for subsequent
hash/upload operations instead of a hardcoded build path. The output policy is bound
to consent, so previous tool grants require confirmation again. Post-build inspection
limits are 2 GiB, 65536 entries and 64 directory levels, with no links and a nonempty
index.html for web builds. These are not write-time disk quotas or proof of a native
installable package.

## 多语言示例 / Localization example

```python
from core.sdk import register_locale, t, creator_build

register_locale({
    "zh": {"game_release": {"done": "构建完成", "failed": "构建失败"}},
    "en": {"game_release": {"done": "Build completed", "failed": "Build failed"}},
})

def cli(argv):
    result = creator_build("@creator/creator387", argv[0], {
        "platform": "web-mobile", "debug": True,
    })
    print(t("game_release.done" if result["success"] else "game_release.failed"))
    return 0 if result["success"] else 1
```

不要重设 worker 默认语言。菜单、帮助、进度、结果和错误使用同一语言设置；
中英文键及插值参数一致。第三方日志保留原文，给出本地化摘要。
CLI 入口返回整数 0..255 或 None；不要返回 Creator/upload 的结果 dict。

交互式 Creator、摘要和上传任务会显示当前语言的取消提示，Windows 可按 Esc 或
Ctrl+C。取消会回收进程且不提交本次插件状态，但不会删除已产生的宿主构建文件，
也无法撤回已发送的上传字节；插件不要宣称“取消后所有外部操作已还原”。

Inherit the host language. Localize menus, help, progress and errors using matching keys
and placeholders. Keep vendor logs unchanged with localized summaries. A CLI entry must
return an exit code (0..255) or None, not an SDK result dictionary.
Interactive host tasks display cancellation hints. On Windows, Esc/Ctrl+C stops the
task without committing plugin state; existing host build files and transmitted bytes
are not undone. Do not claim that cancellation rolls back external side effects.

## 上传边界 / Upload boundaries

上传使用 HTTPS PUT 和预签名 URL，需要 `network` 及精确源目录读取授权。
使用 `artifact_info(directory, relative)` 在同一目录授权下获取大小及 SHA256，
再将摘要传给 `upload_file`；宿主在上传快照时重新校验，文件变化则拒绝。
两者使用同一精确目录读取权限；一次授权在同一调用中复用，无需重复弹窗。
插件不能因为知道路径就自行读取宿主文件。
单文件最大 512MiB，源文件会先复制校验再发送；不自动重试、不自动撤回已发字节。
预签名 URL 是秘密，不写入日志或长期状态。2xx 不等于独立验证远端对象摘要。
摘要接口同样只支持普通、无链接文件，最大 512MiB；可撤权、有执行期限，不返回文件内容。

Uploads require manifest network permission and exact source-directory consent. Supply
a host-computed artifact SHA256 from `artifact_info`. It shares the exact directory grant
with upload and returns only size/hash, up to 512MiB. Files are snapshotted and checked
again before PUT; changed bytes are refused.
There is no automatic retry/rollback of transmitted bytes. Keep signed URLs secret.
A 2xx response alone does not independently prove the remote object checksum.

运行时、源码或语言资源改变后重新 prepare；内容变化后的旧信任不自动续用。
遇到恢复记录时先检查详情，使用精确记录摘要恢复，不手动删除未知临时目录。

准备过程被强制中断后，可在“清理闲置原生缓存 → 中断的运行时或工具准备”中
查看并确认，或运行 `plugin inspect-native-cache prepare`，再用
`plugin clean-native-cache prepare ID --record-sha256 SHA256` 清理。
活动任务会阻止清理；旧版本没有归属记录的 staging 目录不自动接管。

Prepare again after SDK/source/locale changes. Changed plugin content needs fresh trust.
Inspect recovery records and confirm their exact digests instead of deleting unknown directories.
For interrupted preparation, select the preparation category in native cache cleanup,
or inspect/clean catalog `prepare` with the exact ownership digest. Active preparations
are protected. Legacy staging directories without receipts are not adopted.
