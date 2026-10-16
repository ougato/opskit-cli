# 垃圾代码候选复核

## 插件生命周期 Git 钩子边界（2026-10-16）

真实临时仓库反例证明：安装时全局模板中的post-checkout、更新时插件仓库中的post-merge均会在宿主执行，早于插件沙箱。反例仅写测试目录中的标记文件，Windows两项旧实现断言失败，原XML保留在`cache/plugin-git-hooks-before-20261016-r1.xml`。

安装和更新中的Git命令统一增加命令行`-c core.hooksPath=/dev/null`，包括rev-parse；Git for Windows也须以真实钩子检查此路径行为。命令行覆盖仓库、全局及环境配置中的hooksPath；测试覆盖模板钩子、默认仓库钩子、仓库指定目录、环境指定目录，并要求正常克隆及拉取内容仍成功。599固定候选三端各143专项及独立源码/XML审计通过（4钩子用例均执行），证据`cache/plugin-git-hooks-20261016-r1/audit.json`；不属于598快照的旧验收范围。安装产物验收及剩余Git配置路径另行核验。

这只关闭Git钩子执行路径，不是Git的沙箱：过滤器、远程助手、凭据助手、SSH命令、配置include、模板与其他本地配置仍需独立审查和约束。不能把hooksPath覆盖解释为任意不可信Git配置可安全执行；完整来源导入、更新事务和外部写入者隔离继续开放。

## 仓库提交边界检查（2026-10-16）

新增`python -I scripts/verify_repository_hygiene.py`并接入六组合回归工作流：核对Git索引和当前非忽略候选，拒绝运行数据、外部插件、虚拟环境、构建产物及环境配置；同时以38个不存在的路径探针检查共享`.gitignore`，不接受本机info/exclude或全局规则替代项目规则。示例`.env.example`允许提交，强制暂存的`.env`目录内容也会拒绝。只报告路径，不读取文件内容；秘密内容/历史及发行包分别由Gitleaks与wheel库存门槛负责。

本工作树实际检查通过：489已跟踪文件，109非忽略候选，无禁止项、无缺失规则；该计数只对应检查当时。三端各8项真实Git夹具回归通过，覆盖强制暂存缓存/插件/环境文件/环境目录/发行产物、示例、私有排除及反向忽略。原始结果和源码摘要在`cache/repository-hygiene-20261016-r2`，此前r1六项为较早规则版本。应用运行代码未改，不把此增量算入当前595应用全量；远端Actions尚未执行。

## 2026-10-16 宿主生产候选清理

本批复核并清理原93项候选中的39项生产候选：8处无用赋值、31个无用导入绑定；Java删除派生无用赋值后，同步移除该函数不再需要的GHPROXY_PREFIX局部导入，实际下载函数的代理回退保留。涉及15个宿主运行文件，未删除官方能力、常量定义或guest SDK文件。当前只剩54项测试候选，不等于全项目没有死代码或全部清理完成。

前14个局部ImportFrom绑定删除后，各导入语句仍保留使用中的名称；后17项包括9个无用局部绑定和8个宿主模块绑定。检查Recipe包__all__、core.sdk导出与固定guest库存、构建隐藏模块、仓库Python/文档消费者及模块初始化；没有找到这些绑定的受支持重导出用途。os/subprocess/tarfile不承担这些驱动的注册职责；core.platform仅在调用get_platform时探测；进度模块定义辅助函数与console，不依赖闲置spinner导入来启动功能。常量本体与真实消费者保留。此判断不承诺兼容未知第三方对宿主内部模块的私有属性依赖。

AST对照确认最后两批仅移除对应导入/别名，没有其他语句或业务调用变化；8处赋值基于主题只读查询、未使用URL和未读空缓冲区逐项确认。合并后30个现有测试文件三端各405项：Windows403通过/2跳过，Linux/macOS各404通过/1跳过；独立XML与585源码摘要复核一致，266份工作树运行文件与已测清单一致。Ruff正确性、静态54候选/新增0/基线未同步0、diff检查通过。证据为 `cache/reviewed-import-cleanup-20261016-r2/review.json` 及关联前两批审计。

清理后整仓、wheel和最终产物仍在接续验收；正在编译的r3为清理前基线，不能覆盖本批。没有代表性性能比较，不宣称这些删除带来运行提速。以下为先前批次历史证据，数字和在途状态仅适用于相应日期。


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
