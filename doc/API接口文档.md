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

## 认证方式

系统使用基于 Cookie 的会话认证：

- 登录成功后，后端会写入 HTTP Only Cookie
- 后续请求由浏览器自动携带 Cookie
- 未登录时访问受保护接口会返回 `401`

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

### `POST /api/admin/users/{user_id}/reset-password`

重置指定用户密码。

### `POST /api/admin/users/{user_id}/toggle-status`

启用或禁用用户。

## 通用接口

### `GET /api/health`

健康检查。

### `GET /api/user`

兼容接口，返回当前登录用户的基础信息。

## 邮箱账号管理

基础前缀：`/api/accounts`

### `GET /api/accounts`

获取当前用户的邮箱账号列表。

账号对象包含 `poll_interval_seconds`，表示新邮件后台轮询间隔，单位秒，默认 `10`。所有在线账号都会按该间隔做后台兜底拉新；支持 IDLE 的账号仍优先使用实时通知。

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

上面三个接口请求体结构一致：

```json
{
  "email": "user@example.com",
  "auth_code": "mail_auth_code",
  "fetch_history": true
}
```

### `PUT /api/accounts/{account_id}`

更新邮箱备注、分组、隐藏状态等信息。

可更新字段：
- `remark`
- `group_name`
- `hide_email`
- `poll_interval_seconds`：新邮件后台轮询间隔，单位秒，范围 `5` 到 `3600`。所有在线账号都会按该间隔做后台兜底拉新。

### `DELETE /api/accounts/{account_id}`

后台删除邮箱账号。

说明：接口只启动后台删除任务并立即返回。后台任务会删除账号、本地缓存邮件记录、附件记录、文件夹统计和本地附件/图片文件。第三方授权不会额外调用撤销接口。

### `POST /api/accounts/{account_id}/disable`

禁用邮箱账号。

说明：禁用账号会保留授权、本地邮件缓存和本地附件文件，只停止自动同步、实时刷新和手动远端刷新。禁用后邮件列表、查询、详情和附件接口只读取本地缓存。

### `GET /api/accounts/delete-jobs`

获取当前用户的账号删除任务。

返回字段沿用历史同步任务结构，其中：

- `status`：`pending`、`running`、`failed` 等任务状态。
- `current_folder`：正在删除的邮箱地址。
- `total_folders`：需要删除的本地文件总数。
- `completed_folders`：已删除的本地文件数。
- `fetched_messages`：已删除的本地缓存邮件数。

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

### `GET /api/messages`

获取单账号单文件夹邮件列表。

常用查询参数：

- `account_id`
- `folder`
- `page`
- `page_size`
- `read_filter`
- `attachment_filter`

`folder` 支持核心文件夹别名：`INBOX`、`Sent`、`Drafts`、`Junk`、`Trash`。后端会根据 IMAP `LIST` 结果解析为服务商真实路径，例如网易已发送 `&XfJT0ZAB-`、Gmail 中文环境已发送 `[Gmail]/&XfJT0ZCuTvY-`。

说明：
- 在线账号的普通列表请求会优先读取本地缓存；当前页缓存不足、远端统计未知，或本地缓存数量少于 IMAP 已知总数时，才会从 IMAP 拉取当前页摘要并同步写入本地缓存。
- 返回的 `total`、`unread_total` 和 `filter_counts.all/unread/read` 以 IMAP 当前状态为准；`filter_counts.read = all - unread`。
- `filter_counts.attachments` 来自本地缓存，表示已缓存摘要中带附件的数量。
- `read_filter=read|unread` 的列表数据仍基于本地缓存筛选；本地筛选结果不足或远端统计未知时，接口会刷新当前远端页和远端计数，用于校正当前页已读状态和顶部计数。

### `GET /api/messages/search`

按关键字搜索邮件。在线账号会先刷新当前账号、当前文件夹的最近一页缓存，再查询本地缓存。

常用查询参数：

- `account_id`
- `folder`
- `keyword`
- `page`
- `page_size`
- `read_filter`
- `attachment_filter`

### `GET /api/messages/refresh`

从远端刷新当前账号、当前文件夹的最近一页邮件，并把缺失或变更的摘要写入本地缓存。

### `GET /api/messages/{message_id}`

获取邮件详情。

常用查询参数：

- `account_id`
- `folder`

说明：正文优先读数据库缓存；附件优先读本地文件。离线账号下，如果本地没有对应数据，会直接返回未找到。

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

主要返回 OAuth 配置字段：`gmail_client_id`、`gmail_client_secret`、`gmail_redirect_uri`、`outlook_client_id`、`outlook_client_secret`、`outlook_redirect_uri`、`has_credentials`、`has_outlook_credentials`。密钥字段为脱敏值。

上传临时附件清理仍由后端定时任务执行，默认每周一 02:00 清理；当前前端不再暴露清理星期和清理时间配置。

### `PUT /api/settings`

更新应用设置。

常用请求字段：`gmail_client_id`、`gmail_client_secret`、`gmail_redirect_uri`、`outlook_client_id`、`outlook_client_secret`、`outlook_redirect_uri`。未传入字段保持原值；密钥字段留空时不会覆盖已保存密钥。

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

响应中的 `folder_progress` 按文件夹返回：
- `cached_count`：本地已缓存邮件数。
- `total_count`：最近一次从 IMAP 统计到的远端总数。
- `unread_count`：最近一次从 IMAP 统计到的远端未读数。
- `is_synced`：`total_count > 0 && cached_count >= total_count`。

前端同步管理页的“已同步邮件”汇总使用 `sum(folder_progress.cached_count) / sum(folder_progress.total_count)`，与各文件夹子标签保持同一口径。

前端同步管理页同时展示收件箱、已发送、草稿箱、垃圾邮箱、已删除的 `cached_count / total_count`。邮件列表手动刷新或后台缓存完成后，后端会推送 `cache_updated` 和 `folder_counts`，同步管理页据此刷新这些进度。

打开邮件详情时补全正文、附件或内嵌图片缓存，不会增加历史同步任务的“已同步邮件”数量；该数量只随新增摘要缓存增长。

### `GET /api/history-sync/jobs/{account_id}`

获取指定邮箱的同步任务状态。

### `POST /api/history-sync/jobs/{account_id}/start`

重置并重新开始历史同步。

### `POST /api/history-sync/jobs/{account_id}/pause`

暂停历史同步。

### `POST /api/history-sync/jobs/{account_id}/resume`

从上次断点继续同步。

### `POST /api/history-sync/jobs/{account_id}/retry`

从当前失败位置继续重试。

## WebSocket

连接地址：

```text
/ws
```

如果配置了 `FLYMAIL_BASE_PATH=/mail`：

```text
/mail/ws
```

常见消息类型：

- `ping`
- `new_mail`
- `cache_updated`：缓存刷新完成。消息包含 `account_id`、`folder`，并尽量携带 `folder_counts`；前端用它刷新侧边栏、当前列表统计和同步管理页进度。
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
