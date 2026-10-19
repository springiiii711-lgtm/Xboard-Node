# 工作流用途与来源

2026-10-17 清理后保留 2 个工作流，均为当前私有仓库新增的手动入口。

比较基线为本地保留的 `upstream/dev`：`0a29338e1f102a462363ce3527417029f89bab28`，不是对上游最新状态的推测。该基线只有 `ci.yml`。

| 文件 | 来源 | 触发方式及作用 | 依赖与保留原因 |
| --- | --- | --- | --- |
| `checkout-build.yml` | 本地新增，首见 `bb168c7` | 手动；Go/安装器回归、镜像构建、隔离 Docker Compose 演练及离线版本检查，不发布 | `ci/validate.py`、`ci/check-workflow.go`、`ci/Dockerfile`、安装器测试和容器夹具；YAML 校验覆盖全部现存工作流 |
| `ghcr-publish.yml` | 本地新增，首见 `fa90f53` | 仅 dev 手动；测试后发布到既有私有 GHCR 包，再由独立 runner 固定 digest 拉取并验证匿名拒绝 | `ci/ghcr.py`、发布保护测试、相同 Docker 测试目标与安装器演练；保留短期令牌及私有性/来源门禁 |

两者的 `push.paths` 仅用于注册，job 要求 `workflow_dispatch`。普通源码或文档推送不会实际构建或发布；镜像默认仍只输出版本，不启动节点服务。

## 已移除的继承工作流

`ci.yml` 从官方上游继承，包含 push/PR 测试、多架构二进制、上游 GHCR 和 Release 发布。导入后其入口 test job 已受 `ENABLE_UPSTREAM_WORKFLOW` 与 `cedar2025/Xboard-Node` 仓库身份限制；依赖 job 在本私有仓库均跳过，且上游镜像目的地并非自建私有包。因此移除这个无效入口，不把它当作当前有效测试链路。

当前实际执行的 config/tracker/panel Go 回归、安装器测试、容器演练及发布门禁全部保留。原上游全仓 make test/arm64/二进制 Release 路径此前未在当前私有工作流执行，本次不声称新增这些覆盖。

`ci/validate.py` 改为要求遗留文件不存在；`checkout-build.yml` 和本地验证命令改用 `.github/workflows/*.yml`，不会继续读取已删除文件。没有改动权限、测试命令、镜像源码或已发布 digest，也不因此重发镜像。历史提交、运行记录和镜像保留。

## 2026-10-19 原生链补齐（待批准推送）

仍保留上述两个工作流，不恢复失效的上游自动发布入口：

- checkout-build 增加原生 job，按官方矩阵构建 Linux amd64/arm64 的 node 和 xbctl，校验 ELF 与源码版本，QEMU 验证 arm64 版本命令；不上传或发布。
- ghcr-publish 增加 target=native/docker/both，默认 native。原生 job 使用短期 contents:write 发布固定 native-<SHA> 私有 Release；独立 contents:read job 重新下载、核验 SHA256/来源并确认匿名访问拒绝。原有 GHCR 流程仅在 docker/both 选择时运行。
- 原生脚本和服务不依赖 Docker；CI 使用 QEMU 只为验证 ARM64 的离线版本命令。新发布代码未在本地调用远程写入接口。
- 原生发行包同时带两种架构，安装端自动检测，不要求用户选择。详细边界见 ops/private-node/README.native.zh-CN.md。
