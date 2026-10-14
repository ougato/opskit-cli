# OpsKit 插件规范（SDK API2）

## 独立开发

插件是独立 Git 仓库，clone 到 `plugins/<repository>`，不加入平台仓库。平台 `plugin/` 是官方生命周期模块，`plugins/` 是第三方工作区，不能混淆。插件不得要求修改 `main.py`、官方注册表或平台依赖文件。

```yaml
name: hello
version: 1.0.0
api_version: 2
kind: python
entry: hello_pkg
permissions: []
```

身份为小写字母开头的字母、数字和下划线；`platform` 和平台核心身份保留。入口必须来自插件自身目录，不接受链接或越界路径。可以声明 `permissions: [filesystem, network]`；不接受宿主 root 权限。容器内文件系统始终限制在沙箱，权限标签不能授予宿主目录。

## 插件自己定义 CLI

平台只提供 `opskit plugin run NAME -- ARGS`，`--` 后的子命令、选项、大小写、帮助和退出码都归插件。平台不保存第三方 argparse/Typer schema，也不导入插件以生成全局帮助。全局官方命令保持官方职责。

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

```text
opskit plugin run hello -- --name Alice
opskit plugin run hello -- --help
```

Python 插件导出 `cli(argv)`，返回 0..255 的整数或 None（0）；argparse 的 SystemExit 退出码被适配。exec 插件直接收到 argv，但运行于 Linux 容器，因此需提供 Linux 入口。插件菜单仍通过 `register() -> core.sdk.ModuleInfo` 的入口执行。

## 准备运行时与信任

需要 Linux Docker daemon；Windows 可使用 WSL Debian Docker，发行版可通过 `plugin.sandbox.wsl_distribution` 配置。操作方先审核已有基础镜像的发布方及registry manifest digest，为实际Linux/Python/架构生成完整wheel哈希锁，再明确准备：

```text
opskit plugin prepare plugins/hello --base-image IMAGE@sha256:DIGEST --lock-file requirements.lock --dependency package==1.2.3
# 源码方式等价入口
python scripts/build_plugin_runtime.py plugins/hello --base-image IMAGE@sha256:DIGEST --lock-file requirements.lock --dependency package==1.2.3
```

IMAGE/DIGEST是格式说明，必须替换为已审核的真实引用和64位SHA256；不提供虚构信任值。--base-image和--lock-file必填，标签或缺锁不能回退到旧方式。源码脚本复用同一CLI，不再维护独立解析/错误逻辑。

锁文件每条是`NAME==VERSION`及一个或多个`--hash=sha256:HASH`，可使用反斜杠续行和注释。所有传递依赖都必须锁定；禁止URL、索引配置、-r递归、extras和条件标记。为实际目标生成具体锁，不混入其他平台的条件分支。每包最多256个wheel hash、最多512包、文件最多1MiB，拒绝链接/硬链接/特殊文件。生成输入示例见examples/plugin-runtime-requirements.in，该文件本身不能用作锁；使用经过审核的锁生成工具、核对包版本与发布文件SHA256，再提供给prepare。

SDK根依赖为rich/PyYAML/typer/platformdirs；平台的HTTP、更新签名、遥测和监控依赖不再自动带入运行时。插件所需的额外包和传递依赖由操作方加入同一锁，--dependency只核对所选包是否与锁一致，不追加未哈希requirements。执行插件Dockerfile、任意requirements URL和修改共享venv均不支持。

安装使用pip isolated模式、固定HTTPS PyPI索引、ignore-installed、require-hashes和only-binary；不构建sdist，不允许预装包跳过锁验证，不用no-deps掩盖缺失传递依赖。哈希绑定文件内容，不代表发布方签名、包安全性或漏洞审查通过。镜像digest仍需独立核对可信发布方。

运行时policy为6，catalog schema为2；记录原始base_reference、规范化锁SHA256、image ID、插件指纹及当前SDK代码/资源SHA256，执行时逐项核对镜像label。旧policy及SDK摘要不符的运行时拒绝执行，必须用审核锁重新prepare。锁可在插件独立仓库维护，prepare不扩大授权；构建中插件内容改变不发布新catalog。

