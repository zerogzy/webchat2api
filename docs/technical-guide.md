# webchat2api 技术文档

版本：0.0.2

## 1. 项目概述

webchat2api 是一个将 Web Chat 服务封装为标准 API 接口的代理服务项目，可用于将网页端会话能力转换为类 OpenAI API 风格的接口，方便第三方系统、自动化脚本、客户端或中间件进行调用。

核心能力：

- FastAPI 后端提供 OpenAI 风格 API。
- 文本 API 支持 GPT 与 Grok 服务商，`/v1/models` 优先动态拉取 GPT 模型并合并静态 Grok 模型，`/v1/chat/completions` 按请求 `model` 分发。
- Next.js Web 管理端提供账号池、用户密钥、代理、日志、图片任务、图片文件和系统配置管理。
- 试验页支持文生文聊天、文本模型批量可用性测试、文生图/图生图切换、图片队列和图片历史。
- 文生文聊天历史保存在浏览器本地，刷新页面后仍保留。
- 图片生成或编辑遇到失效账号时，会在同次任务中跳过该账号并轮换下一个可用账号，直到成功或账号池耗尽。
- 账号导出按 GPT/Grok 服务商分别生成 `webchat2api-gpt.txt` / `webchat2api_grok.txt`；TXT 内容每行一个 `access_token` 或 `sso` 凭据。
- 账号 `provider` 选择 `gpt` 或 `grok`，账号 `type` 仍表示 plan、subscription 等套餐或订阅类型。
- 支持远程账号来源配置、来源同步和管理员直接注入账号 payload。
- 支持 ChatGPT Web 与 Grok Console 独立网络 profile，Grok Console 可配置 `cf_clearance`。
- 支持 Docker CLI 和 Docker Compose 部署。
- 支持登录密钥和用户 API Key 鉴权。

默认部署信息：

- 服务地址：`http://localhost:83`
- 管理后台：`http://localhost:83`
- API Base URL：`http://localhost:83/v1`
- 默认登录密钥：`admin`

生产环境部署后请立即修改默认登录密钥，避免未授权访问。

## 2. 项目架构

```text
webchat2api
├── API 服务层
├── Web 管理端
├── 鉴权模块
├── 会话 / Token 管理模块
├── 远程账号注入模块
├── 网络 Profile 模块
├── Proxy 转发模块
├── 配置管理模块
├── 日志模块
└── Docker 部署模块
```

### 2.1 API 服务层

相关文件：`main.py`、`api/app.py`、`api/ai.py`、`api/accounts.py`、`api/system.py`、`api/image_tasks.py`。

职责：

- 接收外部 HTTP 请求。
- 校验请求参数和 Bearer Token。
- 调用账号池、Web Chat、图片任务、代理和存储服务。
- 返回标准 JSON 或 OpenAI 风格响应。

主要路由：

- `GET /health`
- `GET /version`
- `POST /auth/login`
- `GET /api/accounts`
- `POST /api/accounts/export`
- `GET /api/remote-account/sources`
- `POST /api/remote-account/sources`
- `POST /api/remote-account/sources/{source_id}`
- `DELETE /api/remote-account/sources/{source_id}`
- `POST /api/remote-account/sources/{source_id}/sync`
- `GET /api/remote-account/sources/{source_id}/sync`
- `POST /api/remote-account/inject`
- `GET /v1/models` 优先通过 `provider=gpt` 账号动态拉取 GPT 模型，并合并静态 Grok 模型。
- `POST /v1/chat/completions` 根据请求中的 `model` 分发到 GPT 或 Grok 账号。
- `POST /v1/images/generations`
- `POST /v1/images/edits`
- `POST /v1/responses`
- `POST /v1/messages`

### 2.2 Web 管理端

相关目录：`web/`、`web/src/app/`、`web/src/components/`、`web/src/store/`。

职责：

- 管理后台登录。
- 管理账号池和用户 API Key。
- 导入、刷新、导出和删除账号。
- 配置代理、基础 URL、备份、图片存储和过滤策略。
- 查看调用日志。
- 管理图片任务和图片文件。
- 在试验页进行文生文、批量模型测试、文生图和图生图。

