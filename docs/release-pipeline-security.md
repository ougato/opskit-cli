# 发布流水线

## 分支与封版

框架长期维护 `develop`、`release`、`master`，本地和远程均使用这三个分支。

1. 在 `develop` 开发。准备发布时，以 GitHub 最新正式标签为准将版本加一，运行 `python scripts/release_bump.py v<N>`，提交版本文件及本次发版改动，再将 `develop` 推送到 GitHub 和 origin。
2. 将 `develop` 合并到 `release`，推送到 GitHub 和 origin。GitHub 在 `release` 上执行全量测试、四平台构建、冻结 CLI 验收和资产预检。
3. 预检成功后，在 `release` 的同一提交上创建 `v<N>` 标签并推送到 GitHub。正式工作流核对分支提交、标签版本和对应的成功预检，发布二进制和 SHA256 文件。
4. 等正式工作流成功，核对 GitHub Release 和默认分支的 `bootstrap.json`。同步默认分支的版本提交，再把 `release` 合并到 `master` 并推送到 GitHub 和 origin，最后切回 `develop`。

预检失败时在 `develop` 修复，再合入 `release` 重新预检。已发布的标签保持固定。

## 流程

1. 创建独立 Python 3.12 测试环境，按哈希锁安装依赖并运行全套测试
2. 校验已提交的 APP_VERSION 与 bootstrap.json，分别构建 Linux x64、Linux arm64、Windows x64 和 macOS arm64
3. 每个平台在源码目录外验证冻结 CLI，上传二进制、SHA256 文件及 manifest.json
4. 发布任务严格核对四平台目录及文件，拒绝额外文件、链接和不匹配的版本、平台、架构、大小或摘要
5. 创建或恢复草稿，上传二进制与 SHA256 共八个文件，核对 GitHub 返回的摘要后公开
6. 从事件提交执行发布脚本，将版本和当前安装脚本写入独立检出的默认分支，版本单调递增且保留分支最新父提交

manifest.json 仅用于流水线核对，不作为同名发布资产上传
完整的已公开版本重跑只读，冲突或不完整的公开版本拒绝覆盖
失败保留草稿和诊断日志，测试产物包含 release-tests.log 与 XML
冻结 CLI 验收无论成功或失败，都单独保存 frozen-check-平台产物名 诊断包，包含各命令的 stdout、stderr 和退出码
诊断包不进入正式发布矩阵，未通过冻结验收的二进制不上传为发布产物
`inspect-native-profiles` 仅检查 Windows AppContainer 恢复记录：Windows 空环境应成功返回，Linux/macOS 应返回退出码 1 及明确的 Windows 平台错误
冻结验收对此命令固定使用英文并检查完整 JSON 错误结构和平台错误文案，其他错误仍阻断发布

## 构建约束

外部 Actions 固定提交，manylinux 镜像固定摘要及 amd64 平台
只有发布任务具有 contents:write，其余任务只读且检出不保留凭据
原生构建通过 scripts/install_release_build.py 创建全新环境，按 requirements-installer.txt 和 requirements-build.txt 安装锁定依赖
原生构建安装依赖时禁用 pip 缓存，setup-python 同样不启用 pip 缓存保存，避免不存在的缓存目录使构建收尾失败
Windows 辅助工具由 scripts/install_windows_build_tools.py 准备，Nuitka 不自动下载缺失工具
构建任务预算 60 分钟，矩阵独立保留失败结果
依赖版本与哈希以 pyproject.toml、uv.lock 及导出清单为准

## 安装与更新

安装脚本和自动更新均要求 SHA256，缺失或不匹配时拒绝写入程序
更新保留 HTTPS、来源、版本、文件边界、私有暂存、原子提交及回滚检查
详见 [发布与更新校验](release-security.md) 和 [CLI 契约](cli-contract.md)

## 本次验证

Windows / Python 3.12 的发布、下载、应用、回滚、候选缓存及身份边界测试：309 passed，5 skipped
Ruff、静态债务增量检查、仓库发布边界、版本一致性及安装脚本语法检查通过
本机全套回归在原生插件运行时文件清单测试超过 30 秒预算，未记为通过
正式出包以新标签触发的 GitHub 发布测试、四平台构建及冻结 CLI 检查结果为准
