# Review

当前没有阻塞发布的未解决审查项。

## 已归档

- [resolved] Poll when the image tool was invoked
  `services/protocol/conversation.py`
  该问题已在当前实现中处理：图片生成/编辑的延迟结果会继续轮询会话，避免在 `tool_invoked: true` 后直接返回中间文本。

## 当前结论

- [validated] 远程账号注入、远程来源同步和来源范围 replace 已在 dev 容器验证，管理员鉴权和响应脱敏符合预期。
- [validated] 网络 profile 模块化与 Grok clearance 配置已落地，配置入口以 `network_profiles.grok_console` 和 `network_profiles.grok_app_chat` 为主，旧 `grok_console_fingerprint` 仍兼容。
- [validated] Browser Bridge 已接入 Grok app-chat 图片链路；Docker 镜像提供 Chromium，entrypoint 会在 Bridge 服务存在时启动它，`browser_bridge_url` 也可指向外部 Bridge。
- [known limitation] Bridge 首次访问 `grok.com` 时的 navigation timeout 可能只是非致命预热警告，不能单独视为请求失败。
- [known limitation] 近期观察到的 Grok app-chat 图片请求失败不是当前 CF/WAF 阻断导致；403 或 429 更可能表示上游账号 tier、capability 或额度不满足请求。
- [known limitation] Grok app-chat 当前只支持图片生成模型；`grok-imagine-image-edit` 和 `grok-imagine-video` 尚未接通运行链路，不作为阻塞发布问题。
- [known limitation] GPT Turnstile 求解与 Grok app-chat FlareSolverr clearance 都是 best-effort，真实挑战仍可能因上游 challenge、账号状态或 solver 结果失败。
- [validated] dev 部署容器 `dev-webchat2api` 已重建运行在 `8083 -> 83`，`/health`、API 检查和 bind-mounted `data/` 持久化已验证。
