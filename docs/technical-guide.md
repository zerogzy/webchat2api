# webchat2api 技术指南

版本：0.0.9

本文是 webchat2api 0.0.9 的 canonical 技术手册，面向部署、集成、运维和排障。文档只描述当前实现，不代表 OpenAI 或 xAI 官方 API 支持。

## 1. 项目定位与边界

webchat2api 将网页端 Chat 服务封装为类 OpenAI API 风格的代理服务，供第三方系统、自动化脚本、客户端或中间件调用。

核心定位：

- 后端由 FastAPI 提供 OpenAI 风格 API、管理 API、图片任务 API 和静态 Web 管理端回退路由。
- 文本 API 支持 GPT 与 Grok 两类 provider，`/v1/chat/completions` 按请求 `model` 分发。
- `/v1/models` 优先通过 `provider=gpt` 账号动态拉取 GPT 模型，并合并内置 GPT fallback 与静态 Grok 模型。
- Grok 分为 Console 与 app-chat 两条网页端链路：无 `mode_id` 的 Grok 文本模型走 Console Responses，带 `mode_id` 的文本、图片生成和图片编辑走 grok.com app-chat。
- 图片生成和图片编辑支持 GPT 图片路径与 Grok app-chat imagine 路径；`grok-imagine-video` 只在模型表声明，当前执行时返回不支持。
- Web 管理端提供账号池、用户密钥、代理、日志、图片任务、图片文件、Cloudflare R2 备份、图片存储和系统配置管理。
- 试验页支持文生文聊天、文本模型批量可用性测试、文生图/图生图切换、图片队列和图片历史。
- 文生文聊天历史保存在浏览器本地，刷新页面后仍保留。
- 支持本地敏感词与可选 OpenAI 兼容 AI 审核；审核前移除 base64 data URI 并截断长文本，`fail_open` 默认放行。
- 支持 Docker CLI 与 Docker Compose 部署；镜像内包含 Python 3.13 slim、uv、Chromium、Node/npm、Playwright bridge 依赖和 Web 静态产物。

默认部署信息：

| 项目 | 默认值 |
| --- | --- |
| 服务地址 | `http://localhost:83` |
| 管理后台 | `http://localhost:83` |
| API Base URL | `http://localhost:83/v1` |
| 默认登录密钥 | `admin` |

生产环境部署后必须立即修改默认登录密钥，避免未授权访问。

## 2. 系统架构总览

```text
webchat2api
├── API 服务层
├── Web 管理端
├── 鉴权模块
├── 会话 / Token / 账号池模块
├── 模型注册与 Provider 路由模块
├── ChatGPT Web 链路
├── Grok Console 链路
├── Grok app-chat 链路
├── 图片生成 / 编辑 / 任务模块
├── 远程账号注入模块
├── 网络 Profile 模块
├── Browser Bridge 模块
├── FlareSolverr Clearance 模块
├── Proxy 转发模块
├── 存储 / 备份模块
├── 日志与内容过滤模块
└── Docker 部署模块
```

主要代码边界：

| 模块 | 相关文件或目录 | 职责 |
| --- | --- | --- |
| API 服务层 | `main.py`、`api/app.py`、`api/ai.py`、`api/accounts.py`、`api/system.py`、`api/image_tasks.py` | 接收 HTTP 请求、校验参数和鉴权、调用账号池与 provider、返回 JSON 或 OpenAI 风格响应。 |
| Web 管理端 | `web/`、`web/src/app/`、`web/src/components/`、`web/src/store/` | 管理后台、账号池、配置、日志、图片任务、图片文件和试验页。 |
| 鉴权 | `services/config.py`、`services/auth_service.py`、`api/support.py` | 登录密钥、用户 API Key、Bearer 与部分 `x-api-key` 支持。 |
| 账号与来源 | `services/account_service.py`、`services/cpa_service.py`、`services/sub2api_service.py`、`services/remote_account_service.py`、`services/storage/` | 保存账号、刷新状态、导入 CPA/Sub2API/GPT/Grok/远程来源、导出凭据。 |
| 模型与协议 | `services/models.py`、`services/protocol/openai_v1_chat_complete.py`、`services/providers/grok.py` | 维护模型规格、Provider 分发、OpenAI 兼容响应封装。 |
| 网络与上游 | `services/network/`、`services/browser_bridge/`、`services/providers/grok.py` | ChatGPT、Grok Console、Grok app-chat、Browser Bridge、FlareSolverr、代理。 |
| 存储与备份 | `services/backup_service.py`、`services/image_storage_service.py`、`services/image_service.py`、`services/image_tags_service.py` | Cloudflare R2 备份、本地/WebDAV/双写图片存储、图片索引和标签。 |
| 内容过滤与日志 | `services/content_filter.py`、`services/log_service.py` | 敏感词、可选 AI 审核、日志写入和查询。 |

