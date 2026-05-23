# 远程账号注入 API 示例

本文给运维和开发人员提供远程账号来源与直接注入接口的请求、响应示例。示例只覆盖当前实现已经支持的行为。

## 功能范围

远程账号注入 API 用于把外部账号 payload 导入本项目账号池，支持两种入口：

- 配置远程来源，手动触发同步。
- 直接提交 `tokens`、`accounts` 或 `payload` 注入。

接口只负责来源配置、同步拉取、payload 归一化和账号池写入。GPT Turnstile、Grok 网络 profile、Cloudflare Cookie、代理和指纹配置是独立能力，不由这些接口控制。

## 鉴权要求

所有接口都需要管理员权限。请求头格式：

```http
Authorization: Bearer <admin-secret-or-admin-key>
```

普通用户 API Key 不能执行这些管理操作。未提供或无效凭据通常返回 `401`，非管理员凭据返回 `403`。

## 路由列表

- `GET /api/remote-account/sources`
- `POST /api/remote-account/sources`
- `POST /api/remote-account/sources/{source_id}`
- `DELETE /api/remote-account/sources/{source_id}`
- `POST /api/remote-account/sources/{source_id}/sync`
- `GET /api/remote-account/sources/{source_id}/sync`
- `POST /api/remote-account/inject`

## 远程来源字段

创建来源时可提交这些字段：

```json
{
  "name": "Team GPT Source",
  "enabled": true,
  "url": "https://accounts.example.test/webchat2api.json",
  "method": "GET",
  "auth_header": "",
  "auth_token": "",
  "bearer_token": "",
  "provider": "gpt",
  "sync_strategy": "merge",
  "interval_seconds": 3600
}
```

字段说明：

- `url` 必填。
- `method` 支持 `GET` 或 `POST`，默认 `GET`。
- `bearer_token` 存在时，同步请求会发送 `Authorization: Bearer <bearer_token>`。
- `auth_header` 与 `auth_token` 同时存在且未设置 `bearer_token` 时，同步请求会发送自定义鉴权头。
- `provider` 可为空字符串、`gpt` 或 `grok`。远程 payload 中单个账号未声明 `provider` 时使用该默认值，空值最终按 GPT 默认处理。
- `sync_strategy` 支持 `merge` 或 `replace`，默认 `merge`。
- `interval_seconds` 会被保存和返回，但当前文档不把它描述为自动调度能力。

## 示例约定

下面示例使用：

- 服务地址：`http://localhost:83`
- 管理员密钥：`admin`
- 示例来源 ID：`a1b2c3d4e5f6`

所有 Token 均为占位符，不要把真实账号凭据写入文档、日志或工单。

## 查看来源列表

请求：

```bash
curl http://localhost:83/api/remote-account/sources \
  -H "Authorization: Bearer admin"
```

响应示例：

```json
{
  "sources": [
    {
      "id": "a1b2c3d4e5f6",
      "name": "Team GPT Source",
      "enabled": true,
      "url": "https://accounts.example.test/webchat2api.json",
      "method": "GET",
      "auth_header": "",
      "provider": "gpt",
      "sync_strategy": "merge",
      "interval_seconds": 3600,
      "last_sync_at": "2026-05-23T10:15:30+00:00",
      "import_job": {
        "job_id": "11112222333344445555666677778888",
        "status": "success",
        "created_at": "2026-05-23T10:15:29+00:00",
        "updated_at": "2026-05-23T10:15:30+00:00",
        "source_id": "a1b2c3d4e5f6",
        "source_name": "Team GPT Source",
        "strategy": "merge",
        "total": 2,
        "added": 1,
        "skipped": 1,
        "removed": 0,
        "failed": 0,
        "errors": []
      },
      "has_auth_token": false,
      "has_bearer_token": false
    }
  ]
}
```

来源响应会隐藏 `auth_token` 和 `bearer_token`，只返回 `has_auth_token` 与 `has_bearer_token`。

## 创建 GET 来源

请求：

```bash
curl http://localhost:83/api/remote-account/sources \
  -H "Authorization: Bearer admin" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Team GPT Source",
    "url": "https://accounts.example.test/webchat2api.json",
    "method": "GET",
    "provider": "gpt",
    "sync_strategy": "merge",
    "interval_seconds": 3600
  }'
```

响应示例：