构建先安装平台固定的pip26.2通用wheel并核对SHA256，再用它安装SDK/插件审核锁；两阶段都禁用版本自检，只允许带哈希wheel，不执行sdist。pip由平台维护，插件依赖锁包含pip会拒绝，防止覆盖或降级构建工具。policy6要求旧镜像重建；固定版本及哈希不代表独立发布签名、基础镜像OS组件安全或无未知漏洞，实际镜像仍需漏洞审计。

构建只包含core/plugin_runtime_inventory.py列出的23个SDK代码/资源文件，不递归复制平台core，不包含更新器、插件管理器和宿主业务发现模块。本批配置事务新增resource_lock.py实际依赖，SDK摘要改变，旧镜像必须重建。新增SDK文件、语言或主题资源必须显式更新库存并验收。冻结版通过同一库存的plugin-runtime-sources.zip计算摘要和准备运行时；源码与wheel直接捕获固定文件。PyInstaller/Nuitka构建均携带该包，但实际后端验收分别记录，不以模拟测试代替原生构建。

有 network 权限时，prepare 还准备平台独立出口代理。代理仅使用平台固定官方基础摘要与 stdlib 脚本，不接受插件依赖、基础镜像或 Dockerfile；SDK不包含 plugin_egress.py。独立 .gateway.json 库存核对实际镜像ID、标签、来源及代码摘要，缺失或不匹配拒绝执行。冻结包保存 SDK 与代理两份明确库存，构建时分别选取。

专用网络要求 Linux bridge、--internal、--ipv6=false 及 com.docker.network.bridge.inhibit_ipv4=true，避免 internal 桥接仍暴露宿主网关；不支持时拒绝创建，不回退普通桥接或宿主网络。当前实测仅覆盖本机 Linux Docker IPv4；远程daemon、rootless及其他配置仍需独立验收。受控 HTTP 出口复验工具为 scripts/verify_plugin_gateway.py，详见 plugin-gateway-isolation.md。

容器worker和出口代理禁用Docker日志落盘（log-driver=none），防止输出额度之外的daemon磁盘增长；worker输出仍经附着管道读取。单行32MiB、累计64MiB按管道原始字节计数，超量先停止本地客户端，再请求daemon删除，避免满管道阻塞删除。资源恢复机制见下述说明；不能认为禁用日志即可覆盖全部资源风险。

worker、出口代理和专用网络创建前，在宿主plugin-resources目录保存有界意图并持有OS租约，创建使用独立所有权label。清理核对当前backend、daemon身份、label及精确名称，再按完整ID删除并确认不存在；不按prefix扫描删除未知资源。清理失败保留记录并报告恢复未完成，正常结果也不能绕过该错误。插件状态在所有资源成功清理后重新核对期限、授权链与内容指纹，再发布。

官方 `plugin reconcile` 在整体预算内扫描最多256条记录，跳过活动租约，只恢复同backend/daemon上有所有权证明的资源；不会执行记录中的命令。输出recovered/active/pending/invalid计数，pending或invalid时退出1并输出失败JSON。每资源清理查询、删除及确认共享30秒预算。真实Docker容器与网络的活动租约、清理失败保留及恢复已通过；这不替代断电、跨用户权限、完整出口与多租户威胁模型验收。

原生验收工具scripts/verify_plugin_native.py的--lifecycle只运行自己生成并授权的合成插件，在新建隔离目录内验证内存/PID/临时盘/状态/输出耗尽、执行期限、运行中撤权及注入取消。每项核对持久状态、具名容器和daemon事件；内存测试必须看到oom事件。取消注入不等于真实操作系统Ctrl-C验收；此工具不执行克隆的未知插件，也不代表完整RPC/获批出口或daemon故障恢复通过。

同一工具的开发验收--rpc创建一个已知服务提供方和两个已知调用方，核对独立计数状态、平台绑定身份及提供方运行时外层调用方撤权后的状态保留/容器回收。它不是平台或插件业务参数，不覆盖全部方法/API/发布者授权策略、并发多租户或daemon故障恢复。scripts/verify_prompt_native.py另用隐藏Windows控制台或Linux PTY验证输入守卫和终端恢复；Linux Ctrl-C字节与OS信号验收分开记录。