技术栈：

| 类型 | 技术 |
| --- | --- |
| 后端语言 | Python 3.13 |
| Web 框架 | FastAPI |
| ASGI 服务 | Uvicorn |
| 前端 | Next.js 静态导出 |
| 包管理 | uv、npm |
| 浏览器运行时 | Chromium、Playwright bridge 依赖 |
| 容器 | Docker、Docker Compose |
| 数据目录 | 默认 `data/` |

前端构建为静态产物，Docker 镜像构建时复制到后端镜像内的 `web_dist`，由 FastAPI 静态回退路由提供访问。

## 3. API 路由与鉴权矩阵

### 3.1 公共与 AI 路由

| 路由 | 方法 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| `/health` | `GET` | 无 | 健康检查，期望返回 `{"status":"ok"}`。 |
| `/version` | `GET` | 无 | 版本查询。 |
| `/auth/login` | `POST` | 登录密钥 | 管理后台登录。 |
| `/v1/models` | `GET` | Bearer；也支持 `x-api-key` | 返回 GPT 与 Grok 模型。 |
| `/v1/chat/completions` | `POST` | Bearer；也支持 `x-api-key` | 文本聊天公共入口，按 `model` 路由。 |
| `/v1/responses` | `POST` | Bearer；也支持 `x-api-key` | Responses 兼容入口。 |
| `/v1/messages` | `POST` | Bearer；也支持 `x-api-key` | Messages 兼容入口。 |
| `/v1/images/generations` | `POST` | Bearer | 图片生成。 |
| `/v1/images/edits` | `POST` | Bearer | 图片编辑。 |

鉴权头：

```http
Authorization: Bearer <密钥>
```

`x-api-key: <密钥>` 仅适用于 `/v1/models`、`/v1/chat/completions`、`/v1/responses` 和 `/v1/messages`。图片接口与管理接口仍使用 Bearer Token。

登录密钥优先级：

1. `LOGIN_SECRET`
2. `WEBCHAT2API_AUTH_KEY`
3. `config.json` 中的 `auth-key`
4. 内置默认值 `admin`

### 3.2 管理路由

| 路由 | 说明 |
| --- | --- |
| `/api/settings` | 读取和保存系统配置。 |
| `/api/auth/users` | 用户 API Key 列表、新增、更新和删除。 |
| `/api/accounts`、`/api/accounts/refresh`、`/api/accounts/update`、`/api/accounts/export` | 账号池管理、刷新、更新和导出。 |
| `/api/cpa/*` | CPA 连接、文件浏览、导入和进度查询。 |
| `/api/sub2api/*` | Sub2API 连接、分组、账号浏览、导入和进度查询。 |
| `/api/remote-account/*` | 远程账号来源、同步、同步状态和管理员直接注入。 |
| `/api/image-tasks/*` | 图片任务列表、文生图任务和图生图任务。 |
| `/api/images`、`/api/images/delete`、`/api/images/download`、`/api/images/download/{image_path}`、`/api/images/tags` | 图片文件、下载、删除和标签管理。 |
| `/images/{image_path}`、`/image-thumbnails/{image_path}` | 本地图片和缩略图访问。 |
| `/api/logs` | 日志查询和清理。 |
| `/api/proxy/test` | 代理连通性测试。 |
| `/api/storage/info` | 存储后端信息和健康检查。 |
| `/api/backups`、`/api/backups/run`、`/api/backups/detail`、`/api/backups/download`、`/api/backups/delete`、`/api/backup/test` | 备份列表、手动备份、详情、下载、删除和 R2 连通性测试。 |
| `/api/image-storage/test`、`/api/image-storage/sync` | WebDAV 图片存储测试和同步。 |

### 3.3 常用 curl 示例

健康、版本与模型：

```bash
curl http://localhost:83/health
curl http://localhost:83/version
curl http://localhost:83/v1/models \
  -H "Authorization: Bearer YOUR_API_KEY"
```

聊天：

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

图片生成：

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

图片编辑：

