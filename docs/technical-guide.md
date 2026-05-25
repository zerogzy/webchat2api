# webchat2api 技术文档

版本：0.0.7

## 1. 项目概述

webchat2api 是一个将 Web Chat 服务封装为标准 API 接口的代理服务项目，可用于将网页端会话能力转换为类 OpenAI API 风格的接口，方便第三方系统、自动化脚本、客户端或中间件调用。

核心能力：

- FastAPI 后端提供 OpenAI 风格 API。
- 文本 API 支持 GPT 与 Grok 服务商，`/v1/models` 优先动态拉取 GPT 模型并合并静态 Grok 模型，`/v1/chat/completions` 按请求 `model` 分发。
- Grok 同时支持 Console 与 app-chat 两条网页端路径，app-chat 模型通过 Grok 网页会话、网络 profile、FlareSolverr clearance 和 Browser Bridge 尝试完成请求。
- Grok app-chat 支持 `grok-imagine-image-lite`、`grok-imagine-image`、`grok-imagine-image-pro` 文生图；`grok-imagine-image-edit` 与 `grok-imagine-video` 已在模型表中声明，但当前不会执行图生图或视频生成。
- Next.js Web 管理端提供账号池、用户密钥、代理、日志、图片任务、图片文件和系统配置管理。
- 试验页支持文生文聊天、文本模型批量可用性测试、文生图/图生图切换、图片队列和图片历史。
- 文生文聊天历史保存在浏览器本地，刷新页面后仍保留。
- 图片生成或编辑遇到失效账号时，会在同次任务中跳过该账号并轮换下一个可用账号，直到成功或账号池耗尽。
- 账号导出按 GPT/Grok 服务商分别生成 `webchat2api-gpt.txt` / `webchat2api_grok.txt`；TXT 内容每行一个 `access_token` 或 `sso` 凭据。
- 账号 `provider` 选择 `gpt` 或 `grok`，账号 `type` 仍表示 plan、subscription 等套餐或订阅类型。
- 支持远程账号来源配置、来源同步和管理员直接注入账号 payload。
- 支持 ChatGPT Web、Grok Console、Grok app-chat 网络 profile，Grok app-chat 可读取 `cf_clearance`、`cf_cookies`、UA、client hints 和 Statsig 字段。
- 支持 Docker CLI 和 Docker Compose 部署。镜像内包含 Python 3.13 slim、uv、Chromium、Node/npm、Playwright bridge 依赖和 Web 静态产物。
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
├── Grok Browser Bridge 模块
├── FlareSolverr Clearance 模块
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
- Grok 账号会归一化 `tier` / `model_tier` 为 `basic`、`super`、`heavy`，并保留账号级 `capabilities`、`app_chat`、`cf_cookies`、`user_agent` 等字段。
- 导出账号数据。导出 TXT 内容仅包含凭据本身，每个账号一行，优先使用 `access_token`，缺失时使用 `sso`。
- 图片生成/编辑时遇到失效账号会跳过并轮换下一个账号。
- `provider` 决定账号服务商，`type` 只记录套餐或订阅类型，不用于选择 GPT/Grok。
- 通过 JSON、SQLite、PostgreSQL 或 Git 存储账号与用户密钥。

Grok app-chat 账号选择：

- `services/account_service.py` 的 `get_grok_app_chat_access_token` 会根据模型规格选择账号。
- `model_tier=basic` 可使用 basic、super、heavy 账号。
- `model_tier=super` 可使用 super、heavy 账号。
- `model_tier=heavy` 只使用 heavy 账号。
- `prefer_best=true` 的模型按 heavy、super、basic 顺序查找可用账号。
- 如果账号声明了 `capabilities`，必须命中模型的 `capability`、`mode_id` 或标准化 tier 之一；未声明 `capabilities` 的账号按通用账号处理。

### 2.5 远程账号注入模块

相关文件：`services/remote_account_service.py`、`api/accounts.py`、`api/support.py`、`services/account_service.py`。

职责：

- `services/remote_account_service.py` 负责远程来源配置、HTTP 拉取、payload 归一化、merge 或 replace 注入。
- `api/accounts.py` 暴露管理员路由，包含来源列表、新增、更新、删除、同步、同步任务查询和直接注入。
- `api/support.py` 对远程来源响应做脱敏，隐藏 `auth_token` 与 `bearer_token`，只返回 `has_auth_token` 与 `has_bearer_token`。
- `services/account_service.py` 执行账号 upsert 和按 `remote_source_id` 的来源范围 replace。

