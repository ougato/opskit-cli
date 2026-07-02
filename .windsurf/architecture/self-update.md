# 自更新机制设计

> 所属主流程：[overview.md](overview.md) → 后台线程 3（opskit-updater）+ 退出时热替换

---

## 1. 设计目标

- 用户无感知后台检测新版本
- 下载完成后不打断当前操作，退出时静默替换
- 网络不可达时优雅降级，不影响正常使用
- Windows 四层防御确保更新必然完成
- 控制面（bootstrap manifest）：急停 kill_switch / 强制升级 min_build / 灰度 rollout%
- 崩溃回滚：新版本反复启动未确认健康时自动回滚（健康探针）
- 所有失败点接入 telemetry 上报，可追溯诊断

---

## 2. 完整生命周期

```mermaid
flowchart TD
    subgraph 启动阶段
        S0[cleanup .old.exe 残留]
        S0 --> S1[update_health.json 健康探针检测]
        S1 -->|未确认 + fails≥MAX_HEALTH_FAILS| ROLLBACK[rollback 回滚到上一备份 + 重启]
        S1 --> S2[update_pending_path.json 兜底处理]
        S2 -->|有标记| S2a[rename pending → self_path]
        S2 --> S3[check_and_apply_pending]
        S3 -->|--post-update 标记| S4[清理 pending / tmp 文件<br/>跳过本次]
        S3 -->|有 pending 文件| S5{验证二进制<br/>PE/ELF 头 + 大小>100KB}
        S5 -->|合法| S6[_do_apply 应用更新]
        S5 -->|不合法| S7[删除 pending + telemetry 上报]
        S3 -->|无 pending| S8[继续启动]
    end

    subgraph 后台检测
        C1[check_update_background]
        C1 --> C2{距上次检查 > check_interval?}
        C2 -->|否| C2a{已有 pending?}
        C2a -->|有| C2b[恢复 _pending_version]
        C2 -->|是| C3[_resolve_update_target]
        C3 --> C3a{控制面 manifest 可用?}
        C3a -->|是| C3b[_evaluate_manifest<br/>kill_switch / min_build / rollout 灰度]
        C3a -->|否 降级| C4[_fetch_latest GitHub Releases API]
        C3b --> C5{应更新?}
        C4 --> C5
        C5 -->|否| C6[更新 last_check 时间戳]
        C5 -->|是| C7[_download_update<br/>多源 + 断点续传 + SHA256 双源校验]
        C7 -->|成功| C8[atomic rename → opskit.pending<br/>写 pending_version 到缓存]
        C7 -->|失败| C9[telemetry 上报 + 静默跳过]
    end

    subgraph 退出阶段
        E1[_on_exit → apply_pending_update]
        E1 --> E2[备份当前版本<br/>opskit.v{N}.{ts}.bak]
        E2 --> E3[SHA256 验证备份完整性]
        E3 --> E4{平台?}
        E4 -->|Linux/macOS| E5[Path.rename 原子替换<br/>保留原文件 mode/uid/gid]
        E4 -->|Windows| E6[_apply_windows 四层防御]
        E5 --> E7[清理旧备份 保留最近 3 个]
        E6 --> E7
    end

    subgraph Windows 四层防御
        W1[① PS 脚本<br/>Wait-Process → Rename-Then-Copy → icacls]
        W2[② 跨驱动器降级<br/>rename 失败时 Copy-Item]
        W3[③ MoveFileEx<br/>DELAY_UNTIL_REBOOT 重启后替换]
        W4[④ update_pending_path.json<br/>兜底标记 下次启动再尝试]
        W1 -->|PS 执行策略被禁| W2
        W2 -->|均失败| W3
        W3 -->|无管理员权限| W4
    end

    style 启动阶段 fill:#1e1e2e,stroke:#89b4fa,color:#cdd6f4
    style 后台检测 fill:#1e1e2e,stroke:#a6e3a1,color:#cdd6f4
    style 退出阶段 fill:#1e1e2e,stroke:#f9e2af,color:#cdd6f4
    style Windows 四层防御 fill:#1e1e2e,stroke:#f38ba8,color:#cdd6f4
```

---

## 3. 核心组件

### 3.1 控制面（Bootstrap manifest）

**文件**：`bootstrap.json`（远程，CDN 优先 + GitHub raw 兜底） / `bootstrap_cache.json`（本地缓存）

```json
{
  "schema_version": 2,
  "channel": "stable",
  "latest_build": 2,
  "display": "v2",
  "min_build": 1,
  "rollout": 100,
  "kill_switch": false,
  "notes": "",
  "update_mirrors": [
    "https://mirror.ghproxy.com/https://github.com/ougato/opskit-cli/releases/download",
    "https://github.com/ougato/opskit-cli/releases/download"
  ]
}
```

