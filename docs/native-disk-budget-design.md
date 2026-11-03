# 磁盘空间策略 / Disk space policy

已确认采用无需管理员的方案；不安装系统组件，不提供磁盘硬配额。
Approved: no elevated component and no filesystem hard quota.

## 配置与入口 / Configuration and entry points

插件管理 →「磁盘空间与构建历史」（第 10 项）；中英文均可使用。
Plugin management → Disk space and build history (item 10), available in both languages.

配置保存在 OpsKit 数据目录下的 `config/common.yaml`，由宿主维护，不放在插件代码内。
Host-owned configuration lives in the OpsKit data directory at `config/common.yaml`.

```yaml
plugin:
  disk:
    minimum_free_mib: 2048
    creator_output_directory: ""
    keep_builds: 5
```

- 最低剩余空间默认 2048 MiB，可配置 256..1048576 MiB。准备工作区时还检查逻辑工作区所需容量。
  Default free-space threshold: 2048 MiB (256..1048576); workspace allocation also checks its logical budget.
- 输出目录留空使用项目 `build`；自定义值必须是已经存在的普通绝对目录。改变输出位置会改变 Creator 授权范围。
  Empty output uses project/build; custom output must be an existing ordinary absolute directory and changes consent scope.
- 默认保护最新 5 个有记录的成功构建，可配置 1..100。保留数仅决定清理候选，不自动删除。
  Retain the latest 5 recorded successful builds (1..100). Retention selects candidates, never deletes automatically.

正式命令 / Official commands:

```text
opskit plugin disk-policy
opskit plugin disk-policy --minimum-free-mib 2048 --keep-builds 5
opskit plugin disk-policy --output-directory E:\builds
opskit plugin build-history E:\game
opskit plugin clean-build E:\game TOKEN --record-sha256 DIGEST
```

使用 `--output-directory ""` 恢复项目目录；交互菜单输出目录直接回车亦可恢复。
Use an empty output-directory argument, or submit an empty directory in the menu, to restore project/build.

## 检查和安全清理 / Checks and safe cleanup

Creator 启动前检查项目盘与输出盘，运行期间每两秒检查输出盘。低空间会拒绝启动或中止任务。
这些检查不能预留物理空间，也不能硬阻止检查间隔内的写入。
Check project/output volumes before Creator starts and output volume every two seconds while running.
Low space refuses or interrupts work; checks neither reserve physical space nor prevent intervening writes.

仅记录成功且通过产物检查的新构建。清理需明确确认，绑定记录摘要、目录身份与文件内容；
未知、修改过、链接/替换目录和最新保留记录都不会被删除。构建与清理共用项目锁。
Only validated successful new builds are recorded. Explicit cleanup binds receipt digest, directory identity and content;
unknown, modified, linked/replaced and retained outputs are protected. Builds and cleanup share a project lock.

宿主记录保存在数据目录 `creator-build-history/`。检查有 30 秒、4096 个目录条目、
每项目 128 条记录/8 GiB 累计记录容量的安全上限；单产物最多 2 GiB/65536 条目。
超限、取消或清理失败会拒绝继续，保留产物和恢复线索，不擅自扩大删除范围。
History is host-private in creator-build-history/. Inspection is bounded to 30 seconds, 4096 ledger entries,
128 records/8 GiB per project; each output is bounded to 2 GiB/65536 entries.
Budget or cleanup failures preserve remaining outputs and receipts instead of widening deletion scope.

清理前停止外部工具使用该输出；项目锁不能锁住其他程序。Creator 项目脚本以用户权限运行，
指定输出位置并不限制脚本写入其他目录。管理员硬配额组件不在本期范围。
Stop external consumers before cleanup; project locks do not coordinate other applications.
Creator project scripts run as the user and can write elsewhere. Elevated hard quotas are out of scope.

Linux/macOS 沿用配置、预算、授权与恢复契约，各自实现并验证原生后端，见
[平台待办](native-backend-porting.md)。
Linux/macOS retain these policy and recovery contracts with independently verified native backends.


## 本次验证 / Follow-up validation

- 相关回归：100 passed、2 skipped；最终磁盘策略/Creator/语言检查：54 passed。
  Related regression: 100 passed, 2 skipped; final policy/Creator/i18n group: 54 passed.
- 真实 Creator 3.8.7：正式 SDK、单次授权、web-mobile 构建成功（退出码 36），69 个文件、15601168 字节；
  报告 `cache/native-disk-creator-20261103-zh/verification.json`。
  Real Creator 3.8.7 succeeded through the formal SDK and one-time consent; 69 files, 15601168 bytes.
- 新 Windows 包：`cache/native-pyinstaller-build-20261103-disk/source/dist/opskit-windows-x64.exe`。
  New Windows binary at the path above. The previous full regression does not cover this follow-up.
- 此增量未重跑此前约 38 分钟的全量回归，也未将源码 Creator 验证描述为新 EXE 的完整构建验证。
  The earlier full suite was not rerun; source Creator acceptance is not frozen end-to-end build proof.

冻结包新增 CLI 检查 5 项通过，报告 `cache/native-disk-frozen-20261103/verification.json`。
Frozen binary: five policy/history/help checks passed.

SHA256: `074182f1ee01d762c9e9e7c4464a456391ff2f41881e8753e16d6f6d3d8bd706`.
