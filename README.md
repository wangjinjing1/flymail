# FlyMail

FlyMail 是一个面向 Docker 部署的多用户邮件客户端，支持多邮箱聚合、历史邮件后台同步、断点续传、附件与内嵌图片缓存，以及本地持久化存储。

感谢原作者 [DinDing1/FlyMail](https://github.com/DinDing1/FlyMail)。当前仓库已基于现有版本重构为独立的 Docker 多用户版，不再依赖飞牛应用中心运行环境。

## 当前能力

- 本地用户名密码登录
- 使用 `.env` 初始化超级管理员
- 管理员创建用户、重置密码、启用/禁用用户
- 普通用户可自行修改密码
- 用户之间邮箱数据相互隔离
- 多邮箱账号管理
- 聚合收件箱
- 历史邮件后台同步
- 同步任务进度查看、暂停、继续、手动刷新
- 从上次断点继续拉取，不从头重跑
- 下载并缓存历史邮件中的附件和内嵌图片
- MySQL 存储业务数据
- 本地 `data/` 目录持久化文件

## 目录说明

- `backend/`：FastAPI 后端
- `frontend/`：Vue 3 前端
- `data/`：本地持久化目录
- `dist/`：构建产物
- `doc/`：设计文档与接口文档

## 环境变量

参考根目录的 [`.env.example`](.env.example)：

```env
APP_PORT=8080
FLYMAIL_BASE_PATH=
FLYMAIL_ADMIN_USERNAME=admin
FLYMAIL_ADMIN_PASSWORD=change_me_please
FLYMAIL_SESSION_SECRET=replace_with_a_long_random_secret
DATABASE_URL=mysql://flymail:change_me@127.0.0.1:3306/flymail?charset=utf8mb4
```

字段说明：

- `APP_PORT`：宿主机暴露端口
- `FLYMAIL_BASE_PATH`：可选，反向代理子路径，例如 `/mail`
- `FLYMAIL_ADMIN_USERNAME`：首次启动时初始化的管理员用户名
- `FLYMAIL_ADMIN_PASSWORD`：首次启动时初始化的管理员密码
- `FLYMAIL_SESSION_SECRET`：会话签名密钥，建议至少 16 位
- `DATABASE_URL`：MySQL 连接串
- `FLYMAIL_DATA_PATH`：可选，Docker 数据目录映射的宿主机路径，默认 `./data`

注意：

- 如果数据库密码中包含 `%`、`@`、`:`、`/` 等特殊字符，需要做 URL 编码。
- `.env` 不会提交到 Git，请在本地自行维护。

## Docker 部署

当前仓库自带 `docker-compose.yml`，会：

- 直接拉取 Docker Hub 镜像 `wangjinjing1/flymail:latest`
- 读取根目录 `.env`
- 将宿主机 `APP_PORT` 映射到容器 `8080`
- 将 `${FLYMAIL_DATA_PATH:-./data}` 映射到容器内 `/app/data`

### 1. 准备 `.env`

```bash
cp .env.example .env
```

Windows PowerShell 可直接复制并编辑：

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
docker build -t wangjinjing1/flymail:latest -t wangjinjing1/flymail:1.0.0 .
```

登录 Docker Hub：

```bash
docker login
```

推送镜像：

```bash
docker push wangjinjing1/flymail:latest
docker push wangjinjing1/flymail:1.0.0
```

当前 `docker-compose.yml` 已默认使用线上拉取镜像模式，服务器上不需要本地构建。

## 数据存储

### MySQL

业务数据保存在你自己的 MySQL 中，主要包括：

- 用户
- 邮箱账号
- 缓存邮件摘要
- 通知
- 用户设置
- 签名
- 历史同步任务

### 本地 `data/` 目录

文件类数据保存在本地 `data/` 目录，适合直接映射到 Docker volume。当前按类型拆分为子目录：

- `data/uploads/`：写信时上传的附件
- `data/history/raw/`：历史邮件原始缓存
- `data/history/attachments/`：历史邮件附件
- `data/history/inline/`：历史邮件内嵌图片
- `data/logs/`：运行日志
- `data/config/`：本地配置与辅助文件

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
- 查看当前任务状态

“继续”是从上次断点恢复，不会从头重跑。

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
