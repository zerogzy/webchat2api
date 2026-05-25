# 功能状态（中文）

本文基于当前仓库实现整理，用于发布前确认哪些功能已经可用、哪些仍在完善、哪些待实现。

| 功能 | 状态 | 说明 |
| --- |:---:| --- |
| 公共健康与版本接口 | ✅ | 已提供 `GET /health`、`GET /version` 和 `POST /auth/login`。默认登录密钥仍为 `admin`，生产环境必须修改。 |
| OpenAI 兼容 `GET /v1/models` | ✅ | 优先通过 `provider=gpt` 账号动态拉取 GPT 模型，失败时回退到匿名或内置 GPT 模型，并合并静态 Grok 文本模型、GPT 图片模型和 Grok app-chat 图片模型。 |
| GPT/Grok 文本服务商拆分 | ✅ | 账号 `provider` 选择 `gpt` 或 `grok`，`type` 只记录套餐、订阅或计划信息，不再用于选择服务商。 |
| OpenAI 兼容 `POST /v1/chat/completions` | ✅ | GPT 模型走 ChatGPT 链路，Grok 文本模型走 `provider=grok` 账号；Grok 模型会按账号 `tier` 和 `capabilities` 路由。 |
| Grok 流式兼容响应 | ✅ | Grok 上游文本结果可封装为 OpenAI 兼容的流式 chunk，已有相关测试覆盖。 |
| OpenAI 兼容 `POST /v1/images/generations` | ✅ | 已支持图片生成，并可通过 `n` 返回多张图片；GPT 图片模型使用 GPT 服务商账号，Grok app-chat 图片模型使用 Grok SSO 账号。 |
| OpenAI 兼容 `POST /v1/images/edits` | ⚠️ | GPT 图片编辑已支持上传图片编辑和多参考图输入；Grok `image_edit` 模型已列入模型表，但 app-chat 图片编辑链路尚未支持。 |
| OpenAI 兼容 `POST /v1/responses` | ✅ | 已支持图片生成工具调用。 |
| Anthropic 兼容 `POST /v1/messages` | ✅ | 路由和协议实现已存在，测试包含非流式与流式调用用例。 |
| 后台管理接口 | ✅ | 已提供 `/api/settings`、`/api/auth/users`、`/api/accounts`、`/api/cpa/*`、`/api/sub2api/*`、`/api/remote-account/*`、`/api/image-tasks/*`、`/api/images*`、`/api/logs`、`/api/proxy/test`、`/api/storage/info`、`/api/backups*`、`/api/backup/test`、`/api/image-storage/*`。 |
| 前端管理后台 | ✅ | 已支持 `/`、`/accounts`、`/image`、`/image-manager`、`/logs`、`/settings`、`/login`，覆盖账号池、用户 API Key、代理、日志、图片任务、图片文件、备份、图片存储和系统配置管理。 |
| 前端试验页 | ✅ | 已支持文生文聊天、文本模型批量可用性测试、文生图、图生图、图片队列和图片历史。 |
| 文生文聊天历史 | ✅ | 浏览器本地保存，刷新页面后仍保留。 |
| 账号池管理 | ✅ | 已支持列表、筛选、批量操作、导入、导出、手动编辑、刷新和删除。 |
| 账号导出 | ✅ | 仅导出 TXT，并按 GPT/Grok 服务商分别下载为 `webchat2api-gpt.txt` / `webchat2api_grok.txt`；文件内容每行一个 `access_token` 或 `sso` 凭据。 |
| 远程账号注入与来源同步 | ✅ | 管理员可配置远程来源、手动同步或直接注入 payload；已验证 merge、来源范围 replace 和响应脱敏。 |
| GPT 账号额度刷新与恢复时间同步 | ✅ | 已支持账号信息刷新，限流账号也会自动继续检查。 |
| Grok 账号导入 | ✅ | 支持 token/cookie 导入并归入 Grok 账号池，不声明官方 xAI API Key 接入；`tier` 支持 basic、super、heavy 路由，`capabilities` 可限制 chat、image、image_edit、video 等用途。 |
| 失效 Token 自动清理 | ✅ | `auto_remove_invalid_accounts` 默认开启，已支持自动移除失效 Token；`auto_remove_rate_limited_accounts` 默认关闭。 |
| CPA 连接管理与导入 | ✅ | 已支持连接新增、修改、查询、删除、远程文件浏览、勾选导入和进度跟踪。 |
| `sub2api` 连接管理与导入 | ✅ | 已支持连接管理、账号浏览和 OpenAI OAuth 账号批量导入。 |
| 代理配置功能 | ✅ | 支持网页端配置全局 HTTP、HTTPS、SOCKS5、SOCKS5H 代理，并可通过 `PROXY_URL` 覆盖。 |
| 内容过滤 | ✅ | 支持 `sensitive_words` 本地匹配和可选 `ai_review` OpenAI 兼容审核；审核前会移除 base64 data URI 并截断长文本，`fail_open` 默认放行。 |
| Cloudflare R2 备份 | ✅ | 支持定时和手动备份、列表、详情、下载、删除和连接测试；可选 openssl AES-256-CBC 加密，可按开关包含配置、CPA、Sub2API、日志、图片任务、账号快照、用户密钥快照和图片。 |
| 图片存储与管理 | ✅ | 支持本地、WebDAV、双写模式，提供 WebDAV 测试和同步；图片索引存储在 `data/image_index.json`，标签存储在 `data/image_tags.json`，本地访问使用 `/images` 和 `/image-thumbnails`。 |
| 网络 Profile 与 Grok `cf_clearance` | ✅ | ChatGPT Web、Grok Console 与 Grok app-chat 网络配置已模块化；`network_profiles.grok_console.cf_clearance` 可附加 Cloudflare clearance Cookie，`network_profiles.grok_app_chat` 可配置 app-chat UA、client hints、Statsig ID、CF Cookie 和请求超时。 |
| FlareSolverr clearance 刷新 | ⚠️ | 配置 `flaresolverr_url` 后，直接 app-chat 请求遇到 403 时会尝试刷新 Cloudflare Cookie 并重试；这是 best-effort clearance 支持，不保证绕过所有 Cloudflare、WAF 或账号限制。 |
| Browser Bridge | ✅ | Docker 镜像内置 Chromium 与 Browser Bridge，`scripts/entrypoint.sh` 会启动 `services/browser_bridge/server.js`；提供 `/health` 和 `/api/chat`，缺少 `x-userid` Cookie 时快速返回 `sso_unavailable`，页面池可通过 `BRIDGE_MAX_PAGES` 和 `BRIDGE_PAGE_IDLE_MS` 配置。 |
| Grok app-chat 图片生成 | ✅ | `grok-imagine-image-lite`、`grok-imagine-image`、`grok-imagine-image-pro` 已通过 Grok app-chat 链路接入，账号池会按模型 tier、capability 和图片额度选择账号。 |
| Grok app-chat 图片编辑与视频 | ❌ | `grok-imagine-image-edit` 和 `grok-imagine-video` 已作为模型能力暴露用于状态标识，但当前 app-chat 运行链路只支持图片生成，尚未支持图片编辑或视频生成。 |
| GPT Turnstile 求解 | ⚠️ | 默认启用 `enable_turnstile_solver` 并在上游要求时尝试生成 Sentinel Turnstile Token；真实 GPT 挑战仍可能失败。 |
| Docker 自托管部署 | ✅ | 当前发布目标使用 Docker CLI 或 Docker Compose，默认服务端口为 `83`；提供默认 bridge、本地构建和 Linux host 网络 Compose 文件，`data/` 需要持久化。 |
| 配置文件卫生 | ✅ | `config.json` 已在 `.gitignore` 中忽略，仓库提供 `config.example.json` 作可提交示例文件；`.env.example` 覆盖存储后端、数据库和 Git 存储变量。 |
| 测试命令 | ✅ | 后端使用 `python3 -m unittest discover -s test -t .`；前端使用 `cd web && npm run typecheck` 和 `cd web && npm run build`；存储脚本为 `python scripts/test_storage.py`。 |
| 更高级的 Token 调度策略 | ⚠️ | 当前已有基础轮询、tier/capabilities 路由、限流记录和恢复检查，更复杂的调度策略仍在完善中。 |
| Render / Vercel 等部署表述 | ⚠️ | 当前主要以 Docker 部署为主，其他平台部署方式暂未重点说明。 |
| 图片尺寸参数 | ✅ | 接口已接收 `size`，并将其作为宽高比或提示词提示传给图片生成链路。 |
| `rt_token` 刷新 | ❌ | 待实现。 |
