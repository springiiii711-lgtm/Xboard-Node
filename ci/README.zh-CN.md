# 节点源码的隔离构建

本目录用于验证当前 checkout 能构建为镜像；不运行节点服务、不生成用户凭据、不读取真实配置、不开放网络端口。镜像默认只输出版本然后退出。

## 文件与验证

- `.github/workflows/checkout-build.yml`：仅手动触发，固定 checkout commit 和 action commit，标准 Ubuntu runner、只读 token。
- `ci/Dockerfile`：遵循 `go.mod` 的 Go 1.26，设置 `GOTOOLCHAIN=local`，固定依赖校验和，禁止隐式升级工具链。下载依赖后，测试和 Linux 编译均使用 `RUN --network=none`。保留上游构建标签。
- `ci/Dockerfile.dockerignore`：只允许 `cmd`、`internal`、锁文件和 CI 源码，排除 `.env*`、配置、密钥、Git 数据、本地报告/二进制。
- `ci/check-workflow.go`：用现有 yaml.v3 依赖解析 workflow，拒绝 YAML 语法或重复键问题；不等同于完整 Actions schema 校验。
- `ci/validate.py`：静态检查 checkout、构建输入、版本、默认入口及不发布策略，不等同于 Docker 运行验证。

在仓库根目录：

```sh
python3 ci/validate.py
go test -mod=readonly -count=1 ./internal/config ./internal/tracker
go run -mod=readonly ./ci/check-workflow.go .github/workflows/checkout-build.yml .github/workflows/ci.yml
docker buildx build --target runtime -f ci/Dockerfile --load -t local/xboard-node-checkout .
docker run --rm --network none local/xboard-node-checkout -v
```

选取的 config/tracker 测试覆盖配置解析、校验、热加载检测及连接/流量计数，使用临时文件和内存数据。它们不验证真实协议连接、内核性能或面板联调；本次不运行会监听端口的服务/集成测试。编译包含完整节点源码，版本来源写入镜像 label 和 `-v` 输出。

根 Dockerfile 保留上游入口，验证必须使用 `-f ci/Dockerfile`。上游 `ci.yml` 的入口测试 job 加了默认关闭变量与原仓库身份双重条件，其依赖 job 在私有副本全部跳过；同时修正旧工作流 Go 版本以读取 `go.mod`，便于后续合并。

## Actions 和交付边界

手动触发前确认账号有足够免费额度，不修改预算、付款方式或权限。工作流不登录 GHCR/Docker Hub，不推送镜像、不上传 artifacts、不保存 Actions cache，并关闭 Docker build-record 自动上传。镜像仅在临时 runner 中离线执行 `-v`，导出 tar、记录 SHA256 后随 runner 清理，因此没有持久下载链接。单次超时为 35 分钟，只构建 `linux/amd64`。

Go/Alpine 使用版本标签，未声明核验过的固定镜像 digest，后续重建不保证逐字节一致。尚无镜像签名、发布、部署、真实节点配置、系统网络/防火墙修改、服务开机启动或生产级联调。本地 Windows 可运行所选 Go 测试和交叉编译；未安装 Docker 时不能将其报告为本机镜像验证成功。

## 后续合并

`origin` 为自己账号的独立私有仓库，`upstream` 为 `cedar2025/Xboard-Node`；保留 Git 历史。后续在分支中 fetch/merge 上游并审查冲突，保留禁止发布条件、隔离测试和版本检查，不强制覆盖，不临时公开仓库。

上游 README 声明 MPL-2.0，但当前快照根目录没有 LICENSE 文件；本次未补造许可证文本或认定再分发权利，正式发布前需确认。依赖也有各自许可证。