安全语义：

- 所有远程账号接口都需要管理员权限，不接受普通用户 API Key 执行管理操作。
- `merge` 会按 `access_token` 合并账号，远程 payload 省略的字段保留现有账号值，因此状态、额度、限流恢复时间、成功失败计数等可变字段不会被空 payload 覆盖。
- `replace` 必须带 `source_id`，并且只替换同一 `remote_source_id` 下的账号，不会删除其他来源或本地账号。
- 来源同步失败返回统一的 `remote account sync failed`，避免把上游地址、鉴权头或异常细节暴露给响应调用方。
- 来源配置响应不返回 `auth_token`、`bearer_token`，账号注入结果只返回计数、策略和来源信息，不返回账号 Token 列表。
- 请求和响应示例见 [远程账号注入 API 示例](./remote-account-api-examples.md)。

### 2.6 网络 Profile 模块

相关文件：`services/network/profiles.py`、`services/network/headers.py`、`services/network/client.py`、`services/network/retry.py`、`services/network/errors.py`。

职责：

- `profiles.py` 定义 ChatGPT Web、Grok Console、Grok app-chat profile，读取全局配置和账号内指纹字段。
- `headers.py` 根据 profile 生成 ChatGPT Web 和 Grok Console 请求头，Grok Console 会把账号 sso/token 和 `cf_clearance` 组合成 Cookie。
- Grok app-chat 请求头由 `services/providers/grok.py` 生成，会合并 profile 字段与账号字段。
- `client.py` 统一创建 `curl_cffi` Session，接入 impersonate、证书校验和代理设置。
- `retry.py` 提供轻量重试策略，用于可重试的上游请求。
- `errors.py` 保留网络 profile 相关错误类型。

配置语义：

- `network_profiles.grok_console` 用于 `https://console.x.ai/v1/responses`，支持 `impersonate`、`user-agent` / `user_agent`、`verify`、`timeout`、`cf_clearance`。
- `network_profiles.grok_app_chat` 用于 `https://grok.com/rest/app-chat/conversations/new`，支持 `impersonate` / `browser`、`user-agent` / `user_agent`、`verify`、`timeout`、`cf_cookies`、`cf_clearance`、`sec-ch-ua`、`sec-ch-ua-mobile`、`sec-ch-ua-platform`、`statsig_id` / `x-statsig-id`。
- `grok_app_chat` 未设置 UA 时会回退到 `grok_console` UA，再回退到内置 Chrome UA。
- `grok_app_chat` 未设置 `cf_clearance` 时会回退到 `grok_console.cf_clearance`。
- Grok app-chat 账号级字段可覆盖 profile 中的 UA、impersonate/browser、Statsig、client hints、`cf_clearance`、`cf_cookies`。
- `grok_console_fingerprint` 是旧版 Grok Console 指纹配置，仍兼容；同名字段以 `network_profiles.grok_console` 为准。
- `chatgpt_fingerprint` 控制 ChatGPT Web 请求的 UA、impersonate、`sec-ch-ua` 和设备会话标识，可被账号内 `fp` 或同名字段覆盖。
- `enable_turnstile_solver` 默认 `true`。当 ChatGPT Sentinel 返回 Turnstile 要求时，后端会尝试生成 `OpenAI-Sentinel-Turnstile-Token`；如果求解返回空值会失败关闭。真实 GPT Turnstile 是否通过仍取决于上游挑战和求解结果，不能承诺全部可解。

### 2.7 Grok Browser Bridge 模块

相关文件：`services/browser_bridge/server.js`、`services/providers/grok.py`、`scripts/entrypoint.sh`、`Dockerfile`。

职责：

- Browser Bridge 是 Grok app-chat 的本地辅助服务，默认监听 `http://127.0.0.1:3080`。
- `services/browser_bridge/server.js` 使用 Playwright 启动真实 Chromium，按 SSO 维护页面池。
- 每个页面通过 `sso` Cookie 登录 `https://grok.com/`，并等待页面建立网页端会话；如果未产生 `x-userid` Cookie，Bridge 会返回结构化 `sso_unavailable` 错误并快速失败，不会继续使用未鉴权页面。
- 页面池默认最多 `BRIDGE_MAX_PAGES=10`，空闲页面会在 `BRIDGE_PAGE_IDLE_MS` 后回收。
- Bridge 拦截浏览器对 `/rest/app-chat/conversations/new` 的请求，将后端传入的 payload 合并到网页自身请求体，再把上游流式响应文本返回给后端。
- Bridge 提供 `GET /health`，返回状态和当前页面数，供 entrypoint 和后端探测。
- 后端 `services/providers/grok.py` 会优先读取 `browser_bridge_url`，未配置时探测 `http://127.0.0.1:3080/health`。探测成功后，Grok app-chat 请求会先走 Bridge；Bridge 不可用时再回退到直接 `curl_cffi` 请求。
- Docker 入口脚本会在启动 FastAPI 前尝试启动 Bridge。Bridge 启动失败不会单独停止 Python 服务，但 Grok app-chat 会少一条通过真实 Chromium 的请求路径。