前端构建为静态产物，Docker 镜像构建时复制到后端镜像内的 `web_dist`，由 FastAPI 静态回退路由提供访问。

### 2.3 鉴权模块

相关文件：`services/config.py`、`services/auth_service.py`、`api/support.py`。

鉴权方式：

- 管理端登录密钥，默认 `admin`。
- 用户 API Key，由管理员在后台生成。
- 请求头统一使用：

```http
Authorization: Bearer <密钥>
```

登录密钥优先级：

1. `LOGIN_SECRET`
2. `WEBCHAT2API_AUTH_KEY`
3. `config.json` 中的 `auth-key`
4. 内置默认值 `admin`

### 2.4 会话 / Token 管理模块

相关文件：`services/account_service.py`、`services/cpa_service.py`、`services/sub2api_service.py`、`services/remote_account_service.py`、`services/storage/`。

职责：

- 保存和读取账号池数据。
- 刷新账号状态、额度、限流状态和恢复时间。
- 导入本地 CPA、远程 CPA、Sub2API、GPT access token 和 Grok token/cookie。
- Grok 账号导入时设置 `provider=grok`；GPT 账号使用 `provider=gpt` 或默认值。
- 导出账号数据。导出 TXT 内容仅包含凭据本身，每个账号一行，优先使用 `access_token`，缺失时使用 `sso`。
- 图片生成/编辑时遇到失效账号会跳过并轮换下一个账号。
- `provider` 决定账号服务商，`type` 只记录套餐或订阅类型，不用于选择 GPT/Grok。
- 通过 JSON、SQLite、PostgreSQL 或 Git 存储账号与用户密钥。

### 2.5 远程账号注入模块

相关文件：`services/remote_account_service.py`、`api/accounts.py`、`api/support.py`、`services/account_service.py`。

职责：

- `services/remote_account_service.py` 负责远程来源配置、HTTP 拉取、payload 归一化、merge 或 replace 注入。
- `api/accounts.py` 暴露管理员路由，包含来源列表、新增、更新、删除、同步、同步任务查询和直接注入。
- `api/support.py` 对远程来源响应做脱敏，隐藏 `auth_token` 与 `bearer_token`，只返回 `has_auth_token` 与 `has_bearer_token`。
- `services/account_service.py` 执行账号 upsert 和按 `remote_source_id` 的来源范围 replace。

路由组：

- `GET /api/remote-account/sources`：查看远程来源列表。
- `POST /api/remote-account/sources`：新增远程来源。
- `POST /api/remote-account/sources/{source_id}`：更新远程来源。
- `DELETE /api/remote-account/sources/{source_id}`：删除远程来源配置。
- `POST /api/remote-account/sources/{source_id}/sync`：拉取指定来源并同步账号。
- `GET /api/remote-account/sources/{source_id}/sync`：查看最近一次同步任务。
- `POST /api/remote-account/inject`：直接注入 payload、accounts 或 tokens。

安全语义：

- 所有远程账号接口都需要管理员权限，不接受普通用户 API Key 执行管理操作。
- `merge` 会按 `access_token` 合并账号，远程 payload 省略的字段保留现有账号值，因此状态、额度、限流恢复时间、成功失败计数等可变字段不会被空 payload 覆盖。
- `replace` 必须带 `source_id`，并且只替换同一 `remote_source_id` 下的账号，不会删除其他来源或本地账号。
- 来源同步失败返回统一的 `remote account sync failed`，避免把上游地址、鉴权头或异常细节暴露给响应调用方。
- 来源配置响应不返回 `auth_token`、`bearer_token`，账号注入结果只返回计数、策略和来源信息，不返回账号 Token 列表。
- 本项目参考 grok2api 管理端注入和 upsert 语义；任意远程 URL 拉取层是本项目自己的实现，不声明来自 grok2api 上游。
- 请求和响应示例见 [远程账号注入 API 示例](./remote-account-api-examples.md)。

### 2.6 网络 Profile 模块

相关文件：`services/network/profiles.py`、`services/network/headers.py`、`services/network/client.py`、`services/network/retry.py`、`services/network/errors.py`。

职责：

