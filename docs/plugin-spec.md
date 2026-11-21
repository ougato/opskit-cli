# OpsKit 插件规范（SDK 4，原生沙盒）

## 平台与边界

插件独立开发，放在 `plugins/<repository>`；平台的 `plugin/` 是官方生命周期模块，不是第三方插件目录。插件不得要求修改平台入口、注册表或共享依赖。

Windows 使用 AppContainer 与 Job Object。插件在隔离验证完成后才执行，无直接宿主网络能力。Linux/macOS 共用后端契约，当前未实现，明确拒绝执行；不得降级为普通宿主进程。插件系统不再使用 Docker 或 WSL，独立 Docker 软件管理不受影响。

当前迁移状态和未完成验收见 [原生沙盒迁移](plugin-native-sandbox-migration.md)。本文描述当前接口，不代表冻结包、崩溃恢复、硬资源预算、Creator 或 S3 发布链路已全部验收。

开发规则、菜单示例和 SDK 完整导出目录见[插件开发指南](plugin-development.md)；旧插件迁移步骤见[原生升级指南](plugin-native-upgrade-guide.md)。
For migration steps and bilingual SDK examples, see the native upgrade guide.

SDK 多选菜单每页最多显示 9 项，按 `n` 或 `p` 翻页时保留勾选状态。

## 清单与入口

