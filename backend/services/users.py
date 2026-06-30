import os
import time
import uuid

from db import create_user, get_user_by_username
from models import User
from services.security import hash_password
from utils.logger import get_logger

logger = get_logger("users")


async def ensure_admin_user() -> None:
    username = os.environ.get("FLYMAIL_ADMIN_USERNAME", "").strip()
    password = os.environ.get("FLYMAIL_ADMIN_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError("FLYMAIL_ADMIN_USERNAME and FLYMAIL_ADMIN_PASSWORD are required")

    existing = await get_user_by_username(username)
    if existing:
        return

    now = time.time()
    user = User(
        id=str(uuid.uuid4()),
        username=username,
        password_hash=hash_password(password),
        role="admin",
        status="active",
        created_at=now,
        updated_at=now,
    )
    await create_user(user)
    logger.info("initialized admin user: %s", username)
