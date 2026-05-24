<h1 align="center">webchat2api</h1>

<p align="center">
  <img src="assets/logo.png" alt="webchat2api logo" width="180" />
</p>

<p align="center">webchat2api 是一个将 GPT/ChatGPT Web 与 Grok/xAI Web 能力封装为标准 API 接口的代理服务项目，提供 FastAPI 后端、Next.js Web 管理端、OpenAI 风格 API、GPT/Grok 账号池管理、文生文/文生图试验页以及 Docker 自托管部署能力。</p>

> [!WARNING]
> 免责声明：本项目涉及对 GPT/ChatGPT Web 与 Grok/xAI Web 能力的逆向研究与封装，仅供个人学习、技术研究与非商业性技术交流使用。严禁用于商业倒卖、批量滥用、违反服务条款或违法违规场景。使用者需自行承担账号受限、封禁及其他法律与合规风险。

> [!IMPORTANT]
> 默认登录密钥为 `admin`，仅适合本地测试。公网或生产环境部署后必须通过 `LOGIN_SECRET` 或 `WEBCHAT2API_AUTH_KEY` 修改为强随机密钥。

## 功能概览

- OpenAI 风格 API：将 GPT/ChatGPT Web 与 Grok/xAI Web 能力包装为 `/v1/models`、`/v1/chat/completions`、`/v1/images/generations`、`/v1/images/edits`、`/v1/responses`、`/v1/messages` 等接口
- GPT/Grok 文本模型：`/v1/models` 优先通过 `provider=gpt` 账号动态拉取 GPT 模型，并合并静态 Grok 模型；`/v1/chat/completions` 按 `model` 自动分发到 GPT 或 Grok 服务商账号
- Grok app-chat：支持通过 grok.com app-chat 路径访问带 `mode_id` 的 Grok 模型，并可走 Browser Bridge 用真实 Chromium 代理请求
- Grok 图片生成：`grok-imagine-image-lite`、`grok-imagine-image`、`grok-imagine-image-pro` 通过 app-chat 图片能力生成图片；`grok-imagine-image-edit` 和 `grok-imagine-video` 已列出但暂未实现
- tier 感知账号选择：Grok app-chat 会按模型所需 `basic`、`super`、`heavy` tier 和账号 `capabilities` 优先选择匹配账号，未匹配时再回退到普通 Grok 轮换
- Web 管理后台：账号池、用户 API Key、代理、日志、图片任务、图片文件和系统配置管理
- 远程账号注入：管理员可配置远程账号来源、手动同步来源，或通过 `/api/remote-account/inject` 注入账号；响应会隐藏来源鉴权 Token 和账号凭据
- 账号服务商：账号 `provider` 选择 `gpt` 或 `grok`，账号 `type` 仍表示套餐或订阅类型
- 试验页：文生文聊天、文本模型批量可用性测试、文生图/图生图切换、图片队列和图片历史
- 文生文聊天历史：保存在浏览器本地，刷新页面后仍保留
- 图片账号轮换：图片生成/编辑遇到失效账号时，会跳过该账号并尝试下一个可用账号
- 网络配置：ChatGPT Web、Grok Console 与 Grok app-chat 请求使用可配置网络 profile，支持独立的指纹、TLS impersonate、超时、代理和 Cloudflare Cookie
- Grok 防护处理：支持手动 `cf_clearance`、FlareSolverr clearance 刷新，以及可选 Browser Bridge 浏览器路径；这些都是尽力而为，不保证绕过所有 Cloudflare/WAF 挑战
- GPT Turnstile：默认启用 `enable_turnstile_solver`，会在 ChatGPT 返回 Turnstile 要求时尝试生成 Sentinel Turnstile Token；该能力依赖上游挑战和求解结果，真实 GPT Turnstile 仍可能失败
- 账号导出：仅导出 TXT，并按 GPT/Grok 服务商分别下载为 `webchat2api-gpt.txt` / `webchat2api_grok.txt`；文件内容每行一个 `access_token` 或 `sso` 凭据
- 部署方式：Docker CLI、Docker Compose

## 界面预览

