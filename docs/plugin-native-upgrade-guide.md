# 原生插件 SDK 4 升级指南

当前接口以 [插件规范](plugin-spec.md) 为准；数据与命令迁移见 [业务分离与迁移](sdk3-business-separation.md)。

清单必须声明整数 `api_version: 4`。插件只依赖公开 `core.sdk`；重新审核内容、信任并 prepare 运行时。旧 SDK 1/2/3 不能执行，也不能通过旧 Creator RPC 绕过升级。已安装的 SDK 3 插件仍在管理列表中，可核对身份后更新或卸载；更新仍要求可验证的原安装来源和内容凭据。

SDK 3 升级到 4：删除 `ensure_software` 和 `software_bin`；`host_archive_directories` 改名为 `host_asar_directories`；删除 `open_service_menu` 和 `uses` 中无效的 `source` 参数，为每个提供或调用的服务填写完整契约。`select` 不再接受无法跨 RPC 序列化的 `on_ready`，选择菜单返回或按 Esc 返回 `None`。完整签名与交互规则见[插件开发指南](plugin-development.md)。

业务从旧专用执行接口迁移到 `host_execute`，自行解释原始退出码，并在验证成功后调用 `host_artifacts("commit", ...)`。未经验证的输出不得记入成功历史。菜单使用 SDK 的 `get_icon()` 与 `t()`，插件通过 `register_locale()` 注册 zh/en 文案。

授权由宿主展示和保存，不得由插件自行预置。新插件不能继承旧插件授权。宿主工具按当前用户权限运行，工程脚本可能随工具执行；提供准确参数、目录及输出范围供授权人检查。非交互调用未获授权时必须失败。

Windows 使用 AppContainer 与 Job Object；Linux/macOS 尚不支持，不回退为普通宿主进程。冻结包与真实工具验收需单独进行。
