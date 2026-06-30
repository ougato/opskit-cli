# 更新功能 精简/重构计划

> 目标：精简、高效、稳定、结构清晰、易维护。本轮只动"更新功能"相关代码，apply/rollback/health/Windows 兜底等稳定性核心**不动**。

## 一、现状结构（core/updater.py，1108 行）

入口（被 main.py 调用）：
- `check_and_apply_pending()` — 启动前台：清残留 / 健康回滚 / 应用 pending（main.py:246）
- `check_update_background(cfg)` — 启动后台：检测 + 下载 pending（main.py:299）
- `confirm_health()` — 启动 +15s 确认健康（main.py:321）
- `apply_pending_update()` / `has_pending_update()` / `pending_version()` — 退出时应用（main.py:464-468）

内部分区（注释已分好）：路径辅助 / bootstrap 控制面 / manifest 评估(kill_switch·min_build·rollout) / pending 检测 / 版本检测 / 下载校验 / 后台检测 / 热替换 / 回滚。

## 二、废弃代码清单（多轮扫描，附证据）

| # | 项 | 位置 | 证据 | 处置 |
|---|----|------|------|------|
| 1 | `update.mirrors` 死配置 | constants.py:33-36 | 下载实际走 `core/mirror.py:get_sources("github_releases")`（updater.py:596），从不读 `update_cfg["mirrors"]`；全仓无 `update_cfg.get("mirrors")` | **删除** |
| 2 | `rate_limit_hit` 缓存字段 | updater.py:455 | 只写不读，全仓无任何读取处 | **删除** |
| 3 | `_should_check` 的 `check_interval` 形参 + 区间分支 | updater.py:416-433 | 改"每次启动"后调用恒传 0，`(now-last)>=check_interval` 永真，区间逻辑已死 | **简化**为 `_update_allowed()`：仅判 `backoff_until` |
| 4 | `last_check` 写入（8 处） | updater.py:160/168/183/319/759/801/919/1011/1032 | 失去区间门控后，唯一消费者是 `_should_check` 的时钟跳变判断(`last>now`)，而该判断本身只为区间门控服务 → 整体失效 | **可选深度精简**（见下，需拍板） |
| 5 | `update.github_token` | updater.py:781 读取 | DEFAULT_CONFIG 无此键、无文档，属隐藏可选项 | 保留功能，建议文档化（不删） |

> 注：`update.channel` / `update.auto_apply` / `update.check_interval` 已在上一提交(093fe3f)删除。

## 三、精简方案

### 方案 A（保守 · 推荐先做，零风险）
1. 删除 `update.mirrors` 死配置（#1）。
2. 删除 `rate_limit_hit` 写入（#2）。
3. `_should_check(check_interval)` → `_update_allowed()`，函数体仅：
   ```python
   def _update_allowed() -> bool:
       cache = _load_check_cache()
       backoff = cache.get("backoff_until", 0)
       return not (backoff and time.time() < backoff)
   ```
   `check_update_background` 内 `if not _should_check(0)` → `if not _update_allowed()`。
4. 缓存 schema 收敛为：`{pending_version, latest, etag, download_version, backoff_until}`，函数顶部加一行 schema 注释。

不拆分文件（避免大重构 / 破坏 import 与测试），仅清理 + 重命名，结构本已分区清晰。

### 方案 B（深度精简 · 在 A 基础上叠加，改动面更大）
5. 移除全部 `last_check` 写入与时钟跳变逻辑（#4）。保留 `latest`（日志 + `_apply_update_pending_path` 兜底恢复 `_pending_version` 用，updater.py:358）。
   - 收益：缓存模型更干净，少 8 处无效写。
   - 代价：需改动 manifest 评估 / apply / windows 多处，测试同步多；与"稳定性核心不动"原则略有张力。

## 四、不动清单（稳定性核心）
- `_do_apply` / `_apply_unix` / `_apply_windows`（含 4 层 Windows 兜底、MoveFileEx、update_pending_path.json）
- `_check_health` / `mark_update_applied` / `confirm_health` / `rollback`
- `fetch_bootstrap` / `_evaluate_manifest`（kill_switch·min_build·rollout 灰度）
- 断点续传 + 版本感知 + SHA256 + 镜像降级（上一提交刚加固）

## 五、验证
- `pytest -q`（基线 469 passed / 11 skipped）。
- 同步测试：`test_should_check_*`、`test_T03`、`test_T21` 等涉及 `_should_check`/`last_check` 的断言。
- 改完确认 main.py 调用点签名不受影响（入口函数签名全部不变）。
