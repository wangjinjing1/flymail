# FlyMail API 接口文档

本文档基于当前 Docker 多用户版本整理，描述主要 HTTP 接口与 WebSocket 连接方式。

## 地址规则

默认情况下：

- HTTP 根路径：`/api`
- WebSocket：`/ws`
- Swagger：`/docs`

如果配置了 `FLYMAIL_BASE_PATH=/mail`，则实际访问前缀会变成：

- HTTP：`/mail/api/...`
- WebSocket：`/mail/ws`
- Swagger：`/mail/docs`

下文示例均以未配置 `FLYMAIL_BASE_PATH` 为例。

## 认证方式

系统使用基于 Cookie 的会话认证。

- 登录成功后，后端会写入 HTTP Only Cookie
- 后续请求由浏览器自动携带 Cookie
- 未登录时访问受保护接口会返回 401

## 认证接口

### `POST /api/auth/login`

登录。

请求体：

```json
{
  "username": "admin",
  "password": "change_me_please"
}
```

响应示例：

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

### `POST /api/auth/logout`

退出登录。

### `GET /api/auth/me`

获取当前登录用户。

### `POST /api/auth/change-password`

当前用户修改自己的密码。

请求体：

```json
{
  "current_password": "old_password",
  "new_password": "new_password"
}
```

## 管理员接口

以下接口仅管理员可用。

### `GET /api/admin/users`

获取用户列表。

### `POST /api/admin/users`

创建用户。

请求体示例：

```json
{
  "username": "alice",
  "password": "password123",
  "role": "user"
}
```

### `POST /api/admin/users/{user_id}/reset-password`

重置指定用户密码。

### `POST /api/admin/users/{user_id}/toggle-status`

启用或禁用用户。

## 通用接口

### `GET /api/health`

健康检查。

响应示例：

```json
{
  "status": "ok",
  "app": "flymail",
  "version": "1.0.3"
}
```

### `GET /api/user`

兼容接口，返回当前登录用户的基础信息。

## 邮箱账号管理

基础前缀：`/api/accounts`

### `GET /api/accounts`

获取当前用户的邮箱账号列表。

### `POST /api/accounts/auth-url`

获取 Gmail / Outlook 的 OAuth 授权地址。

请求体示例：

```json
{
  "provider": "gmail",
  "redirect_uri": "https://example.com/api/auth/callback",
  "fetch_history": true
}
```

说明：

- `fetch_history` 为可选字段
- 为 `true` 时，邮箱添加成功后会自动创建历史同步任务

### `POST /api/accounts/add-qq`

添加 QQ 邮箱。

### `POST /api/accounts/add-icloud`

添加 iCloud 邮箱。

### `POST /api/accounts/add-netease`

添加网易邮箱。

上述三个接口请求体结构一致：

```json
{
  "email": "user@example.com",
  "auth_code": "mail_auth_code",
  "fetch_history": true
}
```

### `PUT /api/accounts/{account_id}`

更新邮箱备注、分组、隐藏状态等信息。

### `DELETE /api/accounts/{account_id}`

删除邮箱账号。

### `POST /api/accounts/{account_id}/test`

测试账号连接状态。

### `POST /api/accounts/{account_id}/rebuild-sync`

清空该账号缓存并重建同步。

## OAuth 回调

### `GET /api/auth/callback`

第三方 OAuth 回调接口，通常由 Gmail / Outlook 等平台重定向调用。

## 文件夹接口

### `GET /api/folders`

获取指定账号的文件夹列表。

常用查询参数：

- `account_id`

### `GET /api/folder-counts`

获取文件夹邮件计数。

常用查询参数：

- `account_id`

## 邮件接口

### `GET /api/messages/unified`

获取聚合收件箱邮件列表。

常用查询参数：

- `page`
- `page_size`
- `account_filter`
- `read_filter`
- `attachment_filter`

### `GET /api/messages`

获取单账号文件夹邮件列表。

常用查询参数：

- `account_id`
- `folder`
- `page`
- `page_size`
- `read_filter`
- `attachment_filter`

### `GET /api/messages/refresh`

强制从远端刷新当前文件夹邮件列表。

### `GET /api/messages/{message_id}`

获取邮件详情。

常用查询参数：

- `account_id`
- `folder`

### `GET /api/messages/{message_id}/attachments/{part_number}`

下载邮件附件。

常用查询参数：

- `account_id`
- `folder`

### `POST /api/prefetch-messages`

后台预取邮件正文到缓存。

### `POST /api/messages/batch-mark-read`

批量标记已读。

### `POST /api/mark-read`

标记单封邮件为已读。

### `DELETE /api/messages/{message_id}`

删除单封邮件。

### `POST /api/messages/batch-delete`

批量删除邮件。

### `POST /api/messages/upload-attachment`

上传写信附件。

### `DELETE /api/messages/upload-attachment`

删除已上传附件。

## 写信与定时发送

### `POST /api/messages/send`

直接发送邮件。

### `POST /api/messages/compose`

写邮件统一入口，支持发送、存草稿、定时发送。

### `GET /api/messages/scheduled`

获取定时发送任务列表。

### `DELETE /api/messages/scheduled/{job_id}`

取消定时发送任务。

## 通知接口

基础前缀：`/api/notifications`

### `GET /api/notifications`

获取通知列表。

### `POST /api/notifications/{notification_id}/read`

标记单条通知已读。

### `POST /api/notifications/read-all`

全部标记已读。

### `DELETE /api/notifications`

清空通知。

## 设置接口

### `GET /api/settings`

获取应用设置。

### `PUT /api/settings`

更新应用设置。

### `GET /api/settings/unified`

获取聚合收件箱账号选择设置。

### `PUT /api/settings/unified`

保存聚合收件箱账号选择设置。

### `GET /api/settings/oauth-diagnostic`

查看 OAuth 配置诊断信息。

## 签名接口

### `GET /api/signature`

获取当前生效的邮件签名设置。

### `PUT /api/signature`

保存当前邮件签名设置。

### `GET /api/signatures`

获取签名模板列表。

### `POST /api/signatures`

创建签名模板。

### `PUT /api/signatures/{sig_id}`

更新签名模板。

### `DELETE /api/signatures/{sig_id}`

删除签名模板。

## 历史邮件同步接口

基础前缀：`/api/history-sync`

### `GET /api/history-sync/jobs`

获取当前用户全部邮箱的历史同步任务状态。

### `GET /api/history-sync/jobs/{account_id}`

获取指定邮箱的同步任务状态。

### `POST /api/history-sync/jobs/{account_id}/start`

开始历史同步。

### `POST /api/history-sync/jobs/{account_id}/pause`

暂停历史同步。

### `POST /api/history-sync/jobs/{account_id}/resume`

从上次断点继续同步。

## WebSocket

连接地址：

```text
/ws
```

如果配置了 `FLYMAIL_BASE_PATH=/mail`：

```text
/mail/ws
```

连接要求：

- 浏览器处于已登录状态
- 会话 Cookie 会自动携带

常见消息类型：

- `ping`
- `new_mail`
- `connection_status`
- `sync_progress`
- `message_state_changed`

## 错误格式

统一错误响应通常为：

```json
{
  "error": "错误信息"
}
```

部分接口会返回更具体的业务字段，建议以前端当前实现和 Swagger 页面为准。
