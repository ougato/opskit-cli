# 官方 CLI 脚本化契约（实施中）

全局 `--output text|json`；JSON 输出为 `{schema_version: 1, ok, data, error}`，连续采样/逐跳输出使用 JSON Lines。`--non-interactive` 拒绝输入和菜单，`--yes` 只批准普通确认。尚未迁移的软件与管理命令仍需补齐统一输出，不代表全部 CLI 已完成。

命令解析错误（未知命令、缺参、类型或范围错误）在根级 JSON 模式返回单个错误 envelope 和退出码 2，不回显原始敏感参数。`--output json --version` 返回版本对象。仅根级 --output 选择平台格式，插件自己的 --output 不影响平台解析。

## 已迁移命令

`plugin prepare PATH --base-image IMAGE@sha256:DIGEST --lock-file FILE [--dependency PACKAGE==VERSION ...]`返回image/prepared/authorized=false，构建本身不授权。base-image/lock-file必填；完整锁包含SDK根和全部传递依赖的确切版本/wheel哈希，dependency只核对不追加。缺锁、标签、无效/不匹配参数退出2；构建/系统错误退出1，取消130；CLI错误不回显Docker/pip异常详情。stderr只显示固定进度，不输出Docker/pip原始日志。镜像查询每次30秒/4KiB，构建客户端600秒/4MiB输出上限；超时/取消/超量回收本地进程树，不写成功catalog。这里是客户端阶段预算，不是prepare完整总期限，也不证明daemon内部构建已取消。旧policy或代码摘要不匹配的运行时必须按当前policy4重建；执行时验证锁及来源digest的label绑定。

`plugin fingerprint [PATH] [--check|-c]`不boot。生成返回status=written及file=CHECKSUMS.yaml；校验只读，ok退出0，missing/mismatch保留status且ok=false、退出1。不存在/链接根目录退出2；操作错误不回显私有路径。生成只修改指定插件内清单，不授予信任。

- `monitor dashboard|cpu|memory|disk|network|processes` 支持 `--once`、`--watch`、`--count`、`--interval`。非 TTY 默认一次；进程支持 `--limit`、`--sort cpu|memory`、`--pid`，网络支持 `--interface`，磁盘支持 `--mount`。

监控无关过滤参数在解析阶段拒绝。`--watch --count 0` 无限采样，正数 count 限定次数，`--once` 与 watch 冲突且 count 不得大于 1。指定 PID 直接读取该进程，消失或无权读取返回空列表；`--pid` 与显式 `--limit` 或 `--sort` 互斥，即使显式值等于默认值也退出2，且不启动采样。未知接口或不可访问挂载点退出 2。显式过滤不进入交互菜单，取消退出 130。
- `network ping HOST --count 4 --timeout 30`。

ping 的 timeout 包含主机解析和实际命令执行；数字 IP 不解析，主机名一次受监督解析后把选定数字 IP 传给 ping，避免命令再解析。预算耗尽立即失败；不可达返回 ok=false、退出 1，同时保留包数和耗时数据。macOS 的 IPv6 使用 ping6，真实平台支持仍须验收。
- `network traceroute HOST --max-hops 20 --timeout 120`。
- `network dns HOST --family any|ipv4|ipv6 --timeout 10`；IP 自动反查，也可明确 `--reverse`。反查仅允许 `--family any`（默认），其他family值在解析调用前退出2；family只过滤正向查询地址。期限范围 0.1..3600 秒；系统解析在独立进程中执行，超时/取消终止并回收进程，不遗留解析线程。预算含进程启动和结果等待，失败退出非零；终止后的必要进程回收耗时另计，不保证硬实时返回。
- `network port-scan HOST --ports 22,80,8000-8010 --timeout 0.5 --concurrency 16 --total-timeout 30`。端口范围 1..65535，并发最大 50；只支持 TCP。timeout 是单连接预算，total-timeout 包含一次 DNS 与全部连接，范围 0.1..3600 秒；耗尽返回错误，不把未扫描端口当关闭。数字 IP 跳过 DNS；主机名使用系统解析的首个排序地址，当前不逐个扫描所有地址。使用非阻塞套接字，超时/取消关闭在途连接，无扫描线程残留。DNS 进程回收耗时另计。
- `network speed-test --url URL --duration 15`：预算包含进程启动、DNS、连接、跳转和读取。用原始响应字节计数，避免自动解压；最多 5 次跳转、1 GiB，达到字节上限返回 limit_reached。测量窗口结束时使用最后确认的累计字节（进度最多每 0.1 秒更新），elapsed_seconds 包含启动/请求与回收，速率据此计算。无数据/请求失败非零退出，不把空结果当成功。取消回收进程；菜单事件取消保留最后确认的速率。
- `network public-ip --timeout 10`：整个候选 API 查询共用预算，响应最多 1 KiB，仅接受非压缩 IP 文本；超时或无有效结果非零退出。请求在独立进程中执行，父进程可取消和回收；必要回收耗时另计。

