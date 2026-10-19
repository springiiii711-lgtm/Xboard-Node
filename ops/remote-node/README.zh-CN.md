# 短命令原生安装入口

此目录实现官方风格的远程安装入口，使用本仓库自行编译的 xboard-node / xbctl，支持 Linux amd64、arm64 和原生 systemd，不要求 Docker。安装命令只传入 HTTPS 面板地址与机器编号；面板机器 Token 在目标终端隐藏输入，仍用于面板与节点之间的认证。

## 当前交付状态

代码与隔离测试可以在私有仓库完成。仓库尚未公开或 Release 尚未提供完整资产时，匿名下载会明确失败，不会索取 GitHub 令牌或回退到上游程序。面板的 `REMOTE_NODE_INSTALL_ENABLED` 默认关闭；只有完成公开审查和真实匿名交付验证后才能启用。仅生成脚本或通过隔离测试不代表公共 URL 已可用。

未来完成公开交付后的短命令形态（此处不是已上线声明）：

```bash
bash -o pipefail -c 'curl -qfsSL --proto "=https" --proto-redir "=https" https://github.com/springiiii711-lgtm/Xboard-Node/releases/latest/download/install.sh | sudo bash -s -- --panel "$1" --machine-id "$2"' -- \
  'https://你的面板地址' 机器编号
```

首次下载入口需要 bash、curl 和 sudo/root；脚本内部安装必要的 Python 3.9+ / CA 证书，检查 Linux、amd64/arm64 和运行中的 systemd。支持 apt、dnf、yum；旧发行版仓库无法提供 Python 3.9+ 时会停止。

## 文件与构建

- `entry.sh.in`：依赖与平台检查模板；完整 Python 下载器在打包时嵌入，无需在面板展开脚本。
- `download.py`：匿名 GitHub 读取、固定版本解析、下载和校验；无发布和可见性修改功能。
- `test_remote.py`：合成发布数据与安装事务回归。
- `ci/remote-package.py`：从已有干净提交的原生构建结果生成本地 `install.sh`、原生 ZIP 与 `SHA256SUMS`；不上传文件。
- `ci/remote-check.py`：将实际构建的包通过匿名 loopback HTTP 夹具下载并校验，不安装服务。

```bash
python3 -B -m unittest discover -s ops/remote-node -p 'test_*.py' -v
python3 -B ci/remote-package.py --directory /tmp/native-build --output /tmp/remote-native
python3 -B ci/remote-check.py --directory /tmp/remote-native
bash /tmp/remote-native/install.sh --help
```

已有 `checkout-build.yml` 会执行这些测试、构建两种架构并用 QEMU 检查 arm64 版本输出。此工作流保持只读权限，不发布产物。真实 systemd 测试只在 GitHub 临时 runner 启动无网络的 sleep/false 夹具。

## 校验与安装事务

脚本只向固定仓库匿名发起 GET，不读取环境 GitHub Token、netrc、curl 配置或代理凭据。先确认仓库公开，再解析一次 latest，要求 `native-<完整提交 SHA>` 的稳定 Release，且 target_commitish 与该提交一致。此后固定下载版本，不在下载过程中重新查询 latest。

包摘要来自 HTTPS GitHub Release API 的资产 digest，检查实际长度与 SHA256，再验证 ZIP 精确文件清单、每个文件摘要、源码身份和两个架构的 ELF 头。拒绝重复路径、目录穿越、链接、错误版本、缺失文件或摘要。摘要用于完整性与来源一致性；不把同源 SHA256 说成独立的代码签名。

安装复用 `ops/private-node/native.py` 的锁、配置权限、绑定保护、重复安装幂等、失败回滚和中断恢复。不接管未知安装，不修改防火墙，不申请证书，不启动 Docker。临时目录自动清理，安装备份按原有策略保留。

`--verify-only` 只下载并验证，不读取机器 Token、不执行节点二进制、不写服务；它不会安装缺失依赖。真实公共 URL 验证必须在仓库和匹配 Release 经批准公开后单独执行。

## 公开前要求

公开仓库意味着分支、标签和历史也会公开，需要先审查敏感信息。MPL/GPL 及依赖许可的对应源码、构建材料与声明也必须准备；仓库公开不自动证明所有依赖源码材料已经齐全。不要用此打包工具直接替代发布审查。