准备只输出固定进度和结果，不公开Docker/pip原始日志（其中可能包含仓库凭据）。镜像查询客户端阶段30秒/4KiB，构建客户端阶段600秒/4MiB输出上限；本地客户端及其子进程在超时/取消/超量时回收。临时文件承载构建上下文输入，不创建后台stdin写线程；失败不发布新catalog。Docker daemon内部构建任务的取消、网络请求及缓存/镜像产物保留仍需真实daemon验收，不能把客户端退出当daemon清理证明。

生成 `CHECKSUMS.yaml`：`opskit plugin fingerprint PATH`。安装后在插件管理菜单核对来源、权限和指纹，再确认信任。更新内容后撤销旧信任，需要重新核对并准备运行时；不会继承授权。校验清单不是发布者签名。

源码指纹/快照发现最多8192个目录条目、64层深度、4096个文件及128MiB实际文件内容；源码提前跳过`.git`与运行缓存目录。plugin.yaml和CHECKSUMS元数据最多1MiB、YAML64层/20000事件，不支持alias展开，手写alias清单请展开为普通字段后重新生成。元数据与快照拒绝硬链接、符号链接和特殊文件。持久状态最多4096个条目（包含空目录）、64层、16MiB，缓存同名目录仍属于用户状态并保留。快照也拒绝junction，文件变更或超额拒绝运行；正常Git检出文件无需调整。

插件库存根目录最多4096个直接条目（隐藏文件和非目录也计数）、256个候选清单（无效清单也计数）。扫描先收集有界目录，再按原目录排序，在解析任何清单前拒绝超额；不返回不完整库存。list/run等CLI超额退出1，菜单报告错误后返回平台，安装无法核对库存时回滚本次克隆。根目录不能是symlink/junction；正常子目录中的链接不参与发现。需要更多插件时应设计经过测量的分页/索引方案，不直接取消扫描额度。

平台配置与信任文件共用core/bounded_yaml.py的读取/解析预算：默认1MiB、64层/20000事件、不允许别名，文件须普通单链接文件且祖先不能是symlink/junction。配置根须字符串键映射；部分更新命令继续采用更小的64KiB读取预算。写入前核对相同解析上限，超额不覆盖原文件；单键配置修改先读取当前文件，原文件损坏则拒绝修改，显式save_config仍是整份替换接口。信任读取失败拒绝执行和服务授权，信任修改失败保留原存储并报错。既有手写YAML锚点/别名需经人工审阅展开后使用；不自动重写真实配置。字节和解析预算不等于阻塞文件系统I/O的硬期限。配置合并不会共享或修改全局默认对象。

执行路径的内容指纹与CHECKSUMS检查使用调用范围内的受监督只读进程，嵌套调用复用同一进程，但每次重新读取/计算内容，返回值仅为内容身份，不是许可。请求通过固定256KiB内存邮箱和事件提交，不使用可能阻塞的父进程管道写入；每次等待取剩余整体期限与300秒较小值，启动前/返回后复核父期限。超时、取消或坏结果关闭读取器；外层作用域结束确认回收，新调用不继承旧读取器。Windows Job与Linux父死亡/存活管道使用同一所有权实现；原生macOS和冻结程序尚待验证。

调用作用域内的实时授权复核也使用同一读取进程，但每次重新读取宿主指定的配置/信任文件，沿用启用、身份和服务边谓词，不缓存许可。请求最多256身份/256服务边，并限制路径/字符串/API整数及邮箱容量；响应仅为固定成功/拒绝原因，不返回文件内容。提交时父进程继续持配置→信任锁等待该次新读取，子进程不申请写锁，返回后核对父期限再发布。详细设计及验收矩阵见plugin-authorization-supervision.md。执行前timeout查询已在读取前建立30秒启动上限，沿用原开始时间计算业务期限并覆盖yield前清理；WSL回退的distribution与代理创建前的当前插件private_targets也使用同一owned reader（作用域外30秒一次性reader），不读取整份设置或其他插件授权；私网列表最多256项、单项512字符、UTF-8 JSON总量16KiB，无network权限不查询。此设置监督的修正后专项双平台各22通过，更广/安装验收仍在运行。作用域外check_authorization、manifest/catalog及状态提交I/O仍未完成整体硬期限设计；已有代理私网列表的实时撤销仍开放，不能据此宣称平台整体硬期限或安全达标。

## SDK 与服务

### 待实施的 RPC 授权契约

