# 发布流水线安全验收

2026-10-16补充运行兼容边界：归档非覆盖发布在旧glibc下新增受限的Linux renameat2系统调用路径，实际固定manylinux/glibc2.17 x86_64专项通过。未知ABI或内核不支持仍拒绝，不以普通rename替代；aarch64只有模拟ABI证据。此修复属于最新585兼容候选，不在当前584冻结编译输入内；旧ELF符号基线或版本命令通过不能证明全部归档安装能力。完整范围及原失败记录见[最新台账](validation-status-20261015.md)。

2026-10-15：当前是本地与原生专项验收，尚未触发 GitHub 实际发布，不代表发布门槛关闭。

最新 Linux r6 已完成两项限定摘要 Nuitka 修复的标准完整构建，产物 SHA256 `d1201ef768af2ba2bd5c28a9e9550b91d91486d7c05bc454710a27a71d9da2fe`；19 项冻结门槛、真实插件基础调用、四种 SIGINT/SIGTERM 及立即 reap/保留 zombie 两种 SIGKILL 场景通过。每场景均检查已有计数器和证明文件不变、精确容器消失及恢复计数；四个普通信号和一个强杀场景需要 reconcile 恢复 1 项，不能宣称全都自动清理。精确场景与归档见[最新验收台账](validation-status-20261015.md)。此应用快照不包含后来磁盘采集修复，也不关闭其他平台冻结、生产信任根或真实远端发布门槛。

## 已实施边界

- 签名脚本来自事件固定提交 `github.sha`，该检出不持久化 Git 凭据。全局 contents:read，仅 release 任务保留 contents:write。
- 资产上传后，默认分支检出到独立 bootstrap-target。固定事件源码提供版本、公钥脚本，Python -I 执行；默认分支提供数据，不执行其中的脚本。--root 目标拒绝符号链接、非普通文件和多硬链接。此检查不保证抵御并发恶意文件系统替换。
- release 任务共享固定并发组，不因新 tag 取消在途发布。队列顺序不作为安全前提。写入前同时检查 APP_VERSION 和 latest_build，拒绝降低任一版本，检查待发布版本、min_build、rollout 的整数类型和范围。相同 build 允许重试，尚不等于资产上传幂等。
- 公钥回写前先执行只读 --preflight，正常版本写入也重复检查。bootstrap 提交基于实际默认分支历史，使用普通非强制 push；其他写入者抢先更新会导致 push 失败，不自动合并或强推覆盖。
- 四平台产物按明确矩阵验收，拒绝缺失/额外目录及文件、链接、错误版本/平台/架构/大小/摘要；元数据每文件上限1MiB。签名前检查原始产物，公开前复核JSON签名、签名原文及分离签名。manifest.json仅供核对，不上传成相互重名的发布资产。每平台公开二进制、校验和及三种签名文件，共20文件。
- 发布采用草稿，移除--clobber及吞掉创建错误的逻辑。通过认证发布清单查找草稿，保留失败现场；重试只补缺失文件，已有文件的名称、大小、uploaded状态和服务端SHA256摘要必须一致。已公开的完整同内容版本重跑只读，已公开的缺失/冲突资产不就地修改。全部远端资产核对后才公开，发布前再次确认tag对应事件提交；安装脚本依赖的latest入口保留，旧整数版本不替换更高的已公开版本。
- release/test工作流的外部action均固定40位提交，不再跟随版本标签。官方仓库引用解析、commit URL及GitHub提交验证结果保存在cache/release-action-pins-20261015-r1.json；这证明来源固定，不表示已经完成action源码安全审计。
- 签名环境独立创建venv，从官方PyPI按requirements-release-signing.txt安装，仅wheel、完整哈希、禁止自动解依赖。此锁是uv.lock的cryptography/cffi/pycparser wheel子集，针对Ubuntu发布任务的CPython3.12，不是跨平台运行时依赖锁。依赖安装不接收私钥，签名步骤只使用该环境的Python -I；缺少固定公钥时拒绝签名，不生成输出。
- manylinux2014_x86_64构建镜像固定为 `quay.io/pypa/manylinux2014_x86_64@sha256:f6a6f153c9cf45470507ebbc27013e5db14ce21331b62fd2c540561f25e5f0a5`，显式Linux/amd64。只读测试、构建和扫描检出全部关闭凭据持久化；只有bootstrap回写检出保留Git凭据，减少构建依赖读取.git中令牌的机会。
- 原生构建统一调用scripts/install_release_build.py，要求CPython3.12及全新环境目录。先用解释器自带ensurepip引导，再按哈希安装固定的setuptools/wheel/packaging，最后安装完整应用和Nuitka依赖闭包。仅Nuitka允许已锁摘要的源码包，关闭独立构建依赖解析、pip缓存和版本检查；临时目录限定在指定环境的tmp内，不依赖系统盘临时空间。安装任一步失败立即停止，已有或部分环境拒绝复用。流水线构建任务有60分钟上限。
- build.py已移除--assume-yes-for-downloads，禁用Nuitka更新检查，关闭其标准输入；缺少辅助工具时拒绝下载并失败。不是已经补齐Windows扫描工具、所有编译器或辅助下载来源，完整构建仍待验收。

