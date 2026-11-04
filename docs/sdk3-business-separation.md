# SDK 3 业务分离与数据迁移

## 升级顺序

1. 备份当前数据目录；本次不自动删除或改写旧业务配置、历史凭据和授权记录。
2. 升级插件清单 `api_version: 3`，移除 `creator_build` / `host.creator_build` 调用，仅导入公开 `core.sdk`。使用通用执行和产物凭据接口，由业务插件解释成功码和产物规则。
3. 重新审核、信任并准备插件运行时。SDK 摘要与运行时策略均已更新。SDK 1/2 插件执行前明确拒绝，不存在兼容执行旁路；RPC 传输版本不随本次 SDK 变化。
4. 用户自行安装、审核 Cocos 插件；框架不自动安装、不自动授信，也不将旧执行授权换成新授权。

## 命令迁移

框架移除 `creator-tools`、`configure-creator`、`remove-creator`、`build-history`、`clean-build`。`plugin disk-policy` 只接受通用最低剩余空间参数；旧业务参数报错，不忽略。

Cocos 插件入口为 `opskit plugin run cocos -- ...`，命令包括 `tools`、`configure-tool NAME EXE`、`remove-tool NAME`、`build TOOL PROJECT --platform web-mobile`、`history PROJECT`、`settings`、`import-config PATH`、`import-history PROJECT RECEIPT`。

## 配置导入

显式调用 `import-config` 或“工具配置 → 导入配置”，选择旧数据目录中的 `config/common.yaml`。界面显示来源、拟导入内容和当前插件配置，确认后才替换。导入 Creator 工具路径、输出目录、保留数量，不导入授权。相同内容重复导入不重复覆盖；不同内容仍需确认。原配置保持原位。

旧历史逐条显式导入。只有来源结构、归属、原目录及父目录身份、实际内容重新核验通过，宿主才为当前插件登记新凭据。记录时间保留用于保留策略。无法证明的历史仅作为旧只读数据，拒绝接管和清理。导入不删除原凭据或产物。

## 验收边界

源码测试、正式原生 worker、真实 Creator 构建、冻结包 CLI 和冻结包插件执行是不同证据，应分别记录。Windows 为当前实现后端；Linux/macOS 继续明确拒绝。真实宿主工具按用户权限运行，可能执行项目脚本。空间检查不属于磁盘硬配额。

本次测试与真实构建结果见 [分离验收](sdk3-separation-acceptance.md)。