```bash
curl http://localhost:83/v1/images/edits \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -F "model=gpt-image-2" \
  -F "prompt=把这张图改成赛博朋克夜景风格" \
  -F "n=1" \
  -F "image=@./input.png"
```

Responses 与 Messages：

```bash
curl http://localhost:83/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "gpt-5",
    "input": "生成一张未来感城市天际线图片",
    "tools": [{"type": "image_generation"}]
  }'

curl http://localhost:83/v1/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "gpt-5",
    "messages": [
      {"role": "user", "content": "你好"}
    ],
    "max_tokens": 256
  }'
```

## 4. 模型与 Provider 路由

`services/models.py` 维护 `gpt` 与 `grok` 服务商模型，并通过 `ModelSpec` 描述 `capability`、`mode_id`、`model_tier`、`prefer_best` 等路由属性。`/v1/chat/completions` 保持公共入口不变，按请求 `model` 选择 provider 与账号。

### 4.1 模型注册分组

| 分组 | 模型 | 路由与说明 |
| --- | --- | --- |
| GPT 动态模型 | 通过 `provider=gpt` 账号动态拉取 | 成功拉取时并入 `/v1/models`。 |
| GPT fallback 文本 | `auto`、`gpt-5`、`gpt-5-thinking`、`gpt-4o`、`gpt-4o-mini` | 没有 GPT 账号或动态拉取失败时保留可见。 |
| GPT 图片 | `gpt-image-2`、`codex-gpt-image-2` | 走 GPT 图片账号池。 |
| Grok Console 文本 | `grok-4.3`、`grok-4`、`grok-4.20`、`grok-4.20-reasoning`、`grok-4.20-non-reasoning`、`grok-4.20-multi-agent` | 无 `mode_id`，走 Console Responses 路径。 |
| Grok app-chat 文本 | `grok-4.20-0309` 系列、`grok-4.20-fast`、`grok-4.20-auto`、`grok-4.20-expert`、`grok-4.20-heavy`、`grok-4.3-beta` | 带 `mode_id`，走 grok.com app-chat。 |
| Grok app-chat 图片 | `grok-imagine-image-lite`、`grok-imagine-image`、`grok-imagine-image-pro`、`grok-imagine-image-edit` | 图片生成或图片编辑，模型元数据标记 `capability`。 |
| Grok video 声明 | `grok-imagine-video` | 只声明为未支持的视频能力，执行返回 `unsupported_model`。 |

当前 Grok token/cookie 无法访问 `console.x.ai` 或 `api.x.ai` 的模型列表端点，因此不做 Grok 动态模型拉取。本项目使用 Grok 网页端 token/cookie 的 provider 处理，不声明官方 xAI API Key 接入。

### 4.2 路由规则

| 请求模型 | Provider | 链路 | 账号选择 |
| --- | --- | --- | --- |
| GPT 文本模型 | `gpt` | ChatGPT Web | `provider=gpt` 账号。 |
| GPT 图片模型 | `gpt` | GPT 图片路径 | `provider=gpt` 图片账号池。 |
| Grok 无 `mode_id` 文本模型 | `grok` | Grok Console Responses | `provider=grok` 账号。 |
| Grok 带 `mode_id` 文本模型 | `grok` | Grok app-chat | `provider=grok` 且匹配 tier/capabilities 的账号。 |
| Grok imagine 图片模型 | `grok` | Grok app-chat | `provider=grok` 且匹配图片 capability、tier/capabilities 的账号。 |

Grok 文本聊天的非流式响应和流式请求都会返回 OpenAI 兼容结果；流式请求封装为 stream-compatible chunks，并保留可见的 `reasoning_content`。

Grok 搜索来源会作为结构化 `search_sources` 返回，并映射为 OpenAI 风格 `url_citation` annotations。流式响应会在最终 `finish_reason=stop` chunk 附带 metadata。`show_search_sources` 默认 `false`，只控制非流式文本末尾是否追加 Markdown `Sources` 来源列表，适合只显示文本内容的客户端。

## 5. GPT / ChatGPT Web 链路

GPT 文本与图片请求使用 GPT provider 账号。模型列表优先动态拉取 GPT 模型；无法动态拉取时使用内置 fallback 文本模型与 GPT 图片模型。

GPT 图片编辑约束：

- `/v1/images/edits` 接受 JSON 或 multipart form 中的图片输入。
- 图片输入可传 URL、data URI 或上传文件。
- 支持多参考图。
- 单张参考图上限 50MiB。
- 当前不支持 `file_id` 图片输入。