命令示例：`opskit --output json monitor memory --once`；`opskit --output json network dns localhost --family ipv4`。查询结果不会启动主菜单或后台更新。

网络采样结果（monitor network及dashboard.net）使用rate_schema_version: 2，外层CLI envelope仍为schema_version: 1。bytes_sent/bytes_recv是原始累计bytes，speed_send/speed_recv为窗口内bytes/s；sampled_at为本次计数读取后的UTC时间，各接口共用一次monotonic观察时间。window_seconds表示相邻成功观察的实际间隔，不是请求interval。interval仍是采样完成后的等待时间。

每次CLI调用或菜单实时页面拥有独立网络基线。第一条rate_status=initial、计数回退为counter_reset、非正时间窗口为invalid_window；以上speed及window均为null，不用0冒充实测。available时窗口为正数、速率可为真实0；下一条从重置后的新基线计算。消失接口立即清理，重现视为首样本。无显式采样会话的直接get_net调用各自独立。菜单对未知速率显示N/A。旧脚本须处理nullable速率，不能盲目作数值运算。

该检测不证明物理接口身份：相同名称的接口若在两个观察之间被替换且累计计数没有回退，当前计数无法区分；仅保证观察到的消失/回退不继承错误基线。不启动探测流量，最多4096个接口，超额显式失败。CPU有自己的采样契约，进程有自己的采样契约，不能用网络schema声明其已完成。

CPU采样（monitor cpu及dashboard.cpu）独立使用rate_schema_version: 2，rate_unit=percent。总量和每核来自同一对cpu_times(percpu=True)读取；只请求一次0.1秒等待，window_seconds记录两次读取完成之间的实际monotonic间隔，started_at/sampled_at为对应UTC时间。调度、定时器精度和读取耗时影响窗口，不保证精确100ms。CLI的interval是采样完成之后等待，与CPU窗口无关。

总使用率按各核CPU时间增量加权，不平均已经四舍五入的每核百分比。Linux从总时间扣除重复包含在user/nice内的guest/guest_nice，iowait不算忙碌；Windows/macOS按psutil公开cpu_times字段约定统计。实现不依赖psutil私有百分比缓存或私有函数。count_logical是本次实际读取的核心数，频率/物理核心数是随后查询的附加元数据，不宣称它们与利用率同时捕获。

available时percent/per_core为数值。counter_reset、topology_changed、invalid_counters、no_ticks或invalid_window时利用率全部null，菜单显示N/A；monitor cpu/dashboard保留数据并返回ok=false、退出1。invalid_window的window_seconds为null；其他无效计数仍保留观察窗口。库存为空或超过4096核心显式失败。计数异常不修成0；零增量不等于实测空闲。一次读取之间同数目的核心替换无法由索引识别，macOS及声明Python版本的原生验收仍需完成，进程采样按下面的独立契约验证。

进程采样（monitor processes）使用每条ProcessEntry的rate_schema_version: 2。CPU的rate_unit=percent、cpu_scope=one_logical_cpu，按本进程user/system时间增量除以实际monotonic窗口，排除子进程时间；多线程可超过100%。created_at是系统创建时间秒数，sampled_at为本条CPU计数读取后的UTC时间，window_seconds为相邻同身份读取间隔。内存百分比沿用psutil相对主机物理内存的定义，不能当容器配额使用率。

--pid范围为1..4294967295；每次CLI调用/菜单页面独立基线，同步采样会话按PID及创建时间核对。首样本initial的CPU为null；下一条同身份available可为真实0。PID复用、计数回退或无效窗口不得继承旧速率。再次用新Process对象复核创建时间，避免其对象缓存导致属性来自不同身份。单PID消失/权限不足/不可读僵尸分别gone/access_denied/zombie，保留一条状态报告、ok=false、退出1；身份改变及异常计数也退出1。直接get_process在不可读取时抛ProcessSamplingError，不再返回None；无显式采样会话的独立调用各自为首样本。