```json
{
  "source": {
    "id": "a1b2c3d4e5f6",
    "name": "Team GPT Source",
    "enabled": true,
    "url": "https://accounts.example.test/webchat2api.json",
    "method": "GET",
    "auth_header": "",
    "provider": "gpt",
    "sync_strategy": "merge",
    "interval_seconds": 3600,
    "last_sync_at": "",
    "import_job": null,
    "has_auth_token": false,
    "has_bearer_token": false
  },
  "sources": [
    {
      "id": "a1b2c3d4e5f6",
      "name": "Team GPT Source",
      "enabled": true,
      "url": "https://accounts.example.test/webchat2api.json",
      "method": "GET",
      "auth_header": "",
      "provider": "gpt",
      "sync_strategy": "merge",
      "interval_seconds": 3600,
      "last_sync_at": "",
      "import_job": null,
      "has_auth_token": false,
      "has_bearer_token": false
    }
  ]
}
```

## 创建 Bearer Token 保护来源

请求：

```bash
curl http://localhost:83/api/remote-account/sources \
  -H "Authorization: Bearer admin" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Protected GPT Source",
    "url": "https://accounts.example.test/protected.json",
    "method": "GET",
    "bearer_token": "example-upstream-bearer-token",
    "provider": "gpt",
    "sync_strategy": "merge"
  }'
```

响应示例：

```json
{
  "source": {
    "id": "b1c2d3e4f5a6",
    "name": "Protected GPT Source",
    "enabled": true,
    "url": "https://accounts.example.test/protected.json",
    "method": "GET",
    "auth_header": "",
    "provider": "gpt",
    "sync_strategy": "merge",
    "interval_seconds": null,
    "last_sync_at": "",
    "import_job": null,
    "has_auth_token": false,
    "has_bearer_token": true
  },
  "sources": [
    {
      "id": "b1c2d3e4f5a6",
      "name": "Protected GPT Source",
      "enabled": true,
      "url": "https://accounts.example.test/protected.json",
      "method": "GET",
      "auth_header": "",
      "provider": "gpt",
      "sync_strategy": "merge",
      "interval_seconds": null,
      "last_sync_at": "",
      "import_job": null,
      "has_auth_token": false,
      "has_bearer_token": true
    }
  ]
}
```

`bearer_token` 不会出现在响应里。

## 创建自定义请求头保护来源

请求：

```bash
curl http://localhost:83/api/remote-account/sources \
  -H "Authorization: Bearer admin" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Header Protected Grok Source",
    "url": "https://accounts.example.test/grok.json",
    "method": "POST",
    "auth_header": "X-Remote-Account-Key",
    "auth_token": "example-upstream-header-token",
    "provider": "grok",
    "sync_strategy": "replace"
  }'
```

响应示例：

```json
{
  "source": {
    "id": "c1d2e3f4a5b6",
    "name": "Header Protected Grok Source",
    "enabled": true,
    "url": "https://accounts.example.test/grok.json",
    "method": "POST",
    "auth_header": "X-Remote-Account-Key",
    "provider": "grok",
    "sync_strategy": "replace",
    "interval_seconds": null,
    "last_sync_at": "",
    "import_job": null,
    "has_auth_token": true,
    "has_bearer_token": false
  },
  "sources": [
    {
      "id": "c1d2e3f4a5b6",
      "name": "Header Protected Grok Source",
      "enabled": true,
      "url": "https://accounts.example.test/grok.json",
      "method": "POST",
      "auth_header": "X-Remote-Account-Key",
      "provider": "grok",
      "sync_strategy": "replace",
      "interval_seconds": null,
      "last_sync_at": "",
      "import_job": null,
      "has_auth_token": true,
      "has_bearer_token": false
    }
  ]
}
```

`auth_token` 不会出现在响应里。

## 更新来源

请求：

```bash
curl http://localhost:83/api/remote-account/sources/a1b2c3d4e5f6 \
  -H "Authorization: Bearer admin" \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": false,
    "sync_strategy": "replace",
    "provider": "gpt"
  }'
```

响应示例：

```json
{
  "source": {
    "id": "a1b2c3d4e5f6",
    "name": "Team GPT Source",
    "enabled": false,
    "url": "https://accounts.example.test/webchat2api.json",
    "method": "GET",
    "auth_header": "",
    "provider": "gpt",
    "sync_strategy": "replace",
    "interval_seconds": 3600,
    "last_sync_at": "",
    "import_job": null,
    "has_auth_token": false,
    "has_bearer_token": false
  },
  "sources": [
    {
      "id": "a1b2c3d4e5f6",
      "name": "Team GPT Source",
      "enabled": false,
      "url": "https://accounts.example.test/webchat2api.json",
      "method": "GET",
      "auth_header": "",
      "provider": "gpt",
      "sync_strategy": "replace",
      "interval_seconds": 3600,
      "last_sync_at": "",
      "import_job": null,
      "has_auth_token": false,
      "has_bearer_token": false
    }
  ]
}
```

更新接口只应用请求里出现且非 `null` 的字段。

## 删除来源

请求：

