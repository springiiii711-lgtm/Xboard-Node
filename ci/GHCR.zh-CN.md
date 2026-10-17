# 私有 GHCR 发布与认证拉取

此流程仅发布到 `ghcr.io/springiiii711-lgtm/xboard-node`，源码仓库为独立私有仓库 `springiiii711-lgtm/Xboard-Node`。只允许从 `dev` 手动触发；push 事件仅用于注册 workflow，job 不执行。`checkout-build.yml` 仍是纯构建验证，已停用的原上游发布工作流已移除。

## 发布流程

1. 检出本次 workflow 的完整 `github.sha`，面板同时检出固定管理员前端子模块。检查仓库私有状态、包名/归属、是否已有同名包或 SHA 标签；已有包必须私有且关联本仓库，否则停止，不修改访问设置。
2. 运行发布保护测试和已有隔离回归测试，构建当前 checkout。先加载镜像并离线核对源码标签、前端/锁文件摘要或节点版本；失败不会发布。
3. 使用该 job 的短期 `GITHUB_TOKEN`，通过 stdin 登录 GHCR。仅 job 获得 `contents: read` 和 `packages: write`，没有创建、扩展或保存长期 PAT/OAuth 凭据。登录发生在构建后，Docker 凭据目录位于 runner 临时目录，不传入构建上下文、参数或镜像。
4. 仅推送 `sha-完整40位提交SHA` 标签，不推送 latest，不覆盖已有 SHA 标签。若同一标签已经存在，复用其 digest 并继续验证。再次查询 package 的私有状态、关联仓库和版本 digest。
5. 独立全新 runner 的 job 仅获得 `packages: read` 和 `contents: read`，认证拉取 `image@sha256:...`，核对来源、摘要和离线运行；另外使用空 Docker 配置确认匿名读取 manifest 被拒绝。结果写入 Actions job summary。

`ci/ghcr.py` 的 GitHub API 请求全部是 GET；它不会创建凭据、修改包可见性、设置仓库访问、删除版本或改变账单。唯一写入 GHCR 的操作是 workflow 中明确的 `docker push`。遇到权限不足或访问设置问题应停止并报告，不能自动扩大权限。

## 触发与拉取

```sh
gh workflow run ghcr-publish.yml --repo springiiii711-lgtm/Xboard-Node --ref dev
# 在已完成合法认证且安装 Docker 的环境中，用成功运行 summary 中的完整 digest：
docker pull ghcr.io/springiiii711-lgtm/xboard-node@sha256:<verified-digest>
```

不要把 token 放进聊天、命令参数、Git 文件或镜像。此流程不为本机/服务器创建拉取凭据；在 Actions 之外拉取私有包需要已有适当认证，缺少时应另行确认所需授权。

## 验证范围与限制

只发布 `linux/amd64`。面板运行检查使用 PHP 命令和构建来源数据，节点只运行 `-v`，均禁用容器网络。没有部署网站、生产数据库或真实节点，没有开放端口。镜像保留此前验证入口，不能把发布成功等同于生产上线测试。

Actions 不上传 artifacts、build record 或持久缓存。镜像保存在私有 GHCR。基础镜像仍使用版本标签，所以相同源码不同时间重建不保证逐字节相同；部署应使用此次验证的 digest。

2026-10-16 核对的官方行为：首次发布新包默认私有；关联仓库默认继承访问权限，但可见性仍单独核查。GHCR 容器存储和带宽当前免费；Actions 仍使用账号包含分钟，触发前检查额度，不修改预算。

- [GHCR 默认私有、短期 token 与来源标签](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [包访问控制与仓库关联](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility)
- [GitHub Packages 计费](https://docs.github.com/en/billing/concepts/product-billing/github-packages)

2026-10-16 的实际 Actions 调用确认：短期令牌返回的 package 数据没有关联仓库 ID。现有账户登录独立核实了两组固定身份：Xboard 的 repository ID `1422070089` / package ID `15963085`，Xboard-Node 的 repository ID `1422070217` / package ID `15963168`。这些 ID 不是凭证。

工作流必须匹配上述不可变包 ID、仓库 ID、包名、所有者及 private 状态。若 API 返回关联仓库字段，它也必须一致；字段省略时仅允许已核实的固定包 ID，并在结果中明确标记需独立复核关联。包被删除、重建、无法访问或出现冲突时均停止，不能创建替代包。每次发布后使用已有账户登录独立查询包的实时仓库关联，记录在本地验证报告中；工作流本身不声称观察到了省略的关联字段。该流程不支持任意新包，也不向 Actions 注入长期凭证。
