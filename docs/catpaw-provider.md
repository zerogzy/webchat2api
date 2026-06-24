# CatPaw Provider

本文档说明 webchat2api 的 CatPaw 接入方式。这里的重点是把 CatPaw 当作一个上游 provider 使用，向外暴露标准的 OpenAI 兼容接口，以及 Anthropic `/v1/messages` 兼容接口，方便 Claude Code 直接接入。

## 已支持能力

| 能力 | 状态 | 说明 |
|---|---|---|
| 文本对话 | 已支持 | 通过 OpenAI 兼容接口转发到 CatPaw 上游 |
| 多轮上下文 | 已支持 | 按请求历史透传 messages |
| 图片输入 | 已支持 | 以 base64 形式传递给上游多模态模型 |
| 工具调用 | 已支持 | Anthropic / OpenAI 两种工具调用格式都可用 |
| Claude Code 接入 | 已支持 | `claude-*` 模型名可路由到 CatPaw |

## 本地凭据

CatPaw 需要本地保存登录凭据。仓库只保留路径约定，不保存真实私有值。

- `data/catpaw_token.json`：保存 `accessToken`、`refreshToken` 和过期时间
- `data/catpaw_rsa.json`：保存解密模型列表和上传响应所需的 RSA 信息

示例结构：

```json
{
  "accessToken": "example-access-token",
  "refreshToken": "example-refresh-token",
  "expires": 1781884461216
}
```

## 环境变量

| 变量 | 说明 |
|---|---|
| `CATPAW_ACCESS_TOKEN` | 直接提供 access token，优先于文件 |
| `CATPAW_REFRESH_TOKEN` | 可选的刷新 token |
| `CATPAW_TOKEN_FILE` | token 文件路径，默认 `data/catpaw_token.json` |
| `CATPAW_RSA_FILE` | RSA 文件路径，默认 `data/catpaw_rsa.json` |
| `CATPAW_MIS_ID` | 用户 MIS ID，用于需要显式身份的请求 |
| `CATPAW_TENANT` | 租户标识 |
| `CATPAW_CLAUDE_ROUTE` | 是否将 `claude-*` 模型路由到 CatPaw，默认开启 |

## 启动方式

```bash
uv sync
export LOGIN_SECRET="your-secret"
export PORT=83
python main.py
```

Claude Code 可按下面方式接入：

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:83"
export ANTHROPIC_AUTH_TOKEN="your-secret"
claude
```

## 模型映射

`GET /v1/models` 会返回 CatPaw 可用模型。对外模型名与上游 `userModelTypeCode` 的映射由 provider 内部维护。

常见模型示例：

- `deepseek-v3.2`
- `longcat-flash`
- `kimi-k2.5`
- `glm-5`
- `MiniMax-M2.5`
- `MiniMax-M2.7`
- `glm-5.1`
- `glm-5v-turbo`
- `kimi-k2.6`

## 路由与限制

- `claude-*` 模型名会默认路由到 CatPaw
- 对话接口主要用于文本和多模态输入，不依赖本地 RSA 文件也能正常聊天
- token 失效后会自动刷新；如果刷新失败，需要重新登录并更新本地 token 文件
- 远程图片 URL 在对话场景中可能不被接受，推荐使用 base64 或先上传再引用

## 说明

CatPaw 的具体逆向细节、接口字段和模型映射以代码实现为准。本文档只保留使用说明和本地配置约定，不包含任何真实账号、密钥或会话信息。