```bash
curl -X DELETE http://localhost:83/api/remote-account/sources/a1b2c3d4e5f6 \
  -H "Authorization: Bearer admin"
```

响应示例：

```json
{
  "sources": []
}
```

删除的是来源配置本身。接口响应只返回删除后的来源列表。

## 触发同步

请求：

```bash
curl -X POST http://localhost:83/api/remote-account/sources/a1b2c3d4e5f6/sync \
  -H "Authorization: Bearer admin"
```

成功响应示例：

```json
{
  "import_job": {
    "job_id": "9999aaaabbbbccccddddeeeeffff0000",
    "status": "success",
    "created_at": "2026-05-23T10:20:00+00:00",
    "updated_at": "2026-05-23T10:20:01+00:00",
    "source_id": "a1b2c3d4e5f6",
    "source_name": "Team GPT Source",
    "strategy": "merge",
    "total": 2,
    "added": 1,
    "skipped": 1,
    "removed": 0,
    "failed": 0,
    "errors": []
  },
  "source": {
    "id": "a1b2c3d4e5f6",
    "name": "Team GPT Source",
    "enabled": true,
    "url": "https://accounts.example.test/webchat2api.json",
    "method": "GET",
    "auth_header": "",
    "provider": "gpt",
    "sync_strategy": "merge",
    "interval_seconds": 3600,
    "last_sync_at": "2026-05-23T10:20:01+00:00",
    "import_job": {
      "job_id": "9999aaaabbbbccccddddeeeeffff0000",
      "status": "success",
      "created_at": "2026-05-23T10:20:00+00:00",
      "updated_at": "2026-05-23T10:20:01+00:00",
      "source_id": "a1b2c3d4e5f6",
      "source_name": "Team GPT Source",
      "strategy": "merge",
      "total": 2,
      "added": 1,
      "skipped": 1,
      "removed": 0,
      "failed": 0,
      "errors": []
    },
    "has_auth_token": false,
    "has_bearer_token": false
  }
}
```

同步是手动触发。响应只返回计数和任务状态，不返回原始账号 Token。

## 查询同步进度

请求：

```bash
curl http://localhost:83/api/remote-account/sources/a1b2c3d4e5f6/sync \
  -H "Authorization: Bearer admin"
```

响应示例：

```json
{
  "import_job": {
    "job_id": "9999aaaabbbbccccddddeeeeffff0000",
    "status": "success",
    "created_at": "2026-05-23T10:20:00+00:00",
    "updated_at": "2026-05-23T10:20:01+00:00",
    "source_id": "a1b2c3d4e5f6",
    "source_name": "Team GPT Source",
    "strategy": "merge",
    "total": 2,
    "added": 1,
    "skipped": 1,
    "removed": 0,
    "failed": 0,
    "errors": []
  }
}
```

该接口返回来源里保存的最近一次 `import_job`。

## 直接注入 tokens

请求：

```bash
curl http://localhost:83/api/remote-account/inject \
  -H "Authorization: Bearer admin" \
  -H "Content-Type: application/json" \
  -d '{
    "tokens": ["example-gpt-token-1", "example-gpt-token-2"],
    "strategy": "merge",
    "source_id": "manual-gpt",
    "source_name": "Manual GPT",
    "provider": "gpt"
  }'
```

响应示例：

```json
{
  "strategy": "merge",
  "source_id": "manual-gpt",
  "source_name": "Manual GPT",
  "total": 2,
  "added": 2,
  "skipped": 0,
  "removed": 0
}
```

`tokens` 里的字符串会归一化为账号 `access_token`，并使用请求级 `provider` 作为默认服务商。

## 直接注入 accounts

GPT 账号示例：

```bash
curl http://localhost:83/api/remote-account/inject \
  -H "Authorization: Bearer admin" \
  -H "Content-Type: application/json" \
  -d '{
    "accounts": [
      {
        "access_token": "example-gpt-token-3",
        "provider": "gpt",
        "type": "plus"
      }
    ],
    "strategy": "merge",
    "source_id": "manual-gpt",
    "source_name": "Manual GPT",
    "provider": "gpt"
  }'
```

响应示例：

```json
{
  "strategy": "merge",
  "source_id": "manual-gpt",
  "source_name": "Manual GPT",
  "total": 1,
  "added": 1,
  "skipped": 0,
  "removed": 0
}
```

Grok 账号示例：

```bash
curl http://localhost:83/api/remote-account/inject \
  -H "Authorization: Bearer admin" \
  -H "Content-Type: application/json" \
  -d '{
    "accounts": [
      {
        "token": "example-grok-token-1",
        "provider": "grok",
        "type": "basic",
        "metadata": {
          "team": "ops"
        }
      }
    ],
    "strategy": "merge",
    "source_id": "manual-grok",
    "source_name": "Manual Grok",
    "provider": "grok"
  }'
```