列表采用同一采集器，CPU排序首次无测量时请求一次0.1秒等待并重新采集；内存排序不为CPU等待。库存/基线最多16384，清理消失PID后再读取，超额失败；PID0空闲伪进程不参与排名。已知值先于未知值，并列保留枚举顺序；默认列表容错保留不可读状态行，--limit可能截去这些行，因此排名仅覆盖可观察的指标，不能证明所有系统进程可读。菜单未知CPU/内存显示N/A。当前完整排序仍O(P log P)、存储O(P)；初始化有两次枚举及额外身份复核成本，性能另测，不能宣称已提速。psutil枚举和同步属性调用尚无整次监督预算，其他平台及真实权限/PID复用验收继续开放。

## 插件生命周期

`plugin list/show NAME/install URL/update NAME/remove NAME/enable NAME/disable NAME/trust NAME/revoke NAME` 为平台能力，插件业务参数仍由 `plugin run NAME -- ARGS` 原样传递。

安装支持 `--branch/-b` 和 `--as`，安装只记录来源与分支，不授权。先用 `plugin show NAME` 核对内容指纹和权限，再显式 `plugin trust NAME --fingerprint SHA256`。`--yes` 不替代指纹。更新内容改变立即撤销原授权，包括降级和校验异常路径。删除只移除插件源码与授权，保留其状态数据；JSON/无交互删除需要 `--yes`。

## 软件查询与偏好

指定名称的 `software install/uninstall/upgrade/switch` 已统一结构化结果，进度送往 stderr；这些入口不执行菜单启动初始化或隐式应用平台更新。编排位于 software/lifecycle_cli.py，执行仍复用菜单的 software.actions。版本型 install/upgrade 要求 --version；switch 只能选择本地已安装版本，upgrade 目标已安装时复用 switch。版本标识拒绝路径分隔符、控制字符等不安全输入。uninstall 要求 --version 或 --all（多版本软件），两者互斥；单版本软件拒绝这两个选项。无交互卸载要求全局 --yes。参数或能力不支持在依赖安装之前拒绝；安装后无法 detect 不再报成功。software manage 保持交互专属，脚本调用模块的具体管理命令。

`software diagnose NAME` 调用所属模块的只读报告，不执行平台 boot 或旧菜单。当前支持 rustdesk/tailscale/xui/wg_server/wg_client；平台或能力不支持提前拒绝。专用 diagnose 与软件入口共享报告；不健康/空检查返回 ok=false、退出 1，保留 checks。RustDesk 普通诊断不读取或输出连接密钥内容、不写状态、不查询公网。报告目前是基础检查，完整监听、API/TLS、路由和连通性仍待补齐。

RustDesk 另验证 TCP 21115/21116/21117 和 UDP 21116 的本机监听与 hbbs/hbbr 可执行文件归属。只读取系统 socket/process 表，不发送探测流量。权限不足或无法验证归属返回失败原因，不把缺乏可见性断言为“端口已关闭”。当前检查未覆盖可选 WebSocket、外部防火墙/公网可达性或安装产物可信性。

`software list/search/installed/versions` 支持 JSON；list/search 支持 `--category` 与 `--platform linux|windows|darwin`。跨平台查询不运行外平台检测，安装状态为 null。`software capabilities NAME` 区分真实脚本化安装、交互向导和分类，未完成的向导不标为已支持。

`software install wg_client --token-file FILE`，FILE 为 `-` 时从 stdin 读取；与 `--token` 互斥，拒绝空值和超过 64 KiB 的输入。

`preferences show` 仅返回主题和语言；`theme list/set NAME`、`language list/set zh|en|auto` 提供显式查询与持久化设置。查询不创建配置文件。

## 已接入的安装配置与管理

`update status [--timeout SECONDS]`（默认10秒）只读查询当前版本、二进制更新模式、待更新文件存在性及缓存版本，不boot、不创建配置/缓存；`pending_verification: not_checked`明确没有验证产物，存在文件不表示可安全应用。

`update check [--timeout SECONDS]`（默认30秒）复用现有更新源/灰度策略，在受监督进程内查询，配置上限64 KiB、元数据上限256 KiB，拒绝压缩响应。检查不写更新缓存、不创建机器标识、不发送遥测；完整放量不需要新建身份，部分灰度缺已有身份时明确失败。网络/无效响应失败不作为“无更新”；无符合策略的版本返回available=false/reason=no_eligible_update，候选版本返回signature_verified=false（此命令尚未下载/验证产物）。期限范围均0.1..3600秒，取消回收worker。显式download及索引任务reconcile已接入；完整应用/恢复事务仍未完成。