本节记录完整实施目标。清单解析、调用边存储、官方批准/撤销命令、宿主路由和worker方法/业务版本校验已接入本地回归；代理查询沙箱describe的实际业务版本。未批准的第三方边和契约外方法在宿主拒绝，监督器持续复查使用过的调用边；嵌套返回或异常被捕获后，依赖保留到外层结束，避免服务边撤销后提交其衍生状态。最终状态切换在配置/信任锁内重查依赖。新worker改变SDK摘要，真实镜像、wheel与完整回归的当前证据必须单独核对，不能只以这些本地检查声称全部RPC安全门槛完成。

操作方先使用opskit plugin show CALLER和show PROVIDER查看双方当前fingerprint及服务契约，再执行opskit plugin authorize-service CALLER SERVICE --caller-fingerprint CALLER_SHA256 --provider-fingerprint PROVIDER_SHA256。批准严格使用调用方清单中的唯一provider、业务版本和方法集合，要求提供方契约支持且双方当前受信任/启用，错误指纹、未知提供方、版本/方法不匹配均拒绝。批准不会执行插件代码；show的service_requirements省略source安装提示，避免将来源凭据回显。opskit plugin revoke-service CALLER SERVICE只撤销该调用边，保留独立执行信任。这些是平台权限管理参数，第三方业务参数仍属于各自cli(argv)。命令参数中的SHA256是格式说明，必须使用实际核对过的值。

目标清单结构：保留provides服务名列表，新增service_contracts映射，每个已提供服务声明api_version（严格整数1..65535，不能bool）及methods（1..64个不重复的公开标识）。uses的受限记录在原有service/source之外包含provider、api_version、methods，三项必须齐全，不允许部分字段被静默丢弃。业务版本7等值不要求等于SDK2。未知授权字段、通配/私有/重复方法、未提供服务的契约和无效类型均拒绝。旧uses语法只保持独立入口的清单兼容，不自动取得新RPC授权。该结构还必须与宿主授权边、worker执行点及SDK描述查询一起实施，解析通过不代表调用已获批准。

1. 分离三种版本：plugin.yaml的api_version表示SDK兼容性，RPC消息自身带平台协议版本，业务服务的service_api_version由提供方声明。业务版本不是平台协议版本。describe返回经类型/范围校验的实际业务版本与明确公开方法集合；拒绝bool、负数、字符串、过额方法和非JSON描述。查询描述也受正常沙箱授权与共享期限限制，不能在宿主导入提供方。
2. 提供方清单声明服务契约，调用方uses声明需要的业务版本及精确方法集合；平台解析时拒绝重复/冲突、非法标识及私有方法，不静默丢弃未知授权字段。第三方调用不接受通配方法；属性、类对象及未声明的可调用成员不能成为RPC入口。worker在执行点核对快照清单、实际对象业务版本及方法可调用性，宿主broker在进入提供方前核对授权。仅describe后call的分离检查不足以防止更新竞态。
3. 操作方批准一条调用边，而不是只批准两个插件独立运行。平台记录调用方名称/内容指纹、提供方名称/内容指纹、服务、业务版本及方法集合；source URL仅作安装提示，不能自报为发布者身份。审批展示当前唯一提供方和权限差异，授权记录在平台数据目录，插件不能直接写入。提供方被另一个同服务插件替换，即使单独已受信任，也不能继承调用边授权。
4. 每次调用从平台绑定caller取身份，拒绝payload覆盖；重新解析双方清单并匹配当前授权指纹。更新、卸载、禁用或撤权使关联调用边失效，不自动扩大方法/版本。授权存储的读改写需跨进程锁和原子落盘，不能沿用无锁的整体YAML读改写而丢失并发撤权。
5. 嵌套调用沿用父期限与整条授权链，每层只使用自己的获批调用边。状态发布与撤权使用同一明确的序列化规则：撤权完成后不能再提交依赖旧授权的状态；不得仅在提交前读一次授权后留下跨进程竞态。长时间持锁导致撤权阻塞需要显式预算与失败语义，不能假装即时撤权。
6. 旧清单可保留独立入口/CLI，但旧uses不能默认获得全部服务方法。迁移工具只生成待审批契约，不推断批准，也不改第三方仓库；未迁移RPC拒绝并给出平台固定的修复说明。官方生命周期命令可管理调用边，插件业务参数仍全部归各自cli(argv)。菜单调用也遵守同一契约，不保留绕过方法/版本检查的open_menu特例。

