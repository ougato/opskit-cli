# Git 规则

---

## 提交日志

### 分类前缀

每条日志以类型前缀开头：

- `修复` — 纠正错误、异常、逻辑缺陷
- `修改` — 功能性调整、重构、配置变更
- `更新` — 文档、依赖、镜像版本、新增功能
- `删除` — 移除文件、废弃代码

### 消息格式

每条日志使用**独立递增序号**，格式：`{序号}. {类型} {简短描述}`

示例：
```
1. 修复 xxx
2. 修改 xxx
3. 修改 xxx
4. 更新 xxx
5. 删除 xxx
```

**禁止**重复使用同一编号：

```
# 错误示例
1. 修复 xxx
1. 修复 xxx

2. 修改 xxx
2. 修改 xxx
```

### 排序原则

1. 按类型分组（修复 → 修改 → 更新 → 删除）
2. 组内按重要性或依赖关系排序
3. 同类项各自独立一行，序号递增

---

## 分支工作流

### 分支职责

| 分支 | 职责 |
|---|---|
| `develop` | 日常开发、修复、功能迭代，所有代码改动必须先在此分支进行 |
| `release` | 稳定发布分支，只接受来自 develop 的合并，打 tag 触发 CI/CD |
| `master` | 最终稳定分支（GitHub 默认分支），只接受来自 release 的合并，代表已发布状态 |

### 发布流程

1. 所有修复和修改在 `develop` 分支上完成
2. 确认无误后合并到 `release` 分支：`git merge develop --no-edit`
3. 在 `release` 分支上打 tag 触发 CI/CD：`git tag vX.Y.Z`
4. 同步推送 tag 到 GitHub 和 GitLab：`git push github vX.Y.Z && git push origin vX.Y.Z`
5. CI 验证通过后，将 `release` 合并到 `master`：`git checkout master && git merge release --no-edit`
6. 推送 `master` 到 GitHub：`git push github master`

### 严格禁止

- 禁止直接在 `release` 分支上修改任何文件
- 禁止将 tag 打到 `develop` 分支触发 CI
- 禁止跳过 `develop` 直接在 `release` 上 hotfix（紧急修复也必须先 develop 再 merge）
- **禁止 `release` 反向合并到 `develop`**（方向只能是 develop → release，绝不反向）

### 完整发布流程（必须严格按序执行）

```
1. 确认当前在 develop 分支
2. 在 develop 上完成所有修改并提交
3. push develop 到双端
4. checkout release
5. merge develop → release（--no-edit）
6. push release 到双端
7. 在 release 上打 tag 并推送到双端
8. CI 验证通过后，checkout master
9. merge release → master（--no-edit）
10. push master 到 GitHub
11. 立即 checkout develop（完成后必须切回，不得停留在其他分支）
```

### 工作分支守则

- **日常工作区永远在 `develop`**，`release` 只用于合并和打 tag，操作完立即离开
- 每次打完 tag 后必须执行：`git checkout develop`
- 如发现自己在 `release` 分支上且有未提交改动，必须先 `git stash`，切到 `develop` 后再 `git stash pop` 继续

### CI/CD 触发规则

- **GitHub Actions**（`release.yml`）：`v*` tag push → test → build（4 目标）→ GitHub Release
- **GitLab CI**（`.gitlab-ci.yml`）：`v*` tag push → release_job → GitLab Release（挂 GitHub 链接）
- tag 必须从 `release` 分支打出，双端同时推送