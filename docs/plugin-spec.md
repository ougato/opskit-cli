# OpsKit 插件规范（SDK API2）

## 独立开发

插件是独立 Git 仓库，clone 到 `plugins/<repository>`，不加入平台仓库。平台 `plugin/` 是官方生命周期模块，`plugins/` 是第三方工作区，不能混淆。插件不得要求修改 `main.py`、官方注册表或平台依赖文件。

```yaml
name: hello
version: 1.0.0
api_version: 2
kind: python
entry: hello_pkg
permissions: []
```

身份为小写字母开头的字母、数字和下划线；`platform` 和平台核心身份保留。入口必须来自插件自身目录，不接受链接或越界路径。可以声明 `permissions: [filesystem, network]`；不接受宿主 root 权限。容器内文件系统始终限制在沙箱，权限标签不能授予宿主目录。

## 插件自己定义 CLI

平台只提供 `opskit plugin run NAME -- ARGS`，`--` 后的子命令、选项、大小写、帮助和退出码都归插件。平台不保存第三方 argparse/Typer schema，也不导入插件以生成全局帮助。全局官方命令保持官方职责。

```python
# hello_pkg/__init__.py
import argparse

def cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="opskit plugin run hello --")
    parser.add_argument("--name", default="world")
    args = parser.parse_args(argv)
    print(f"Hello {args.name}")
    return 0
```

```text
opskit plugin run hello -- --name Alice
opskit plugin run hello -- --help
```

Python 插件导出 `cli(argv)`，返回 0..255 的整数或 None（0）；argparse 的 SystemExit 退出码被适配。exec 插件直接收到 argv，但运行于 Linux 容器，因此需提供 Linux 入口。插件菜单仍通过 `register() -> core.sdk.ModuleInfo` 的入口执行。

## 准备运行时与信任

需要 Linux Docker daemon；Windows 可使用 WSL Debian Docker，发行版可通过 `plugin.sandbox.wsl_distribution` 配置。先准备已有 `python:3.12-slim` 基础镜像，随后操作方明确选择依赖：

```text
opskit plugin prepare plugins/hello --dependency package==1.2.3
# 源码方式等价入口
python scripts/build_plugin_runtime.py plugins/hello --dependency package==1.2.3
```

准备操作会下载第三方包，必须核对依赖来源。执行插件 Dockerfile、任意 requirements URL 和修改共享 venv 都不受支持。基础镜像解析为本地不可变 image ID，构建后的 image ID 与插件内容指纹一起记录。依赖目前由操作方明确传入，尚未自动解析插件依赖锁文件。二进制发行版用户需借助相同版本源码或 wheel 准备运行时；源码必须与监督器 SDK 版本匹配。

生成 `CHECKSUMS.yaml`：`opskit plugin fingerprint PATH`。安装后在插件管理菜单核对来源、权限和指纹，再确认信任。更新内容后撤销旧信任，需要重新核对并准备运行时；不会继承授权。校验清单不是发布者签名。

## SDK 与服务

仅依赖 `core.sdk` 的稳定接口。`plugin_data_dir(自己的身份或下划线转连字符别名)` 映射到容器 `/data/state`；不能选择其他插件命名空间。不能安装宿主软件或获取任意宿主文件。数据总量限制 16 MiB，单次容器临时 `/data` 32 MiB，`/tmp` 16 MiB。需要大文件业务时，应另行设计按能力授权的流式文件传输，不扩大宿主挂载范围。

服务提供方声明 `provides: [service_name]` 并导出 `provide_service(name)`；调用方声明 `uses: [{service: service_name}]`。SDK `get_service` 返回 RPC 代理；参数和结果必须是 JSON 可序列化值。受授权状态目录内的 Path 可由平台编码，函数回调、进程内对象和共享全局状态不跨边界。UI 调用经过固定能力 broker。

服务调用方身份由平台监督器绑定，插件传入的 caller 不能改变宿主状态分区。插件自身入口只使用 `<data>/plugin-data/<name>/self`，服务调用使用 `<data>/plugin-data/<provider>/callers/<authenticated-caller>`；自身入口也不挂载其他调用方状态。服务 provider 必须唯一，调用和缓存使用时重新检查授权。循环调用或超过四层嵌套被拒绝；并发状态事务采取拒绝并提示重试。

## 网络

网络通过每次调用独立代理。默认拒绝私有、回环、链路本地与保留地址。需要自建 S3 等私有端点时，操作方配置：

```yaml
plugin:
  sandbox:
    timeout: 300
    private_targets:
      hello:
        - "storage.example.internal:9000"
```

这是精确授权，不支持通配符。SDK或第三方库须使用 HTTP(S)_PROXY；直接 TCP 网络不可用。证书验证不得关闭。授权公共网络仍允许插件将自己已获得的数据发送到公网，须依据数据敏感度决定是否授予网络权限。

## API1 迁移

将 CLI parser 移到插件包内并导出 `cli(argv)`；把所有数据路径改为自身沙箱路径；共享 pip 安装改为明确准备运行时；Python 对象/回调改为 JSON RPC；宿主命令和 root 操作移到官方明确授权的能力设计，不能以兼容为由恢复宿主执行。验证后更新 api_version 和 CHECKSUMS，在插件独立仓库中提交迁移。

旧宿主 plugin-data 不自动注入沙箱，避免错误挂载或泄露其他调用方数据。备份后人工迁移自己的配置和必要小文件到新状态目录；凭据和真实运行数据不进入 Git。
