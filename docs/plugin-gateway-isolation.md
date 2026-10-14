# 插件出口代理的独立信任边界

第三方依赖只能影响业务 worker，不能进入负责私网限制的出口代理。运行平台必须独立准备并验证代理；没有有效代理时，有 network 权限的插件拒绝运行，不回退到插件镜像或宿主网络。

## 已复现的问题

旧实现用插件 SDK 镜像启动 core.plugin_egress。插件依赖 wheel 可以带 Python 的 .pth 启动钩子；固定依赖摘要只能固定这些代码，不能使它们成为平台可信代码。合成实验在实际旧 SDK30ce3718 中通过只读挂载模拟已安装 .pth，改写 ipaddress 判定：默认拒绝的127.0.0.1，在没有私网授权时被代理策略接受。实验只做字面IP解析，没有建立连接，也没有安装真实恶意 wheel；两个实验容器均确认清理。

证据位于 cache/gateway-dependency-boundary-4119360085a04517896cf5e6515e9a25/verification.json。该样例证明 Python 启动钩子能够污染同镜像的策略进程，不是完整的远程插件安装攻击链验证。

## 实现

- core.plugin_gateway 使用平台固定官方 Python OCI 摘要，不接受插件的基础镜像、依赖锁、Dockerfile或镜像参数。构建上下文只有 Dockerfile 和平台 gateway.py；不安装第三方依赖，移除 site-packages 与 pip 启动工具。
- 插件 prepare 仅在 manifest 声明 network 时准备共享平台代理。代理使用独立构建锁、保留的 .gateway.json 库存与不可变 image ID；执行时核对策略版本、固定基础来源、平台源摘要和实际镜像标签。插件名不能与保留库存文件冲突。代理源变更不改变 SDK 源摘要。
- 代理以独立容器运行，继承既有只读根、UID65534、无 capability、no-new-privileges、日志关闭和资源额度；显式指定 Python entrypoint，以 -I -S -u 执行固定路径脚本。不会加载插件 site-packages、PYTHONPATH 或启动钩子。
- SDK策略升至6，SDK库存移除 plugin_egress.py。冻结程序的源码包保存两个明确库存，准备时分别选取 SDK 或代理，不能把整包复制到插件镜像。
- 私网列表仍在创建代理前重新读取当前插件的精确授权，代理令牌/网络/清理沿用现有资源账本；无 network 权限不查代理、不创建网络。此改动没有增加第三方业务CLI参数到平台。

## 验证与未完成项

首轮专项 Windows163通过/1跳过，Linux164通过；随后补充坏YAML脱敏用例和代理构建工具清理断言，后续终态见 implementation-acceptance.md。实际平台代理 c692074f5280c0f677fb9b195b23b99ab26dca1d35fb79537a210ba7f4e94532 的基线/同一钩子复验均拒绝回环，两个探测容器清理确认。未与外部服务建立连接，不能代替完整HTTP/CONNECT/DNS、Docker宿主桥接可达性、运行中授权变更或daemon故障矩阵。

后续必须验证实际插件 prepare 与执行、物理包清单/OS漏洞、源码与冻结包库存、完整RPC/出口矩阵和当前wheel；也需核对 Docker internal 网络对宿主桥接地址的可达性。固定来源和非root不表示镜像无漏洞。基础来源或构建约束变更须更新代理策略身份，并重新验收，禁止静默复用旧库存。

## 宿主桥接边界与本批复验

受控实测发现普通 internal bridge 仍可访问宿主桥接地址上的自建监听；加入 inhibit_ipv4 后该地址不能绑定监听，同时同网络容器仍可通信。生产 _network 已明确指定 bridge/internal/IPv6禁用/inhibit_ipv4，不支持即失败，没有普通桥接回退。证据 internal-bridge-boundary-19e6a1bace50491eb5268f255688dfec/verification.json；仅当前daemon和IPv4，不代表全部网络部署。

仓库内 scripts/verify_plugin_gateway.py 要求全新 --output 目录，调用实际 _network，仅创建自有HTTP peer、客户端及网络；两种授权均能连接代理、不能直连peer，未授权代理访问拒绝，精确授权可读自有标记，宿主网关监听均不能绑定。精确资源清理并确认缺失、恢复账本各项0。实际运行 cache/gateway-committed-verifier-20261014/verification.json 退出0；它未覆盖完整插件 prepare/invoke 生命周期、CONNECT/DNS或动态撤权。