内容过滤适用于文本请求：`sensitive_words` 为空数组时不启用本地敏感词；配置后对文本做本地包含匹配，命中即拦截。`ai_review.enabled=true` 时调用 OpenAI 兼容接口做审核，配置项包括 `base_url`、`api_key`、`model`、`prompt` 和可选 `fail_open`。发送到审核模型前会把 base64 data URI 替换为 `[image]`，并对超长文本保留头尾后截断。`fail_open` 默认 `true`；设置为 `false` 后审核异常会按严格策略拦截。

## 6. Grok Console 与 app-chat 链路

### 6.1 Grok Console

无 `mode_id` 的 Grok 文本模型走 Console Responses 路径。Console 请求使用 `network_profiles.grok_console` 中的 UA、impersonate、origin、referer、sec-ch-ua、cf_clearance、cookie 等配置，并可由账号字段补充凭据。

### 6.2 Grok app-chat

带 `mode_id` 的 Grok 文本模型、Grok 图片生成和 `grok-imagine-image-edit` 图片编辑走 grok.com app-chat。app-chat 可读取账号级或 profile 级的 `cf_clearance`、`cf_cookies`、UA、client hints、Statsig 字段。

app-chat 错误语义：

| 状态或错误 | 账号反馈 | 说明 |
| --- | --- | --- |
| `401` | 标记账号异常 | 通常表示 SSO/token 无效或 Cookie 无法完成网页端鉴权。 |
| `403` | 返回给调用方，不标记异常 | 通常表示账号 tier、权限、Cloudflare 或上游策略不允许当前请求；不会自动回退到 Browser Bridge。 |
| `429` | 标记账号限流 | 按账号受限处理并等待后续恢复检查。 |
| `408` / `504` | 上游超时语义 | 表示上游或 Bridge 请求超时。 |
| `image_generation_failed` / `image_edit_failed` | 不表示账号必然失效 | 请求结束但未解析到图片 URL。 |

重要边界：直接 app-chat `403` 会返回给调用方，不会自动回退到 Browser Bridge，也不会标记账号异常。配置了 FlareSolverr 时，403 可以触发 clearance 刷新并重试一次；若仍失败则按普通错误返回。

## 7. 图片生成、图片编辑与图片任务

### 7.1 图片模型能力

| 模型 | capability | mode_id | tier | 当前行为 |
| --- | --- | --- | --- | --- |
| `gpt-image-2` | image / image_edit | 无 | GPT 账号池 | GPT 图片生成与编辑。 |
| `codex-gpt-image-2` | image | 无 | GPT 账号池 | GPT 图片生成。 |
| `grok-imagine-image-lite` | `image` | `fast` | `basic` | Grok app-chat 文生图。 |
| `grok-imagine-image` | `image` | `auto` | `super` | Grok app-chat 文生图。 |
| `grok-imagine-image-pro` | `image` | `auto` | `super` | Grok app-chat 文生图。 |
| `grok-imagine-image-edit` | `image_edit` | app-chat 编辑 payload | 按账号匹配 | Grok app-chat 图生图。 |
| `grok-imagine-video` | `video` | 无 | 未支持 | 当前返回 `unsupported_model`。 |

Grok 文生图会通过 app-chat 打开 `enableImageGeneration` 与 `enableImageStreaming`，从 app-chat event 中提取完成进度为 100 的 `imageUrl`。如果 app-chat 完成后没有返回图片 URL，接口返回 `image_generation_failed`。

### 7.2 图片编辑约束

GPT 图片编辑：

- 接受 JSON 或 multipart form 图片输入。
- 支持 URL、data URI 或上传文件。
- 支持多参考图。
- 单张参考图上限 50MiB。
- 当前不支持 `file_id`。

Grok 图片编辑：

- 模型为 `grok-imagine-image-edit`。
- 通过 app-chat upload-file 上传参考图，创建 `MEDIA_POST_TYPE_IMAGE` 父 post，再发送 image-edit payload。
- 仅支持 `size=1024x1024`。
- 最多 7 张参考图。
- `n<=2`。
- prompt 中的 `@IMAGE1`、`@IMAGE2` 等占位符会替换为对应上传资产引用。

`/api/image-tasks/*` 使用同一图片生成/编辑 handler。图片生成或编辑遇到失效账号时，会在同次任务中跳过该账号并轮换下一个可用账号，直到成功或账号池耗尽。

## 8. 账号池、tier、quota 与状态反馈