<p align="center">
  <img src="assets/screenshot-overview.png" alt="webchat2api 界面预览 1" width="780" />
</p>

<p align="center">
  <img src="assets/screenshot-chat.png" alt="webchat2api 界面预览 2" width="780" />
</p>

## 快速开始

### Docker CLI 部署

新用户可先克隆仓库并进入项目目录：

```bash
git clone https://github.com/zqbxdev/webchat2api
cd webchat2api
```

构建本地镜像并运行：

```bash
docker build -t webchat2api:latest .

docker run -d \
  --name webchat2api \
  --restart unless-stopped \
  -p 83:83 \
  -v $(pwd)/data:/app/data \
  -e PORT=83 \
  -e HOST=0.0.0.0 \
  -e LOGIN_SECRET=admin \
  webchat2api:latest
```

如需容器访问宿主机代理：

```bash
docker run -d \
  --name webchat2api \
  --restart unless-stopped \
  --add-host=host.docker.internal:host-gateway \
  -p 83:83 \
  -v $(pwd)/data:/app/data \
  -e PORT=83 \
  -e HOST=0.0.0.0 \
  -e LOGIN_SECRET=admin \
  -e PROXY_URL=http://host.docker.internal:7890 \
  webchat2api:latest
```

部署后访问：

- 服务地址：`http://localhost:83`
- 管理后台：`http://localhost:83`
- API Base URL：`http://localhost:83/v1`
- 默认登录密钥：`admin`

Docker 镜像内置 Chromium、Node.js、npm 和 Grok Browser Bridge。容器启动时，`scripts/entrypoint.sh` 会先在 `BRIDGE_PORT` 上启动 `services/browser_bridge/server.js`，默认端口为 `3080`，并短暂探测 `/health`；即使 Bridge 未就绪，也会继续启动 FastAPI。

生产环境请立即修改默认密钥：

```bash
docker run -d \
  --name webchat2api \
  --restart unless-stopped \
  -p 83:83 \
  -v $(pwd)/data:/app/data \
  -e LOGIN_SECRET=your-strong-secret \
  webchat2api:latest
```

### Docker Compose 部署

`docker-compose.yml` 使用本地镜像 `webchat2api:latest`。请先按上面的命令构建镜像，再启动服务：

```bash
docker compose up -d
```

如需通过 Compose 构建并启动，可使用本地构建配置：

```bash
docker compose -f docker-compose.local.yml up -d --build
```

常用命令：

```bash
docker logs -f webchat2api
docker restart webchat2api
docker compose down
```

### 本地开发

启动后端：

```bash
uv sync
LOGIN_SECRET=admin uv run python main.py
```

启动前端开发服务：

```bash
cd web
npm install
npm run dev
```

## Grok Browser Bridge、防护与账号 tier

Grok Console 与 grok.com app-chat 是不同上游路径。本项目没有接入官方 xAI API，也不声称提供官方兼容能力。Console 路径可使用 `network_profiles.grok_console.cf_clearance` 附加手动 Cookie；app-chat 路径可使用 `network_profiles.grok_app_chat` 覆盖 UA、impersonate、`cf_clearance`、`cf_cookies`、`sec-ch-ua`、`x-statsig-id` 等字段。

如配置 `flaresolverr_url`，直接 app-chat 请求遇到 Cloudflare 或 403 时会尝试通过 FlareSolverr 刷新 clearance 并重试。Browser Bridge 是独立浏览器路径，后端会优先使用 `browser_bridge_url`，未配置时会探测 `http://127.0.0.1:3080/health`。Browser Bridge 的接口是 `POST /api/chat {sso,payload}` 和 `GET /health`，请求会经真实 Chromium 页面发往 grok.com。

> [!WARNING]
> Cloudflare、WAF、账号风控和上游配额都可能变化。手动 clearance、FlareSolverr 和 Browser Bridge 都是尽力而为，不能保证长期可用。

