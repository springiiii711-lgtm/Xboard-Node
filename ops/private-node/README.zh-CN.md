# 自建私有节点镜像安装流程

此目录是本项目的部署定制层。上游根目录 `install.sh`、Dockerfile 和 README 的安装说明保留，方便合并官方更新；自己的面板应只生成本目录安装器的命令。不要再运行上游 curl 一键安装命令，否则仍会安装上游 Release 二进制。

下面是一个已经发布及独立拉取验证的固定版本示例。日常安装以自己后台生成命令中的 digest 为准，安装器不内置默认版本，升级必须显式指定：

```text
ghcr.io/springiiii711-lgtm/xboard-node@sha256:e1bc5486d5db54b87abd88231b084a216226a0d0f3f2aedf418a81e0b3064094
```

对应源码 `654b41364559d25667db208a2f41db08d123c5e6`，平台 `linux/amd64`。安装器拒绝 latest、仅 SHA tag、其他仓库和不完整 digest。以后采用新镜像前应先验证发布、拉取、来源及运行契约，再显式更新面板 `PRIVATE_NODE_IMAGE`。本地修改安装器不自动发布镜像，也不改变已经发布的镜像。


本次安全修复镜像的 [CI](https://github.com/springiiii711-lgtm/Xboard-Node/actions/runs/38983233062) 与 [私有发布、独立固定 digest 拉取及匿名拒绝验证](https://github.com/springiiii711-lgtm/Xboard-Node/actions/runs/38985583196) 均已通过。安装说明的后续提交只更新版本元数据，不重新构建或发布镜像；镜像来源仍是上述完整源码提交。本次未部署节点、配置服务器认证或创建凭证。

## 运行边界

提交、构建或发布此安装器不会自动安装节点。以下服务器命令属于另行确认部署时的操作说明；install/upgrade/recover 都可能改变目标服务器上的容器，--dry-run 才是无副作用预览。当前镜像的默认命令是 `-v`；安装器生成的 Compose 明确设置 `entrypoint: [xboard-node]` 和 `command: [-c, /etc/xboard-node/config.json]`，不能仅 docker run 镜像后期待它自动服务。

## 私有脚本如何获取

私有仓库的 raw URL 不能匿名 curl。安装器不自动下载或自我更新，也不接受 GitHub 令牌。

推荐由维护者在有现成合法仓库访问权限的电脑上获取审核过的完整提交（现有 Git/SSH 登录、已登录 GitHub 网页下载或已有 gh 登录均可）。无需把源码仓库凭证配置到节点服务器。推送前也可以从已审核的本地工作区制作候选包：

```sh
python3 ops/private-node/package.py --output local-review/xboard-node-installer.zip
```

打包器仅包含 `install.py`、本说明及文件摘要清单，排除仓库其他文件、配置、测试与凭证。ZIP 旁生成 `.sha256`。经已有授权的 SSH/SFTP 或受控文件传输渠道把 ZIP 送到服务器，并通过可信渠道核对完整 ZIP SHA-256；摘要只校验一致性，不能替代可信来源或代码签名。

服务器上先核对摘要并解压审核，然后由管理员将 `install.py` 放到固定位置：

```sh
sha256sum -c xboard-node-installer.zip.sha256
unzip xboard-node-installer.zip -d reviewed-installer
sudo install -d -o root -g root -m 0755 /opt/xboard-node-deploy
sudo install -o root -g root -m 0755 reviewed-installer/install.py /opt/xboard-node-deploy/install.py
```

不要运行来历不明的脚本；不要把令牌拼进下载 URL、复制的命令或 ZIP。脚本部署目录应由 root 持有，普通用户不可写。安装器与镜像分别固定版本：升级镜像不会更新脚本，替换脚本也不会升级容器。

## GHCR 拉取认证

服务器必须已经安装 Python 3.9+、Docker Engine 和 Compose v2，具有本地 Linux amd64 引擎。脚本不安装 Docker、不修改用户组、系统网络、防火墙或仓库/镜像权限；检测到旧版 `/etc/xboard-node/config.yml` 或 `xboard-node.service` 会退出，旧服务迁移需要单独审查。

安装器以 root 执行，因此需要同一执行身份已有 GHCR 拉取权限。普通用户的 docker login 不一定能供 sudo 使用。若无现有认证，由用户另行决定提供仅有包读取权限、可撤销和轮换的合法凭证；服务器不能复用 Actions job 的短期 `GITHUB_TOKEN`。按 GitHub 官方文档，GHCR 外部客户端通常使用有 `read:packages` 权限的 classic PAT，账户本身也必须有该私有包访问权。这里不创建、不扩权或保存任何真实凭证。

在用户自行授权配置认证后，可用 Docker 的隐藏密码提示，而不是命令行密码参数：

```sh
sudo docker login ghcr.io --username YOUR_GITHUB_USERNAME
```

在 Password 提示处输入凭证。不要使用 `--password TOKEN`、带 token 的 URL、聊天转发、shell history 明文或 `set -x`。Docker 认证应使用已有凭证助手；没有助手时 Docker 可能把认证信息存入该用户的配置文件，须由管理员决定存储和轮换策略。另一种方式是在受控进程中通过 `--password-stdin` 输入，不能把令牌字面量写进管道命令。安装器只调用 pull，不进行 login，不读取或复制 Docker 凭证文件；拉取失败直接退出，不回退到公开镜像。

## 安装、重复执行与升级

先用任何电脑可运行的离线预览查看配置，`--dry-run` 不读取 Token、不调用 Docker/网络、不写文件：

```sh
python3 install.py install --panel https://panel.example.com --machine-id 1 --image ghcr.io/springiiii711-lgtm/xboard-node@sha256:e1bc5486d5db54b87abd88231b084a216226a0d0f3f2aedf418a81e0b3064094 --dry-run
```

日后完成分发、认证和部署确认后，在目标服务器执行自己后台复制的命令。命令只含面板 HTTPS 地址、机器编号和固定 digest；从后台查看机器 Token，在安装器隐藏提示中输入。此 Token 用于连接面板，与 GHCR 登录凭证不同。非交互场景仅提供 `--token-stdin`，由已有机密管理机制向标准输入传入，不能直接在命令中 echo 明文。

固定目录 `/var/lib/xboard-node-docker`（可通过 `--state-dir` 指定 `/var/lib` 下专用目录），固定 Compose project 为 `xboard-private-node`，一个主机只管理一份 machine 绑定。文件内容：

- `active.json`：当前镜像 digest、镜像 ID、面板地址、机器编号、版本目录，不含 Token。
- `releases/<随机编号>/config.json`：节点配置与机器 Token，主机权限 0600、父目录 0700，只读挂入容器；不通过 Docker 环境变量或命令行传递 Token。
- 同目录 `compose.json`：镜像、启动参数及文件挂载，不含 Token。JSON 是 Compose 支持的 YAML 子集。
- `data/`：持久化内核配置和证书，升级保留。配置中持久路径固定为 `/var/lib/xboard-node/kernel`、`/var/lib/xboard-node/certs`。
- `pending.json`：切换前写入的恢复记录，检测到中断时拒绝新的安装/升级。文件锁拒绝并发操作。

生成的容器使用 host 网络承载面板下发的节点端口，但不调用任何防火墙或网络权限修改命令。未启用特权容器或 NET_ADMIN；需要这些权限的能力不在本安装器范围。诊断 HTTP 端口关闭，不额外开放健康端口。成功只表示目标容器通过连续运行检查，不代表面板握手、代理协议或公网连通性验证成功，需在面板单独确认在线状态。

同机器、同 digest 重复 install/upgrade 只检查现有容器状态，不重建、不重新读取 Token；不同绑定拒绝覆盖。升级须明确给出新 digest：

```sh
sudo python3 /opt/xboard-node-deploy/install.py upgrade --image ghcr.io/springiiii711-lgtm/xboard-node@sha256:NEW_VERIFIED_64_HEX_DIGEST
```

升级先拉取并核对 digest、平台、OCI 来源标签及离线 `-v`；失败不触碰旧服务。通过后复制旧配置（保留 Token 和自定义字段）、复用 data，再以 `--pull never` 切换。启动失败会恢复旧 Compose 与旧镜像；整个命令仍返回非零。旧镜像不可用导致回退失败时保留 pending 和全部数据，不会假报成功或自动下载其他版本。

被中断或回退失败时，在排查 Docker 后显式恢复：

```sh
sudo python3 /opt/xboard-node-deploy/install.py recover
```

恢复会回到切换前版本，首次安装失败则移除本工具的候选容器。它不删除镜像、历史配置或数据。管理员应单独制定受保护配置的留存/清理策略，不要把含 Token 的 releases 目录打包进源码仓库。

## 非部署测试与合并上游

```sh
python3 -B -m unittest discover -s ops/private-node/tests -p 'test_*.py' -v
go test -mod=readonly -count=1 ./internal/config ./internal/tracker
```

Python 状态机测试使用 FakeDocker：验证首次安装、重复执行、同版本升级、保留配置、认证失败、错误来源/摘要、启动失败回退、回退失败/恢复、已有项目冲突、输入校验及错误输出不泄漏 Token。Go 测试用实际配置解析器读取同一份合成 JSON 夹具，不启动节点。Docker CLI 参数、Compose 结构以及 `-c` 契约有代码级测试；没有实际 Linux Docker/Compose 部署测试时，不能把模拟测试当成服务器安装已通过。

合并官方代码时重点复核 `internal/config`、`cmd/xboard-node` 参数与镜像 entrypoint；定制实现集中于 `ops/private-node`，上游根安装脚本和根 Dockerfile 没有改动。CI 包含离线 Python 测试和 `ci/installer-container-check.py`：仅 GitHub 临时 runner、root 所有的临时文件，以 network=none、无端口的 shell 占位进程验证真实 Compose 挂载、文件锁、切换、回退及中断恢复；不启动节点服务、不连接面板。注册表认证和真实镜像身份由独立发布/拉取流程验证。原发布权限和触发策略不变。

参考：[GitHub GHCR 认证及固定 digest](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)、[Docker 登录与凭证存储](https://docs.docker.com/reference/cli/docker/login/)。
