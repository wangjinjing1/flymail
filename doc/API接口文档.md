# FlyMail API 接口文档

本文档按当前 Docker 多用户版本整理，覆盖主要 HTTP 接口和 WebSocket 连接方式。

默认服务地址：

- HTTP: `http://localhost:8080`
- Swagger: `http://localhost:8080/docs`
- WebSocket: `ws://localhost:8080/ws`

如果配置了 `FLYMAIL_BASE_PATH=/mail`，则前缀相应变为：

- HTTP: `http://localhost:8080/mail/api/...`
- WebSocket: `ws://localhost:8080/mail/ws`

## 认证

系统使用 Cookie 会话认证。登录成功后，后端会写入 HTTP Only Cookie，后续请求自动携带。

### POST /api/auth/login

登录。

请求体：

```json
{
  "username": "admin",
  "password": "change_me_please"
}
```

响应：

```json
{
  "success": true,
  "user": {
    "id": "uuid",
    "uid": "uuid",
    "username": "admin",
    "role": "admin",
    "status": "active"
  }
}
```

### POST /api/auth/logout

退出登录。

响应：

```json
{
  "success": true
}
```

### GET /api/auth/me

获取当前登录用户。

响应：

```json
{
  "id": "uuid",
  "uid": "uuid",
  "username": "admin",
  "role": "admin",
  "status": "active"
}
```

### POST /api/auth/change-password

当前用户修改自己的密码。

请求体：

```json
{
  "current_password": "old_password",
  "new_password": "new_password"
}
```

响应：

```json
{
  "success": true,
  "updated_at": 1718000000.0
}
```

## 管理员接口

以下接口仅管理员可用。

### GET /api/admin/users

获取用户列表。

响应：

```json
{
  "users": [
    {
      "id": "uuid",
      "username": "admin",
      "role": "admin",
      "status": "active",
      "created_at": 1718000000.0,
      "updated_at": 1718000000.0
    }
  ]
}
```

### POST /api/admin/users

创建用户。

请求体：

```json
{
  "username": "alice",
  "password": "password123",
  "role": "user"
}
```

响应：

```json
{
  "success": true
}
```

### POST /api/admin/users/{user_id}/reset-password

重置用户密码。

请求体：

```json
{
  "new_password": "new_password123"
}
```

响应：

```json
{
  "success": true
}
```

### POST /api/admin/users/{user_id}/toggle-status

启用或禁用用户。

响应：

```json
{
  "success": true,
  "status": "disabled"
}
```

## 通用接口

### GET /api/health

健康检查。

响应：

```json
{
  "status": "ok",
  "app": "flymail",
  "version": "1.0.2"
}
```

### GET /api/user

兼容接口，返回当前登录用户的基础信息。

响应：

```json
{
  "uid": "uuid",
  "username": "admin"
}
```

## 邮箱账号管理

基础路径：`/api/accounts`

### GET /api/accounts

获取当前用户的邮箱账号列表。

### POST /api/accounts/auth-url

获取 Gmail / Outlook 的 OAuth 授权地址。

请求体：

```json
{
  "provider": "gmail",
  "redirect_uri": "https://example.com/api/auth/callback"
}
```

支持额外字段：

- `fetch_history: true` 添加账号后自动创建历史同步任务

### POST /api/accounts/add-qq

添加 QQ 邮箱。

### POST /api/accounts/add-icloud

添加 iCloud 邮箱。

### POST /api/accounts/add-netease

添加网易邮箱。

这些接口请求体统一为：

```json
{
  "email": "user@example.com",
  "auth_code": "mail_auth_code",
  "fetch_history": true
}
```

### PUT /api/accounts/{account_id}

更新备注、分组、隐藏邮箱设置。

### DELETE /api/accounts/{account_id}

删除邮箱账号。

### POST /api/accounts/{account_id}/test

测试邮箱连接。

## OAuth 回调

### GET /api/auth/callback

OAuth 回调接口。通常由第三方平台重定向调用，不需要手工请求。

## 文件夹

### GET /api/folders

获取当前账号的文件夹列表。

查询参数：

- `account_id`

### GET /api/folder-counts

获取文件夹计数。

查询参数：

- `account_id`

## 邮件

基础路径：`/api/messages`

主要能力：

- 普通收件箱列表
- 聚合收件箱列表
- 邮件详情
- 附件下载
- 批量已读
- 批量删除
- 正文预取
- 上传附件

### GET /api/messages/unified

获取聚合收件箱。

常用查询参数：

- `page`
- `page_size`
- `account_filter`
- `read_filter`
- `attachment_filter`

### GET /api/messages

获取单账号文件夹邮件列表。

常用查询参数：

- `account_id`
- `folder`
- `page`
- `page_size`
- `read_filter`
- `attachment_filter`

### GET /api/messages/{message_id}

获取邮件详情。

查询参数：

- `account_id`
- `folder`

### GET /api/messages/{message_id}/attachments/{part_number}

下载附件。

查询参数：

- `account_id`
- `folder`

### POST /api/messages/prefetch

预取邮件正文。

### POST /api/messages/delete

批量删除。

### POST /api/messages/mark-read

单条标记已读。

### POST /api/messages/batch-mark-read

批量标记已读。

### POST /api/messages/upload

上传写信附件。

## 写信与定时发送

### POST /api/compose/send

发送邮件，支持立即发送和定时发送。

### GET /api/compose/scheduled

获取当前用户待执行的定时任务。

### DELETE /api/compose/scheduled/{job_id}

取消定时发送。

## 通知

基础路径：`/api/notifications`

### GET /api/notifications

获取通知列表。

### POST /api/notifications/{notification_id}/read

标记单条通知已读。

### POST /api/notifications/read-all

全部已读。

### DELETE /api/notifications

清空通知。

## 设置

基础路径：`/api/settings`

### GET /api/settings

获取应用设置。

### PUT /api/settings

更新应用设置。

### GET /api/settings/unified

获取聚合收件箱账号选择。

### PUT /api/settings/unified

保存聚合收件箱账号选择。

### GET /api/settings/oauth-diagnostic

OAuth 配置诊断。

## 签名

基础路径：`/api/signatures`

### GET /api/signatures

获取签名列表。

### POST /api/signatures

创建签名。

### PUT /api/signatures/{id}

更新签名。

### DELETE /api/signatures/{id}

删除签名。

## 历史邮件同步

基础路径：`/api/history-sync`

### GET /api/history-sync/jobs

获取当前用户全部邮箱的历史同步任务状态。

### GET /api/history-sync/jobs/{account_id}

获取指定邮箱同步状态。

### POST /api/history-sync/jobs/{account_id}/start

开始同步历史邮件。

### POST /api/history-sync/jobs/{account_id}/pause

暂停同步。

### POST /api/history-sync/jobs/{account_id}/resume

从断点继续同步。

## WebSocket

连接地址：

```text
ws://localhost:8080/ws
```

如果配置了 `FLYMAIL_BASE_PATH=/mail`：

```text
ws://localhost:8080/mail/ws
```

连接要求：

- 浏览器需已登录
- 会话 Cookie 会自动携带

消息类型包括：

- `ping`
- `new_mail`
- `connection_status`
- `sync_progress`
- `message_state_changed`

## 错误格式

统一错误响应：

```json
{
  "error": "错误信息"
}
```
