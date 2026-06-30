# 提权（root）操作规范

## 背景与核心原则

OpsKit **没有**进程级整体提权（不存在启动即 sudo 的逻辑，`require_root` 未被调用）。
因此任何需要 root 的系统操作，都必须**逐命令提权**，统一经 `core.privilege.run_as_root` 执行：

- 非 root 用户：自动在命令前加 `sudo`
- 已是 root：直接执行
- Windows：直接执行（提权由调用层保证）

**铁律：装、卸、升级必须对称提权。** 不允许「安装时提权、卸载/升级时不提权」——
否则普通用户安装能成功，卸载/升级却报 `Permission denied`，或静默失败导致卸载不干净
（典型：`apt-get purge` 未提权 → 包没真正卸掉 → 重装仍提示「已安装」）。

---

## 哪些操作需要提权

凡是触及以下资源的操作都属于 root 操作，必须走 `run_as_root`：

| 操作类型 | 说明 |
|---|---|
| 包管理 | `apt-get` / `yum` / `dnf` / `apk` / `pacman` / `zypper` 的 install / remove / purge / update |
| 系统服务 | `systemctl` enable/disable/start/stop/restart/daemon-reload、`service`、`update-rc.d`、`chkconfig` |
| 删除系统文件/目录 | `/etc/**`、`/usr/**`、`/var/**`、`/run/**`、`/root/**`、`/lib/systemd/**` 下的 `rm` |
| 写入系统文件 | 向 `/etc/**`（apt 源、sysctl、systemd unit、nginx conf 等）、`/root/**` 写内容 |
| 内核/网络参数 | `sysctl -w`、`iptables`、`chown` 系统目录 |

**例外（无需提权）**：仅读取/探测（`tailscale version`、`systemctl is-active`、`wg show`、
`nginx -t`、`which` 等），以及写入当前用户目录（`core/paths.py` 的 `data_dir()` 等用户态路径）。

---

## 正确用法

### 1. 包管理：必须用 `get_runner()`（已内置提权）

`core/pkg_runner.py` 的系统级包管理器（apt/yum/dnf/apk/pacman/zypper）已设 `needs_root = True`，
内部统一经 `run_as_root` 执行，调用方零感知。brew / choco / winget / msi 为 `needs_root = False`
（brew 禁止 root，Windows 由平台提权）。

```python
# ✅ 正确：apt/yum 等自动提权，普通用户也能装/卸
from core.pkg_runner import get_runner
get_runner().install(["nginx"])
get_runner().remove(["nginx"])
```

```python
# ❌ 禁止：裸 subprocess 调用包管理器（既违反 pkg-runner 规范，又不提权）
subprocess.run(["apt-get", "install", "-y", "nginx"])
subprocess.run(["apt-get", "purge", "-y", "tailscale"])   # 非 root 静默失败 → 卸不干净
```

### 2. 系统服务：用 `core/service.py` 或 `run_as_root`

```python
# ✅ 正确：封装层已用 run_as_root
from core.service import enable_now, disable_now
enable_now("nginx")
disable_now("nginx")

# ✅ 正确：直接提权
from core.privilege import run_as_root
run_as_root(["systemctl", "daemon-reload"], capture_output=True, text=True, check=False)
```

```python
# ❌ 禁止
subprocess.run(["systemctl", "disable", "--now", "wg-quick@wg0"])
```

### 3. 删除系统文件/目录：用 `run_as_root(["rm", ...])`

```python
# ✅ 正确（参考 tailscale/server.py:remove_tailscale_artifacts、rustdesk/server.py）
from core.privilege import run_as_root
run_as_root(["rm", "-rf", str(state_dir), str(repo_file)], check=False,
            capture_output=True, text=True)
```

```python
# ❌ 禁止：普通用户删 root 文件 → [Errno 13] Permission denied
Path("/etc/apt/sources.list.d/foo.list").unlink(missing_ok=True)
shutil.rmtree("/var/lib/foo")
```

### 4. 写入系统文件：写临时文件再用 root 拷贝（不要直接 write_text）

`Path("/etc/...").write_text(...)` 在非 root 下必然失败。应写到临时文件，再以 root 落位：

```python
# ✅ 正确：tempfile + run_as_root(["cp"/"install"/"tee"])
import tempfile
from pathlib import Path
from core.privilege import run_as_root

with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
    f.write(content)
    tmp = f.name
run_as_root(["install", "-m", "0644", tmp, "/etc/sysctl.d/99-wg.conf"],
            capture_output=True, text=True, check=False)
Path(tmp).unlink(missing_ok=True)
```

```python
# ❌ 禁止：直接写系统路径
Path("/etc/sysctl.d/99-wg.conf").write_text("net.ipv4.ip_forward=1\n")
with open("/etc/wireguard/wg0.conf", "a") as f: ...
```

---

## 新增软件时的强制检查清单（Code Review 必查）

新增任何 Recipe，或修改现有软件的 install / uninstall / upgrade 时，逐项确认：

- [ ] 所有包管理操作经 `get_runner()`，无裸 `subprocess.run(["apt-get"/"yum"/...])`
- [ ] 所有 `systemctl` / `service` 操作经 `core/service.py` 或 `run_as_root`
- [ ] 删除 `/etc`、`/usr`、`/var`、`/run`、`/root` 下文件经 `run_as_root(["rm", ...])`，无裸 `unlink` / `shutil.rmtree`
- [ ] 写入系统文件经「临时文件 + `run_as_root` 拷贝」，无直接 `write_text` / `open(系统路径, "w"/"a")`
- [ ] **对称性**：install 提权的每一处，对应的 uninstall / upgrade 也提权
- [ ] 以**非 root 普通用户**走完 安装 → 卸载 → 重装 全流程，卸载后 `detect()` 返回 `None`（确认卸干净、重装不再提示「已安装」）
- [ ] 只读探测命令（version / is-active / show / `nginx -t`）不要无谓提权

---

## 相关文件

| 文件 | 职责 |
|---|---|
| `core/privilege.py` | `run_as_root()` 逐命令提权（非 root 加 sudo），`is_root()` 检测 |
| `core/pkg_runner.py` | 包管理器策略；系统级管理器 `needs_root = True` 自动提权 |
| `core/service.py` | `enable_now` / `disable_now` 等服务操作，内部已用 `run_as_root` |
| `tailscale/server.py` | 提权删除系统文件（`remove_tailscale_artifacts`）、root 写文件参考实现 |
| `software/recipes/rustdesk/server.py` | `run_as_root(["rm", ...])`、服务管理的范例 |