限制说明：

- Browser Bridge 不是官方接口，也不保证绕过所有 Cloudflare 或上游风控。
- Bridge 依赖有效 SSO Cookie、容器内 Chromium、Node/npm 依赖和可访问的 `grok.com`。
- 同一个 SSO 页面忙碌时会返回 429 风格的忙碌响应，调用方需要稍后重试。

### 2.8 FlareSolverr Clearance 模块

相关文件：`services/network/flaresolverr.py`、`services/providers/grok.py`、`services/config.py`。

配置项：

- `flaresolverr_url`：FlareSolverr 服务地址，空值表示禁用。
- `flaresolverr_timeout_sec`：FlareSolverr 求解超时时间，默认 `60` 秒。

流程：

- Grok app-chat 直接请求返回 403 时，且配置了 `flaresolverr_url`，后端会调用 FlareSolverr 请求 `https://grok.com`。
- 求解成功且响应中包含 UA 与 `cf_clearance` 时，后端会把 UA、`cf_clearance`、完整 `cf_cookies` 写入 `network_profiles.grok_app_chat`，并更新当前请求头后重试一次 app-chat。
- 如果配置了全局代理，FlareSolverr payload 会附带同一个代理地址。

限制说明：

- 该流程是可选的 best effort 路径，不会保证挑战一定可解。
- 只有 FlareSolverr 返回有效 `solution.userAgent` 和 `cf_clearance` Cookie 时才会产生可用 clearance 数据。
- 求解失败、响应缺少关键字段或未配置 `flaresolverr_url` 时，后端会继续按普通错误语义处理，不会宣称已完成 Cloudflare 绕过。

### 2.9 Proxy 转发模块

相关文件：`services/proxy_service.py`。

职责：

- 为上游 Web Chat / ChatGPT / Grok 请求配置 HTTP、HTTPS、SOCKS5 或 SOCKS5H 代理。
- 提供代理连通性测试。
- 环境变量 `PROXY_URL` 可覆盖配置文件中的 `proxy` 字段。

### 2.10 日志模块

相关文件：`services/log_service.py`、`api/system.py`。

默认日志文件位置：`data/logs.jsonl`。

日志会对请求摘要做截断处理，但仍不建议在提示词、Token、Cookie 或 Session 中写入敏感信息。

### 2.11 GPT/Grok 模型集成

相关文件：`services/models.py`、`services/protocol/openai_v1_chat_complete.py`、`services/providers/grok.py`、`services/account_service.py`。

集成要点：

- `services/models.py` 维护 `gpt` 与 `grok` 服务商模型，并通过 `ModelSpec` 描述 `capability`、`mode_id`、`model_tier`、`prefer_best`。
- GPT 模型列表优先通过 `provider=gpt` 账号动态拉取；没有 GPT 账号或拉取失败时，回退到匿名 ChatGPT Web 模型和内置 GPT fallback。
- 没有 `mode_id` 的 Grok 文本模型走 Console Responses 路径，如 `grok-4.3`、`grok-4`、`grok-4.20`、`grok-4.20-reasoning`、`grok-4.20-non-reasoning`、`grok-4.20-multi-agent`。
- 带 `mode_id` 的 Grok 模型走 app-chat 路径，如 `grok-4.20-0309` 系列、`grok-4.20-fast`、`grok-4.20-auto`、`grok-4.20-expert`、`grok-4.20-heavy`、`grok-4.3-beta`。
- `/v1/chat/completions` 保持公共入口不变，按请求 `model` 选择对应服务商账号。
- Grok 文本聊天的非流式响应和流式请求都会返回 OpenAI 兼容结果；流式请求会封装为 stream-compatible chunks，并保留可见的 `reasoning_content`。
- Grok 上游返回 402 或 429 时按账号受限处理，记录限流状态和恢复检查。
- 现有 Grok token/cookie 无法访问 `console.x.ai` 或 `api.x.ai` 的模型列表端点，因此当前不做 Grok 动态模型拉取。
- 本项目使用 Grok 账号 token/cookie 的 provider 处理，不声明官方 xAI API Key 接入。

