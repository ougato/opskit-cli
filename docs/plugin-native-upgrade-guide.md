# 原生插件 SDK 3 升级指南

当前接口以 [插件规范](plugin-spec.md) 为准；数据与命令迁移见 [业务分离与迁移](sdk3-business-separation.md)。

清单必须声明 `api_version: 3`。插件只依赖公开 `core.sdk`；重新审核内容、信任并 prepare 运行时。旧 SDK 1/2 不能执行，也不能通过旧 Creator RPC 绕过升级。

业务从旧专用执行接口迁移到 `host_execute`，自行解释原始退出码，并在验证成功后调用 `host_artifacts("commit", ...)`。未经验证的输出不得记入成功历史。菜单使用 SDK 的 `get_icon()` 与 `t()`，插件通过 `register_locale()` 注册 zh/en 文案。

授权由宿主展示和保存，不得由插件自行预置。新插件不能继承旧插件授权。宿主工具按当前用户权限运行，工程脚本可能随工具执行；提供准确参数、目录及输出范围供授权人检查。非交互调用未获授权时必须失败。

Windows 使用 AppContainer 与 Job Object；Linux/macOS 尚不支持，不回退为普通宿主进程。冻结包与真实工具验收需单独进行。