账号 `provider` 决定服务商，可取 `gpt` 或 `grok`。账号 `type` 只记录 plan、subscription 等套餐或订阅类型，不用于选择 GPT/Grok provider。

账号池能力：

- 保存和读取账号池数据。
- 刷新账号状态、额度、限流状态和恢复时间。
- 导入本地 CPA、远程 CPA、Sub2API、GPT access token 和 Grok token/cookie。
- Grok 账号导入时设置 `provider=grok`；GPT 账号使用 `provider=gpt` 或默认值。
- Grok 账号会归一化 `tier` / `model_tier` 为 `basic`、`super`、`heavy`，并保留账号级 `capabilities`、`app_chat`、`cf_cookies`、`user_agent` 等字段。
- 通过 JSON、SQLite、PostgreSQL 或 Git 存储账号与用户密钥。

Grok app-chat 账号选择：

| 模型 tier | 可用账号 tier |
| --- | --- |
| `basic` | `basic`、`super`、`heavy` |
| `super` | `super`、`heavy` |
| `heavy` | `heavy` |

补充规则：

- `services/account_service.py` 的 `get_grok_app_chat_access_token` 会根据模型规格选择账号。
- 如果账号声明了 `capabilities`，必须命中模型的 `capability`、`mode_id` 或标准化 tier 之一。
- 未声明 `capabilities` 的账号按通用账号处理。
- `prefer_best=true` 的模型优先选择更高 tier 的账号。
- Grok 上游返回 402 或 429 时，后端会按账号受限处理并等待后续恢复检查。

账号导出：

- 导出按 GPT/Grok 服务商分别生成 TXT 文件。
- GPT 文件名固定为 `webchat2api-gpt.txt`。
- Grok 文件名固定为 `webchat2api_grok.txt`。
- TXT 内容每行一个 `access_token` 或 `sso` 凭据，优先使用清理后的 `access_token`，缺失时使用清理后的 `sso`。
- `access_tokens` 为空数组时导出指定 provider 的全部账号。

账号导出示例：

```bash
curl http://localhost:83/api/accounts/export \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "provider": "gpt",
    "access_tokens": ["ACCESS_TOKEN_1"]
  }'
```

## 9. 网络 Profile、FlareSolverr 与 Browser Bridge

### 9.1 网络 Profile

支持 ChatGPT Web、Grok Console、Grok app-chat 三类网络 profile。常用配置项：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `proxy` | 空 | HTTP、HTTPS、SOCKS5 或 SOCKS5H 代理。 |
| `PROXY_URL` | 空 | 环境变量，可覆盖配置文件中的 `proxy`。 |
| `network_profiles.chatgpt.*` | 见配置 | ChatGPT Web 请求 headers、fingerprint 等。 |
| `network_profiles.grok_console.user-agent` | 见内置默认 | Grok Console 请求 UA。 |
| `network_profiles.grok_console.impersonate` | 自动推断或 `chrome136` | Grok Console curl-cffi impersonate。 |
| `network_profiles.grok_console.cf_clearance` | 空 | Grok Console 请求附加的 Cloudflare clearance Cookie，也可作为 app-chat fallback 数据来源。 |
| `network_profiles.grok_app_chat.user-agent` | 见内置默认 | Grok app-chat 请求 UA，可由账号字段覆盖。 |
| `network_profiles.grok_app_chat.impersonate` | 自动推断或 `chrome136` | Grok app-chat curl-cffi impersonate。 |
| `network_profiles.grok_app_chat.cf_clearance` | 空 | Grok app-chat 请求附加的 `cf_clearance`。 |
| `network_profiles.grok_app_chat.cf_cookies` | 空 | Grok app-chat 请求附加的 Cloudflare Cookie 串。 |
| `network_profiles.grok_app_chat.statsig_id` | 见内置默认 | Grok app-chat `x-statsig-id`。 |
| `chatgpt_fingerprint` | 见示例配置 | ChatGPT Web 请求指纹。 |
| `grok_console_fingerprint` | 空 | 旧版 Grok Console 指纹配置，仍兼容；建议迁移到 `network_profiles.grok_console`。 |

### 9.2 FlareSolverr

配置项：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `flaresolverr_url` | 空 | 设置后，Grok app-chat 403 时会尝试获取 UA 与 clearance 后重试。 |
| `flaresolverr_timeout_sec` | `60` | FlareSolverr 单次求解超时时间。 |

流程：