Grok app-chat 图片模型：

- `grok-imagine-image-lite`：`capability=image`，`mode_id=fast`，`model_tier=basic`。
- `grok-imagine-image`：`capability=image`，`mode_id=auto`，`model_tier=super`。
- `grok-imagine-image-pro`：`capability=image`，`mode_id=auto`，`model_tier=super`。
- 上述三个模型会通过 app-chat 打开 `enableImageGeneration` 与 `enableImageStreaming`，从 app-chat event 中提取完成进度为 100 的 `imageUrl`。
- `grok-imagine-image-edit`：`capability=image_edit`，当前明确返回不支持 Grok image editing。
- `grok-imagine-video`：`capability=video`，当前明确返回不支持 Grok video generation。
- 如果 app-chat 完成后没有返回图片 URL，接口返回 `image_generation_failed`。

错误语义：

- 401：Grok app-chat authentication failed，通常表示 SSO/token 无效或未通过鉴权。
- 403：Grok app-chat forbidden，通常表示账号 tier、权限、Cloudflare 或上游策略不允许当前请求。
- 429：Grok app-chat rate limited，账号会按限流语义处理。
- 408 / 504：Grok app-chat upstream timeout，表示上游或 Bridge 请求超时。
- `image_generation_failed`：Grok 图片请求结束但未解析到图片 URL。

## 3. 技术栈说明

| 类型 | 技术 |
| --- | --- |
| 后端语言 | Python 3.13 |
| Web 框架 | FastAPI |
| ASGI 服务 | Uvicorn |
| 前端框架 | Next.js 16、React 19、TypeScript |
| UI / 状态 | Tailwind CSS、Radix UI、Zustand、localforage |
| HTTP 客户端 | curl-cffi、axios |
| Browser Bridge | Node.js、Playwright、Chromium |
| 存储 | JSON、SQLite、PostgreSQL、Git 存储后端 |
| 容器化 | Docker |
| 部署方式 | Docker CLI、Docker Compose |
| 鉴权方式 | 登录密钥、用户 API Key |
| 配置方式 | 环境变量、本地 `config.json`、Web 管理端设置 |
| 包管理 | Python `uv`、前端 npm/bun lockfile、Bridge npm |

## 4. 目录结构说明

```text
webchat2api/
├── api/                         # FastAPI 路由层
├── services/                    # 业务服务层
│   ├── browser_bridge/          # Grok Browser Bridge Node 服务
│   ├── network/                 # 网络 profile、请求头、Session、FlareSolverr 和重试工具
│   ├── protocol/                # OpenAI/Anthropic 兼容协议实现
│   └── storage/                 # JSON/数据库/Git 存储后端
├── utils/                       # 通用工具
├── web/                         # Next.js 管理端源码
│   ├── src/app/                 # 页面路由
│   ├── src/components/          # UI 组件
│   ├── src/lib/                 # 前端 API 请求封装
│   └── src/store/               # 前端状态存储，含图片历史和文本聊天历史
├── scripts/                     # 维护脚本和 Docker entrypoint
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
├── 技术文档.md
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
| `network_profiles` | 见示例配置 | 否 | 网络 profile 配置，可包含 `grok_console` 与 `grok_app_chat`。 |
| `flaresolverr_url` | 空 | 否 | FlareSolverr 服务地址，空值表示禁用。 |
| `flaresolverr_timeout_sec` | `60` | 否 | FlareSolverr 求解超时时间。 |
| `browser_bridge_url` | 空 | 否 | Grok Browser Bridge 地址；空值时后端自动探测 `http://127.0.0.1:3080`。 |
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
| `network_profiles.grok_console.cf_clearance` | 空 | Grok Console 请求附加的 Cloudflare clearance Cookie，也可作为 app-chat fallback。 |
| `network_profiles.grok_app_chat.user-agent` | 见内置默认 | Grok app-chat 请求 UA，可由账号字段覆盖。 |
| `network_profiles.grok_app_chat.impersonate` | 自动推断或 `chrome136` | Grok app-chat curl-cffi impersonate。 |
| `network_profiles.grok_app_chat.cf_clearance` | 空 | Grok app-chat 请求附加的 `cf_clearance`。 |
| `network_profiles.grok_app_chat.cf_cookies` | 空 | Grok app-chat 请求附加的 Cloudflare Cookie 串。 |
| `network_profiles.grok_app_chat.statsig_id` | 见内置默认 | Grok app-chat `x-statsig-id`。 |
| `chatgpt_fingerprint` | 见示例配置 | ChatGPT Web 请求指纹。 |
| `grok_console_fingerprint` | 空 | 旧版 Grok Console 指纹配置，仍兼容；建议迁移到 `network_profiles.grok_console`。 |
| `flaresolverr_url` | 空 | 设置后，Grok app-chat 403 时会尝试获取 UA 与 clearance 后重试。 |
| `flaresolverr_timeout_sec` | `60` | FlareSolverr 单次求解超时时间。 |
| `browser_bridge_url` | 空 | 指定 Browser Bridge 地址；Docker 默认本地启动并可自动探测。 |
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
- ChatGPT 图片生成和图片编辑使用 GPT 服务商账号；Grok 文生图使用 app-chat 支持的 Grok imagine 模型。
- Grok 图生图与视频模型会显示为模型能力，但当前请求会返回不支持。
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
- GPT 图片仍走 GPT 图片账号池。
- Grok 文生图可使用 `grok-imagine-image-lite`、`grok-imagine-image`、`grok-imagine-image-pro`。
- Grok 图生图输入和 `grok-imagine-image-edit` 当前返回不支持。
- `grok-imagine-video` 当前返回不支持。
- 图片任务保留队列和历史记录。
- 遇到失效账号时，后端会在同次任务中排除该账号并尝试下一个可用账号，直到成功或账号池耗尽。