- `profiles.py` 定义 ChatGPT Web 与 Grok Console profile，读取全局配置和账号内指纹字段，并处理 `network_profiles.grok_console` 覆盖旧配置。
- `headers.py` 根据 profile 生成 ChatGPT Web 和 Grok Console 请求头，Grok Console 会把账号 sso/token 和 `cf_clearance` 组合成 Cookie。
- `client.py` 统一创建 `curl_cffi` Session，接入 impersonate、证书校验和代理设置。
- `retry.py` 提供轻量重试策略，用于可重试的上游请求。
- `errors.py` 保留网络 profile 相关错误类型。

配置语义：

- `network_profiles` 是新的网络配置入口，当前主要使用 `network_profiles.grok_console`。
- `network_profiles.grok_console.cf_clearance` 会作为 `cf_clearance` Cookie 附加到 Grok Console 请求，适合已取得 Cloudflare clearance 的部署。
- `grok_console_fingerprint` 是旧版 Grok Console 指纹配置，仍兼容；同名字段以 `network_profiles.grok_console` 为准。
- `chatgpt_fingerprint` 继续控制 ChatGPT Web 请求的 UA、impersonate、`sec-ch-ua` 和设备会话标识，可被账号内 `fp` 或同名字段覆盖。
- `enable_turnstile_solver` 默认 `true`。当 ChatGPT Sentinel 返回 Turnstile 要求时，后端会尝试生成 `OpenAI-Sentinel-Turnstile-Token`；如果求解返回空值会失败关闭。真实 GPT Turnstile 是否通过仍取决于上游挑战和求解结果，不能承诺全部可解。

### 2.7 Proxy 转发模块

相关文件：`services/proxy_service.py`。

职责：

- 为上游 Web Chat / ChatGPT / Grok 请求配置 HTTP、HTTPS、SOCKS5 或 SOCKS5H 代理。
- 提供代理连通性测试。
- 环境变量 `PROXY_URL` 可覆盖配置文件中的 `proxy` 字段。

### 2.8 日志模块

相关文件：`services/log_service.py`、`api/system.py`。

默认日志文件位置：`data/logs.jsonl`。

日志会对请求摘要做截断处理，但仍不建议在提示词、Token、Cookie 或 Session 中写入敏感信息。

### 2.9 GPT/Grok 文本模型集成

相关文件：`services/models.py`、`api/ai.py`、Grok 账号处理代码。

集成要点：

- `services/models.py` 维护 `gpt` 与 `grok` 服务商模型。
- GPT 模型列表优先通过 `provider=gpt` 账号动态拉取；没有 GPT 账号或拉取失败时，回退到匿名 ChatGPT Web 模型和内置 GPT fallback。
- Grok 控制台模型使用内置列表：`grok-4.3`、`grok-4`、`grok-4.20`、`grok-4.20-reasoning`、`grok-4.20-non-reasoning`、`grok-4.20-multi-agent`。
- `/v1/chat/completions` 保持公共入口不变，按请求 `model` 选择对应服务商账号。
- Grok 当前支持文本聊天，非流式响应和流式请求都会返回 OpenAI 兼容结果；流式请求会封装为 stream-compatible chunks。
- Grok 上游返回 402 或 429 时按账号受限处理，记录限流状态和恢复检查。
- 现有 Grok token/cookie 无法访问 `console.x.ai` 或 `api.x.ai` 的模型列表端点，因此当前不做 Grok 动态模型拉取。
- 本项目使用 Grok 账号 token/cookie 的 provider 处理，不声明官方 xAI API Key 接入。

## 3. 技术栈说明

| 类型 | 技术 |
| --- | --- |
| 后端语言 | Python 3.13 |
| Web 框架 | FastAPI |
| ASGI 服务 | Uvicorn |
| 前端框架 | Next.js 16、React 19、TypeScript |
| UI / 状态 | Tailwind CSS、Radix UI、Zustand、localforage |
| HTTP 客户端 | curl-cffi、axios |
| 存储 | JSON、SQLite、PostgreSQL、Git 存储后端 |
| 容器化 | Docker |
| 部署方式 | Docker CLI、Docker Compose |
| 鉴权方式 | 登录密钥、用户 API Key |
| 配置方式 | 环境变量、本地 `config.json`、Web 管理端设置 |
| 包管理 | Python `uv`、前端 npm/bun lockfile |