## 构建锁维护

2026-10-16增量：独立`installer`组锁定pip26.2.1并导出requirements-installer.txt。CI/发布测试、独立wheel安装、签名环境及原生构建安装器均先按此锁安装pip，再处理其余依赖。CI使用独立引导步骤使Windows原生命令失败直接阻断后续步骤；签名步骤自身不安装依赖、不扩大秘密作用域。初次ensurepip只负责引入已审核工具wheel，仍不是所有工具来源与运行环境安全性的完整证明。应用AnyIO/idna安全升级与验证范围见[依赖基线](dependency-security-baseline.md)，远端与原生发布验收仍开放。

依赖权威来源为pyproject.toml与uv.lock。release-build组固定Nuitka4.3/setuptools84.0.0/wheel0.48.0；本批uv.lock只新增这三个包，已有包版本未变。两个pip清单由uv导出，不能各自手工升级：

```powershell
uv export --locked --only-group installer --no-annotate --no-header --output-file requirements-installer.txt --quiet
uv export --locked --no-default-groups --group release-build --no-emit-project --no-annotate --no-header --output-file requirements-release-build.txt --quiet
uv export --locked --only-group release-build --no-emit-package nuitka --no-annotate --no-header --output-file requirements-release-bootstrap.txt --quiet
uv export --locked --no-default-groups --extra dev --group release-build --no-emit-package nuitka --no-emit-project --no-annotate --no-header --output-file requirements-ci.txt --quiet
uv export --locked --no-default-groups --no-emit-project --no-annotate --no-header --output-file requirements-runtime.txt --quiet
```

测试验证导出包的完整传递依赖闭包、版本及所有文件哈希与uv.lock一致。更新锁后重新验证各声明构建平台；不把签名专用锁当作应用运行时或构建锁。

CI与发布前测试已改用requirements-ci.txt，仅wheel、哈希校验和禁止依赖解析。CI锁包含应用、dev测试及构建后端依赖，排除不参与wheel打包的Nuitka。editable安装禁止依赖解析与构建隔离下载；wheel使用已锁构建后端且--no-isolation，独立安装环境先按requirements-runtime.txt安装依赖，再以--no-deps安装本地产物。引导pip仍来自受选Python解释器/ensurepip；不是声称所有工具及操作系统依赖均已固定。Windows3.12/Linux3.10真实全新环境安装与pip check通过，Windows另验证editable安装及第二次pip check；macOS Intel的既有wheel缺口仍保留。

## 证据

早期六文件批次 ZIP SHA256 `a6ab92362f8dec5cfe63dfd75867e4765c2ee1fbe406e7836a2ab5a9dafe165c`：Linux/macOS 各29通过，Windows加既有签名测试32通过；证据在F盘cache/release-target-native-20261015-r1。真实临时Git仓库验证默认分支最新父提交保留，伪造分支脚本不执行，越界链接拒绝。

随后9项反例在旧版本脚本上全部失败，保存cache/release-monotonic-baseline-20261015-r1.xml。修复后的六文件 ZIP SHA256 `95b3abc2892f7256914d02dad276e946510597eff25c592e9dfcedbca9c842b3`：Linux/macOS各40通过，Windows加既有签名测试43通过，无跳过。新增实际本地bare远端、两个发布者验收：v8先推送，旧检出的v7普通push被拒绝；重新检出后v7预检被拒绝，远端仍v8。清单、Linux/macOS XML在cache/release-monotonic-native-20261015-r1，Windows XML在cache/release-monotonic-windows-20261015-r2.xml。Ruff和差异空白检查通过。