`opskit --output json --yes update apply`只允许打包可执行程序模式；源码模式退出2且不创建数据。需要确认，JSON/无交互必须使用根级--yes。应用要求缓存中明确的、更高整数pending_version，普通完整产物及真实Ed25519签名，不猜版本。成功结果为version、state（Windows scheduled，其他平台applied）和health_confirmed=false；scheduled不代表替换已完成。同步替换使用原子操作，跨文件系统先校验暂存副本再提交；Unix替换后异常/取消尝试恢复已校验备份及重试产物。备份清理失败不会误报应用失败。目标级版本权威及索引任务reconcile已接入；仍缺真实断电、未索引/旧任务、完整marker凭据及退役；apply未提供总期限参数，不能标完整生产验收。

现有更新下载服务限制单个产物≤1 GiB（含断点），拒绝压缩、过大声明或实际写入；拒绝暂存链接/硬链接，私有目录不可写时失败，不使用共享系统temp固定文件。下载和签名侧文件写入使用暂存区锁；应用、marker原子提交、回滚及Windows helper另共享程序目标锁，路径不依赖数据目录。完整启动/异步任务恢复仍待完成。Mach-O头识别只是基本格式检查，真实产物仍须签名验证和平台运行验收。

内部健康回滚现使用本地明确backup_id及目标/版本/摘要记录，原子恢复，失败保留恢复记录。新记录缺失或不一致时拒绝，旧式备份不自动猜选；没有新增公开rollback命令。健康/marker记录最多64 KiB、普通JSON对象，拒绝链接/硬链接，原子私有写入。启动marker替换成功后的清理失败不冒充应用失败。恢复记录是本地一致性凭据，不是发布者签名；Windows失联恢复、断电及真实跨用户权限验收仍未完成。

Windows任务使用独立产物副本及随机脚本目录，helper持有暂存区及程序目标字节锁并在目标旁校验复制后原子提交，保留原ACL；移除直接覆盖/共享pending重启移动回退。健康记录仅在实际提交后写入。status若缓存含apply_job，则增加id/version/state/health_confirmed白名单对象；scheduled表示最后调度记录，不证明helper当前存活，applied表示记录的提交完成，不等于健康确认，applied_recovery_incomplete表示已提交而恢复记录失败。任务索引不能越界，记录受64 KiB与普通对象约束；原始异常和路径不输出。目标级版本权威、配额及索引任务reconcile见下节；全生命周期失联恢复和任务退役仍待完成。

`software install ohmyzsh --config FILE` 要求 `schema_version: 1`、布尔 `with_p10k` 与 `switch_shell`；`software install tailscale --config FILE` 要求版本字段及布尔 `exit_node`。拒绝缺字段、未知字段和字符串代替布尔值。配置示例位于 docs/examples。Tailscale 安装不发起登录，出口节点默认示例关闭。

`tailscale status/diagnose/login/down/restart`；登录默认返回授权 URL，不等待浏览器完成，也不隐式发布出口节点。`--timeout SECONDS`（默认 60，范围 1..3600）包含初始登录、状态查询及显式 `--wait` 的授权等待，超出总预算失败；晚到的成功状态也不作为成功。`--advertise-exit-node` 单独批准出口发布，当前 macOS 集成不支持，显式选项在登录前拒绝。登录失败不回显命令输出，取消退出 130；真实守护进程与提权进程树回收仍需平台验收。

`ohmyzsh status`、`ohmyzsh p10k apply-preset/configure`、`ohmyzsh shell switch`。configure 是交互专属，JSON/无交互模式拒绝；脚本化提权使用 sudo -n，不弹密码输入。

`software install xui --config FILE` 要求显式 host，支持 panel_port/panel_user/panel_base_path/vless_port/sni/dest。密码从 panel_password_file 读取；未提供时生成并要求 credentials_output_file。路径相对配置目录解析，敏感导出拒绝覆盖。安装要求 Linux/systemd，API 或服务配置失败不报成功。

