# 原生后端接入 / Native backend porting

当前仅实现 Windows；Linux/macOS 未完成前必须本地化拒绝执行。
不得添加普通 subprocess 或 Docker/WSL 回退来绕过后端缺失。
Windows is implemented. Linux/macOS must refuse execution until their native
backends are verified; ordinary host processes and Docker/WSL are not fallbacks.

## 共用边界 / Shared boundaries

| 层 / Layer | 责任 / Responsibility |
| --- | --- |
| `native_sandbox.py` | Backend/Process/Limits 契约及平台选择 / contracts and selection |
| `native_process.py` | 受限 stdio、语言环境、IO 回收 / stdio, language, IO cleanup |
| `native_invocation.py` | 私有快照、运行期限、RPC / snapshots, deadlines, RPC |
| `plugin_capabilities.py` | 本次/永久/拒绝、指纹、撤权 / consent, fingerprint, revocation |
| 宿主 broker / Host broker | 目录读取、摘要、上传、固定工具操作 / privileged operations |
| 平台后端 / OS backend | 真正建立隔离、验证后启动、回收进程树 / enforce, verify, launch, reap |

`launch()` 进入上下文前完成隔离验证，任何步骤失败都不得执行插件代码。
只提供指定只读/读写树和明确 stdio，不继承其他句柄、环境秘密或直接网络能力。
退出上下文必须结束整个所属进程树，包括父进程已退出的情况；不按进程名杀其他任务。
Enforce and verify isolation before executing plugin instructions. Expose only specified
trees and stdio, with no inherited secrets or direct networking. Reap the entire owned
process tree on exit without terminating unrelated processes by name.

## Linux 待办 / Linux pending

- 确认目标发行版的非特权 namespace、挂载及网络隔离可用性；不可用则拒绝。
- 实现只读运行时/源码、私有可写目录、无宿主网络、最小环境和 IPC。
- 选定 CPU/内存/进程数限制机制，验证权限不足和设置失败时插件未执行。
- 处理解释器布局和取消输入适配，不能继续硬编码 `python.exe` 或 Windows 按键。
- 验证崩溃归属凭据、租约、清理、打包资源及中英文提示。

Verify unprivileged namespaces/mount/network isolation on supported distributions,
resource-limit enforcement, interpreter layout, cancellation, recovery and packaging.
Missing prerequisites must fail closed.

## macOS 待办 / macOS pending

- 先验证受支持系统版本上可交付的原生隔离机制，不将旧工具名称当支持承诺。
- 验证文件、网络、子进程边界及签名/公证要求，再接入共同契约。
- 明确资源限制可实现范围；不能用 Windows Job 的测试结果代替 macOS 证据。
- 适配解释器、取消输入、恢复和发行包，保持相同授权与多语言行为。

Select and validate a deliverable native mechanism on supported macOS releases,
including signing/notarization, filesystem/network/process boundaries and limits.
Implement interpreter, cancellation, recovery and packaging without borrowing
Windows evidence as proof.

## 后端完成门槛 / Completion gate

必须在各自原生系统验证：未授权宿主读写拒绝、源码不可写、只读/可写树、
原始 TCP/UDP 拒绝或未送达、代理网络、环境/句柄泄露、超时/取消/撤权、
子进程回收、状态提交/回滚、崩溃恢复、实际资源上限及最终发行包 zh/en。
测试端点先从宿主验证可达，UDP send 返回不等于送达；不能只看错误类型。
Tests on each target OS must cover these boundaries and the actual final bilingual
binary. Validate receiver reachability first and distinguish send acceptance from delivery.

磁盘硬预算单独设计：目录扫描和完成后产物检查不是写入硬配额。
宿主 Creator 项目脚本以用户权限运行，不能描述成受上述目录隔离保护。
Disk hard budgets require a separate mechanism. Post-build scans are not write quotas,
and authorized host Creator scripts are not confined by the plugin's directory isolation.