发布资产批次11文件ZIP SHA256 `4bb930ce26a878370dfce5ec4307e088ad0aa59b76a86ad418da0f1bfb4c3367`，Linux/macOS各75通过，Windows加既有签名测试78通过，无跳过；清单/XML在cache/release-assets-native-20261015-r1。覆盖实际生成测试密钥调用sign_release.py、完整矩阵、签名篡改、草稿失败重试、上传成功但响应丢失、公开版本只读重试、已有资产冲突、服务端摘要二次核对、HTTP错误拒绝、tag变动及旧版本不替换latest。状态化gh协议测试使用内存服务，无外部写入。另实际只读调用GitHub公开仓库API，验证gh --include响应解析及服务端资产digest字段可用；不是草稿写入验收。Windows首次矩阵契约测试误用默认GBK读取UTF-8工作流，原失败保存在cache/release-assets-windows-20261015-r3.xml；修正显式编码后上述最终批次通过。

签名依赖与action固定批次15文件ZIP SHA256 `719181db96c7cc5c9d91d555fe7431fb79d4d9b5886d987905e7b2a4d7237bbe`：Linux/macOS各79通过，Windows加既有签名测试82通过。158个wheel哈希与PyPI对应版本元数据一致，当次PyPI vulnerabilities列表为空，见cache/release-signing-pypi-20261015-r1.json；这不替代完整漏洞审计。独立venv按哈希安装cryptography50.0.2/cffi2.1.1/pycparser3.11后，Windows CPython3.12、Linux CPython3.11的真实签名与20文件复核、模块来源及pip check均通过。安装报告、Linux/macOS XML和探针在cache/release-signing-native-20261015-r1，Windows XML在cache/release-signing-windows-20261015-r1.xml。源码清单与探针适用范围分开，不把Linux3.11探针写作生产Ubuntu3.12验收。

Intel macOS13的可选独立签名环境安装失败：50.0.2官方macOS wheel只有ARM64，按仅wheel策略无兼容包。macos-install-limitation.json记录此限制；没有放开源码构建、换旧版或改写失败证据。该机79项源码回归使用原验证环境通过，不是新签名锁安装通过。macOS Intel的运行时锁/依赖兼容性仍需独立处理；当前发布矩阵声明darwin-arm64。

manylinux摘要批次：官方manifest确认Linux/amd64及config摘要，实际拉取后ImageID匹配 `a38955ae...`。禁网、无宿主挂载、cap-drop=ALL的受限容器验证CPython3.12.15、glibc2.17、GCC10.2、OpenSSL3.5.9及静态库归档。另在临时容器恢复精确Python静态库，实际GCC链接最小嵌入程序并成功导入ssl/sqlite3/ctypes；ELF所需GLIBC符号最高2.17。诊断容器均确认已删除，镜像保留为缓存。工作流与凭据边界9项检查通过，三文件摘要及官方manifest、镜像信息、探针、清理结果在cache/manylinux-pin-20261015-r1。

嵌入探针首轮有C字符串转义和Windows输出解码错误；第二轮未导出静态Python符号导致_ssl加载失败，原脚本及失败日志保留。第三轮按静态嵌入要求添加--export-dynamic后通过，证据在embedding-r3。不是修改产品来绕过失败，也不是完整Nuitka构建证明；产品冻结产物的动态扩展加载、最终GLIBC符号与CLI运行仍需实际验收。官方镜像固定不等于漏洞扫描或全构建可复现。

构建锁批次最终21文件ZIP SHA256 `2fc1934ea123b882bbf1e003b1c2a8371ee7309f9760dd514aa209974afd5fb0`：Windows89通过，Linux/macOS各86通过，无跳过；后两端不含依赖完整平台源码的3项既有更新签名测试。清单/XML在cache/release-build-source-20261015-r3。真实Nuitka4.3确认函数在DEVNULL输入下返回拒绝，无网络下载。Windows CPython3.12与固定manylinux CPython3.12的最终安装脚本、锁定源码wheel构建及pip check均成功；安装日志、输入摘要、清理结果在cache/release-build-native-20261015-r3。三个诊断容器均确认删除，环境保留供后续冻结编译。