Grok app-chat 模型会按所需账号层级选号：`basic` 可跑 lite 和 fast，`super` 可跑 auto、expert、图片标准和 pro，`heavy` 优先用于 heavy 模型。账号可额外设置 `capabilities` 缩小可用范围。`grok-imagine-image` 和 `grok-imagine-image-pro` 如果账号没有对应 tier、图片权限或剩余额度，可能返回 `403` 或 `429`，这通常是上游账号限制，不一定是 Cloudflare 或 WAF 问题。

## API 示例

所有 AI 接口均使用 Bearer Token 鉴权：

```http
Authorization: Bearer <LOGIN_SECRET 或用户 API Key>
```

健康检查：

```bash
curl http://localhost:83/health
```

返回：

```json
{"status":"ok"}
```

模型列表：

```bash
curl http://localhost:83/v1/models \
  -H "Authorization: Bearer admin"
```

`/v1/models` 会优先使用已导入的 `provider=gpt` 账号动态拉取 GPT 模型；如果没有可用 GPT 账号或拉取失败，会回退到匿名/内置 GPT 模型。Grok 当前使用内置模型列表，因为现有 Grok token/cookie 无法访问 `console.x.ai` 或 `api.x.ai` 的模型列表端点。Grok 示例模型包括 `grok-4.3`、`grok-4`、`grok-4.20`、`grok-4.20-reasoning`、`grok-4.20-non-reasoning`、`grok-4.20-multi-agent`、`grok-imagine-image-lite`、`grok-imagine-image`、`grok-imagine-image-pro`。

聊天接口：

```bash
curl http://localhost:83/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer admin" \
  -d '{
    "model": "auto",
    "messages": [
      {"role": "user", "content": "你好"}
    ]
  }'
```

也可直接选择 Grok 模型，接口会按模型分发到 `provider=grok` 的账号：

```bash
curl http://localhost:83/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer admin" \
  -d '{
    "model": "grok-4.3",
    "messages": [
      {"role": "user", "content": "你好"}
    ]
  }'
```

GPT 图片生成接口：

```bash
curl http://localhost:83/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer admin" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "一只漂浮在太空里的猫",
    "n": 1,
    "response_format": "b64_json"
  }'
```

Grok 图片生成接口，经 grok.com app-chat 路径。`grok-imagine-image-lite` 通常需要 basic 及以上账号，`grok-imagine-image` 和 `grok-imagine-image-pro` 通常需要 super 及以上账号，并受上游图片配额限制：

```bash
curl http://localhost:83/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer admin" \
  -d '{
    "model": "grok-imagine-image-lite",
    "prompt": "一只漂浮在太空里的猫",
    "n": 1,
    "response_format": "b64_json"
  }'
```

当前 Grok app-chat 图片生成支持 `grok-imagine-image-lite`、`grok-imagine-image`、`grok-imagine-image-pro`。`grok-imagine-image-edit` 和 `grok-imagine-video` 暂未支持，请不要把它们当成可用的图生图或视频接口。ChatGPT 图片生成/编辑仍使用 GPT 服务商账号。

账号导入说明：

导入 GPT 账号时使用 `provider=gpt` 或保持默认；导入 Grok 账号 token/cookie 时使用 `provider=grok`。`provider` 负责选择服务商，`type` 仍用于记录 plan、subscription 等套餐或订阅类型。

远程账号注入面向管理员使用，适合把外部账号源同步到本项目账号池。常用接口包括：

- `GET /api/remote-account/sources` 查看已配置来源
- `POST /api/remote-account/sources` 新增来源
- `POST /api/remote-account/sources/{source_id}/sync` 手动同步来源
- `POST /api/remote-account/inject` 直接注入 payload、accounts 或 tokens

远程来源支持 `merge` 和 `replace`。`replace` 只替换同一 `source_id` 下的远程账号，不会清空其他来源或本地账号。来源配置响应会隐藏 `auth_token` 和 `bearer_token`，同步失败只返回通用错误，避免泄漏上游地址中的敏感细节。本项目参考了 grok2api 管理端注入和 upsert 思路，远程来源抓取层由本项目自行实现。

请求和响应示例见 [远程账号注入 API 示例](./docs/remote-account-api-examples.md)。

账号导出接口：