- Grok app-chat 直接请求返回 403 且配置了 `flaresolverr_url` 时，后端调用 FlareSolverr 请求 `https://grok.com`。
- 求解成功且响应中包含 UA 与 `cf_clearance` 时，后端把 UA、`cf_clearance`、完整 `cf_cookies` 写入 `network_profiles.grok_app_chat`，更新当前请求头后重试一次 app-chat。
- 如果配置了全局代理，FlareSolverr payload 会附带同一个代理地址。
- 求解失败、响应缺少关键字段或未配置 `flaresolverr_url` 时，后端继续按普通错误语义处理。

FlareSolverr 是可选 best effort 路径，不保证挑战一定可解，也不会宣称已经完成 Cloudflare 绕过。

### 9.3 Browser Bridge

Browser Bridge 是用于 Grok app-chat 的可选真实 Chromium 辅助路径，相关代码位于 `services/browser_bridge/`。它不是官方接口，也不保证绕过所有 Cloudflare 或上游风控。

Fallback 语义：

| 场景 | 行为 |
| --- | --- |
| 显式配置 `browser_bridge_url` | 后端优先预检并使用该 Bridge。 |
| 未配置 `browser_bridge_url`，直接 app-chat 返回 `408`、`502`、`503`、`504` | 后端探测本机 Bridge，如可用则尝试回退。 |
| 直接 app-chat 返回 `403` | 直接 app-chat `403` 会返回给调用方，不会自动回退到 Browser Bridge，也不会标记账号异常；可由 FlareSolverr best effort 刷新后重试。 |
| 同一个 SSO 页面忙碌 | Bridge 返回 429 风格忙碌响应，调用方稍后重试。 |

限制：

- Bridge 依赖有效 SSO Cookie、容器内 Chromium、Node/npm 依赖和可访问的 `grok.com`。
- Docker 入口脚本会先启动 Browser Bridge，再启动 Python 后端。
- Browser Bridge 默认使用容器内 `BRIDGE_PORT=3080` 与 `CHROMIUM_PATH=/usr/bin/chromium`。
- 通常不需要额外挂载 Bridge 端口，因为 Python 后端访问容器内本地地址。

## 10. 存储、备份与远程账号

### 10.1 账号与用户密钥存储

账号和用户密钥可通过 JSON、SQLite、PostgreSQL 或 Git 存储。默认运行数据位于 `data/`，生产环境应挂载持久化目录并限制访问权限。

### 10.2 远程账号

相关文件：`services/remote_account_service.py`、`api/accounts.py`、`api/support.py`、`services/account_service.py`。

远程账号能力：

- 配置远程账号来源。
- HTTP 拉取远程 payload。
- 对 payload 做归一化。
- 支持 merge 或 replace 注入账号。
- 管理员可直接注入账号 payload。
- `replace` 必须带 `source_id`，且只替换同一 `remote_source_id` 下的账号，不会删除其他来源或本地账号。
- 来源同步失败返回统一的 `remote account sync failed`，避免把上游地址、鉴权头或异常细节暴露给响应调用方。

### 10.3 Cloudflare R2 备份

备份服务使用 Cloudflare R2 兼容 S3 API，配置项位于 `backup`。

| 能力 | 说明 |
| --- | --- |
| 定时备份 | 随 FastAPI 生命周期启动定时任务。 |
| 手动备份 | 通过 `/api/backups/run` 触发。 |
| 包含范围 | `backup.include` 控制 `config`、`cpa`、`sub2api`、`logs`、`image_tasks`、`accounts_snapshot`、`auth_keys_snapshot`、`images`。 |
| 加密 | `backup.encrypt=true` 时通过 openssl AES-256-CBC 生成 `.tar.gz.enc`，需要配置 `passphrase`；关闭加密时生成 `.tar.gz`。 |
| 保留策略 | `rotation_keep` 控制保留数量，`interval_minutes` 控制定时备份间隔。 |
| 管理操作 | R2 连通性测试、备份列表、详情、下载和删除。 |

### 10.4 图片存储

相关文件：`services/image_storage_service.py`、`services/image_service.py`、`services/image_tags_service.py`、`api/system.py`。

图片存储规则：

- 默认本地图片存储在 `data/images`。
- 图片索引写入 `data/image_index.json`。
- 图片标签写入 `data/image_tags.json`。
- `image_storage.mode` 支持 `local`、`webdav`、`both`。
- `both` 表示本地保存并同步到 WebDAV。
- `/api/image-storage/test` 测试 WebDAV 配置。
- `/api/image-storage/sync` 执行 WebDAV 同步。