`xui status/diagnose/traffic/logs`；日志支持 `--lines` 和 `--follow`，状态掩码密码、私钥、UUID 和连接链接。`xui links --output-file FILE` 只向新建私有文件导出，stdout 不含链接。现有 systemd 快照隐藏入口仍被定时器调用，暂保留兼容。

XUI diagnose 额外比对 state 和实际面板配置、验证面板监听归属、以 SQLite 只读连接检查 VLESS 入站启用及 REALITY，并探测固定 loopback CSRF API（默认最多 3 秒、64 KiB、不使用代理/重定向/压缩）。不输出 token/凭据/数据库内容；CSRF 健康不等于认证 API 或 REALITY 握手通过，总预算已接入，深度检查及真实平台仍待补齐。

XUI报告内部现共用30秒预算：状态读取和监听枚举使用可终止的一次性进程；状态仅接受最多64 KiB的普通JSON文件，返回诊断所需端口/路径，不传递密码、私钥、链接。版本/服务/面板设置使用有界输出的进程树监督；面板设置查询只用sudo -n，权限不足直接失败。数据库锁等待/查询与API接收剩余预算，拒绝晚到的成功结果。CLI已提供 --timeout（默认30秒，0.1..3600），真实Linux提权/原生打包与特殊文件系统仍需验收。

`software install wg_server --config FILE` 要求 domain/public_ip/credentials_output_file，支持 subnet_octet（1..254）、label、acme_email。令牌仅导出到新的私有文件；443 被其他服务占用时拒绝，不替换或停止该服务。

`wireguard server status/diagnose/peer list/add/rename/remove/export`。节点 ID 为现有公钥，与名称和菜单顺序无关；add/rename 要求 `--name`，export 要求 `--output-file`，脚本删除使用 `--yes`。普通结果不包含令牌。

`wireguard client status/diagnose [--tunnel ID]`、`wireguard client tunnel list/add --token-file FILE/remove ID/update-token ID --token-file FILE` 已接入。ID 为现有 `wg_iface`，不按菜单序号选择；删除使用全局 `--yes`。安装拒绝覆盖现有配置，新隧道名称受 Linux 网卡名称长度限制；失败清理隧道配置和服务，保留共享依赖。更新快照旧配置、状态文件和服务启用/活动状态，健康检查通过才提交；失败尝试恢复，恢复不完整明确报错。普通查询不输出 token/UUID。诊断目前检查服务和配置存在性，完整路由、转发及连通性检查仍待补齐。

WireGuard两种报告内部共用30秒预算：状态读取和服务查询受进程树监督；客户端配置只验证普通文件和非空，不读取私钥内容。Tailscale报告同样共用30秒预算，macOS复用一次状态查询；普通命令查询采用有界capture。预算耗尽不开始后续检查，晚到健康结果拒绝。RustDesk文件元数据使用一次性worker，服务与监听共用预算。五种software diagnose和专用诊断CLI均已提供 --timeout（默认30秒，0.1..3600）；真实平台、深度检查与特殊文件系统仍待验收。必要的进程创建和回收包含实际耗时，预算不构成硬实时承诺。

程序目标旁共享版本记录现将from/to build与实际SHA256、随机事务ID及prepared/applied阶段绑定。应用在目标锁内拒绝未知摘要、损坏记录、降级和未重启的旧运行进程；授权回滚单独记录反向转换。Windows延迟任务提交前核对绑定的prepared记录，被新任务替代时拒绝。记录写入中断后按实际旧/新摘要解释版本；这不代表完整断电恢复已验收，也不抵御可修改宿主私有文件的同账户攻击者。


## 显式签名更新下载

`update download --timeout SECONDS`已接入（默认300秒，范围0.1..3600），只在打包模式执行；源码模式退出2且不创建运行数据。候选由现有版本/rollout/kill-switch和可信仓库策略选择，不接受任意URL或强制旧版本。成功返回candidate_version、candidate_id、signature_verified=true、state=downloaded、applied=false；没有符合策略的版本返回available=false。

下载、checksum、签名校验、摘要复核和发布在同一个可终止worker及总预算中进行；实际下载worker持有固定缓存根的暂存区OS锁，阻止并发下载/应用/清理；父进程异常退出而worker仍存活时不会提前放开锁。复用现有下载器，暂存到私有随机update-candidates/ID目录；完成验证和持久化后才原子发布缓存指针。此前候选不会被新下载的残包或坏签名覆盖。应用、启动检查及status解析同一指针；apply仍重新验签，status仍只称not_checked。

