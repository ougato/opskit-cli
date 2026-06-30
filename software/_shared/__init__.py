"""版本化软件 recipe 的共享基础设施

集中复用 8 个多版本软件（mysql / mongodb / golang / java / nodejs /
postgresql / python / redis）中重复的样板逻辑：

- snapshot：安装快照读写（SnapshotStore）
- versions：在线版本列表四级降级解析（resolve_versions）
- drivers：平台驱动工厂（make_driver）

各 recipe 的 common.py 通过薄封装委托至此，保持对外函数签名不变。
"""
