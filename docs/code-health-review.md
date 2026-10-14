# 垃圾代码候选复核

## 监控模块导入复核（2026-10-13，已清理）

monitor/commands.py的typing.Any，以及monitor/menu.py的rich.columns.Columns、rich.layout.Layout和core.prompt._read_key，当前仅在导入处出现。仓库Python/文档中未发现从这两个模块导入候选或以模块属性访问候选；CLI动态映射只指向show_*和get_*业务函数，菜单实际读取使用_kbhit等保留接口。build.py隐藏导入的是整个菜单/采集模块，不是这些符号；SDK __all__及固定guest库存没有导出这四个符号。它们属于明确可清理的宿主导入，不涉及删除监控能力。当前完整回归仍在运行，先保留生产源码稳定；实际删除、静态基线同步和相关菜单/采样回归完成后再登记清理成功。

前述是清理前的证据记录。四项现已删除并同步静态基线，108存量/0新增/0差异；清理后菜单/采样/CLI/静态相关86项通过（cache/monitor-import-cleanup-20261013.log），diff通过。新wheel及完整验收终态另见implementation-acceptance.md，不引用旧批次替代当前产物。

静态基线的123项是F401/F841候选，不是123段已经证实的死代码。删除前核对模块内部读引用、仓库调用方、SDK公开导出、动态加载和打包库存；不能据单纯文本搜索删除功能或兼容入口。

## 核心模块宿主导入（2026-10-13）

以下11项已复核并删除。清理前完整回归2022通过/13跳过；清理后相关回归与安装包检查另行记录，不混称同一源码时点。

| 文件 | 候选 | 证据与边界 |
|---|---|---|
| core/mirror.py | json、GITHUB_API_RELEASES、MAX_RETRY_DOWNLOAD、TIMEOUT_DOWNLOAD_STALL | json模块无读引用；resp.json是HTTP响应方法。三个常量仅在导入列表出现；所有实际下载/重试期限读取保留，常量定义保留。仓库无从mirror导入这些符号或访问对应模块属性的调用 |
| core/platform.py | dataclasses.field、typing.Any | 当前数据类使用dataclass和实际注解，无field/Any读引用；保留全部平台探测与公开Issue/Platform接口 |
| core/privilege.py | require_root局部print_error、get_icon | require_root只使用t和msg；Unix/Windows实际打印路径均在各自执行函数中导入print_error。移除局部冗余导入不删提权流程 |
| core/progress.py | TimeElapsedColumn | 进度列组装没有使用该列，其他列和进度生命周期全部保留 |
| core/telemetry/sentry_provider.py | sys | 无sys读引用，Sentry协议、DSN配置及上报行为保留 |
| core/update_download.py | worker局部json | 已统一使用updater结构化记录读写，没有json读引用；保留签名、候选发布和恢复事务 |

以上模块不属于core/plugin_runtime_inventory.py的21项固定guest库存；core.sdk.__all__不导出候选符号。检查范围包含当前仓库Python调用方与文档，并不声称检查了未知第三方对宿主内部模块的私有依赖。第三方仅应使用稳定core.sdk接口。

暂保留core/config.py的os、APP_NAME及core/constants.py的Path。它们同为静态未使用导入，但文件属于guest SDK库存；清理会改变code摘要，需在专门批次重建镜像并重新验收，不应与仅宿主导入清理混同。另有109项仍需逐项复核，共112项保留候选。

已同步移除对应11项静态基线，guest摘要保持e206ac9a0961d3e34567d2dc4c39439b3c478b3ed264cf29329f5b8c04d3de6d不变。固定Ruff门槛112项/新增0/基线差异0，进度/提权/遥测导入烟测及diff检查通过；相关平台/镜像/下载/静态门槛回归78项通过，新wheel验收仍在运行，结果以implementation-acceptance.md为准。没有代表性性能测量，不以少几个import宣称运行显著提速。