**字段语义**：
| 字段 | 含义 |
|------|------|
| `latest_build` | 最新整数 build；`> APP_VERSION` 才更新 |
| `min_build` | 低于此 build 强制升级（忽略灰度比例） |
| `rollout` | 灰度放量百分比 0-100，按机器指纹哈希分桶 |
| `kill_switch` | 紧急熔断，置 true 时全网停止自更新 |
| `display` / `notes` | 展示版本串 / 更新说明 |

**拉取策略**（`fetch_bootstrap`）：
- `ThreadPoolExecutor` 并发请求所有 `BOOTSTRAP_URLS`（CDN 优先），取最快返回的结果
- 成功 → 写入本地缓存 `bootstrap_cache.json`
- 全部失败 → 读本地缓存兜底；仍无 → 返回 None

**评估**（`_evaluate_manifest`）：
1. `kill_switch` → 直接放弃本次更新
2. `latest_build <= APP_VERSION` → 不更新
3. `APP_VERSION < min_build` → 强制更新（跳过灰度）
4. 否则按 `_machine_bucket()`（机器指纹 SHA256 % 100）与 `rollout` 比较，未命中则跳过

### 3.2 版本检测（控制面优先 + GitHub 降级）

**触发条件**：`time.time() - last_check >= check_interval`（默认 86400s）

`_resolve_update_target()` 决策顺序：
1. **控制面优先**：`fetch_bootstrap()` 拿到含 `latest_build` 的 manifest → `_evaluate_manifest` → 命中则构造下载 URL `{GITHUB_BASE}/{repo}/releases/download/v{build}/{filename}`（SHA256 走 `.sha256` 旁车）
2. **降级**：manifest 不可用 → `_fetch_latest` 调 GitHub Releases API
   - URL：`GITHUB_API_RELEASES = "https://api.github.com/repos/{repo}/releases/latest"`
   - 速率限制：`X-RateLimit-Remaining < GITHUB_RATELIMIT_SAFE(5)` 时记录退避时间戳
   - 403 / 429 → telemetry 上报 `fetch_latest_rate_limited` + 静默跳过

### 3.3 下载与校验

**多源策略**：
1. 从 `mirror.get_sources("github_releases")` 获取已排序的镜像列表
2. 每个源最多重试 `MAX_RETRY_DOWNLOAD`（5）次，指数退避 `DOWNLOAD_RETRY_BASE_DELAY`
3. 断点续传：已下载部分发送 `Range: bytes=N-` 头，stall 超时 `TIMEOUT_STALL_SECS`（30s）

**SHA256 双源校验**：
1. 优先从 Release Body 中按文件名解析哈希值
2. Body 中找不到时尝试下载独立 `{asset_url}.sha256` 文件
3. 两路都失败 → telemetry 上报 `download_no_sha256` + 中止

**ETag 缓存**：下载成功后缓存 ETag，下次请求发送 `If-None-Match`，304 直接使用缓存

**磁盘检查**：下载前检测剩余空间，不足时 telemetry 上报 `download_disk_full`

**资产命名规则**（`_asset_filename()`）：`opskit-{os}-{arch}[.exe]`
- os: `linux` / `windows` / `darwin`
- arch: `x64`（amd64）/ `arm64` / `armv7`

### 3.4 热替换 — Windows 四层防御

`_apply_windows(pending, self_path, new_version)` 策略优先级：

| 层 | 策略 | 机制 | 失败条件 |
|----|------|------|----------|
| 1 | **PS 脚本 Rename-Then-Copy** | `Wait-Process` 等父进程退出 → `Rename-Item` 旧 exe → `.old` → `Rename-Item` pending → exe → `icacls` 修权限 | PS 执行策略被 GPO 禁 |
| 2 | **跨驱动器降级** | PS 脚本内 `Copy-Item` 覆盖 | PS 进程无法启动 |
| 3 | **MoveFileEx 延迟重启** | `ctypes.windll.kernel32.MoveFileExW` + `MOVEFILE_DELAY_UNTIL_REBOOT` | 无管理员权限 |
| 4 | **update_pending_path.json 兜底** | 写标记文件，下次启动时 `_apply_update_pending_path()` 重新尝试 rename | 连文件写入也失败 |

### 3.5 热替换 — Linux/macOS

```python
# _apply_unix
old_stat = self_path.stat()
backup.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(self_path, backup)          # 备份当前版本
pending.rename(self_path)                # 原子 rename（同分区必然原子）
self_path.chmod(old_stat.st_mode)        # 还原 mode
os.chown(self_path, old_stat.st_uid, old_stat.st_gid)  # 还原 uid/gid
```

### 3.6 崩溃回滚（健康探针）