## 4. 目录结构说明

```text
webchat2api/
├── api/                         # FastAPI 路由层
├── services/                    # 业务服务层
│   ├── network/                 # 网络 profile、请求头、Session 和重试工具
│   ├── protocol/                # OpenAI/Anthropic 兼容协议实现
│   └── storage/                 # JSON/数据库/Git 存储后端
├── utils/                       # 通用工具
├── web/                         # Next.js 管理端源码
│   ├── src/app/                 # 页面路由
│   ├── src/components/          # UI 组件
│   ├── src/lib/                 # 前端 API 请求封装
│   └── src/store/               # 前端状态存储，含图片历史和文本聊天历史
├── scripts/                     # 维护和迁移脚本
├── test/                        # Python 单元测试
├── data/                        # 运行数据目录，部署时建议挂载，禁止提交
├── docs/                        # 公开文档与内部过程记录
├── Dockerfile
├── docker-compose.yml
├── docker-compose.local.yml
├── config.example.json          # 安全示例配置，复制为 config.json 后再本地修改
├── .env.example
├── pyproject.toml
├── README.md
├── VERSION
└── docs/technical-guide.md
```

## 5. 配置说明

| 配置项 | 默认值 | 是否必填 | 说明 |
| --- | --- | --- | --- |
| `PORT` | `83` | 否 | 服务监听端口。Docker 默认暴露 `83`。 |
| `HOST` | `0.0.0.0` | 否 | 服务监听地址。 |
| `LOGIN_SECRET` | `admin` | 否 | 管理端默认登录密钥，优先级最高。 |
| `WEBCHAT2API_AUTH_KEY` | 空 | 否 | 兼容旧配置的登录密钥覆盖项。 |
| `WEBCHAT2API_BASE_URL` | 空 | 否 | 外部访问基础 URL，用于生成图片文件访问地址。 |
| `PROXY_URL` | 空 | 否 | 上游代理地址。 |
| `STORAGE_BACKEND` | `json` | 否 | 存储后端：`json`、`sqlite`、`postgres`、`git`。 |
| `DATABASE_URL` | 空 | 否 | SQLite/PostgreSQL 连接字符串。 |
| `GIT_REPO_URL` | 空 | Git 后端必填 | Git 存储后端仓库地址。 |
| `GIT_TOKEN` | 空 | Git 后端通常必填 | Git 仓库访问令牌。 |
| `GIT_BRANCH` | `main` | 否 | Git 存储分支。 |
| `GIT_FILE_PATH` | `accounts.json` | 否 | Git 存储账号文件路径。 |
| `GIT_AUTH_KEYS_FILE_PATH` | `auth_keys.json` | 否 | Git 存储用户密钥文件路径。 |
| `network_profiles` | 见示例配置 | 否 | 网络 profile 配置，当前包含 `grok_console`。 |
| `enable_turnstile_solver` | `true` | 否 | ChatGPT Turnstile 出现时尝试求解，失败时关闭请求。 |

`config.example.json` 是可提交的安全示例。需要本地覆盖时复制为 `config.json` 后再修改：

```bash
cp config.example.json config.json
```

`config.json` 是本地运行文件，可能包含代理、备份密钥或其他敏感配置，不应提交。后端会写入 `config.json` 保存 Web UI 设置，部署时如果把该文件只读挂载，管理后台的设置保存会失败。`data/` 保存账号、用户密钥、日志、图片任务和图片文件等运行数据，必须保持未提交。

