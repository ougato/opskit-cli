# OpsKit 自更新与本地验收

## 更新顺序

1. 打包程序进入主菜单后启动后台任务。已下载的更新可以在正常退出或下一次启动时安装。
2. 优先读取 GitHub 默认分支的 `bootstrap.json`，按版本、急停和灰度策略决定目标。控制面不可用时查 GitHub Releases 的最新正式版本。目标 build 大于当前 build 即可升级，中间的版本无需逐个安装。
3. 从 release 元数据或 GitHub 的 SHA-256 旁车文件取得预期摘要。旁车不可达时，按目标标签向 GitHub API 查询同名、同 URL 的资产摘要。镜像可传输更新包，下载字节必须匹配预期摘要。
4. 下载在独立子进程和私有暂存目录中进行。程序校验格式与摘要后原子发布候选索引。已有候选的版本和字节都匹配时直接复用。下载中断、校验失败或发布前异常会保留原候选。
5. 安装阶段再次核对候选，备份当前程序。Linux 原子替换可执行文件，Windows 由独立助手在原进程退出后执行替换。PyInstaller 在启动时应用更新后使用新的解包环境重启。确认新版本健康后完成事务，启动失败走已有回滚流程。

## 本机回归

在仓库根目录执行以下测试。网络响应由测试提供，使用临时数据目录。

```bash
python -m pytest tests/test_update_download.py tests/test_update_cli.py \
  tests/test_auto_update.py tests/test_auto_update_acceptance.py \
  tests/test_update_apply.py tests/test_windows_update_job.py
```

`test_v1_jumps_to_v11_with_github_digest_and_mirror_bytes` 从 build 1 直接选择 build 11，并覆盖控制面源返回无效数据、旁车和直连下载失败、从 GitHub API 获取摘要、从镜像下载、首次灰度身份持久化。其他用例覆盖坏包、超时、候选发布中断和重复下载。

## WSL 二进制验收

在 WSL 用两个独立源码副本构建打包程序，例如以 `git archive v1` 取得历史源码，以修复后的源码构建 v11 测试候选。只在测试副本的 `core/constants.py` 中设置各自的 `APP_VERSION` 和以下端点。构建工具、Python 和运行依赖可预装，测试期间更新请求仅访问本机。

```python
GITHUB_BASE = "https://127.0.0.1:18443"
GITHUB_API_RELEASES = "https://127.0.0.1:18443/repos/{repo}/releases/latest"
BOOTSTRAP_URLS = ["https://127.0.0.1:18443/bootstrap.json"]
```

使用 `python build.py build --backend pyinstaller` 或对应发布后端分别构建，并保留候选的标准资产文件名 `opskit-linux-x64`。历史 v1 的 `build.py` 如果与当前 PyInstaller 的 distutils hook 冲突，可在 v1 测试副本移除 `--exclude-module=distutils`。在带有 OpenSSL 和 Python 3.11 以上版本的 WSL 中执行

```bash
python scripts/verify_local_update.py /path/to/v1/dist/opskit-linux-x64 \
  /path/to/v11/dist/opskit-linux-x64 --from-build 1 --to-build 11
```

脚本创建临时数据目录和本地 HTTPS 服务，启动旧程序，等候候选发布，按菜单快捷键正常退出，再检查可执行文件版本和 SHA-256。用两个当前实现的相邻 build 加上 `--apply-on-startup` 可在候选发布后中断旧进程，再验证下次启动自动替换、重启和健康确认。

加入 `--omit-sidecar` 可令本地服务对 SHA-256 旁车返回 503，验证当前版本使用按标签查询的资产摘要继续下载。

测试二进制和证书只用于本地。正式发布仍使用原始端点和发布工作流。

验证 Linux 正式包的替换和重启行为时，旧版测试程序应使用 `--backend nuitka` 构建。Nuitka 单文件程序启动后会运行解包进程，更新写入和重启都必须指向外层可执行文件。已经发布且受此路径问题影响的旧包（如 v1），需要用包含修复的正式包手动替换一次。