验收必须覆盖：SDK1/2与业务版本不同的正常调用；无授权/未声明方法/错误版本/提供方替换/伪造caller拒绝；描述与执行间清单或代码变更拒绝；两个调用方的状态隔离；嵌套撤权和并发授权写入不丢失；撤权与提交的真实跨进程竞争；源码、wheel和当前SDK镜像中相同拒绝行为。旧接口失败属于迁移行为，应有明确诊断和文档，不能只删除旧测试获取通过。业务版本7/SDK2、方法与版本拒绝、两调用方分区及嵌套撤权已有真实Docker证据；并发多用户、描述/执行竞态完整矩阵及全部发布产物仍待验收。

仅依赖 `core.sdk` 的稳定接口。`plugin_data_dir(自己的身份或下划线转连字符别名)` 映射到容器 `/data/state`；不能选择其他插件命名空间。不能安装宿主软件或获取任意宿主文件。数据总量限制 16 MiB，单次容器临时 `/data` 32 MiB，`/tmp` 16 MiB。需要大文件业务时，应另行设计按能力授权的流式文件传输，不扩大宿主挂载范围。

服务提供方声明provides及对应service_contracts，并导出provide_service(name)；调用方uses必须声明service/provider/api_version/methods且取得操作方批准。仅旧服务名声明不足以运行RPC。SDK get_service返回RPC代理；参数和结果必须是JSON可序列化值。受授权状态目录内的Path可由平台编码，函数回调、进程内对象和共享全局状态不跨边界。UI调用经过固定能力broker。

服务调用方身份由平台监督器绑定，插件传入的 caller 不能改变宿主状态分区。插件自身入口只使用 `<data>/plugin-data/<name>/self`，服务调用使用 `<data>/plugin-data/<provider>/callers/<authenticated-caller>`；自身入口也不挂载其他调用方状态。服务 provider 必须唯一，调用和缓存使用时重新检查授权。循环调用或超过四层嵌套被拒绝；并发状态事务采取拒绝并提示重试。

当前broker调用信封只接受rpc_version、type、method、args、kwargs字段；rpc_version必须为严格整数1，不能bool/浮点/字符串，也不接受缺失或其他版本。type若提供必须为call，method为受限ASCII字符串，args为列表，kwargs为字典，总请求限额64KiB。身份、fingerprint等额外字段拒绝。service.call只能传四个位置参数[service, method, business_args, business_kwargs]，service.describe只传一个位置参数[service]；这两种调用的外层kwargs必须为空，caller由宿主填入。每次分发前复核当前及外层授权链。远程UI仅提供select、multi_select、confirm、text_input、pause、clear_screen、print_header；paged_select的回调在插件沙箱内运行并使用远程select，不暴露宿主分页回调入口。

传输版本与清单SDK api_version=2、业务service_api_version相互独立。初始request.json、worker回调、宿主回复及worker结果全部由固定SDK生成rpc_version=1；接收端先验证版本，不能把旧消息自动当版本1，也不进行降级协商。worker初始验证在状态复制和SDK/插件加载之前；宿主验证在broker分发或结果状态发布之前。插件业务参数里的版本字段不改变传输头，插件作者通过SDK调用即可，不需要在各自CLI重复实现协议。SDK摘要改变后须重新prepare，未知/旧版本拒绝。

结果是最后一条协议消息：收到结果后再次call或result均拒绝，不分发能力、不发布状态。成功结果字段精确为rpc_version/type/value/state，state必须为字符串；错误结果仅rpc_version/type/error，error必须为非空字符串。宿主回复仅允许rpc_version加value、非空字符串error或严格布尔True的cancel三种互斥形状；缺字段、未知字段、混合成功/错误及伪布尔取消拒绝。普通非协议输出仍受总输出额度约束。初始请求校验见下文；这些规则不代表全部协议状态机及故障矩阵已验收。

worker初始请求字段精确为rpc_version/action/name/entry/kind/args/kwargs/service/method/caller/service_contract。版本、身份、动作和参数组合在路径解码、状态复制和SDK/插件加载之前校验。entry/cli不能混入服务字段或kwargs，cli参数为字符串列表；exec入口按容器POSIX路径规则拒绝绝对路径、父目录和反斜杠，平台映射必须提供linux入口。service.call/describe仅适用于Python插件并要求有效契约；方法须已声明，describe不得携带业务参数。此校验共享既有服务契约规则，不改变RPC传输版本或业务版本。最新SDK须重建并验收，不能沿用旧镜像证据。