## 11. 配置与环境变量

常用环境变量与配置：

| 项 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `83` | 服务监听端口。 |
| `HOST` | `0.0.0.0` | 服务监听地址。 |
| `LOGIN_SECRET` | 空 | 优先级最高的登录密钥。 |
| `WEBCHAT2API_AUTH_KEY` | 空 | 兼容登录密钥环境变量。 |
| `PROXY_URL` | 空 | 覆盖配置文件中的代理地址。 |
| `BRIDGE_PORT` | `3080` | Browser Bridge 监听端口。 |
| `CHROMIUM_PATH` | `/usr/bin/chromium` | Browser Bridge 使用的 Chromium 路径。 |
| `browser_bridge_url` | 空 | 显式指定 Browser Bridge 地址。 |
| `enable_turnstile_solver` | `true` | ChatGPT 要求 Turnstile 时是否尝试生成 Sentinel Turnstile Token。 |
| `show_search_sources` | `false` | 是否在非流式文本末尾追加 Markdown `Sources`。 |
| `flaresolverr_url` | 空 | FlareSolverr 服务地址。 |
| `flaresolverr_timeout_sec` | `60` | FlareSolverr 求解超时。 |

配置文件卫生：

- `.gitignore` 已忽略本地 `config.json`。
- `config.json` 可保存本地密钥、代理、R2、WebDAV、CPA、Sub2API、远程账号和内容过滤配置。
- 不要提交 Token、Cookie、Session、SSO、`cf_clearance`、R2 密钥或用户 API Key。

## 12. Web 管理端与试验页

管理后台入口：`http://localhost:83`。主要页面包括 `/`、`/accounts`、`/image`、`/image-manager`、`/logs`、`/settings` 和 `/login`。

管理后台能力：

- 账号池列表、搜索、筛选、刷新、删除和状态编辑。
- 账号导入：access token、本地 CPA、远程 CPA、Sub2API、Grok token/cookie 和远程账号来源。
- 管理后台可按服务商 `provider` 和套餐 `type` 分别筛选账号。
- 账号导出：按 GPT/Grok 服务商分别下载 TXT。
- 用户 API Key 管理。
- CPA、Sub2API 和远程账号来源配置、同步和导入。
- 代理、基础 URL、备份、图片存储、用户密钥、CPA、Sub2API 和内容过滤等配置。
- 日志查看与清理。
- 图片任务和图片文件管理。

试验页位于 `/image`，通过顶部切换区分文本试验和图像试验。

文本试验：

- 调用 `/v1/chat/completions`。
- 聊天消息保存在浏览器本地 localforage 中，刷新页面后仍保留。
- 错误消息会显示在聊天历史中，但不会作为下一次 API 请求上下文发送。
- “批量测试模型”会从 `/v1/models` 获取 GPT/Grok 模型，逐个调用文本模型并显示 `pending`、`testing`、`success`、`error` 状态。
- 提供清空文本聊天记录功能。

图像试验：

- 文生图调用 `/v1/images/generations`。
- 图生图调用 `/v1/images/edits`。
- GPT 图片仍走 GPT 图片账号池。
- Grok 文生图可使用 `grok-imagine-image-lite`、`grok-imagine-image`、`grok-imagine-image-pro`。
- Grok 图生图可使用 `grok-imagine-image-edit`，限制为 `1024x1024`、最多 7 张参考图、`n<=2`。
- `grok-imagine-video` 当前返回不支持。
- 图片任务保留队列和历史记录。
- 遇到失效账号时，后端会在同次任务中排除该账号并尝试下一个可用账号，直到成功或账号池耗尽。

## 13. 部署与运维

### 13.1 Docker CLI

构建镜像：

```bash
docker build -t webchat2api:latest .
```

Docker 构建分为两段：

- Web 构建阶段使用 `node:22-alpine`，通过 `package-lock.json` 和 `npm ci` 安装前端依赖并执行 `npm run build`，产物写入 `web/out`。
- 运行阶段使用 `python:3.13-slim`，安装 `uv`、Chromium、Node/npm、Playwright 运行依赖、curl、git 和数据库编译依赖。
- Python 依赖通过 `uv sync --frozen --no-dev --no-install-project` 安装。
- Browser Bridge 依赖在 `/app/services/browser_bridge` 内通过 `npm ci --omit=dev` 安装。
- Web 静态产物复制到 `/app/web_dist`，由 FastAPI 提供访问。
- `scripts/entrypoint.sh` 会先启动 Browser Bridge，再执行 `uv run python main.py`。

