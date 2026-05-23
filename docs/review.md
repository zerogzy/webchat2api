# Review

当前没有阻塞发布的未解决审查项。

## 已归档

- [resolved] Poll when the image tool was invoked
  `services/protocol/conversation.py`
  该问题已在当前实现中处理：图片生成/编辑的延迟结果会继续轮询会话，避免在 `tool_invoked: true` 后直接返回中间文本。

## 当前结论

- [validated] 远程账号注入、远程来源同步和来源范围 replace 已在 dev 容器验证，管理员鉴权和响应脱敏符合预期。
- [validated] 网络 profile 模块化与 Grok `cf_clearance` 配置已落地，配置入口以 `network_profiles.grok_console` 为主，旧 `grok_console_fingerprint` 仍兼容。
- [known limitation] GPT Turnstile 求解为 best-effort，真实挑战仍可能因上游 challenge 或 solver 结果失败。
- [validated] dev 部署容器 `dev-webchat2api` 已重建运行在 `8083 -> 83`，`/health`、API 检查和 bind-mounted `data/` 持久化已验证。
