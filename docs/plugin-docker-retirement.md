# 旧插件 Docker 资源的一次性清理 / Retired plugin Docker resources

当前插件不再创建或使用 Docker 资源。独立 Docker 软件管理功能保留。本说明只供曾运行旧插件版本的操作者手动清理；新安装不需要 Docker，也不运行这些命令。

Plugins no longer create or use Docker resources. The standalone Docker software manager remains available. These manual steps apply only to installations that used the old plugin runtime; new installations do not require Docker.

1. 停止旧版本 OpsKit 和旧插件任务，保存旧数据目录及资源记录。确认 Docker context，避免操作另一台机器。
   Stop old OpsKit/plugin tasks and retain the old data directory and resource records. Confirm the Docker context before inspecting resources.

   ```text
   docker context show
   docker ps -a --no-trunc --filter label=opskit.cleanup-token
   docker network ls --no-trunc --filter label=opskit.cleanup-token
   docker image ls --no-trunc --filter label=org.opskit.sandbox
   ```

2. 逐项使用完整 ID 检查，核对旧记录的 cleanup token、名称和 Docker daemon。标签与名称只用于发现，不能单独证明可删除。没有对应记录、仍有使用者或归属不明的对象保留。
   Inspect each full ID and compare its cleanup token/name with the old records on the same daemon. Labels and names are discovery aids, not deletion authorization. Retain unknown or still-used resources.

   ```text
   docker inspect <FULL_CONTAINER_ID>
   docker network inspect <FULL_NETWORK_ID>
   docker image inspect <FULL_IMAGE_ID>
   ```

3. 操作者确认具体对象属于已停止的旧插件后，按容器、网络、镜像顺序逐个删除。先正常停止容器，不使用批量匹配删除或强制删除镜像；删除失败先检查占用。
   After confirming ownership of each stopped legacy resource, remove containers, then networks, then unused images. Stop containers normally first. Do not bulk-delete matches or force-remove images; investigate resources still in use.

   ```text
   docker stop <FULL_CONTAINER_ID>
   docker rm <FULL_CONTAINER_ID>
   docker network rm <FULL_NETWORK_ID>
   docker image rm <FULL_IMAGE_ID>
   ```

4. 再次检查具体 ID 已不存在。旧工作目录、信任记录和配置独立保留，不能从 Docker 标签反推目录归属。不要运行 `system prune`、`volume prune`，不要卸载 Docker 软件来完成插件迁移。
   Verify each exact ID is absent. Preserve old workspaces, trust records and configuration separately; Docker labels do not prove filesystem ownership. Do not use `system prune`, `volume prune`, or uninstall Docker as part of plugin migration.

本次实现不会自动执行上述清理，也不会删除未知 Docker 资源。
The migration does not execute these steps automatically or delete unknown Docker resources.