`config.json` 中的重要配置：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `auth-key` | `admin` | 管理端登录密钥；会被 `LOGIN_SECRET` 或 `WEBCHAT2API_AUTH_KEY` 覆盖。 |
| `refresh_account_interval_minute` | `5` | 限流账号后台检查间隔。 |
| `image_retention_days` | `15` | 本地图片保留天数。 |
| `image_poll_timeout_secs` | `120` | 图片任务轮询超时时间。 |
| `proxy` | 空 | 上游代理地址，会被 `PROXY_URL` 覆盖。 |
| `base_url` | 空 | 生成图片 URL 的基础地址，会被 `WEBCHAT2API_BASE_URL` 覆盖。 |
| `backup` | 默认关闭 | 云备份配置。 |
| `image_account_concurrency` | `3` | 图片账号并发数量。 |
| `network_profiles.grok_console.impersonate` | `edge101` | Grok Console 请求使用的 curl-cffi impersonate。 |
| `network_profiles.grok_console.user-agent` | 见示例配置 | Grok Console 请求 UA。 |
| `network_profiles.grok_console.verify` | `true` | Grok Console TLS 校验开关。 |
| `network_profiles.grok_console.timeout` | `60` | Grok Console 请求超时时间。 |
| `network_profiles.grok_console.cf_clearance` | 空 | Grok Console 请求附加的 Cloudflare clearance Cookie。 |
| `chatgpt_fingerprint` | 见示例配置 | ChatGPT Web 请求指纹。 |
| `grok_console_fingerprint` | 空 | 旧版 Grok Console 指纹配置，仍兼容；建议迁移到 `network_profiles.grok_console`。 |
| `enable_turnstile_solver` | `true` | 是否在 ChatGPT 要求 Turnstile 时尝试生成 Sentinel Turnstile Token。 |

默认登录密钥为：

```text
admin
```

生产环境部署后请立即修改默认登录密钥。

## 6. 管理后台与试验页

### 6.1 管理后台

管理后台入口：`http://localhost:83`。

后台主要能力：

- 账号池列表、搜索、筛选、刷新、删除和状态编辑。
- 账号导入：支持 access token、本地 CPA、远程 CPA、Sub2API、Grok token/cookie 和远程账号来源。
- ChatGPT 图片生成和图片编辑仍只使用 GPT 服务商账号，不声明 Grok 图片能力。
- 管理后台可按服务商 `provider` 和套餐 `type` 分别筛选账号。
- 账号导出：仅导出 TXT，并按 GPT/Grok 服务商分别下载为 `webchat2api-gpt.txt` / `webchat2api_grok.txt`；文件内容每行一个 `access_token` 或 `sso` 凭据。
- 用户 API Key 管理。
- 代理、基础 URL、备份、图片存储等配置。
- 日志查看与清理。

### 6.2 试验页

试验页位于 `/image`，通过顶部切换区分文本试验和图像试验。

文本试验：

- 调用 `/v1/chat/completions`。
- 聊天消息会保存在浏览器本地 localforage 中，刷新页面后仍保留。
- 错误消息会显示在聊天历史中，但不会作为下一次 API 请求上下文发送。
- 提供“批量测试模型”按钮，会从 `/v1/models` 获取 GPT/Grok 模型，逐个调用文本模型并显示 `pending`、`testing`、`success`、`error` 状态。
- 提供清空文本聊天记录功能。

图像试验：

- 文生图调用 `/v1/images/generations`。
- 图生图调用 `/v1/images/edits`。
- 图片任务保留队列和历史记录。
- 遇到失效账号时，后端会在同次任务中排除该账号并尝试下一个可用账号，直到成功或账号池耗尽。

## 7. Docker CLI Proxy 部署方式

### 7.1 构建镜像

```bash
docker build -t webchat2api:latest .
```

### 7.2 启动容器

标准启动命令：

```bash
docker run -d \
  --name webchat2api \
  --restart unless-stopped \
  -p 83:83 \
  -e PORT=83 \
  -e HOST=0.0.0.0 \
  -e LOGIN_SECRET=admin \
  webchat2api:latest
```

带数据目录挂载：

```bash
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

如需访问宿主机代理：

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

## 8. Docker Compose 部署方式

```yaml
services:
  webchat2api:
    image: webchat2api:latest
    container_name: webchat2api
    restart: unless-stopped
    ports:
      - "83:83"
    environment:
      PORT: 83
      HOST: 0.0.0.0
      LOGIN_SECRET: admin
      PROXY_URL: http://host.docker.internal:7890
    volumes:
      - ./data:/app/data
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

启动：

