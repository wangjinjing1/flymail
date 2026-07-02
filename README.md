# FlyMail

FlyMail 是一个面向 Docker 部署的多用户邮件客户端，支持多邮箱账号管理、历史邮件后台同步、断点续传、附件与内嵌图片缓存，以及本地持久化存储。

感谢原作者 [DinDing1/FlyMail](https://github.com/DinDing1/FlyMail)。当前仓库已经基于现有版本重构为独立的 Docker 多用户版本，不再依赖飞牛应用中心运行环境。

## 当前能力

- 本地用户名密码登录
- 使用 `.env` 初始化超级管理员
- 管理员创建用户、重置密码、启用/禁用用户
- 普通用户可自行修改密码
- 用户之间邮箱数据相互隔离
- 多邮箱账号管理
- 历史邮件后台同步
- 同步任务进度查看、暂停、继续、重试、手动刷新
- 从上次断点继续拉取，不从头重跑
- 下载并缓存历史邮件中的附件和内嵌图片
- MySQL 存储业务数据
- 本地 `data/` 目录持久化文件

## 目录说明

- `backend/`: FastAPI 后端
- `frontend/`: Vue 3 前端
- `data/`: 本地持久化目录
- `dist/`: 构建产物
- `doc/`: 设计文档与接口文档

## 环境变量

参考根目录的 [`.env.example`](.env.example)：

```env
APP_PORT=8080
FLYMAIL_BASE_PATH=
FLYMAIL_ADMIN_USERNAME=admin
FLYMAIL_ADMIN_PASSWORD=change_me_please
FLYMAIL_SESSION_SECRET=replace_with_a_long_random_secret
DATABASE_URL=mysql://flymail:change_me@127.0.0.1:3306/flymail?charset=utf8mb4
FLYMAIL_HTTP_PROXY=
FLYMAIL_HTTPS_PROXY=
FLYMAIL_ALL_PROXY=
FLYMAIL_NO_PROXY=127.0.0.1,localhost
```

字段说明：

- `APP_PORT`: 宿主机暴露端口
- `FLYMAIL_BASE_PATH`: 可选，反向代理子路径，例如 `/mail`
- `FLYMAIL_ADMIN_USERNAME`: 首次启动时初始化的管理员用户名
- `FLYMAIL_ADMIN_PASSWORD`: 首次启动时初始化的管理员密码
- `FLYMAIL_SESSION_SECRET`: 会话签名密钥，建议至少 16 位
- `DATABASE_URL`: MySQL 连接串
- `FLYMAIL_DATA_PATH`: 可选，Docker 数据目录映射的宿主机路径，默认 `./data`
- `FLYMAIL_HTTP_PROXY`: 可选，HTTP 出站代理
- `FLYMAIL_HTTPS_PROXY`: 可选，HTTPS 出站代理，Gmail/Google OAuth 建议配置这个
- `FLYMAIL_ALL_PROXY`: 可选，全局代理
- `FLYMAIL_NO_PROXY`: 可选，不走代理的地址

注意：

- 如果数据库密码中包含 `%`、`@`、`:`、`/` 等特殊字符，需要做 URL 编码。
- 如果服务器直连 Google / Microsoft 不通，可以在 `.env` 中配置代理变量，然后执行 `docker compose up -d --force-recreate` 让容器重新加载环境变量。
- `.env` 不会提交到 Git，请在本地自行维护。

## Docker 部署

当前仓库自带 `docker-compose.yml`，会：

- 直接拉取 Docker Hub 镜像 `wangjinjing/flymail:latest`
- 读取根目录 `.env`
- 将宿主机 `APP_PORT` 映射到容器 `8080`
- 将 `${FLYMAIL_DATA_PATH:-./data}` 映射到容器内 `/app/data`

### 1. 准备 `.env`

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

### 2. 启动

```bash
docker compose pull
docker compose up -d
```

### 3. 查看日志

```bash
docker compose logs -f flymail-app
```

### 4. 停止

```bash
docker compose down
```

## 镜像构建与发布

本地构建：

```bash
docker build -t wangjinjing/flymail:latest -t wangjinjing/flymail:1.0.0 .
```

登录 Docker Hub：

```bash
docker login
```

推送镜像：

```bash
docker push wangjinjing/flymail:latest
docker push wangjinjing/flymail:1.0.0
```

## 数据存储

### MySQL

业务数据保存在你自己的 MySQL 中，主要包括：

- 用户
- 邮箱账号
- 缓存邮件摘要与正文
- 通知
- 用户设置
- 签名
- 历史同步任务

### 本地 `data/` 目录

文件类数据保存在本地 `data/` 目录，适合直接映射到 Docker volume。当前按类型拆分为子目录：

- `data/uploads/`: 写信时上传的临时附件
- `data/document/`: 历史邮件关联的非图片附件
- `data/picture/`: 历史邮件图片与内嵌图片
- `data/logs/`: 运行日志

附件与图片会按邮件年月继续分目录，便于本地直接查看和归档。

## 历史邮件同步

添加邮箱时可以选择“同步历史邮件”。

如果开启：

1. 后端会创建后台同步任务
2. 按文件夹与分页逐步拉取历史邮件
3. 自动下载附件与内嵌图片
4. 将结果写入 MySQL 和本地缓存目录

同步页面支持：

- 查看每个邮箱的同步进度
- 手动刷新任务状态
- 暂停同步
- 继续同步
- 从失败位置重试

“继续”和“重试”都会尽量从上次断点恢复，不会默认从头重跑；如果执行“重置同步”，才会先删除本地记录后重新同步。同步管理页的已同步邮件数只按本地摘要缓存计算，打开邮件补全正文和附件不会改变这个数量。

## 邮箱账号禁用与删除

账号管理页编辑邮箱时可以禁用账号或删除账号。

- 禁用账号：保留授权、本地邮件和本地附件，只停止自动同步、实时刷新和手动远端刷新。禁用后列表、查询、详情和附件只读取本地缓存。
- 删除账号：后台删除账号、本地邮件缓存和本地附件文件。用户确认后会立即回到账户列表页，并显示删除进度；删除完成后列表会自动刷新。

## 邮件列表与计数口径

在线账号打开普通邮件列表时，后端会优先读取本地缓存；只有当前页缓存不足、远端统计未知，或本地缓存数量少于 IMAP 已知总数时，才会从 IMAP 拉取当前页摘要并写回缓存。本地缓存用于离线查看、搜索、附件状态和详情加速。

文件夹计数以 IMAP 返回的当前状态为准：收件箱和垃圾邮箱显示未读数，已发送、草稿箱和已删除显示邮件总数。收件箱列表顶部的“全部 / 未读 / 已读”同样使用 IMAP 总数和未读数计算，其中已读数为 `全部 - 未读`。附件数量仍来自本地缓存，因为标准 IMAP 计数接口不直接返回附件总数。

同步管理页面的“已同步邮件”使用各文件夹本地缓存数与远端总数汇总，口径为 `sum(cached_count) / sum(total_count)`；下方各文件夹标签使用同一套 `cached_count / total_count` 数据。

普通后台新邮件缓存可以保存新邮件正文与附件，但不会累加历史同步任务里的“已下载附件 / 内嵌图片”指标；这些指标只表示历史同步任务本身下载的文件数量。

后台获取新邮件或刷新缓存完成后，会通过 WebSocket 推送最新文件夹统计；邮件列表侧边栏、当前列表顶部统计和同步管理页会据此刷新。

账号管理页可以配置新邮件后台轮询间隔，范围为 5 到 3600 秒。所有在线账号都会按该间隔做后台兜底拉新；Gmail、QQ、Outlook 仍优先使用 IMAP IDLE 实时通知，但即使 IDLE 没有触发，也会由周期同步补拉最近邮件。
邮件搜索会先刷新当前邮箱账号、当前文件夹的最近一页缓存，再查询本地缓存，避免刚到达但尚未写入缓存的邮件搜不到。

## 邮箱登录失效后还能否查看历史邮件

如果邮件已经同步到本地：

- 邮件摘要仍可查看
- 已缓存的正文仍可查看
- 已缓存的附件和图片仍可访问

如果某封邮件尚未同步完成，或者某些内容仍依赖远端邮箱实时获取，那么在邮箱登录失效后，这部分内容暂时无法继续拉取，直到账号重新可用。

## 权限模型

### 管理员

管理员可以：

- 登录系统
- 创建用户
- 重置用户密码
- 启用或禁用用户
- 使用全部邮件功能

管理员不能查看任何用户的原始密码。

### 普通用户

普通用户可以：

- 登录系统
- 修改自己的密码
- 管理自己的邮箱账号与邮件数据

每个用户的数据默认彼此隔离。

## 本地开发

后端：

```bash
cd backend
pip install -r requirements.txt
python main.py
```

前端：

```bash
cd frontend
npm install
npm run dev
```

## 文档

- [API 接口文档](doc/API接口文档.md)
- [Docker 多用户重构设计](doc/2026-06-30-docker-multi-user-refactor-design.md)
- [历史邮件同步设计记录](doc/history-sync-design-2026-06-30.md)

## 许可证

[GPL-3.0](LICENSE)