宿主请求、worker回调/结果及宿主回复共用plugin_json严格编解码。JSON对象任何层级不得重复字段，转义后同名字段也拒绝；数字必须有限，NaN、Infinity、-Infinity及1e999等溢出值拒绝。发送端不能把非有限业务值写入管道，接收端在分发/发布状态前拒绝非标准消息。合法字符串、整数、有限浮点、布尔、null和明确Path编码保持原语义。此变更增加guest代码库存plugin_json.py并改变SDK摘要，旧镜像必须重新prepare；本地客户端协议测试不代替当前SDK的实际容器验收。

共用编解码器在调用JSON对象解析器前检查：文本最多32Mi字符、嵌套最多64层、容器/对象键/字符串/原始值合计最多131072项、非字符串token最多4300字符。原始字节行额度和broker业务请求64KiB额度另行检查，字符额度不代替字节额度。worker回复读取与文本额度对齐，合法超过1MiB的回复不再被旧读取上限截断。发送端在编码完成后检查这些额度、拒绝向管道发送超限消息；目前不能阻止序列化本身的内存分配，也不构成完整硬期限保证。SDK代码摘要改变后必须重建镜像；当前预算的原生容器验收仍待完成。

宿主先按原始字节识别OPSKIT_RPC:前缀，协议正文必须严格UTF-8；非法单字节、过长编码等在broker分发和状态发布前拒绝。普通输出仍按UTF-8替换坏字节用于显示，不能因此恢复或伪造一条RPC。有效Unicode参数和结果保持原值。

执行预算从invoke读取策略之前开始；状态线程锁等待、镜像检查、网络准备和快照阶段消耗同一monotonic预算。嵌套服务最多使用父调用剩余时间，策略更短时进一步缩小；不能重新获得完整timeout。Docker控制操作最多使用剩余预算或30秒中较小值，返回时再次检查，过期准备不能启动业务容器。线程锁等待到期失败，跨进程锁仍立即拒绝竞争。

源码与seed快照复制在独立host进程中执行，父进程复用已有监督器并使用调用剩余时间；普通超时/取消会终止并确认复制进程退出，再清理本次临时目录。子进程只执行平台复制代码，不导入插件业务模块。进程启动成本也计入预算，不承诺极短timeout能启动插件。回收可能附加最多两次1秒join；内核不可中断I/O、父进程崩溃和冻结程序spawn尚需原生验收。此监督范围不代替授权存储、指纹、状态提交等其余同步I/O的期限设计。

通用host worker现增加父进程存活保护：Windows在任务开始前加入kill-on-close Job，Linux设置父死亡通知，独立存活管道辅助检测parent退出。Windows/Linux已用独立parent强制退出、worker真实阻塞读取验证回收；macOS、冻结程序和启动导入/内核阻塞场景仍待验收。父崩溃后的临时快照文件不会由进程回收自动清理，须保留可核对归属后再恢复，不扫描删除未知系统临时目录。

超时后的本地进程回收和已归属容器/网络清理有独立预算，每资源的查询、删除和确认共享30秒总预算；必要清理耗时不计入业务期限，不保证硬实时返回。宿主broker UI在读键、方向键后续字符、读行及清空输入时，以作用域回调检查共享期限和整条嵌套调用链授权；Windows kbhit和POSIX select按最多0.05秒请求轮询，不创建输入线程。授权在UI和运行循环约每秒核对，准备后启动容器前与提交状态前再次核对。操作系统调度、同步输出、文件系统/指纹及配置读取仍不可抢占，不宣称完整硬期限。

