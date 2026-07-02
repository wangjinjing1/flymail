import asyncio
import io
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from config import GATEWAY_PREFIX
from data_paths import BASE_DATA_DIR, LOGS_DIR, ensure_data_dirs
from db import init_db
from deps import get_current_user
from errors import AppError, app_error_handler
from routes.accounts import router as accounts_router
from routes.admin_users import router as admin_users_router
from routes.auth import oauth_callback_app, router as oauth_router
from routes.compose import router as compose_router
from routes.folders import router as folders_router
from routes.local_auth import router as local_auth_router
from routes.messages import router as messages_router
from routes.notifications import router as notifications_router
from routes.settings import router as settings_router, sync_gmail_config, sync_outlook_config
from routes.signatures import router as signatures_router
from routes.websocket import router as websocket_router
from schemas import HealthResponse, UserResponse
from services.settings import async_load_settings
from services.sync import sync_service
from services.upload_cleanup import start_upload_cleanup, stop_upload_cleanup
from services.users import ensure_admin_user
from utils.logger import get_logger, setup_logging
from utils.proxy_env import apply_proxy_env
from version import VERSION

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ensure_data_dirs()
apply_proxy_env()
setup_logging(data_dir=str(LOGS_DIR))
logger = get_logger("main")

_ui_dir_env = os.environ.get("FLYMAIL_UI_DIR")
if _ui_dir_env and Path(_ui_dir_env).exists():
    UI_DIR = Path(_ui_dir_env)
else:
    _app_dir = Path(__file__).parent
    UI_DIR = _app_dir / "ui" if (_app_dir / "ui" / "index.html").exists() else _app_dir.parent / "dist" / "ui"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await ensure_admin_user()

    settings = await async_load_settings()
    sync_gmail_config(settings)
    sync_outlook_config(settings)

    asyncio.create_task(sync_service._start_all_idle())
    from services.scheduler import start_scheduler

    start_scheduler()
    start_upload_cleanup()
    logger.info("startup complete data_dir=%s", BASE_DATA_DIR)
    yield
    await sync_service._stop_all_idle()
    await stop_upload_cleanup()
    from services.scheduler import shutdown_scheduler

    shutdown_scheduler()


app = FastAPI(
    title="FlyMail",
    description="FlyMail Docker 多用户邮件系统",
    version=VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(AppError, app_error_handler)


class StripPrefixMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if GATEWAY_PREFIX and scope.get("type") in ("http", "websocket"):
            path = scope.get("path", "")
            if path.startswith(GATEWAY_PREFIX):
                scope["path"] = path[len(GATEWAY_PREFIX):] or "/"
                scope["root_path"] = GATEWAY_PREFIX
        await self.app(scope, receive, send)


app.add_middleware(StripPrefixMiddleware)

app.include_router(local_auth_router)
app.include_router(admin_users_router)
app.include_router(accounts_router)
app.include_router(oauth_router)
app.include_router(compose_router)
app.include_router(folders_router)
app.include_router(messages_router)
app.include_router(notifications_router)
app.include_router(settings_router)
app.include_router(signatures_router)
app.include_router(websocket_router)


@app.get("/api/health", response_model=HealthResponse)
async def health():
    return {"status": "ok", "app": "flymail", "version": VERSION}


@app.get("/api/user", response_model=UserResponse)
async def get_user(request: Request):
    user = await get_current_user(request)
    return {"uid": user.id, "username": user.username}


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    if full_path:
        file_path = UI_DIR / full_path
        if file_path.is_file():
            return FileResponse(str(file_path))
    return FileResponse(str(UI_DIR / "index.html"))


if __name__ == "__main__":
    host = os.environ.get("APP_HOST", "0.0.0.0")
    port = int(os.environ.get("APP_PORT", "8080"))
    logger.info("starting server host=%s port=%d data_dir=%s", host, port, BASE_DATA_DIR)
    uvicorn.run(app, host=host, port=port, log_level="warning")