保留首轮cache/release-build-native-20261015-r1中manylinux因/root/.cache权限导致的Nuitka wheel失败，以及第二轮r2的Windows系统临时盘ENOSPC失败。Windows失败时C盘约75MiB可用，原日志还包含本地编码的错误文本；没有删证据或清理用户磁盘。最终脚本关闭pip缓存，并让ensurepip、pip及源码构建的临时文件留在环境目录，Windows在F盘完成安装。r2的manylinux安装最终也通过，但NTFS映射上的安装耗时不能作为程序性能结论；r3使用WSL原生文件系统。最终源码专项和安装验收仍不等于完整OpsKit编译通过。

## 未关闭项

第一项信号修复的完整r3产物已通过19项冻结CLI、真实Docker基础三次调用及SIGINT/SIGTERM分别投递启动器和进程组的四场景。二进制31,206,064字节，SHA256 `72297acb26fff888cfc7a93433b4dd42e0407c86076bd14135690bc12f3d49bf`，F盘cache/native-signal-build-20261015-r2/artifact-r3保存产物。四场景状态均保留，SIGINT返回130/SIGTERM返回143；有三场景需reconcile恢复1项，最终active/pending/invalid均0，不是返回前全部自动清理证明。尚未包括第二项父进程关系修复或SIGKILL生命周期。

为下一轮保留已完成后端编译的2780个文件及两条模板链接，逐项哈希校验并验证568输入文件、生成registry/SDK摘要。首次恢复因容器绝对链接无法在宿主解引用而失败，未进入编译；改为校验目标后保留链接。后一次使用旧--recompile-c-only参数在Nuitka4.3解析阶段拒绝，失败日志保留；未作为成功或更改工具安全断言。r6改回标准构建命令，以只读挂载将全新environment-r4映射到原工具路径，使用两项精确摘要修复，构建仍在运行；是否复用对象以真实编译结果为准。没有使用开发调试选项替代正常构建，也未改写已归档r3产物。

父进程寿命修复的独立最小冻结程序已通过：保留启动器为僵尸不回收时，内层约6.03秒退出。修改MainProgram.c的POSIX检测为直接getppid关系，原文件SHA256 `8c6052f2b309924b5686e652c0d4f050dff5c0a370ef3c7f236561627605b88a`，补丁后`ea1b7b3ed2eaaf952d9be64b22db4ca0d5c8a00a33d8a6b9278f1bc21376b9ea`。安装器现先校验两个原文件再修改，防止第二文件漂移时部分应用；专项Linux3.14/macOS各8通过，Windows含构建与工作流22通过3个POSIX跳过，Ruff通过。独立environment-r4按哈希锁重新安装退出0，回执两个摘要均与各自探针匹配，F盘cache/native-signal-build-20261015-r2保留回执/安装/探针记录。当前在途完整构建r3仍只包含第一项信号退出码补丁；两项修复的最终完整产物、实际插件状态保护及回收仍待验收，不能用最小程序代替。

后续真实生命周期9类与RPC验收已通过，F盘cache/frozen-plugin-lifecycle-20261015-r2/reports保留报告；故障矩阵的宿主是源码，不应把同批冻结CLI基础调用推断为冻结产物的全部故障验收。真实Linux冻结程序的SIGINT投递（启动器/进程组）发现退出0，原失败保留。Nuitka4.3启动器在handler回收子进程后再次读取wait状态；独立环境最小修复两种投递返回130。安装器临时修复仅接受版本4.3及原OnefileBootstrap.c SHA256 `3ed26f09286629c3898452db484c7153827127521309c409395b197b1d8d7b95`，保存修改后摘要，漂移/重复应用拒绝；补丁与实际编译环境逐字节核对通过。Windows14通过1跳过、后续Linux3.14/macOS补丁专项各5通过；这些不代替完整二进制验收。上游锁定修复并通过相同真实信号验收后移除此兼容补丁。