沙箱UI读取要求TTY，重定向输入在读入前拒绝；普通官方交互不安装该守卫。守卫内Ctrl-C/Ctrl-D原始控制字符传播KeyboardInterrupt，pause不屏蔽或吞掉取消；ESC保留UserCancel语义。finally恢复守卫、信号处理及POSIX终端设置。状态先在独立staging解压，随后按配置锁→信任锁固定顺序取得跨进程锁，在锁内重查期限/完整授权链后切换状态并完成补偿。若撤权或禁用先完成，状态发布拒绝；若提交先持锁，撤权/禁用明确返回busy且需要重试，不能报告成功。通用配置单键设置在锁内重读最新配置，避免语言/主题旧快照恢复已禁用插件；save_config仍是显式整份替换接口，也必须取配置锁。提交/补偿不被期限强行中断，锁外同步读取不可抢占，RPC调用边授权已实施，状态提交也重查已使用服务边。不得将本地客户端回收当成daemon故障恢复证明。

uses.source仅是安装来源提示，不能作为身份授权。uses.provider绑定具体提供方，操作方审批绑定双方内容指纹、业务版本和方法；执行重新核对清单及worker实际业务版本。提供方或调用方重新信任不继承旧边。发布者身份与生产签名信任仍需独立设计，不能比较插件自报URL代替。当前SDK文件以core/plugin_runtime_inventory.py固定库存为准，code摘要变化时必须重建运行时；policy号相同不代表代码摘要仍兼容。完整并发/竞态、断电和跨用户恢复仍待验收。

## 网络

网络通过每次调用独立代理。默认拒绝私有、回环、链路本地与保留地址。需要自建 S3 等私有端点时，操作方配置：

```yaml
plugin:
  sandbox:
    timeout: 300
    private_targets:
      hello:
        - "storage.example.internal:9000"
```

这是精确授权，不支持通配符。SDK或第三方库须使用 HTTP(S)_PROXY；直接 TCP 网络不可用。证书验证不得关闭。授权公共网络仍允许插件将自己已获得的数据发送到公网，须依据数据敏感度决定是否授予网络权限。

平台Docker控制查询每次30秒、合并输出16KiB，失败只返回固定错误，超时/取消/超量回收本地客户端树。网络查询只读取Networks字段，代理地址必须是有效IPv4，不接受空值、URI/凭据文本、非字符串、回环/链路本地/保留/多播地址；允许Docker自定义bridge地址池，包括CGN地址。无network权限不创建代理或网络。清理失败恢复已接入上述账本，真实Docker的活动租约、删除失败保留与恢复已有证据；断电和跨用户恢复仍待验收。

插件业务stdout仍归插件协议；平台不将guest任意error字段回显到自身异常。坏JSON/非对象协议消息固定失败。运行时catalog只保存锁/来源/image/插件指纹等执行权威，不复制冗余依赖选择列表；来源引用最多4096字符，catalog读写上限16KiB。

## API1 迁移

将 CLI parser 移到插件包内并导出 `cli(argv)`；把所有数据路径改为自身沙箱路径；共享 pip 安装改为明确准备运行时；Python 对象/回调改为 JSON RPC；宿主命令和 root 操作移到官方明确授权的能力设计，不能以兼容为由恢复宿主执行。验证后更新 api_version 和 CHECKSUMS，在插件独立仓库中提交迁移。

旧宿主 plugin-data 不自动注入沙箱，避免错误挂载或泄露其他调用方数据。备份后人工迁移自己的配置和必要小文件到新状态目录；凭据和真实运行数据不进入 Git。

## 私网授权运行中撤销

已有代理仅保留启动时的列表。平台把该列表绑定到调用作用域，同一次受监督配置/信任复核要求原授权全部仍存在；新增项不扩展当前代理，删除任一项终止调用并回收worker、代理和网络。嵌套调用同时检查外层与内层授权，提交前仍复核。检测沿用每秒轮询，但阻塞的监督读取及清理各受剩余期限约束；不是撤销瞬间断开或逐请求授权，不支持将正在使用的私网授权静默降级后继续调用。具体证据和未完成部署范围见plugin-gateway-isolation.md。

独立出口代理当前策略2：固定构建移除infocmp命令，并清除/usr/bin、/usr/sbin、/usr/local/bin普通文件的setuid/setgid位；不删除Python所需的共享库。策略1库存不能用于执行，network插件须重新prepare；SDK策略6与SDK代码库存保持独立。scripts/verify_plugin_gateway.py现同时检验批准镜像的物理包/命令/权限清单及受控网络生命周期，不绕过镜像身份解析。减少攻击面不等于OS包漏洞全部修复，最新适用性与范围见plugin-base-image-audit.md。