发布点是最后的原子缓存记录写入。超时/取消发生在该点附近时可能已发布完整候选而响应未返回，应查询status后重试；不得将操作失败解释为保证没有提交。必要的OS文件操作/锁创建及worker回收不构成硬实时保证。尚无跨调用断点续传。新候选分配前执行有界库存检查和受控清理：最多4个目录条目、总文件字节2GiB+1MiB，并为下一次1GiB下载另预留128KiB元数据。当前候选和启动marker引用始终保留；只回收有效所有权记录下的普通文件，拒绝链接/硬链接/损坏记录，未知内容保留，额度不足则拒绝新下载。库存最多64个根条目，不自动递归删除用户目录；超限或不确定旧目录需独立审查。

后台更新复用该下载服务，不维护第二套发布流程。下载、checksum和签名每次重定向均要求HTTPS、无URL凭据，最多5次跳转；签名/checksum拒绝压缩，响应上限4KiB。所有下载错误只报告安全阶段，不回显URL、token或传输异常。实际原生产物和正式签名发布仍需验收。

Windows调度任务目录现固定于cache/update-jobs/ID，helper使用固定缓存根的暂存锁，不随candidate_id改变。任务拥有独立摘要绑定快照；未被marker引用的旧候选可清理而不损坏任务产物。异步任务配额及失联恢复仍未完成。


## Windows任务阶段与健康归属

任务创建时写schema_version/job_id/version/target/parent_pid及preparing所有权记录；快照和脚本完成后写launching，再严格原子合并cache.apply_job，只有该索引持久化成功才Popen。快照、脚本、意图或索引失败不启动helper。Popen失败写launch_failed结果，status能通过已存索引读取。

Popen成功后写scheduled及可用的helper_pid。若该写入失败，保留已索引launching而不把活helper标launch_failed、不另建重试marker。apply此时仍只表示调度已交给OS；status的launching是缓存意图，不能判断helper是否存活。result缺失、launching和scheduled均不等于替换完成。

helper实际提交后写入的健康记录包含job_id；confirm_health保留该归属。status的health_confirmed必须同时满足整数版本、confirmed=true、同一job_id及非明确失败阶段。旧记录只有相同版本不能确认具体任务，返回false。新增metadata job_id必须与目录及索引一致；仅返回白名单字段，不导出目标路径、PID、摘要或原始异常。

进程身份快照与任务配额见下节；失联恢复和断电日志仍未完成，helper_pid本身不是存活证明。


## Helper启动确认与只读存活快照

Windows helper启动后、等待父进程前写私有running.json，绑定job_id/version/PID、原生FILETIME创建时刻、实际脚本路径与进程映像。平台启动前持久化helper_executable。update status的apply_job新增helper_status：alive、exited、identity_mismatch或unknown。

alive需要确认记录有效、任务ID和版本匹配、原生创建ticks精确一致、实际映像与启动前记录相符、argv中真实-File参数指向该任务apply.ps1，且读取后复核创建身份。缺确认或权限不足是unknown；PID被复用或映像/参数不符是identity_mismatch，不向该PID发送信号。确认过的进程不存在是exited。

这是查询时快照，不保证将来存活。result与helper_status分开：scheduled+exited可能是失联或结果写入失败，status只读且不改成failed/applied。完整事务恢复必须核对目标摘要、事务阶段及备份；不凭进程退出猜测结果。查询仍在一次性worker及原有总预算中，启动确认最多64KiB且拒绝链接/硬链接。创建时刻、PID、argv和路径不对外返回。

## Windows任务存储配额

新job.json采用schema_version=2，显式记录backup_id及transaction_id（无引用时null），status保留schema1读取兼容。调度在缓存根锁内、创建任务和启动helper之前预留快照大小加128KiB元数据预算；最多8条目、2GiB+1MiB总预算，未知及旧内容计入预算。

只有配额不足时清理无目标事务且可证明未启动、无ACK/结果的preparing任务，或无目标事务、存在失败结果和ACK且身份probe确认exited的任务。活动索引、health/marker任务及备份引用受保护；未知文件、旧记录、成功应用、恢复不完整、缺确认及不确定任务均保留。非递归删除并复核文件身份，所有权记录最后删除。不能安全释放空间则拒绝调度，不改变旧索引或执行目标；这不是完整失联恢复，不新增公开cleanup/force参数。

## 显式失联记录核对

