# 历史邮件同步设计记录

日期：2026-06-30

## 目标

- 每个邮箱只保留一个历史同步任务
- 支持暂停、继续、断点续传
- 可以查询每个邮箱的同步进度
- 历史同步时下载附件和内嵌图片
- 已同步邮件在账号后续登录失败时仍可查看

## 断点语义

- `暂停`：仅停止后台拉取任务，已入库和已落盘的数据保留
- `继续`：从上次的 `current_folder + current_page + current_uid` 继续，不从头重跑
- `开始`：创建新的历史同步任务并从头扫描

## 存储策略

- 邮件正文和摘要：MySQL `cached_messages`
- 历史任务进度：MySQL `history_sync_jobs`
- 原始邮件：`data/history/raw/<account>/<folder>/<uid>.json`
- 附件：`data/history/attachments/<account>/<uid>/`
- 内嵌图片：`data/history/inline/<account>/<uid>/`

## 离线查看

- 邮件详情优先读取本地缓存
- 附件下载优先读取历史缓存文件
- 内嵌图片在历史同步时直接替换为 `data:` URI，避免离线渲染失败
- 如果邮箱后续登录失败，已经同步完成的邮件仍可查看；未同步到本地的数据不可查看