## 7. Docker CLI Proxy 部署方式

### 7.1 构建镜像

```bash
docker build -t webchat2api:latest .
```

Docker 构建分为两段：

- Web 构建阶段使用 `node:22-alpine` 安装前端依赖并执行 `npm run build`，产物写入 `web/out`。
- 运行阶段使用 `python:3.13-slim`，安装 `uv`、Chromium、Node/npm、Playwright 运行依赖、curl、git 和数据库编译依赖。
- Python 依赖通过 `uv sync --frozen --no-dev --no-install-project` 安装。
- Browser Bridge 依赖在 `/app/services/browser_bridge` 内通过 `npm install --production` 安装。
- Web 静态产物复制到 `/app/web_dist`，由 FastAPI 提供访问。
- `scripts/entrypoint.sh` 会先启动 Browser Bridge，再执行 `uv run python main.py`。

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

Browser Bridge 默认使用容器内 `BRIDGE_PORT=3080` 与 `CHROMIUM_PATH=/usr/bin/chromium`。通常不需要额外挂载端口，因为 Python 后端会访问容器内本地地址。

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

日志中出现 `[entrypoint] Starting Browser Bridge on port 3080...` 与 `[entrypoint] Browser Bridge ready` 表示 Bridge 已通过 `/health` 探测。未出现 ready 不代表主服务一定不可用，但 Grok app-chat 可能会回退到直接请求。

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
Browser Bridge 健康检查：容器内 http://127.0.0.1:3080/health
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

该接口返回 GPT 与 Grok 模型。Grok 文本示例包括 `grok-4.3`、`grok-4`、`grok-4.20`、`grok-4.20-reasoning`、`grok-4.20-non-reasoning`、`grok-4.20-multi-agent`、`grok-4.20-0309` 系列和 `grok-4.20-fast` / `auto` / `expert` / `heavy`。Grok 图片示例包括 `grok-imagine-image-lite`、`grok-imagine-image`、`grok-imagine-image-pro`，模型元数据会对非聊天模型标记 `capability`。

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
    "model": "grok-4.20-auto",
    "messages": [
      {"role": "user", "content": "你好"}
    ]
  }'
```

Grok Console 与 app-chat 都会返回 OpenAI 兼容结果。带 `mode_id` 的 Grok app-chat 模型会优先尝试 Browser Bridge，再回退到直接 app-chat 请求；遇到 403 且配置了 FlareSolverr 时，会尝试刷新 clearance 后重试。

### 11.5 图片生成接口

```bash
curl http://localhost:83/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "grok-imagine-image",
    "prompt": "一只漂浮在太空里的猫",
    "n": 1,
    "response_format": "url"
  }'