```yaml
name: hello
version: 1.0.0
api_version: 4
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

Windows x64 的安装入口自动准备独立采集工具和 SDK 运行环境，无需先手动执行准备命令：

```text
opskit plugin install https://git.example.com/team/hello.git --branch main
```

菜单流程为：输入来源 → 自动准备采集工具 → 受限传输及隔离审核 → 自动准备并校验运行环境 → 展示版本、权限并确认信任 → 安装成功。拒绝信任或在确认时取消，按本次安装凭据撤回目录；不能证明目录归属时保留并报告。命令行安装同样自动准备，但保留显式授信语义，输出的 trust 状态不会被自动改成已授权。

自动准备优先复制本机可用的 CPython 3.10+ Windows x64 与 Git 工具，在 AppContainer 中探测后才发布采集 catalog；本机安装目录不会被修改，也不会直接执行宿主包装脚本。采集 Python 与插件执行运行时分开：插件运行时使用锁定的 CPython 3.14 Windows x64，以匹配 SDK wheel ABI。只对缺少、不兼容或自检失败的组件使用随发行包固定的 `core/plugin-bootstrap.json` 补齐，固定 Python、MinGit 和 SDK wheel 的来源、版本、大小与 SHA256；运行时不查询 GitHub releases API、不运行宿主 pip、安装脚本或仓库代码。下载缓存先校验。传输 pack 在隔离 Git 中校验与导出。首次下载失败可重新安装，已完成且校验通过的组件会复用；失败不会发布半成品插件。正常界面只报告检查环境、必要补齐、获取仓库和准备运行环境，不逐项打印压缩包或依赖文件名。

SSH 验证准确主机/端口的本机 known_hosts 后，受控尝试 SSH agent 和默认私钥；不自动接受未知服务器，不执行 SSH config 的命令。HTTPS 认证被拒绝后可使用 Windows 凭据管理器中的精确 Git 目标。没有可用认证时，菜单请求显式凭据文件；命令行可通过 `--fetch-profile` 提供。安装菜单中用户亲自输入的仓库地址允许本次连接该主机和端口的私网地址；更新已有来源时须明确确认，命令行须指定 `--allow-private`。许可均不持久保存。

`opskit plugin fingerprint PATH` 生成 CHECKSUMS.yaml，用于内容校验。首次安装时确认来源、权限和内容指纹。批准后即信任该安装来源在记录分支上的后续已校验更新。框架完成发布验证后续接新指纹和永久目录授权（保留原权限和路径范围），更新菜单不会再次打开信任确认。来源改变、已撤销信任、内容不符或远端版本低于本地时拒绝更新，保留当前安装。旧框架已清空指纹的安装可在核对当前指纹后用 `opskit plugin trust <插件> --fingerprint <指纹>` 恢复信任。单次授权随调用结束失效。其他宿主能力继续绑定具体指纹。跨插件服务授权的续接规则见服务授权章节。

更新排障可运行 `python main.py --output json plugin update --all`，逐项检查更新状态和失败数。插件正在其他任务或进程中运行时，更新返回插件忙碌提示，等待调用结束后重试，不删除锁文件或强制替换。工作区容量不足、占用和分配失败会分别提示；分配失败时，OpsKit 日志记录失败阶段、异常类别及系统错误码，不记录凭据和原始异常文本。分配结果无法确认时保留现场，不按目录名强制清理。

## 高级离线准备

自动安装包含 SDK 基础依赖。具有额外依赖的插件仍需维护者提供经过审核的完整离线 wheel 和哈希锁；不会执行仓库的 requirements、setup.py 或自动安装任意依赖。以下命令保留给高级离线准备和维护使用，普通安装不需要手动执行。

```text
opskit plugin prepare-fetch --python-runtime C:\tools\python
```

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

`artifact_batch_info(directory, relatives)` 使用同一目录读取授权和同样的文件校验，按请求顺序返回不同相对路径对应的大小和 SHA-256。每批最多 `HOST_ARTIFACT_BATCH_LIMIT` 个路径，当前为 512。整批读取失败时不返回部分摘要。

`artifact_info` returns only host-computed file size and SHA256 under explicit directory
consent (ordinary files, up to 512MiB). Upload verifies its snapshot against that digest.
Both operations share the same scoped grant; once consent is reused within the invocation.

插件通过 SDK 请求能力，不直接扩大隔离目录：

- `read_host_file(directory, relative, *, missing_ok=False)`：显式目录读取授权，单文件最多 1MiB，拒绝越界与链接。目录以 `~/` 开头时由宿主按当前用户主目录展开，仍对具体目标目录授权。仅在指定 `missing_ok=True` 且文件缺失时返回 `None`。
- `read_host_files(directory, relatives)`：复用同一目录读取授权和路径校验，接受 1～64 个不同相对路径。每个文件最多 1 MiB，整批最多 8 MiB，按请求顺序返回字节。任一文件无法读取时整批失败。
- `compare_write_host_file(directory, relative, expected_sha256, content)`：Windows 宿主现有普通文件的有界写入（最多 16KiB）。可使用显式目录读写授权；否则写入授权绑定文件路径及原、新内容摘要。独占打开后再次比对原始内容，再写入并读回验证；普通异常、撤权或取消时尝试在同一独占句柄内恢复原内容。拒绝链接、硬链接和路径越界。此接口不是断电原子事务，进程被强制终止或断电时可能需要人工检查文件；插件应在写入前明确说明并安排恢复流程。
- `create_host_file(directory, relative, content)`：经目录读写授权或精确文件路径和内容摘要授权后创建新文件，最多 16KiB；只接受单级文件名，使用同目录临时文件并以不覆盖方式发布。授权撤销或普通错误时清理属于本次调用的文件，强制中止时临时文件可能遗留。
- `host_artifacts("remove_verified_file", {directory, file, sha256})`：支持 `HOST_ARTIFACT_REMOVE_FILE` 的 Windows 宿主在目录读写授权下删除摘要匹配的单个普通文件，最多 1 MiB。宿主拒绝链接，删除前复核授权、文件身份和内容。普通错误时尝试恢复原路径。
- `host_file_status(directory, relative)`：经目录读取授权后返回单级普通文件是否存在和 SHA-256；文件最多 16KiB，拒绝链接和硬链接。
- `host_path_info(path, directory=False, write=False, execute=False)`：授权后验证绝对普通路径与目录身份。仅目录可传 `write=True` 显式申请包含子目录的读写授权；同时传 `execute=True` 可显式申请该目录树的读写和工具执行授权。该授权允许工作目录在目录树内的工具以当前用户权限运行，工具能够访问目录外文件；撤权、指纹及宿主身份检查仍有效。旧的读写授权不会升级为执行授权。
- `host_find_executable(name, preferred="")`：Windows 上依次查找可选已配置路径、宿主 PATH、相邻的 `cmd`/`bin` 目录和常见安装目录中的同名 `.exe`；仅接受简单可执行文件名，跳过相对路径、链接、硬链接、超出执行大小限制及无效路径，不执行程序。返回绝对路径前请求目录读取授权并复核身份；查找 `adb.exe` 时也检查 `ANDROID_HOME`、`ANDROID_SDK_ROOT` 和 Android SDK 默认安装目录。找不到时返回空字符串，拒绝授权和取消不会自动继续搜索。执行仍需独立授权。

```python
from core.sdk import host_find_executable

