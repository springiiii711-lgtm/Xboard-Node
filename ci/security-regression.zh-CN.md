# 节点日志安全回归

本分支尚未合并或发布，不启动节点服务。修复前 WebSocket debug 会打印带认证 token 的 URL，以及完整用户/配置载荷；HTTP 请求失败也可能把 GET URL 中的 token 放进错误文本。

修改保持面板认证协议不变：日志只保留连接 origin、已知事件名和载荷字节数；不打印消息体、查询串、userinfo、路径、未知事件原文或远端错误体。HTTP/WS 传输错误保留 `errors.Is/As` 的原因链，默认错误文本隐藏敏感内容。排错时现在主要依赖状态码、错误类型和事件，而非原始响应。

本机使用已有模块缓存并设置 `GOPROXY=off`、`GOSUMDB=off`、`GOTOOLCHAIN=local`、`GOTELEMETRY=off`、`-mod=readonly`。

- 修复前 `TestSecurityTransportErrorOmitsCredentials` 已复现令牌出现在真实 Client.GetConfig 的错误字符串中。
- 修复后 `go test ./internal/panel -run 'TestSecurity(Transport|Status|LogFields)' -count=1 -timeout=30s` 通过，3 个测试均不打开套接字。
- `internal/nlog` 没有测试文件，不计为回归通过数。
- WebSocket 合成端到端测试使用本机 `httptest` 回环服务；Windows 沙箱拒绝连接，错误为 `connectex: An attempt was made to access a socket in a way forbidden by its access permissions.` 未绕过限制，也未计为通过。

`ci/Dockerfile` 已添加网络隔离的面板回归命令，包括 WebSocket 合成回环、HTTP 既有回归。待批准后由 `checkout-build.yml` 构建和执行；不会发布镜像或启动真实代理服务。

修复不清理已有日志，不更换已有令牌，不审计或修改第三方代理内核。旧二进制安装/升级路径仍是独立供应链加固事项；当前私有镜像安装路径使用固定摘要。
