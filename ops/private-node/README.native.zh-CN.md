# 私有原生安装

节点目标不需要 Docker。后台远程命令下载经过 SHA256 固定的引导程序，隐藏输入 GitHub 下载授权后获取固定提交的私有 Release，校验完整 ZIP，再自动识别 Linux amd64/arm64、安装 systemd 服务。机器 Token 在另一个隐藏提示中输入；两个 Token 均不出现在命令参数或安装日志。GitHub 下载授权不会写入目标机配置，机器 Token 只保存在 root 专用的 `/etc/xboard-node/config.yml`（0600，目录0700）。

## 已核对的官方范围

上游 `cedar2025/Xboard-Node` 的 dev 提交 `0a29338e1f102a462363ce3527417029f89bab28`：

- `.github/workflows/ci.yml`、`Makefile` 原生矩阵为 Linux amd64、arm64，每种架构都有 `xboard-node` 和 `xbctl`；官方 v1.13 Release 已有这四个文件。没有官方 Windows/macOS 原生服务安装支持，也没有 386/armv7 发布产物。
- 构建 `CGO_ENABLED=0`，节点标签为 `with_quic with_utls with_wireguard with_acme with_clash_api`。同一节点二进制包含 sing-box、xray 两个内核，不必分别下载；默认 singbox。
- 官方 `install.sh` 自动识别 x86_64/amd64、aarch64/arm64，要求 Linux 上运行 systemd。依赖安装明确处理 Debian、Ubuntu、CentOS、RHEL、Rocky、AlmaLinux、Fedora；其他发行版只是 best-effort，未承诺所有系统版本。
- 官方远程入口是 `https://raw.githubusercontent.com/cedar2025/xboard-node/dev/install.sh`，从官方 Release 下载原生二进制。此前私有 `/opt/.../install.py --image ...` 是本项目后加的 Docker 链，不能据此认为上游只支持 Docker。

## 本项目构建、检查、发布

```sh
python3 -B ci/native-build.py --source-revision "$(git rev-parse HEAD)" --output /tmp/xboard-native-build
python3 -B -m unittest discover -s ops/private-node/tests -p 'test_*.py' -v
```

输出完整 ZIP、SHA256SUMS、manifest（源码提交、架构、内核、标签及逐文件 SHA256）。脚本拒绝覆盖输出目录；发布构建必须是干净的精确 checkout。`--allow-dirty` 仅供本地编译验证，其产物带 `local_validation_only=true`，发布和安装都拒绝使用。

保留现有两个工作流：`checkout-build.yml` 增加原生矩阵编译和 Linux 版本检查；`ghcr-publish.yml` 增加 native 发布选项，保留 docker 兼容。不会新增第三个工作流。原生发布使用该 job 的短期 `GITHUB_TOKEN` 和 `contents:write`，固定 `native-<完整提交>`，不覆盖旧版本，拒绝公开仓库。CI 检查 amd64 及 QEMU arm64 的两个程序版本；不启动真实节点。

发布完成后必须把 `source_revision`、`bootstrap_sha256`、`archive_sha256` 三个非敏感值同步到面板的 `node_deployment` 配置。未发布前面板原生入口明确报“原生发布尚未配置”，不能伪装成已有可安装版本。发布源码和产物不会自动更新线上配置。

重新检查已有版本可在同一 `ghcr-publish.yml` 选择 `verify-native`，填写完整 `source_revision` 和已知 `archive_sha256`。它只运行只读下载验证，不重新构建、上传或改动 Release。下载授权按 RFC 6750 的 Bearer 字符集校验，支持更长的不透明短期令牌；机器 Token 使用独立规则。换行、引号、反斜杠和控制字符始终拒绝。

## 新服务器上的体验与权限

后台复制命令不要求预置 `/opt` 文件，不让用户选择架构或 Docker。命令会检查 Linux，必要时通过已识别发行版包管理器安装 Python 3、curl、CA 证书；Python 要求3.9+，不安装 Docker，不更改防火墙或证书。遇到不支持的包管理器或过旧 Python 时明确退出，不能据发行版名称承诺兼容所有旧版本。

私有仓库不能匿名下载。运行时需要管理员已有、仍有效、可读本私有仓库 Contents 的短期授权（如已有 GitHub App 安装令牌），在隐藏提示中输入。这个实现不创建 GitHub App/PAT，不要求长期凭证，不把已经结束的 Actions job token交给服务器使用。若尚无这样的短期授权，安装仍会受阻；也可以另行批准使用已有受信任电脑的 GitHub 会话下载、校验 ZIP 后经 SSH 传送，避免给节点提供 GitHub 凭据。不得把令牌贴进聊天。

原生安装采用上游相同的 `/usr/local/bin/xboard-node`、`/usr/local/bin/xbctl`、`/etc/xboard-node/config.yml` 和 `xboard-node.service`。默认 machine mode、singbox，健康端口关闭；不生成 VPN 密钥，不申请证书。拒绝覆盖无管理标记的既有安装或 Docker 状态。重复相同安装不重新索取 Token、不重启；新提交保留配置并备份旧文件，启动失败尝试恢复，失败记录保留。中断后用已校验的安装器执行 `sudo python3 native.py recover`。

原生受管安装禁止 `xbctl upgrade` 回到官方公开 Release，更新应使用后台新版本的私有远程安装命令。服务启动检查仅表示进程稳定，面板心跳、端口可达和客户端连通仍需实际部署后独立验证。

## 当前验证边界

Windows 本地可运行合成安装/回滚/隐私回归、PHP 命令生成检查和 Linux 双架构交叉编译。Linux CI另执行两个架构的版本命令及真实 systemd 演练：后者只在显式启用的 GitHub 临时 runner 中运行无网络的 sleep/false 夹具，验证首次失败、重试、幂等、升级回滚和恢复，不启动真实节点。私有 Release 使用独立任务重新下载核验。上述 CI 结果不能当作目标服务器部署或真实协议联调成功。