```bash
curl http://localhost:83/api/accounts/export \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer admin" \
  -d '{
    "provider": "gpt",
    "access_tokens": []
  }'
```

`provider` 可选 `gpt` 或 `grok`，用于分别导出 GPT/Grok TXT 文件；GPT 下载文件名固定为 `webchat2api-gpt.txt`，Grok 下载文件名固定为 `webchat2api_grok.txt`。`access_tokens` 为空数组时导出该服务商全部账号；内容仅包含凭据本身，每个账号一行，优先使用清理后的 `access_token`，缺失 `access_token` 时使用清理后的 `sso`。

## 配置

核心配置见 `.env.example`、`config.example.json` 和 [技术指南](./docs/technical-guide.md)。如需本地覆盖配置，请复制示例文件：

```bash
cp config.example.json config.json
```

`config.json` 是本地运行配置，可能包含代理、备份密钥或其他敏感值，不应提交。后端会写入 `config.json` 保存 Web UI 设置，部署时如果把该文件只读挂载，管理后台的设置保存会失败。`data/` 保存账号、用户密钥、日志、图片任务等运行数据，必须保持本地忽略，永远不要提交到代码仓库。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `83` | 服务监听端口 |
| `HOST` | `0.0.0.0` | 服务监听地址 |
| `LOGIN_SECRET` | `admin` | 管理后台默认登录密钥 |
| `WEBCHAT2API_AUTH_KEY` | 空 | 兼容旧配置的登录密钥覆盖项 |
| `WEBCHAT2API_BASE_URL` | 空 | 生成图片访问 URL 时使用的外部基础地址 |
| `PROXY_URL` | 空 | 上游请求使用的 HTTP/HTTPS/SOCKS 代理 |
| `network_profiles` | 见 `config.example.json` | ChatGPT Web、Grok Console 和 Grok app-chat 网络 profile |
| `network_profiles.grok_console.cf_clearance` | 空 | Grok Console 请求附加的 Cloudflare `cf_clearance` Cookie |
| `network_profiles.grok_app_chat` | 空 | Grok app-chat 请求 profile，可配置 `user-agent`、`impersonate`、`timeout`、`cf_clearance`、`cf_cookies`、`sec-ch-ua`、`x-statsig-id` 等字段 |
| `chatgpt_fingerprint` | 见 `config.example.json` | ChatGPT Web 请求指纹，可配置 UA、impersonate 和 `sec-ch-ua` 等字段 |
| `grok_console_fingerprint` | 空 | 旧版 Grok Console 指纹配置，仍兼容；同名字段会被 `network_profiles.grok_console` 覆盖 |
| `enable_turnstile_solver` | `true` | ChatGPT Turnstile 要求出现时尝试求解，不保证所有真实挑战都能通过 |
| `flaresolverr_url` | 空 | FlareSolverr 服务地址，配置后可为 Grok app-chat 尝试刷新 Cloudflare clearance |
| `flaresolverr_timeout_sec` | `60` | FlareSolverr 单次求解超时时间，单位秒 |
| `browser_bridge_url` | 空 | Grok Browser Bridge 地址；留空时后端会探测 `http://127.0.0.1:3080` |
| `BRIDGE_PORT` | `3080` | Docker 入口脚本启动 Browser Bridge 使用的端口 |
| `STORAGE_BACKEND` | `json` | 存储后端：`json`、`sqlite`、`postgres`、`git` |
| `DATABASE_URL` | 空 | SQLite/PostgreSQL 连接字符串 |
| `GIT_REPO_URL` | 空 | Git 存储后端仓库地址 |
| `GIT_TOKEN` | 空 | Git 存储后端访问令牌 |

## 文档

完整技术指南见：[docs/technical-guide.md](./docs/technical-guide.md)。

功能状态见：[docs/feature-status.md](./docs/feature-status.md)。

上游 SSE 会话协议参考见：[docs/upstream-sse-conversation.md](./docs/upstream-sse-conversation.md)。

## 社区支持

欢迎在 [linux.do](https://linux.do/) 社区交流反馈。

## 致谢

感谢 https://github.com/chenyme/grok2api 和 https://github.com/basketikun/chatgpt2api。
