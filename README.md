# FlyMail

FlyMail 是一个运行在 Docker 中的多用户邮件系统，支持多邮箱聚合、历史邮件后台同步、断点续传、附件和内嵌图片缓存、本地数据目录挂载，以及管理员/普通用户分权使用。

感谢原作者 [DinDing1/FlyMail](https://github.com/DinDing1/FlyMail)。本仓库当前版本已经从飞牛应用中心安装形态重构为原生 Docker 多用户版本。

## 当前特性

- 本地用户名密码登录
- `.env` 初始化超级管理员
- 管理员创建用户、重置密码、启用/禁用用户
- 普通用户修改自己的密码
- 每个用户的数据独立隔离
- 多邮箱账号管理
- 聚合收件箱
- 历史邮件后台同步
- 同步进度查看、暂停、继续
- 从上次断点继续同步
- 下载并缓存附件、内嵌图片
- MySQL 存储业务数据
- 本地 `data/` 目录持久化文件

## 目录说明

- `backend/` 后端服务
- `frontend/` 前端应用
- `data/` 本地持久化目录
- `dist/` 构建产物
- `doc/` 设计和接口文档

## 环境变量

参考 [`.env.example`](D:/Job Files/Idea Projects/Personal/FlyMail/.env.example:1)：

```env
APP_PORT=8080
FLYMAIL_BASE_PATH=
FLYMAIL_ADMIN_USERNAME=admin
FLYMAIL_ADMIN_PASSWORD=change_me_please
FLYMAIL_SESSION_SECRET=replace_with_a_long_random_secret
DATABASE_URL=mysql://flymail:change_me@127.0.0.1:3306/flymail?charset=utf8mb4
```

说明：

- `APP_PORT`：宿主机暴露端口
- `FLYMAIL_BASE_PATH`：可选，部署在反向代理子路径时使用，例如 `/mail`
- `FLYMAIL_ADMIN_USERNAME`：首次启动时初始化的管理员用户名
- `FLYMAIL_ADMIN_PASSWORD`：首次启动时初始化的管理员密码
- `FLYMAIL_SESSION_SECRET`：会话签名密钥，至少 16 位
- `DATABASE_URL`：MySQL 连接串

## Docker Compose

项目当前使用的 `docker-compose.yml` 会：

- 从当前仓库构建镜像
- 读取 `.env`
- 暴露 `APP_PORT`
- 将 `./data` 映射到容器内 `/app/data`

启动：

```bash
docker compose up -d --build
```

停止：

```bash
docker compose down
```

查看日志：

```bash
docker compose logs -f flymail-app
```

## 数据存储

### MySQL

业务数据保存在你自己部署的 MySQL 中，包括：

- 用户
- 邮箱账号
- 缓存邮件摘要
- 通知
- 用户设置
- 签名
- 历史同步任务

### 本地 `data/` 目录

文件类数据保存在本地 `data/` 中，适合直接做 Docker volume 映射。

当前会按类型拆分子目录，主要包括：

- `data/uploads/` 邮件上传附件
- `data/history/` 历史邮件原始缓存
- `data/history/attachments/` 历史邮件附件和内嵌图片
- `data/logs/` 日志
- `data/config/` 本地配置和密钥文件

## 历史邮件同步

添加邮箱时可选择“同步历史邮件”。

如果选择：

1. 后端会创建后台同步任务
2. 按文件夹和分页慢慢拉取历史邮件
3. 自动下载附件和内嵌图片
4. 将结果写入数据库和本地缓存

同步页面支持：

- 查看每个邮箱的同步进度
- 手动刷新
- 暂停
- 继续
- 查询当前状态

继续同步是从上次断点继续，不会从头重跑。

## 登录失败后是否还能看历史邮件

如果某些历史邮件已经同步并缓存完成：

- 邮件摘要仍可从本地缓存查看
- 已缓存的正文、附件、图片仍可访问

如果某封邮件还没有完成同步，或者需要实时向远程邮箱再取一次，那登录失效时这部分内容可能无法继续拉取，直到账号重新可用。

## 管理员与普通用户

### 管理员

管理员可以：

- 登录系统
- 创建用户
- 重置用户密码
- 启用/禁用用户
- 使用全部邮件功能

管理员不能查看任何用户的原密码。

### 普通用户

普通用户可以：

- 登录系统
- 修改自己的密码
- 管理自己的邮箱账号和邮件数据

每个用户的数据彼此隔离。

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

- API 文档：[doc/API接口文档.md](D:/Job Files/Idea Projects/Personal/FlyMail/doc/API接口文档.md:1)
- 重构设计：[doc/2026-06-30-docker-multi-user-refactor-design.md](D:/Job Files/Idea Projects/Personal/FlyMail/doc/2026-06-30-docker-multi-user-refactor-design.md:1)

## 许可证

[GPL-3.0](LICENSE)