标准启动：

```bash
docker run -d \
  --name webchat2api \
  --restart unless-stopped \
  -p 83:83 \
  -v $(pwd)/data:/app/data \
  -e PORT=83 \
  -e HOST=0.0.0.0 \
  -e LOGIN_SECRET=your-strong-secret \
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
  -e LOGIN_SECRET=your-strong-secret \
  -e PROXY_URL=http://host.docker.internal:7890 \
  webchat2api:latest
```

### 13.2 Docker Compose

`docker-compose.yml` 使用本地镜像 `webchat2api:latest`，适合普通 bridge 网络部署。`docker-compose.local.yml` 可本地构建后启动。`docker-compose.host.yml` 是 Linux host 网络模式的独立文件，不要和默认 Compose 文件叠加使用；host 网络会让服务直接监听宿主机 `83` 端口，必须先设置强随机 `LOGIN_SECRET` 并做好防火墙或反向代理限制。

Compose 示例：

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
      LOGIN_SECRET: your-strong-secret
      PROXY_URL: http://host.docker.internal:7890
    volumes:
      - ./data:/app/data
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

启动与日志：

```bash
docker compose up -d
docker logs -f webchat2api
```

日志中出现 `[entrypoint] Starting Browser Bridge on port 3080...` 与 `[entrypoint] Browser Bridge ready` 表示 Bridge 已通过 `/health` 探测。未出现 ready 不代表主服务一定不可用，但 Grok app-chat 可能会回退到直接请求。

### 13.3 更新与运行检查

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

## 14. 测试、排障与安全建议

### 14.1 测试与检查

后端单元测试：

```bash
python3 -m unittest discover -s test -t .
```

`-t .` 用于指定项目根目录，避免 `test/utils.py` 遮蔽项目内的 `utils` 包。

前端类型检查和构建：

```bash
cd web
npm run typecheck
npm run build
```

存储后端检查脚本：

```bash
python scripts/test_storage.py
```

Browser Bridge 可选健康语义测试位于 `services/browser_bridge/test_health.js`，需要 Node 环境。

### 14.2 常用排障命令

```bash
docker ps
docker logs -f webchat2api
docker exec -it webchat2api sh
docker restart webchat2api
curl http://localhost:83/health
```

容器内检查 Browser Bridge：

```bash
docker exec -it webchat2api sh
curl http://127.0.0.1:3080/health
```

### 14.3 常见问题

| 问题 | 检查方向 |
| --- | --- |
| 管理后台无法登录 | 确认 `LOGIN_SECRET`、`WEBCHAT2API_AUTH_KEY` 或 `config.json` 中的 `auth-key`；不要继续使用默认 `admin`。 |
| `/v1/models` 缺少 GPT 动态模型 | 检查是否有可用 `provider=gpt` 账号；动态拉取失败时会保留 GPT fallback 与静态 Grok 模型。 |
| Grok 账号无法调用 | 确认导入时 `provider=grok`，并保留 token/cookie 等网页端凭据；`type` 不决定 provider。 |
| app-chat 模型不可用 | 检查账号 `tier` / `model_tier`、`capabilities`、Cookie、UA、client hints、Statsig 和 Cloudflare clearance。 |
| app-chat 返回 401 | 通常表示 SSO/token 无效或 Cookie 无法完成网页端鉴权，账号会按异常处理。 |
| app-chat 返回 403 | 直接 app-chat `403` 会返回给调用方，不会自动回退到 Browser Bridge，也不会标记账号异常；可配置 `flaresolverr_url` 尝试刷新 clearance。 |
| app-chat 返回 429 或 402 | 后端按账号受限处理，等待后续恢复检查。 |
| 图片生成遇到账号失效 | 后端识别 token invalid、token revoked、invalidated oauth token 等失效错误，并在同次任务中跳过该账号继续尝试。 |
| Grok 文生图无图片 URL | 如果上游完成但没有返回图片 URL，会返回 `image_generation_failed`。 |
| Grok 视频不可用 | `grok-imagine-video` 只声明当前能力和路由信息，实际执行返回 `unsupported_model`。 |
| 账号导出返回 400 或为空 | 检查是否选择账号或传空数组导出全部、请求头是否有权限、账号是否仍存在。 |

### 14.4 安全建议

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