previous_git_path = ""  # Or a previously saved absolute path.
git = host_find_executable("git.exe", previous_git_path)
if not git:
    # Ask the user for a path in an interactive workflow.
    pass
```

The Windows host finds an ordinary executable in the preferred location, PATH or conventional install directories. Directory consent is required before returning a match; execution requires its own authorization. An empty string means no candidate was found.
- `host_file_version(path)`：在 Windows 经所在目录读取授权后检查普通可执行文件的固定版本信息，返回四位文件版本和产品版本；接口不运行可执行文件。
- `host_asar_directories(directory, relative, member)`：经所在目录读取授权后，列出 ASAR 压缩包中指定目录的直接子目录名称；不返回归档文件内容，目录项、名称及 ASAR 头均有上限并拒绝链接。
- `list_host_files(directory, *, missing_ok=False, subdirectories=None)`：经目录读取授权后，递归返回排序后的普通文件相对路径，路径使用 `/`。可用 `subdirectories` 指定最多 32 个直属子目录，路径仍相对于 `directory` 返回。启用 `missing_ok` 时跳过尚未创建的目录。拒绝链接、重解析点、硬链接和特殊文件。单次最多遍历 65,536 项，每个子树最多 64 层，返回的路径 UTF-8 总长度最多 1 MiB。文件内容和摘要分别通过各自的 SDK 接口读取。
- `host_execute(executable, arguments, directory, options=None)`：Windows 宿主工具执行，绝对 `.exe` 路径、独立参数数组、绝对工作目录；不经过 shell。返回原始 `returncode`。
  SDK 提供 `finish_host_tool_output(success)` 时，可设置 `defer_output: true`。宿主保留本次插件调用的工具输出，进度仍实时更新。等业务校验完成后传 `true` 丢弃输出，或传 `false` 在终端按原顺序回放。中断或调用退出时，未处理的输出由宿主回放。此选项与 `capture_output` 互斥。
  SDK 提供 `HOST_TOOL_STAGE_ONLY_PROGRESS` 时，可同时省略 `progress_marker` 和 `progress_label`，仅显示阶段动画和阶段结果。
  SDK 提供 `HOST_TOOL_COMPACT_DIAGNOSTICS` 时，可用 `quiet_prefixes` 省略已知的提示行，用 `compact_diagnostics` 将匹配字面标记的提示去重并收起 JavaScript 调用栈。
  当 SDK 提供 `HOST_TOOL_PROGRESS` 时，可传入 `progress` 描述实时输出。其字段为 `stages`（首项为 `[名称]`，后续为 `[名称, 开始标记]`，按顺序匹配行内字面文本）、`progress_marker`（后接整数和百分号的字面标记）、`progress_label`、`failed_label`、`diagnostics`（按行匹配的诊断字面标记，忽略大小写）和 `success_codes`（展示阶段完成状态的退出码）。终端显示当前阶段的动画与工具报告的进度；诊断行和其缩进续行保留原文。非交互输出保留阶段结束行与诊断，不重绘。此选项仅影响展示，与 `capture_output` 互斥，不改变授权范围或返回的工具退出码。
  SDK 的 `with progress_step(message, failed_label):` 为无细分百分比的插件阶段使用同款安装进度动画。正常退出显示 100%，异常退出显示失败状态。支持 `HOST_COUNTED_PROGRESS` 时，可以传入 `total=正整数` 并使用上下文返回的回调上报累计完成量。正常退出且完成量达到总数才显示 100%。
  options 支持 `output_parent`、布尔值 `capture_output`、`capture_after`、`tool_dependencies`、`stdin_base64`、`config_base64`、`timeout_seconds`、`output_header`。`output_header` 是可选的简短面包屑字符串列表，仅交互终端在授权完成并启动工具前清屏显示标题；重定向和非交互输出不清屏，且它不改变授权范围。`output_parent` 创建独立输出目录，并将参数中的 `{output_directory}` / `{output_name}` 替换为本次路径/名称，返回 `output: {id, directory}`，仅为待登记凭据。执行授权记录原始参数模板和输出父目录，而不是每次生成的随机目录名；模板以外的参数变化仍需要重新授权。`capture_output=true` 将合并的标准输出和标准错误作为 `captured_output` 返回插件（最多 16KiB，超限失败），不再流式显示；该选项纳入执行授权范围。`capture_after` 是最多 128 字符的单行标记，正常流式显示仍保留，且仅当标记恰好出现一次并有下一行时，将下一行作为 `captured_after` 返回（最多 16KiB，否则为 null）；标记纳入执行授权范围。`stdin_base64` 可提供最多 16KiB 的标准输入，执行授权绑定输入内容的长度及 SHA-256，默认不开放输入。`config_base64` 可提供至多 16KiB 的临时工具配置；必须在参数中使用 `{config_file}`，宿主在本次执行期间建立临时文件，正常退出时清理，执行授权绑定内容长度和摘要，参数模板保持稳定。宿主被强制终止时临时文件可能残留，调用方不可将秘密写入此配置。`timeout_seconds` 是 1..3600 的整数，默认为 300，显式值纳入授权范围。外部进程在独立的有限期限内运行时不消耗插件计算期限，但仍持续检查插件授权、工具授权和资源身份；超时或撤权会回收进程树。`tool_dependencies` 是至多 8 个普通绝对 `.exe` 路径，其父目录加入工具的 PATH。调用 `java.exe -jar <绝对路径>` 时也可绑定该命令中的同一普通 JAR 文件，JAR 所在目录不加入 PATH；授权包含可执行文件路径、摘要和搜索目录，并在启动前重验文件。目录内其他程序也可能通过 PATH 被调用，故授权展示完整搜索目录。默认仍流式显示，不向插件返回内容，也不额外添加搜索目录。
- `host_artifacts(action, parameters)`：插件隔离的产物凭据管理。`mkdir` 使用 `{directory}`，只允许在已存在的普通父目录下创建一级目录；`commit` 使用 `{id, required_files}`，要求本次目录非空、必需文件非空；可选 `expected_files` 将插件已验证文件的 SHA-256 与实际快照绑定。仅对宿主执行创建的 `opskit-{id}` 待登记目录，登记操作请求输出父目录的写入授权并验证目录及父目录身份，因而不受随机目录名影响；导入和删除仍请求具体产物目录的写入授权。`list` 使用 `{resource, keep}`，返回有效性、保留保护、清理候选和宿主计算的 `record_sha256`；`clean` 使用 `{id, record_sha256, keep}`，只删除自身有效且不受保留保护的凭据目录。
  `keep` 为 1..100。快照最多 2GiB / 65536 条目 / 64 层；拒绝链接、硬链接、身份或内容变化、未知目录和活动资源锁。执行和凭据操作共享工作目录资源锁。失败输出保持待登记，不自动清理。
  显式迁移使用 `adopt`：`{resource, directory, snapshot, required_files, completed_ns}`；snapshot 包括 `identity`、`parent_identity`、`inventory_sha256`、`bytes`，宿主重新核验全部字段后才接管，不能用插件提交的摘要直接删除目录。
- `https_request(url, method, headers, body)` 是受限 HTTPS RPC，需要 network 权限。请求与响应各限 1 MiB，返回 `status`、`body` 和 `etag`。响应没有有效的强 ETag 时，`etag` 为 `None`。
- `upload_files(directory, files, *, publish_last=0)`：通用批量上传，最多 4096 项且受 RPC 大小限制。每项为 `[relative, put_url, sha256, headers, get_url_or_none]`，结果按输入顺序返回。尾部 `publish_last` 项等前一组校验通过后才开始。宿主限制并发，统一显示已完成字节的百分比和最近两秒实际发送的正文速率。提供 GET 地址时，回读校验成功才计入进度。失败后已上传的对象可能保留。
- `upload_file(directory, relative, url, sha256, headers=None, *, verify_url=None)`：单文件 HTTPS PUT，最多 512MiB；`verify_url` 可传入同一对象的签名 GET URL，宿主流式回读并核对大小和 SHA-256。已有相同内容时复用对象；
  同时要求 network 权限和宿主目录读取授权，必须给出待上传内容的 SHA256。

宿主工具按用户权限执行，并可能执行工程脚本；授权界面必须说明此边界。默认命令授权绑定插件指纹、工具摘要、参数、工作目录及输出范围。支持撤权、取消、超时、进程树回收和日志限额。通用接口不判断特定工具成功码或构建平台。

插件可通过 `authorization_scope: "tool"` 请求更宽的工具授权：绑定插件指纹、工具路径和摘要，允许任意参数及工作目录，须经宿主明确展示并由用户授权；不支持 `output_parent`、`config_base64` 和 `tool_dependencies`。默认仍按具体命令授权，原有授权不会自动扩大。

授权菜单由框架统一提供，显示本次授权、永久授权和返回；返回或按 Esc 取消本次请求，不保存拒绝记录，下次申请可再次提示。插件只提交要执行的操作与目标，不能决定或写入授权选项。永久授权绑定内容身份与所展示的范围，可通过菜单或 `plugin permissions list` / `plugin permissions revoke ID` 查看和撤销。非交互模式没有授权时明确拒绝。

目录读取授权覆盖所选目录及其全部子目录，原有同插件、同指纹的目录读取授权也按此规则复用；按路径组件识别范围，不包括同名前缀的兄弟目录。目录读取不会自动授予写入；显式申请的目录读写授权覆盖同一目录树内的受控读取、文件写入及产物操作，但不包含工具执行。显式的目录读写及执行授权也覆盖该目录树中启动的宿主工具，工具可访问项目外文件；旧读写和逐文件授权不会自动扩大。宿主继续拒绝越界路径、链接和重解析点。撤销父目录授权后，子目录若无其他有效授权将立即失效。

框架选择、确认和输入界面复用逐行信息模板：`select`、`select_input`、`confirm` 和 `text_input` 都接受 `info_lines`，多行内容逐行显示；`description` 也使用同一行样式。其中选择及输入行按纯文本显示，`confirm` 保留 Rich markup 语义，动态内容需转义。插件业务界面的面包屑应保留实际入口、已选对象和当前功能上下文；字段名称由输入提示展示，不能用通用根标题替代当前功能。插件可用 SDK 的同名参数呈现业务信息，但只有宿主能力调用才会触发框架持有的权限决策；显示相似菜单不能获得授权。目录授权提示仅展示权限、资源和授权选项，多个权限用 ` | ` 分隔。单独申请执行宿主工具时仍展示用户权限及目录非隔离边界。描述和说明行末尾不加标点，动态内容按原样展示。权限管理详情保留完整指纹和权限 ID。扩展交互须同步宿主实现、SDK、沙盒转发和取消、非交互、中英文测试，不以展示接口代替授权接口。

仅需选择选项时使用 SDK `select` 或 `paged_select`：数字单键立即选择，分页默认使用 `p` / `n` 及框架统一的“上一页”“下一页”；`select` 的快捷键占用翻页键时自动选择其他按键。需要完整文本时使用 `select_input` 或 `text_input`，以回车确认；单键菜单不得提示回车选择。`paged_select` 可用从零开始的 `initial_page` 定位初始页面，不自动确认选项。等待操作使用 SDK `waiting`：宿主统一显示临时彩色点阵，授权和交互时暂停，作用域结束、异常或取消时清除；非交互或重定向时不输出动画。取消和权限监督不受显示方式影响。

中英文是必验项。菜单、授权、进度、结果、错误、帮助及平台未实现提示遵循程序当前语言；新增文案同时加入 zh/en，键和插值参数一致。插件使用 SDK 的 t/current_lang/register_locale 接入自身语言资源。机器错误码、命令、配置和协议字段不随语言改变。第三方工具原始日志可保留原文，但平台提供本地化结果摘要。

## 状态、期限与清理

`plugin_data_dir` 只能访问自身隔离状态。自身状态分区为 `<data>/plugin-data/<name>/self`，服务分区为 `<data>/plugin-data/<provider>/callers/<authenticated-caller>`；调用方由平台绑定，不接受插件伪造。插件不能读取其他分区。

执行使用私有快照和工作区。成功返回不立即提交：先确认进程及工作区清理，再重新检查期限、内容指纹和授权链，最后提交状态。执行、清理、撤权或期限检查失败均不提交。状态上限 16MiB、4096 条目、64 层；扫描额度不等于硬磁盘配额。

嵌套调用共享父期限，不能重新获得完整 timeout。并发状态事务冲突明确失败。`plugin inspect-workspaces` 仅观察，不根据目录名称推断所有权或授权删除。旧容器 reconcile/锁退休命令已经移除；原生恢复只能处理可证明身份与归属的记录，不能用删除任意遗留目录替代。

Windows 原生进程树默认 Job 预算为 256MiB 内存、16 个进程、整机 CPU 总容量的 25% 硬上限。宿主通过公共 `NativeLimits` 契约传入预算，插件不能修改；后续平台后端沿用此契约。所有限制必须在挂起进程恢复前成功设置，设置失败不执行插件。CPU 上限控制速率，整体执行期限另行限制持续时间；目录扫描仍不能代替磁盘硬配额。

## SDK 服务契约

只依赖 `core.sdk` 稳定接口。SDK api_version=4、RPC 传输版本和业务服务版本相互独立。

提供方声明 provides 及 service_contracts，每个契约包含严格整数 api_version（1..65535）及 1..64 个不重复公开 methods，并导出 provide_service(name)。调用方 uses 必须明确 service/provider/api_version/methods；拒绝通配、私有、重复方法和未知授权字段。旧 uses 不自动取得全部方法权限。

操作方核对 `plugin show CALLER` 和 `plugin show PROVIDER` 后，使用：

```text
opskit plugin authorize-service CALLER SERVICE --caller-fingerprint CALLER_SHA256 --provider-fingerprint PROVIDER_SHA256
opskit plugin revoke-service CALLER SERVICE
```

SHA256 必须换为核实后的实际指纹。批准精确绑定调用方、提供方、服务版本与方法集，不执行插件。独立运行授信仍需另行批准服务调用。同一来源的已验证更新会把已有服务授权续接到新指纹，前提是相关服务声明保持一致。更换提供方、变更契约或撤权后需要重新批准。调用和状态提交继续检查整条授权链。

`request_plugin_install(provider: str) -> bool` 是服务提供方唯一面向插件的安装入口。宿主从当前运行身份读取 `uses`，使用宿主登记的来源、分支和最低版本，并在隔离区核对候选指纹、权限和服务契约。用户在同一确认页批准安装、候选授信及指定服务调用边。有效授权直接复用。SDK 调用成功后可在当前操作中获取服务。

get_service 返回 JSON RPC 代理，参数与结果必须可序列化；函数、回调和共享对象不跨边界。循环调用及超过四层嵌套拒绝。宿主先校验授权，worker 在执行点核对快照契约、实际业务版本和公开方法，不在宿主导入提供方。

跨插件菜单通过已声明并授权的 `open_menu` 方法调用。提供方可约定 `action: navigate` 和白名单内的 `destination`，以公开目标标识映射自身菜单。菜单退出后将控制权交还调用方，参数和结果随业务契约记录。调用方身份由宿主绑定。示例见[跨插件菜单导航](plugin-development.md#跨插件菜单导航)。

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
字节不能撤回。提供签名回读 URL 时，瞬时 PUT 故障先回读，若内容一致则完成。回读确认目标不存在且 PUT 带有 `If-None-Match: *` 时，可退避后再次核查并重试，最多发送三次。永久性错误或摘要冲突会终止任务。条件写入的并发保护取决于存储服务是否实际执行该请求头。
HTTP 2xx 不等同于独立验证了远端存储的 SHA256；返回摘要表示经本地核对的发送内容。
插件可用 `upload_files` 一次传入目录的相对文件清单和签名地址。宿主统一显示百分比、完成件数与正文传输速率，签名查询参数不参与展示。暂不支持分片上传、跨文件事务或自动签发 URL。文件上传失败时，宿主只显示相对文件名、失败阶段和有限的错误类型或 HTTP 状态。PUT 结果不明确时只尝试回读核对摘要。

`upload_file` targets presigned HTTPS PUT. The host copies a private snapshot and validates
the complete expected SHA256 before streaming chunks; payloads never enter JSON RPC.
HTTPS/443, checked numeric addresses, TLS verification and the original query are preserved;
redirects and ambient proxies are refused. Content-Length and Content-MD5 are supplied by
the host, so the presigner must allow those exact request headers. Results contain only
status, bytes and sha256, never response bodies or signed URLs. Revocation, cancellation
and deadlines stop the owned worker; bytes already delivered cannot be recalled. Inspect
remote object state before retrying an ambiguous failure; PUT is not automatically retried.
A 2xx result is not independent verification of the stored object's SHA256. The returned
digest describes locally verified outgoing content. Plugins can submit a directory file list and signed URLs through `upload_files`. The host shows progress, completed file count and payload rate. Signed queries are kept out of the display. Multipart uploads, cross-file transactions and automatic URL signing are outside this API. Failures display the relative filename, upload phase and a bounded reason or HTTP status. An ambiguous PUT is resolved only by verifying the object digest.

SDK 源码摘要因接口变化而改变，已有插件须重新 prepare；SDK 1/2/3 插件必须升级到 4 才能安装和执行。旧版插件不进入管理列表，旧目录和数据不会自动删除。
工作目录逻辑预留同步调整为每项 512MiB，最多 8 项，总预留上限 4GiB；包含保留目录，
仍不是文件系统磁盘硬配额。上传快照持有实际 worker 租约，清理与恢复使用同一身份记录。

The SDK source digest changed; existing plugins must prepare their runtimes again. Existing
SDK 1/2/3 plugins must migrate to SDK 4, be reinstalled and prepare their runtime again. Older plugins are excluded from management; their directories and data are not automatically deleted. Workspace logical reservation is now 512MiB per workspace,
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

### SDK 4 迁移

见[插件开发指南](plugin-development.md)和[原生升级指南](plugin-native-upgrade-guide.md)。SDK 版本与 RPC 传输版本独立；本次不递增 RPC 版本。任何第三方插件只能导入公开 `core.sdk`，不得导入框架私有模块。[SDK 3 业务分离记录](sdk3-business-separation.md)仅供历史迁移参考。

交互输入的默认值由框架直接显示，不附加括号。需要让用户直接编辑上次输入或建议版本时，使用 `text_input(..., initial=value)`。


### 输入界面的 ESC 返回

`text_input` 在 ESC 时抛出 `UserCancel`。单步输入由直接打开它的菜单捕获，再重绘菜单。菜单中的 ESC 与「返回」都只离开本层。多步输入用 `input_steps(*steps)`，每个回调接收该字段上一次确认的值，首次进入接收 `None`。后续字段按 ESC 回到前一字段，首字段按 ESC 将 `UserCancel` 交还直接调用方。普通字段用 `initial` 提供可编辑的上一次值，敏感字段不预填明文。Ctrl+C 沿用框架的全局退出行为。完整表单提交前的草稿由调用方管理，已经逐项保存的配置在返回时保留。