完整修复构建首轮受WSL只读/I/O错误中断。Debian实际VHD已在D盘，重启后rw/fsync/回读恢复，但/tmp环境被清空；源码及已归档证据保留。重建环境改用持久`/var/tmp/opskit-native-signal-20261015-r2`，首个后台安装退出255；全新environment-r3前台安装退出0，完整编译容器opskit-native-signal-build-20261015-r3仍在运行，日志直接写F盘cache/native-signal-build-20261015-r2。没有用失败安装或旧二进制充作修复成功。

SIGKILL还需区分内层应用与外层启动器。原二进制在独立只读内存监控命令下复现：杀死启动器后保持其僵尸4秒，内层仍活；回收启动器后10秒观察内退出，报告launcher-lifetime-r2.json在上述F盘目录。Nuitka MainProgram.c用kill(pid,0)检测父存活，会将僵尸视为存在；此前communicate先等管道、再wait的探针因此可能延迟父回收。首个诊断脚本误传不支持的--output jsonl，错误已保留，修正为json且检查首个输出与父存活后才取得此证据。独立复制工具环境正在验证直接父关系检测，不修改在途完整构建的工具环境；尚未应用为产品修复，也不宣称SIGKILL门槛关闭。

2026-10-15 Linux x64完整冻结构建已取得成功终态：第二轮退出0，1640.6秒，31,206,056字节，二进制SHA256 `af6ddd96117c077bba0291bde023e3c4853001b46b9d2c0f88be02f4930ab160`。仍是568文件输入快照的测试公钥变体，不是生产签名发布。固定manylinux禁网隔离容器18项CLI检查通过，二进制所需GLIBC最高2.14，解压共享库中62项可识别GLIBC版本、最高2.17；SDK ZIP与生成的受控输入逐成员逐字节一致。此检查不等于动态库漏洞审计或所有Linux发行版兼容性。

同一二进制在Debian WSL宿主另外111项CLI检查通过，含98个帮助入口、JSON/退出码、CPU与PID首样本语义、无任务恢复；SDK内容一致、只读命令未创建平台运行目录。首轮宿主探针错误沿用非冻结wheel的update reconcile退出2契约；实际冻结程序正确返回退出0及no_job。保留accept-frozen-host-r1.py和原宿主输出，第二轮改为明确验证冻结契约，不放宽为接受任意状态。二进制、manifest/checksum、构建终态、原始输出ZIP及三份verification在cache/native-compile-source-20261015-r1。

发布构建的旧version-only检查已替换为scripts/verify_frozen_cli.py，按矩阵精确选择二进制、脱离源码工作目录，19项只读检查覆盖官方根help、版本、插件元数据/无效修复、CPU和冻结恢复契约；失败即阻断上传。当前Linux实际二进制19项通过。五文件快照ZIP `d6ee98db16cb78e35cc4d254819b9233c07938ebcd06cbaf7a20f342e623d84c` 三端各19项测试通过，覆盖错误版本、无效JSON/envelope、错误退出码、存储副作用、超时、异常更新任务与证据目录复用拒绝；清单/XML在cache/frozen-gate-source-20261015-r1。Windows/macOS只验证门槛脚本，尚无对应冻结产物结果。Windowsonefile缓存仍由打包器处理，本脚本不声明它全部位于输出目录。

同一Linux冻结二进制还通过真实Docker基础插件验收。源码监督器两次、冻结CLI一次共三次，每次21项隔离/参数/权限/配额观察检查全部通过，测试状态counter依次1/2/3；最终daemon未观察到这批插件执行容器残留。使用旧已审核base digest和wheel锁重新构建测试SDK镜像 `sha256:20d2ec615f51e7f36b251ad6c36e06e6d0a542928078bed7a3461cd0fbd03ce7`，code绑定包含测试公钥变体，不替换正式SDK策略7镜像或其漏洞处置结论。cache/frozen-plugin-20261015-r1保留命令/终态、构建上下文库存、实际调用结果和CLI日志；这批未执行完整RPC、获批网络、生命周期故障和daemon恢复矩阵。

