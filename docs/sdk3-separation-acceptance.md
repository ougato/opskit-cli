# SDK 3 分离验收（2026-11-04）

## 范围

框架源码：`E:/ougato/tools/cli`。独立插件：`E:/ougato/project/opskit/plugin/cocos`。测试数据、样例工程和冻结包均置于 CLI 的 cache 子目录；没有安装或授信用户的实际 Cocos 插件，没有自动迁移或删除用户旧数据。

## 已验证

- 框架活动 core/plugin 代码不含 Creator/Cocos 业务规则；插件业务源码仅导入公开 core.sdk。SDK 版本为 3，运行时策略 9，RPC 传输版本独立。
- 菜单中英文、五项管理/维护路由、四字名称、主题图标列宽、返回上层；Windows TTY 中实际查看主菜单和维护菜单，验证数字返回、Esc 返回和 Ctrl+C 退出。标题间距及后台 HTTP 回归保留。
- 原生调用、执行预算、提示保护、服务契约、安全边界与业务回归合计一组 115 项通过。菜单与维护叶子回归一组 54 项通过。最终业务、安全、菜单、标题和 HTTP 回归为 **94 通过、1 跳过**。
- 工具执行覆盖拒绝、参数变化、工具替换、原始退出码、静默撤权、超时与取消回收。资源锁按真实目录身份绑定，拒绝歧义路径。
- 产物凭据覆盖活动锁、未知/跨插件、内容变化、身份替换、保留保护、凭据摘要、失败不入成功历史和旧记录重新核验。
- Creator 3.8.7 的真实 web-mobile 构建：正式原生 worker，关闭 debug，返回 36；新产物 69 文件、11,343,062 字节，index.html 非空。JSON configPath 保留 debug 的布尔类型。证据：`cache/cocos-sdk3-final-20261104/verification.json` 及 `cache/sdk3-frozen-20261104/cocos-final-source.log`。
- PyInstaller 独立冻结包构建成功。分开进行了 30 项 CLI 验证、中英文原生 worker 隔离、状态提交、失败回滚与撤权验证；各阶段结果保存于 `cache/sdk3-frozen-20261104/`，报告带各自二进制 SHA256，不能混作同一构建证据。

## 实际边界

符号链接测试在当前 Windows 没有创建权限时跳过，其余链接/路径检查有独立断言。真实工具验收仅覆盖 Creator 3.8.7 web-mobile；windows/android 仅验证参数和非空导出工程策略，未编译、安装或运行原生应用。Linux/macOS 未新增支持。逻辑空间检查不等于磁盘硬配额。真实网络发布/S3 不属于本次分离验收。

没有运行整个仓库全量测试；使用针对性测试和实际工具/冻结包验收。旧配置和历史的真实用户数据导入未执行，使用隔离样本覆盖拒绝、确认、重复导入、旧授权不导入、内容变化拒绝接管等路径。

## 最终冻结包与真实构建

最终二进制 SHA256：`41261a55d239110b78d129096b758010c782ab729ecc7cefc82463b7e921a7d9`，路径 `cache/sdk3-frozen-20261104/source/dist/opskit-windows-x64.exe`。

最终包通过 30 项 CLI 检查，报告位于 `cache/sdk3-frozen-20261104/release-cli/verification.json`。

通过该包在 Windows TTY 中实际调用独立 SDK 3 Cocos 插件：逐项选择本次授权，展示宿主权限边界、工具摘要、参数、项目与输出范围。Creator 使用 JSON configPath 开启 debug，返回 36。产物凭据登记后再次重算快照匹配，index.html 非空。证据：`cache/sdk3-frozen-20261104/cocos-frozen-verification.json`。

框架不自动授信实际插件；以上运行使用隔离的验收副本与样例工程。当前插件源码的后续安装仍需用户按常规审核、信任并 prepare。