```python
# 替换成功 → mark_update_applied(build): 写 {build, confirmed:False, fails:0}
# 新版本平稳运行 HEALTH_CONFIRM_DELAY(15s) 后 → confirm_health(): confirmed:True
# 每次未确认健康的启动 → _check_health() 将 fails+1
#   fails >= MAX_HEALTH_FAILS(2) 且仍未确认 → rollback() 回滚 + 重启
#   探针 build 与当前 APP_VERSION 不一致（已回滚/残留）→ 清理探针
```

相比旧的 `startup_ok.json`「起不来才回滚」，健康探针能兜住「能起来但很快崩溃 / 反复重启」的坏版本。

### 3.7 回滚

```python
def rollback() -> bool:
    # 按 mtime 降序找最近的 opskit.v*.bak
    # shutil.copy2 恢复到 self_path
    # chmod +x (Linux/macOS)
```

备份命名：`opskit.v{APP_VERSION}.{timestamp}.bak`，保留最近 3 个。

### 3.8 Telemetry 上报

所有失败点通过统一的 `_report(event_id, exc=None, level="error", **ctx)` 上报，  
自动携带：`component=updater`、`app_version`、`platform`。

---

## 3.9 发布自动化（CI · 打 tag = 全网灰度发布）

工作流：`.github/workflows/release.yml`，`push: tags: v*` 触发。

```mermaid
flowchart LR
    T[打 tag v{N}] --> A[notify-start hook]
    A --> B[test: release_bump v{N} + pytest]
    B --> C[build 矩阵<br/>linux-x64/arm64 · win · mac<br/>每个 runner 先 release_bump 再 nuitka]
    C --> D[release: 上传产物到 GitHub Release]
    D --> E[publish bootstrap: 把 build 写回默认分支<br/>constants.py + bootstrap.json + push]
```

**关键：版本同步脚本** `scripts/release_bump.py`
- `v{N}` → 整数 build N，写回两处单一事实源：
  1. `core/constants.py` 的 `APP_VERSION = N`（决定**编译进二进制的自报版本**）
  2. `bootstrap.json` 的 `latest_build / display / min_build / rollout / notes`
- **必须在 build 前执行**：`build.py` 仅用 tag 做产物文件名，二进制自报版本来自 `constants.py`。若不同步，tag v7 构建出的二进制仍自报 2 → 客户端永远认为"有新版本"→ 更新自锁。
- `--check` 校验一致性（latest_build==APP_VERSION、min_build≤latest_build、rollout∈0-100），CI release 阶段做兜底校验。
- `--min-build` / `--rollout` / `--notes` 可在发版时覆盖控制面字段，实现强制升级 / 灰度放量 / 公告。

**控制面发布路径**：
- GitHub raw 主源：`publish bootstrap` 步骤把 bumped `bootstrap.json` commit 回默认分支
- ghproxy 镜像作兜底（见 `BOOTSTRAP_URLS`）

---

## 4. 防重启循环

- 下载成功后写入 `pending_version` 到 `update_check.json`
- 启动时检测到 `--post-update` 参数 → 清理 `opskit.pending` / `opskit.pending.tmp`，直接返回
- 二进制验证失败（MZ/ELF 头不匹配 / 文件 < 100KB）→ 删除 pending + telemetry 上报

---

## 5. 配置项

```yaml
# config/common.yaml
update:
  enabled: true              # 是否启用自更新
  channel: stable            # 更新通道
  check_interval: 86400      # 检查间隔（秒）
  auto_apply: true           # 退出时自动应用
  repo: ougato/opskit-cli    # GitHub 仓库（含 org）
  mirrors:                   # 硬编码回退镜像
    - https://mirror.ghproxy.com/https://github.com/ougato/opskit-cli/releases/download
    - https://github.com/ougato/opskit-cli/releases/download
```

---

## 6. 文件路径

| 文件 | 路径 | 说明 |
|------|------|------|
| 待应用二进制 | `{data}/cache/opskit.pending` | 下载完成待替换的新版本 |
| 下载临时文件 | `{data}/cache/opskit.pending.tmp` | 下载中间态，完成后 rename |
| 更新检测缓存 | `{data}/cache/update_check.json` | last_check / pending_version / ETag |
| Bootstrap 缓存 | `{data}/cache/bootstrap_cache.json` | 远程 bootstrap.json 的本地副本 |
| 版本备份 | `{data}/backups/opskit.v{N}.{ts}.bak` | 替换前备份（时间戳命名，保留最近 3 个） |
| PowerShell 脚本 | `{data}/cache/opskit_update.ps1` | Windows Rename-Then-Copy 一次性脚本 |
| 健康探针 | `{data}/cache/update_health.json` | 崩溃回滚（build / confirmed / fails） |
| 机器指纹 | `{data}/cache/machine_id` | 灰度 rollout 分桶用的稳定随机 ID |
| 兜底标记 | `{data}/cache/update_pending_path.json` | Windows 四层均失败后写入，下次启动重试 |