工具链链接修复后的最终11文件ZIP摘要 `434f11d600890afc3b63afe6bc4742c58071ef73105190a77c79bc5794757f51`，cache/ci-lock-source-20261015-r3保存清单和三端XML；Windows3.12/Linux3.10/macOS3.12各12项通过。macOS使用既有环境，只验证源码/锁清单契约，不能表述为Intel新锁安装通过。Windows当前工作树与11文件清单逐项一致。Linux最终测试关闭pytest缓存，避免只读输入挂载的缓存警告。

Windows另完成真实已锁构建后端的`build --no-isolation --wheel`，wheel摘要 `be7f71dd7857773da2f5d57a02d04347d07af147748b1cd47ff5f1e2c85796a9`，核对265个运行时源/资源文件、270个wheel成员。全新独立venv按requirements-runtime.txt哈希安装后，以--no-deps安装此wheel，pip check通过；脱离源码执行98个help入口和总共111项CLI检查，以及两种Windows Python路由，模块来源均属于该环境，只读命令未创建运行数据。安装/检查日志、状态和verification.json在cache/wheel-ci-lock-20261015-r1；wheel及inventory在cache/ci-lock-native-20261015-r1。这是Windowswheel证据，不代替其他平台或冻结二进制。

Intel兼容性调查保存在cache/cryptography-intel-compatibility-20261015-r1，包括官方PyPI元数据。50.0.2没有Intel wheel且当次公告列表为空；49.0.0同样无Intel wheel且带有效漏洞公告；48.0.1/47.0.0/46.0.7有Intel wheel但均带有效公告。未据此降级或豁免，后续需评估受审工具链构建当前受维护版本或提供受支持的验收架构。公告列表不等于本项目可利用性结论，仍需逐项审查。

CI锁11文件完整夹具ZIP摘要 `799caa18847d9fcb9e98a1e993f43e03e0e57e9fa12263ace55b7e6c6bc970cf`，清单在cache/ci-lock-source-20261015-r2。Windows3.12与Linux3.10按锁新建环境后各12项通过，覆盖依赖闭包、锁版本/哈希和工作流安装边界；证据分别在cache/ci-lock-native-20261015-r1、cache/ci-lock-linux-20261015-r2，Linux安装日志在r1。首轮Linux夹具漏带三份被测锁文件，安装成功但2项测试失败；原8文件夹具及失败保留，最终使用完整11文件夹具重验。不是产品锁或安全断言退让。源码范围不包括完整仓库，不能代替三系统两Python组合远端CI、独立wheel流程及最终整仓验收。

完整Linux冻结编译的568文件快照与当前CI锁增量分开记录：前者输入ZIP为 `4ebd9c0571aa31b7084daad137470731d3220e982a8326c372a2f60b9983a3e9`，见cache/native-compile-source-20261015-r1。首轮禁网容器完成Python分析后在C阶段退出1：SCons DefaultEnvironment默认PATH不含/opt/rh/devtoolset-10，回退到缺失ilink。独立探针复现原失败，给同一GCC/g++添加/usr/local/bin链接后探针通过；发布流程已接入此预置，当时第二轮完整编译已启动，最终成功结果及验收见本节开头。首轮compiler.log、nuitka-crash-report.xml、failed-container-state.json及探针保留，重试句柄在retry-container.json。scons-diagnostic-status.json的0仅为诊断包装器退出，内部日志仍显示编译失败，不能作为编译通过。Nuitka报告可能排除_json和_bisect加速模块，后续需检查实际产物与性能；本次未做前后性能比较，不宣称运行提速。

草稿写入、失败重试与公开切换尚未在真实GitHub验收仓库执行。资产digest缺失或超过1000条发布记录的查找范围会明确拒绝，不降级为只核对大小。草稿不向普通下载者公开不等于仓库写入者不可见；检查不能阻止有权限的外部操作者在检查后改tag或资产，生产仍需tag保护及权限约束。发布test任务及回归环境的依赖锁已接入，六组合原生与真实远端安装运行尚待完成；Nuitka所需编译器/辅助工具需要预置并审核来源，不能恢复自动同意下载。完整四平台冻结构建、manylinux镜像漏洞审查、action远端执行及源码审查仍待验收。tag创建权限、生产签名密钥、Intel macOS依赖兼容性与远端CI仍需验收。GitHub并发配置尚无实际远端运行证据。上述专项不替代全项目最终回归，也不修改插件OS漏洞处置门槛。