响应示例：

```json
{
  "strategy": "merge",
  "source_id": "manual-grok",
  "source_name": "Manual Grok",
  "total": 1,
  "added": 1,
  "skipped": 0,
  "removed": 0
}
```

`accounts` 中可使用 `access_token` 或 `token`。单个账号的 `provider` 优先于请求级 `provider`。

## merge 与 replace

### merge 示例

请求：

```bash
curl http://localhost:83/api/remote-account/inject \
  -H "Authorization: Bearer admin" \
  -H "Content-Type: application/json" \
  -d '{
    "accounts": [
      {
        "access_token": "example-existing-token",
        "provider": "gpt",
        "type": "plus"
      }
    ],
    "strategy": "merge",
    "source_id": "a1b2c3d4e5f6",
    "source_name": "Team GPT Source",
    "provider": "gpt"
  }'
```

响应示例：

```json
{
  "strategy": "merge",
  "source_id": "a1b2c3d4e5f6",
  "source_name": "Team GPT Source",
  "total": 1,
  "added": 0,
  "skipped": 1,
  "removed": 0
}
```

`merge` 按 `access_token` 合并。远程 payload 省略状态、额度、恢复时间、成功失败计数等可变字段时，现有账号状态会保留。

### replace 示例

请求：

```bash
curl http://localhost:83/api/remote-account/inject \
  -H "Authorization: Bearer admin" \
  -H "Content-Type: application/json" \
  -d '{
    "tokens": ["example-new-source-token"],
    "strategy": "replace",
    "source_id": "a1b2c3d4e5f6",
    "source_name": "Team GPT Source",
    "provider": "gpt"
  }'
```

响应示例：

```json
{
  "strategy": "replace",
  "source_id": "a1b2c3d4e5f6",
  "source_name": "Team GPT Source",
  "total": 1,
  "added": 1,
  "skipped": 0,
  "removed": 1
}
```

`replace` 必须提供 `source_id`，只替换同一 `remote_source_id` 下的账号，不会删除其他来源账号或本地手动账号。`replace` 会拒绝空 payload。

## 远程来源 payload 格式

同步接口从远程 URL 获取 JSON。当前支持包装对象或数组。

`tokens` 包装对象：

```json
{
  "tokens": ["example-token-1", "example-token-2"]
}
```

`accounts` 包装对象：

```json
{
  "accounts": [
    {
      "access_token": "example-token-3",
      "provider": "gpt",
      "type": "plus"
    },
    {
      "token": "example-token-4",
      "provider": "grok",
      "type": "basic"
    }
  ]
}
```

数组：

```json
[
  "example-token-5",
  {
    "access_token": "example-token-6",
    "provider": "gpt"
  }
]
```

## 常见错误响应

### 400 请求参数错误

创建来源缺少 `url`：

```json
{
  "detail": {
    "error": "url is required"
  }
}
```

直接注入缺少 payload：

```json
{
  "detail": {
    "error": "payload, accounts, or tokens is required"
  }
}
```

`replace` 缺少 `source_id`：

```json
{
  "detail": {
    "error": "replace requires source_id"
  }
}
```

`replace` 空 payload：

```json
{
  "detail": {
    "error": "replace requires a non-empty account payload"
  }
}
```

禁用来源同步：

```json
{
  "detail": {
    "error": "source is disabled"
  }
}
```

### 401 未认证

```json
{
  "detail": {
    "error": "密钥无效或已失效，请重新登录"
  }
}
```

### 403 非管理员

```json
{
  "detail": {
    "error": "需要管理员权限才能执行这个操作"
  }
}
```

### 404 来源不存在

```json
{
  "detail": {
    "error": "source not found"
  }
}
```

### 502 同步失败

```json
{
  "detail": {
    "error": "remote account sync failed"
  }
}
```

同步失败响应使用固定的脱敏消息 `remote account sync failed`，不会返回上游地址、鉴权头、账号 Token 或原始响应正文。

## 行为备注

- 来源响应隐藏 `auth_token` 和 `bearer_token`，只暴露 `has_auth_token` 与 `has_bearer_token`。
- 直接注入和同步响应是 count-only，不返回原始账号 Token 或账号列表。
- `replace` 需要 `source_id`，按来源范围替换，并拒绝空 payload。
- `merge` 在 payload 省略可变账号状态时保留现有状态。
- GPT Turnstile 和 Grok 网络 profile 是单独配置主题，不由远程账号注入 API 控制。
- `interval_seconds` 可保存为来源字段。当前示例不声明它会触发自动同步调度。
- 本项目实现了自己的远程 URL 来源拉取层，不声明 grok2api 上游已经支持任意远程 URL 账号抓取。