最终代理专项 Windows164通过/1跳过、Linux165通过；网络创建约束专项两平台各64通过，均退出0。显式指定 site-packages 路径的物理分发元数据探测为空，基线/合成启动钩子均拒绝回环，容器清理确认（gateway-native-validated-20261014）。policy6 SDK ad31a18c8c752822ea855cf3495c2f7355be5984afd27d7914ae2492b1370664 真实RPC矩阵通过、27worker清理、恢复账本各项0；样例无network权限，不能代替完整网络插件验收。

实际SDK与独立gateway同数据库OS扫描均完成：Critical0/High44/Medium58/Low61/Unknown3，High对应8编号，处置未关闭。SDK实际13个Python包 pip-audit 查询0已知匹配，不涵盖OS、解释器、宿主或未知漏洞。当前wheel fa07622f28fd639b6931cbcc96b5174a40b617ade5ce6f269e88aae88052ac80 源码/RECORD核对通过，251运行文件/256成员；安装入口及完整源码回归终态另见实施台账，未完成前不登记通过。

## 完整合成插件网络入口

scripts/verify_plugin_gateway.py 增加成对 --base-image/--lock-file 选项，先验证审核digest与完整wheel锁，再生成独立gateway_probe插件，使用实际 plugin_runtime.build、plugin_sandbox.invoke及持久状态发布。43245退出0，cache/gateway-full-plugin-20261014/verification.json 记录无私网授权/精确授权/撤销后再次调用三场景，代理均可达、直连均拒绝，仅精确授权读取自有peer标记；实际policy6 SDK与独立gateway身份均匹配。恢复账本recovered/active/pending/invalid全0。该初轮不覆盖运行中的私网撤销。

随后验证器新增保持实际CONNECT隧道期间的注入取消及插件身份撤销，核对已提交状态不变、暂存状态不发布；结果终态见实施台账。注入KeyboardInterrupt不是操作系统Ctrl-C，插件身份撤销不是私网授权列表运行中撤销。新目录每次独占创建，调用后恢复OPSKIT_DATA_DIR，全部网络目标仍为自建peer。

最终网络生命周期8325退出0（cache/gateway-plugin-lifecycle-20261014/verification.json）：上述三次调用及CONNECT已建立后的注入取消、身份撤销通过，已提交proof保持原字节、uncommitted未发布、恢复账本四项0。本批验证器可复用；仍需实际私网列表在已有代理中的撤销、完整DNS/CONNECT目标策略、真实OS信号与daemon故障验收。

运行中私网授权缺口已追加实现与实测：原授权列表随_network绑定到线程调用作用域，实时身份/服务复核使用同一次配置读取检查原列表仍为当前授权子集；去重/重排或新增不扩展已有代理，删除任一项终止调用，嵌套检查包含外层。19314退出0，gateway-private-revocation-20261014实际CONNECT建立后移除私网授权，出现固定private network authorization revoked错误、原提交状态保持、资源恢复四项0。双平台相关专项各82通过。只证明当前Docker IPv4及轮询式撤权，不证明即时逐请求撤权、DNS完整策略或daemon故障矩阵。

清理后发布也保留授权依赖：实际invoke在_network退出后仍保留已使用私网列表，最终状态提交前复核，顶层finally恢复外层；嵌套已使用授权持续到调用方提交。请求最多256份已使用列表，总编码64KiB，每份沿用256项/16KiB限制。双平台最终专项各85通过；gateway-private-commit-20261014实际cleanup_revoke在全部网络清理后撤权，拒绝发布，原proof不变、uncommitted不存在、恢复账本四项0。该精确竞态由验证器在真实资源作用域退出后改变授权触发，不是断电或全部署配置证明。

## 正式网关策略2

候选方案已纳入固定构建，独立POLICY从1升至2，强制旧catalog失效。98809实际gateway359ebe4520dd3b516b0894ef81fcb9a3784eec7390a2498d37b546dc03b6daa2完成正式构建/标签与catalog身份复核，没有实验解析器覆盖；仓库验证器物理检查infocmp不存在、三个可执行目录setid位为空、site-packages分发元数据为空，SSL/SQLite/策略导入成功，实际UID65534/CapEff0/NoNewPrivs1/Seccomp2。完整合成网络插件及CONNECT期间取消/身份撤销/私网撤销/清理后撤权通过，资源恢复四项0。SDK镜像仍ad31a18c，未把网关硬化误混入插件依赖库存。双平台相关专项各95通过。

实际新镜像独立OS复验仍High44包记录，因包元数据和共享库保留，不是测试失败或漏洞清零。网关移除受影响infocmp不能代替SDK的该组件处置，四项util-linux、libacl、homed与Archive::Tar的权限/适用性分析和完整部署门槛仍按基础镜像审计追踪。