```bash
docker compose up -d
```

查看日志：

```bash
docker logs -f webchat2api
```

配置文件卫生：

`.gitignore` 已忽略本地 `config.json`，该文件可用于保存本地密钥、代理和运行时配置。仓库已提供 `config.example.json` 作为可提交示例。

停止服务：

```bash
docker compose down
```

## 9. 部署完成后的访问信息

```text
服务地址：http://服务器IP:83
本机访问：http://localhost:83
管理后台：http://服务器IP:83
默认登录密钥：admin
API Base URL：http://服务器IP:83/v1
Chat Completions：POST http://服务器IP:83/v1/chat/completions
```

## 10. 登录与鉴权说明

打开 `http://localhost:83`，输入登录密钥。

默认登录密钥：

```text
admin
```

默认密钥仅适合本地测试。公网或生产环境部署后，必须通过环境变量或配置文件修改 `LOGIN_SECRET`。

API 调用鉴权：

```http
Authorization: Bearer YOUR_API_KEY
```

管理员密钥可直接作为 Bearer Token 使用。也可以在管理后台创建用户 API Key，供第三方服务调用。

## 11. API 使用说明

### 11.1 健康检查

```bash
curl http://localhost:83/health
```

期望返回：

```json
{"status":"ok"}
```

### 11.2 版本查询

```bash
curl http://localhost:83/version
```

### 11.3 模型列表

```bash
curl http://localhost:83/v1/models \
  -H "Authorization: Bearer YOUR_API_KEY"
```

该接口返回 GPT 与 Grok 文本模型。Grok 示例模型包括 `grok-4.3`、`grok-4`、`grok-4.20`、`grok-4.20-reasoning`、`grok-4.20-non-reasoning`、`grok-4.20-multi-agent`。

### 11.4 聊天接口

```bash
curl http://localhost:83/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "auto",
    "messages": [
      {"role": "user", "content": "你好"}
    ]
  }'
```

选择 Grok 模型时，请求路径仍为 `/v1/chat/completions`，后端会分发到 `provider=grok` 的账号：

```bash
curl http://localhost:83/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "grok-4.3",
    "messages": [
      {"role": "user", "content": "你好"}
    ]
  }'
```

当前 Grok 支持文本聊天；非流式响应和流式请求都会返回 OpenAI 兼容结果，流式请求会封装为 stream-compatible chunks。

### 11.5 图片生成接口

```bash
curl http://localhost:83/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "一只漂浮在太空里的猫",
    "n": 1,
    "response_format": "b64_json"
  }'
```

图片生成和图片编辑只使用 GPT 服务商账号，不声明 Grok 图片能力。

### 11.6 图片编辑接口

```bash
curl http://localhost:83/v1/images/edits \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -F "model=gpt-image-2" \
  -F "prompt=把这张图改成赛博朋克夜景风格" \
  -F "n=1" \
  -F "image=@./input.png"
```

### 11.7 Responses 接口

```bash
curl http://localhost:83/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "gpt-5",
    "input": "生成一张未来感城市天际线图片",
    "tools": [{"type": "image_generation"}]
  }'
```

### 11.8 账号导出接口

```bash
curl http://localhost:83/api/accounts/export \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "provider": "gpt",
    "access_tokens": ["ACCESS_TOKEN_1"]
  }'
```

`provider` 可取 `gpt` 或 `grok`，接口按服务商返回 TXT 文件：GPT 文件名固定为 `webchat2api-gpt.txt`，Grok 文件名固定为 `webchat2api_grok.txt`。`access_tokens` 为空数组时导出该服务商全部账号；内容仅包含凭据本身，每个账号一行，优先使用清理后的 `access_token`，缺失 `access_token` 时使用清理后的 `sso`。

## 12. 日志与排错

```bash
docker ps | grep webchat2api
docker logs -f webchat2api
docker exec -it webchat2api sh
docker restart webchat2api
docker rm -f webchat2api
docker build -t webchat2api:latest .
curl http://localhost:83/health
```

## 13. 常见问题

### 13.1 无法访问管理后台

检查容器是否运行：

```bash
docker ps
docker logs -f webchat2api
```