```

GPT 图片模型仍使用 GPT 图片账号池。Grok 文生图支持 `grok-imagine-image-lite`、`grok-imagine-image`、`grok-imagine-image-pro`，通过 app-chat 返回图片 URL。若上游完成但没有返回图片 URL，会返回 `image_generation_failed`。

### 11.6 图片编辑接口

```bash
curl http://localhost:83/v1/images/edits \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -F "model=gpt-image-2" \
  -F "prompt=把这张图改成赛博朋克夜景风格" \
  -F "n=1" \
  -F "image=@./input.png"
```

图片编辑当前只支持 GPT 图片路径。`grok-imagine-image-edit` 已在模型表中标记为 `image_edit`，但请求会返回 `unsupported_model`，说明 Grok image editing 尚未支持。

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

容器内可检查 Browser Bridge：

```bash
docker exec -it webchat2api sh
curl http://127.0.0.1:3080/health
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

对 Grok app-chat，401 通常表示 SSO/token 无效或 Cookie 无法完成网页端鉴权。

### 13.6 账号导出返回 400 或导出为空

当前版本已支持 access-token-only 账号导出。若仍遇到导出失败，请检查：

- 是否已选择至少一个账号，或请求中 `access_tokens` 是否为空数组用于导出全部账号。
- 请求头是否包含管理员密钥或有权限的用户 API Key。
- 账号是否仍存在于账号池中。

### 13.7 图片生成遇到账号失效

后端会识别 token invalid、token revoked、invalidated oauth token 等失效错误，并在同次图片生成任务中跳过该账号，尝试下一个可用账号。若所有账号都不可用，接口才会返回失败。

Grok 文生图如果上游没有返回图片 URL，会返回 `image_generation_failed`。这表示 app-chat 请求结束，但没有解析到可用图片结果。

### 13.8 Grok 账号无法调用

确认账号导入时已设置 `provider=grok`，并保留 token/cookie 等 Grok 网页端凭据。`type` 字段只表示 plan、subscription 等套餐信息，不会决定服务商。若 Grok 上游返回 402 或 429，后端会按账号受限处理并等待后续恢复检查。

如果调用 app-chat 模型，还需要检查：

- 账号是否有匹配的 `tier` / `model_tier`，如 basic、super、heavy。
- 账号如果设置了 `capabilities`，是否包含请求模型需要的 `capability`、`mode_id` 或 tier。
- `browser_bridge_url` 是否可访问，或容器内 `http://127.0.0.1:3080/health` 是否正常。
- `network_profiles.grok_app_chat` 中的 UA、Cookie、client hints、Statsig 是否与当前账号环境一致。
- 如遇 403，可配置 `flaresolverr_url` 尝试刷新 clearance，但该路径不能保证成功。

### 13.9 Grok 图片编辑或视频不可用

`grok-imagine-image-edit` 与 `grok-imagine-video` 只在模型规格中声明当前能力和路由信息，实际执行时会返回 `unsupported_model`。当前支持的 Grok 图片能力是 app-chat 文生图：`grok-imagine-image-lite`、`grok-imagine-image`、`grok-imagine-image-pro`。

## 14. 安全建议

- 生产环境不要使用默认密钥 `admin`。
- 不要将管理后台直接暴露到公网。
- 建议通过反向代理启用 HTTPS。
- 建议限制访问 IP。
- API Key 不应写入前端代码。
- 日志中不要输出完整 Token、Cookie、Session、SSO、`cf_clearance` 或其他敏感凭据。
- 定期更新镜像。
- 默认运行数据保存在 `data/`，建议做好备份和访问权限控制，禁止提交到代码仓库。
- 文本试验聊天历史保存在浏览器本地存储中，共用浏览器环境时请注意清理。
- Browser Bridge 会在真实 Chromium 中使用 SSO Cookie，请只在可信运行环境中启用。

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

更新后检查 `/health`、`/version`、管理后台登录、账号导出、试验页文本聊天历史、容器内 Browser Bridge `/health`，以及至少一个目标 Grok Console 或 app-chat 模型。

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
默认登录密钥 admin 仅建议本地测试使用，生产环境请立即修改 LOGIN_SECRET。config.json 和 data/ 是本地运行文件，禁止提交到代码仓库；后端会写入 config.json 保存 Web UI 设置，只读挂载会导致设置保存失败。Grok SSO、Cloudflare Cookie、Browser Bridge 和 FlareSolverr 配置都属于敏感运行信息，不应写入公开仓库或日志。
```
