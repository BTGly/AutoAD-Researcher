# Linux/amd64 离线 Docker 部署

本交付方式提供 Linux/amd64 的**离线安装**，不等于运行时完全隔离网络。交付包包含一个由
`docker save` 导出的镜像 tar、其 SHA-256 清单、`docker-compose.yml`、
`DEPLOYMENT.md` 和 `image-tag.txt`。目标机不需要项目源码，也不需要执行
`docker build`、访问 Docker Hub 或 GitHub Container Registry。

## 构建交付包

在具有 Docker Engine、能构建 Linux/amd64 镜像的构建机中，从项目根目录执行：

```bash
scripts/package_offline_deployment.sh \
  --tag YOUR_IMAGE_TAG \
  --output-dir /absolute/path/autoad-offline-package
```

`YOUR_IMAGE_TAG` 是本次交付的精确镜像标识。该脚本会拒绝覆盖已有目录，构建时
显式指定 `linux/amd64`，再检查镜像元数据，并生成：

```text
autoad-offline-package/
├── autoad-researcher-linux-amd64.tar
├── autoad-researcher-linux-amd64.tar.sha256
├── docker-compose.yml
├── DEPLOYMENT.md
└── image-tag.txt
```

构建机可先核对导出文件：

```bash
cd /absolute/path/autoad-offline-package
sha256sum --check autoad-researcher-linux-amd64.tar.sha256
```

项目还提供手动 GitHub Actions 工作流 `package-offline-linux-amd64`。在 GitHub
Actions 页面选择待交付的分支或提交并运行它；留空 `image_tag` 时，它使用
`autoad-researcher:offline-<短提交号>`。成功后下载同名 Workflow Artifact，其中会额外
包含 `BUILD-INFO.txt`（提交、引用、镜像标签、平台和 workflow run 标识）。Artifact 默认
保留 14 天，适合首次验证打包链路；长期分发策略应在确认实际 tar 大小后再确定。

## 目标机部署

1. 将整个 `autoad-offline-package/` 目录复制到目标 Linux/amd64 主机。
2. 在该目录中验证镜像包未损坏并导入镜像：

   ```bash
   sha256sum --check autoad-researcher-linux-amd64.tar.sha256
   docker load --input autoad-researcher-linux-amd64.tar
   ```

3. 读取交付时固定的镜像标识并启动：

   ```bash
   export AUTOAD_IMAGE="$(cat image-tag.txt)"
   docker compose --project-name autoad-researcher up -d
   ```

4. 等待健康检查通过，再打开 `http://TARGET_HOST:8000`：

   ```bash
   docker compose --project-name autoad-researcher ps
   curl --fail http://127.0.0.1:8000/api/health
   ```

Compose 会持久化两类数据：`runs_data` 保存 Run、Source、Evidence、任务与
Job 状态；`config_data` 保存容器中的 `.autoad` 配置目录。不要删除这两个 volume，
除非明确要清除所有本地运行数据。

## Worker 与材料处理

镜像默认启用嵌入式 Worker。用户只需在界面上传附件，或直接在对话中发送网页/GitHub
URL；不需要启动第二个 Worker 进程，也不需要进入容器编辑任务文件。若 PDF、网页或
仓库处理停滞，先查看容器日志和 Job 状态：

```bash
docker compose --project-name autoad-researcher logs --tail=200 autoad
docker compose --project-name autoad-researcher ps
```

`AUTOAD_EMBEDDED_WORKER=0` 只用于受控诊断，不是普通部署或用户文档中的运行路径。

## 离线安装与运行时网络

“离线”在本文中仅指目标机可通过 `docker load` 安装并启动，不需要在安装阶段拉取镜像。
运行时是否需要网络取决于用户选择的功能：

| 功能 | 运行时网络 |
| --- | --- |
| 上传附件、解析本地材料、查看已有 Run | 不一定 |
| 对话中发送网页 URL | 需要 |
| 对话中发送 GitHub 仓库 URL 并克隆 | 需要 |
| 调用远程 LLM API | 需要 |
| 完全本地模型与已上传资料 | 可离线 |

因此，隔离网络的部署应只使用本地资料与本地模型能力；不要把“镜像已离线导入”理解为网页、
GitHub 或远程模型在无网络下仍可用。

## 停止、重启与升级

停止服务但保留数据：

```bash
docker compose --project-name autoad-researcher down
```

重新启动使用同一条 `up -d` 命令。升级时先备份 volume，导入新 tar 后以新的
`AUTOAD_IMAGE` 启动；不要使用 `down -v`，否则会删除运行状态。

离线目标机的备份工具镜像、保留周期和恢复授权由部署方管理；本交付包不会在备份时隐式
拉取额外镜像。恢复前必须停止服务，并且恢复操作必须明确指向
`autoad-researcher_runs_data`。恢复会覆盖该 volume 中的数据，因此应先核对备份来源并
按部署方的受控恢复流程执行。