`update reconcile --timeout SECONDS`（默认30，0.1..3600）只在打包模式运行，源码退出2且不创建运行数据。它修复记录，不执行产物、替换程序、自动回滚或发送进程信号，不需要--yes。一次性worker自行持有缓存根与当前可执行目标锁；监督包含库存读取、身份核对、摘要与记录提交，超时/取消后回收worker，部分已原子提交的记录保留供重试。和下载一样，响应丢失时应查询status或再次核对，不把超时当作“没有任何记录写入”。

仅处理cache.apply_job引用的schema2任务，目标从当前可执行路径取得，拒绝缓存任意路径。核对同一upgrade事务ID/from/to build/摘要，以及普通单链接、大小有界的backup_id文件和侧车记录。helper必须经ACK身份核对为exited；无ACK/权限不足/复用PID/存活时返回state=deferred及固定reason，不猜测结果。另允许明确尚未到launching、无ACK/结果的preparing关闭未提交事务，不能据此宣称新字节已提交。

目标匹配新摘要：将目标事务补为applied，重建缺失的job_id/backup_id健康记录，保留同任务既有confirmed及fails；目标匹配旧摘要且事务未applied：事务标aborted，恢复凭据为failed/reason=target_unchanged。aborted不能接受新摘要作为版本权威，也不能被旧helper的prepared检查通过。未知摘要、备份损坏、事务已替代及同版本其他任务健康记录拒绝；不改变可执行文件和备份。

恢复凭据单独写recovery.json，包含schema/job_id/version/transaction_id/state/reason及目标摘要；status验证绑定及摘要后优先使用它，可覆盖缺失、损坏或恢复不完整的helper结果，增加reconciled=true。stdout仍只输出白名单ID/版本/状态，路径、摘要、PID和原始异常不输出。无索引返回no_job；旧任务返回deferred/legacy_job。启动在健康回滚、marker重试及post-update清理之前，以30秒预算核对已有索引；未解决或出错时暂停自动更新写入，不自动重复调度。

备份清理在缓存根锁内保留最近3份及cache/health/marker/schema2任务的所有备份引用；旧/未知任务不能证明引用完整则保留全部。库存最多256个备份目录条目、64个任务、每任务16文件；有损坏引用或链接拒绝清理，删除前复核文件身份且不递归。未索引/旧任务恢复、成功任务退役、marker完整凭据、真实断电及原生发布继续待验收。

## 健康记录读改写与后台确认

mark_update_applied、confirm_health、启动失败计数及回滚判断现在与helper/reconcile使用同一缓存根OS锁，从读取到写入完整持锁。记录必须为普通私有有界JSON；build是正整数，confirmed为布尔，fails为非负整数（旧缺省值0），可选job_id必须是32位十六进制ID。坏记录保留，不在日志回显原始异常或记录内容。

健康发布不得覆盖更高build；同build的重放保留confirmed/fails/job_id，相同build但备份归属不同拒绝覆盖。当前运行build不匹配时，确认与启动检查都不更改或删除记录。回滚成功仍保留健康/备份引用，供恢复与后续退役；不能仅凭布尔返回值删除可能尚未写完的恢复证据。

普通confirm_health返回布尔：true表示已确认或无需更改，false表示失败/竞争，均不保证其他版本已健康。后台在HEALTH_CONFIRM_DELAY之后，以默认360秒的monotonic竞争重试窗口等待根锁，每次重读最新记录；只重试明确ResourceBusyError，不把坏JSON/unsafe路径或其他OS错误当竞争。窗口到期保留记录、固定日志说明延后。此窗口限制锁竞争重试，未承诺文件I/O或线程调度硬实时期限。

备份清理失败不撤销已持久化健康确认。索引任务reconcile复用同一健康类型检查并保留既有确认和计数。不同数据目录/不同程序目标的完整健康归属、其他终态任务退役，以及原生多账户验收仍需继续完成。

## 回滚历史与候选策略补充（2026-10-13）

update status在没有活动任务时可读取last_apply_job；有有效退役凭据的回滚任务报告state=rolled_back、active=false、restored_version，health_confirmed=false。历史结果不表示helper当前存活，且不以旧result.json覆盖有效退役结果。

update check对目标升级下限拒绝的build返回available=false、reason=rolled_back_version，不返回候选版本。目标升级build须严格高于历史最高回滚build；download在传输前、apply在备份前及事务准备时重新检查。check仍只读，不代表候选签名已验证。
