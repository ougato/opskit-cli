# CLI 非交互式模式设计方案

## 开源社区调研总结

| 工具 | 非交互方式 | 说明 |
|---|---|---|
| **rustup** | `-y` 标志 + `--default-toolchain` 等选项 | `curl ... \| sh -s -- -y --default-toolchain nightly --profile minimal` |
| **Homebrew** | `NONINTERACTIVE=1` 环境变量 | `NONINTERACTIVE=1 brew install ...` |
| **apt** | `-y` / `--assume-yes` + `DEBIAN_FRONTEND=noninteractive` | 双层：CLI 标志 + 环境变量 |
| **Terraform** | `-auto-approve` 标志 | `terraform apply -auto-approve` |
| **Ansible** | `--extra-vars` / `vars_prompt` 跳过 | 通过 `-e` 传入所有变量 |
| **nvm** | 天然非交互 | `nvm install 20` 直接执行，无任何 prompt |
| **Docker Compose** | 天然非交互 | 声明式配置文件，无 prompt |
| **1Panel** | `1pctl` CLI 子命令 | Web 面板 + CLI 双入口 |

## 最佳实践归纳

### 三层非交互策略（业界共识）

1. **`-y` / `--yes` 全局标志** — 跳过所有确认弹窗（rustup / apt / terraform）
2. **`--参数名 值` 直传** — 跳过所有交互输入（rustup `--default-toolchain`）
3. **环境变量 `OPSKIT_NONINTERACTIVE=1`** — CI/CD 管道中一次性禁用所有 prompt（Homebrew 模式）

### 核心设计原则

- 每个交互点必须有对应的 CLI 参数或环境变量替代
- 缺少必填参数时，非交互模式应报错退出（而非静默使用默认值）
- 可选参数在非交互模式下使用合理默认值
- 所有参数均可通过 `--help` 查看

## 本项目实施方案

### 全局标志

```
--yes, -y       跳过所有确认弹窗（自动选 yes）
```

环境变量：`OPSKIT_YES=1` 等效于 `--yes`

### software install 参数

```
opskit software install <NAME> [OPTIONS]
  --token, -t     WireGuard 客户端连接令牌
  --version       指定安装版本号
  --yes, -y       跳过确认弹窗
```

### software uninstall 参数

```
opskit software uninstall <NAME> [OPTIONS]
  --yes, -y       跳过确认弹窗
  --all           卸载所有版本（多版本软件）
```

### software upgrade 参数

```
opskit software upgrade <NAME> [OPTIONS]
  --version       指定升级目标版本
  --yes, -y       跳过确认弹窗
```

### software diagnose / manage 参数

```
opskit software diagnose <NAME>    # 天然非交互（直接输出诊断信息）
opskit software manage <NAME>      # 进入管理菜单（交互式）
```

### monitor 参数

```
opskit monitor dashboard           # 天然非交互
opskit monitor cpu                 # 天然非交互
opskit monitor memory              # 天然非交互
opskit monitor disk                # 天然非交互
opskit monitor network             # 天然非交互
opskit monitor processes           # 天然非交互
```

### network 参数

```
opskit network ping <HOST>         # 直传主机名
opskit network traceroute <HOST>   # 直传主机名
opskit network dns <HOST>          # 直传域名/IP
opskit network port-scan <HOST>    # 直传主机名
opskit network speed-test          # 天然非交互
opskit network public-ip           # 天然非交互
```

## 实施步骤

1. core/prompt.py 添加全局 `_auto_yes` 状态，`confirm()` 和 `select()` 在非交互模式下自动返回
2. main.py 顶层 `app` 添加 `--yes` 回调，设置 `core.prompt._auto_yes = True`
3. network 子命令添加 HOST 参数
4. software install/uninstall/upgrade 添加 `--yes` 选项
5. 更新 README.md
