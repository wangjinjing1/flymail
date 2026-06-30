"""
飞邮 (FlyMail) 本地开发测试脚本
在 Windows 上直接运行，无需 Unix Socket，使用 HTTP 端口方便调试

使用方式:
    cd backend
    pip install -r requirements.txt
    python dev.py

启动后访问:
    http://localhost:8080/api/health
    http://localhost:8080/docs  (Swagger UI)
    http://localhost:51010/api/auth/callback  (OAuth 回调端口)
"""
import asyncio
import os
import sys
import uvicorn

# 设置本地开发环境变量
os.environ["FLYMAIL_ENV"] = "development"
os.environ["FLYMAIL_DATA_DIR"] = os.path.join(os.path.dirname(__file__), "data")

# 确保 data 目录存在
data_dir = os.environ["FLYMAIL_DATA_DIR"]
os.makedirs(data_dir, exist_ok=True)

# 将 backend 目录加入 Python 路径
sys.path.insert(0, os.path.dirname(__file__))

from main import app, oauth_callback_app


async def run_dev_servers(app_port: int, oauth_port: int):
    from uvicorn import Config, Server

    app_server = Server(Config(app, host="0.0.0.0", port=app_port, log_level="warning"))
    oauth_server = Server(Config(oauth_callback_app, host="0.0.0.0", port=oauth_port, log_level="warning"))
    await asyncio.gather(app_server.serve(), oauth_server.serve())


if __name__ == "__main__":
    port = int(os.environ.get("FLYMAIL_PORT", "8080"))
    oauth_port = int(os.environ.get("OAUTH_PORT", "51010"))
    from utils.logger import get_logger
    logger = get_logger("dev")
    logger.info("飞邮 FlyMail 本地开发服务器")
    logger.info("地址: http://localhost:%d", port)
    logger.info("API:  http://localhost:%d/api/health", port)
    logger.info("文档: http://localhost:%d/docs", port)
    logger.info("OAuth回调: http://localhost:%d/api/auth/callback", oauth_port)
    try:
        asyncio.run(run_dev_servers(port, oauth_port))
    except KeyboardInterrupt:
        logger.info("服务器已停止")
