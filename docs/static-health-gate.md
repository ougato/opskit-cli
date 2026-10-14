# 静态代码健康门槛

开发依赖固定Ruff 0.17.0，配置位于pyproject.toml，CI依次执行：

```console
python -m ruff check .
python scripts/check_static_debt.py
```

第一条检查语法、未定义名称、无效控制流、恒真判断和无意重定义等正确性问题，当前基线为零；不以noqa或全项目忽略隐藏已有实际缺陷。跨文件pytest fixture用明确的同名导出保留注册用途。

第二条只跟踪F401/F841候选，docs/static-debt-baseline.json记录当前94项候选。按文件、规则、消息及出现次数比较，不因行号变化失效；不允许新增种类/文件或增加次数。清理候选时必须同次缩减基线，不保留已经解决的豁免额度。工具版本必须与基线一致，升级时重新审查差异，不自动接纳新增候选。

候选不等于垃圾代码。公开SDK、Recipe动态注册、资源加载、跨平台驱动、pytest fixture及构建导入必须先核对调用与导出契约；不得批量运行unsafe-fixes或把未使用导入全部删除。基线只是可维护的存量清单，不代表这94项都必须保留、都已安全或都已完成清理。位于同一文件且消息/数量相同的候选移动不会形成新增诊断，仍需代码审查其实际责任边界。

本轮修复MySQL Windows驱动缺subprocess导致junction操作失败，控制cmd加/d避免AutoRun；补齐三个版本驱动的Path注解、网络threading注解；清理重复导入和HTTP函数内从未读取的last_exc。测试使用一次性目录，没有写用户注册表或真实安装路径。该静态门槛不验证cmd路径转义、解压目录逃逸、沙箱隔离、事务恢复或性能；这些仍按项目完整健康计划分别验收。


## 已复核的下一批导入候选

2026-10-14 当前源码复核；全量回归运行期间暂不改源码，以下不是已删除清单。

| 位置 | 候选 | 证据与决策 |
|---|---|---|
| core/config.py | os、core.constants.APP_NAME | 本模块无读取；仓库无config.os/config.APP_NAME或对应from导入；未声明为配置公开接口。APP_NAME常量本体保留在constants，只清理此处遗留导入 |
| core/constants.py | pathlib.Path | 常量定义不使用Path，未声明__all__导出，仓库无constants.Path使用；常量模块职责无需重导出Path |
| software/base.py | dataclasses.field | dataclass用于InstallStep，field无读取；仓库Recipe调用、构建hidden-import、SDK导出均不依赖base.field。保留dataclass、Recipe和异常公开接口 |

上述模块没有按这些名字执行动态注册或资源加载；检索缺引用只是证据的一部分，已结合模块实现及SDK/构建职责核对。移除时同步删除对应四条基线，不保留已经消除的额度，运行静态正确性及相关配置/Recipe回归。其他103个候选仍需逐项分析；跨文件pytest fixture、显式SDK导出及必要平台分支不可据此批量删除。

网络菜单另复核9个候选，仍待本批回归结束后删除：6个未用导入time、rich.live.Live、shield_ctrlc、print_info/print_success/print_warning；show_dns的error_c/muted以及端口扫描菜单的error_c三处未读局部赋值。其余函数里的同名变量实际用于渲染，必须保留，不能按名称全文件删除。

依据：这些导入没有本模块读取或仓库重导出使用，菜单注册调用show_*函数而不依赖这些名字；get_color仅查询主题字典并返回字符串，三处未读赋值没有必须保留的副作用。清理时保留_ctrlc_guard、信号与进程回收、表格渲染、暂停交互和所有已使用颜色。该批与上面的4项合计13个候选，目前均未删除，基线仍107项；不要将审核数量当成已消除数量或宣称启动提速。


上述13项在双平台traceroute全量回归终态后已清理，基线同期缩减107→94。保留所有使用中的同名变量和公开接口。Ruff关键正确性通过，静态候选94/新增0/已解决未移除0；相关配置/Recipe/菜单/网络回归结果以实施验收台账为准。
