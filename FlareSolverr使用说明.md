# FlareSolverr 集成 — 过 Cloudflare 5s 盾

starrailawards 升级了 Cloudflare 反爬（PAT/codec/TLS 多重指纹检测），patchright 和 playwright_stealth 都过不去。
方案：起一个 FlareSolverr 服务，它内部用真实 Chrome 跑过 Cloudflare 流程，把 `cf_clearance` cookie 返回给我们脚本注入用。

## 一次性准备

### 1. 装 Docker Desktop

Windows: https://www.docker.com/products/docker-desktop/
装完启动 Docker，看到任务栏 Docker 图标稳定（不闪烁）即可。

### 2. 启动 FlareSolverr 容器

打开 PowerShell / cmd，跑：

```powershell
docker run -d --name=flaresolverr --restart=unless-stopped -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest
```

- `-d`：后台运行
- `--name=flaresolverr`：容器名
- `--restart=unless-stopped`：开机自启
- `-p 8191:8191`：端口映射

启动后 FlareSolverr 监听在 `http://localhost:8191`。

### 3. 验证 FlareSolverr 在跑

浏览器打开 http://localhost:8191/v1 应该返回类似：
```json
{"msg": "FlareSolverr is ready!", ...}
```

或 PowerShell:
```powershell
curl http://localhost:8191/v1
```

## 在脚本里启用

1. 启动 [双击这里启动.bat](双击这里启动.bat)
2. GUI 上找到 **"FlareSolverr URL"** 输入框
3. 填 `http://localhost:8191`（默认端口）
4. 点开始

脚本每开一个新 ctx 会自动调 FlareSolverr 过 Cloudflare 拿 `cf_clearance` cookie 注入。

## 注意事项

| 项 | 说明 |
|---|---|
| **每票成本** | FlareSolverr 过一次 Cloudflare 大约 15-40 秒。比之前每票多耗这么多。6 并发并行调用会更快但 FlareSolverr 默认 max-connections 是 1 |
| **代理 IP 问题** | 当前实现 FlareSolverr 用**它容器自己的网络**过 Cloudflare（你本机 IP）。投票请求仍走代理 IP。Cloudflare 看 `cf_clearance` cookie 放行，**不严格校验 IP**（多数情况下，但偶尔会要求重新验证） |
| **关 FlareSolverr** | `docker stop flaresolverr` |
| **删容器** | `docker rm flaresolverr` |
| **看日志** | `docker logs -f flaresolverr` |
| **FlareSolverr 失败** | 脚本日志会写 `FlareSolverr 调用异常` 或 `返回非 ok`。看 docker logs 找原因 |

## 验证集成是否生效

脚本日志会出现：
```
[INFO] [0] FlareSolverr 过 Cloudflare 中 (代理 ...) ...
[OK]  [0] FlareSolverr 拿到 N 个 cookie，cf_clearance=有, UA=Mozilla...
[INFO] [0] 已注入 FlareSolverr 返回的 N 个 cookie
```

如果都正常但 starrailawards 仍然弹 Cloudflare 验证，可能：
1. `cf_clearance` 跟你 FlareSolverr 容器 IP 绑定 → 投票请求走代理 IP 时 Cloudflare 不认
2. Cloudflare 等级升级了，连 FlareSolverr 也过不去（很少见）

不行的话告诉脚本作者贴日志，再调。