确认端口映射是否正确：

```bash
-p 83:83
```

确认防火墙是否放行端口 `83`。

### 13.2 登录密钥错误

确认启动容器时的环境变量：

```bash
-e LOGIN_SECRET=admin
```

如果修改过密钥，请使用新的密钥登录。

### 13.3 容器无法访问宿主机代理

Linux 环境需要添加：

```bash
--add-host=host.docker.internal:host-gateway
```

并设置：

```bash
-e PROXY_URL=http://host.docker.internal:7890
```

### 13.4 端口被占用

检查端口：

```bash
lsof -i :83
```

或更换宿主机端口：

```bash
-p 8080:83
```

访问地址变为：`http://localhost:8080`。

### 13.5 API 返回 401

检查请求头是否包含 Bearer Token：

```http
Authorization: Bearer YOUR_API_KEY
```

### 13.6 账号导出返回 400 或导出为空

当前版本已支持 access-token-only 账号导出。若仍遇到导出失败，请检查：

- 是否已选择至少一个账号，或请求中 `access_tokens` 是否为空数组用于导出全部账号。
- 请求头是否包含管理员密钥或有权限的用户 API Key。
- 账号是否仍存在于账号池中。

### 13.7 图片生成遇到账号失效

后端会识别 token invalid、token revoked、invalidated oauth token 等失效错误，并在同次图片生成任务中跳过该账号，尝试下一个可用账号。若所有账号都不可用，接口才会返回失败。

### 13.8 Grok 账号无法调用

确认账号导入时已设置 `provider=grok`，并保留 token/cookie 等 Grok 网页端凭据。`type` 字段只表示 plan、subscription 等套餐信息，不会决定服务商。若 Grok 上游返回 402 或 429，后端会按账号受限处理并等待后续恢复检查。

## 14. 安全建议

- 生产环境不要使用默认密钥 `admin`。
- 不要将管理后台直接暴露到公网。
- 建议通过反向代理启用 HTTPS。
- 建议限制访问 IP。
- API Key 不应写入前端代码。
- 日志中不要输出完整 Token、Cookie、Session 或其他敏感凭据。
- 定期更新镜像。
- 默认运行数据保存在 `data/`，建议做好备份和访问权限控制，禁止提交到代码仓库。
- 文本试验聊天历史保存在浏览器本地存储中，共用浏览器环境时请注意清理。

## 15. 更新与维护

远程镜像更新：

```bash
docker pull webchat2api:latest
docker rm -f webchat2api
docker run -d \
  --name webchat2api \
  --restart unless-stopped \
  -p 83:83 \
  -v $(pwd)/data:/app/data \
  -e LOGIN_SECRET=your-strong-secret \
  webchat2api:latest
```

本地构建更新：

```bash
git pull
docker build -t webchat2api:latest .
docker rm -f webchat2api
docker run -d \
  --name webchat2api \
  --restart unless-stopped \
  -p 83:83 \
  -v $(pwd)/data:/app/data \
  -e LOGIN_SECRET=your-strong-secret \
  webchat2api:latest
```

更新后检查 `/health`、`/version`、管理后台登录、账号导出和试验页文本聊天历史。

## 16. 交付状态

已完成：

- 项目名称统一为 `webchat2api`。
- Docker 镜像名建议为 `webchat2api:latest`。
- 容器名建议为 `webchat2api`。
- 默认端口为 `83:83`。
- 默认登录密钥为 `admin`。
- 技术文档已更新。
- README 与技术文档中的项目名称和端口一致。

## 部署完成提示

```text
webchat2api 部署完成。

服务地址：
http://localhost:83

如果部署在服务器上，请访问：
http://服务器IP:83

管理后台：
http://localhost:83

默认登录密钥：
admin

容器名称：
webchat2api

查看日志：
docker logs -f webchat2api

重启服务：
docker restart webchat2api

安全提醒：
默认登录密钥 admin 仅建议本地测试使用，生产环境请立即修改 LOGIN_SECRET。config.json 和 data/ 是本地运行文件，禁止提交到代码仓库；后端会写入 config.json 保存 Web UI 设置，只读挂载会导致设置保存失败。
```
