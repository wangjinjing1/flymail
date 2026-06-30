from fastapi import HTTPException, Path as FastAPIPath, Request

from db import get_accounts, get_user_by_id
from models import Account, User
from services.security import get_session_user_id
from utils.logger import get_logger

logger = get_logger("deps")


async def get_current_user(request: Request) -> User:
    user_id = get_session_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")
    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="登录已失效")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="用户已被禁用")
    return user


async def get_uid(request: Request) -> str:
    user = await get_current_user(request)
    return user.id


async def require_admin(request: Request) -> User:
    user = await get_current_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


async def get_accounts_by_uid(uid: str = None, request: Request = None) -> list[Account]:
    if uid is None and request is not None:
        uid = await get_uid(request)
    return await get_accounts(uid or "")


async def get_account(
    account_id: str = FastAPIPath(..., description="账号 ID"),
    uid: str = None,
    request: Request = None,
) -> Account:
    if uid is None:
        if request is None:
            raise HTTPException(status_code=400, detail="缺少请求上下文")
        uid = await get_uid(request)
    accounts = await get_accounts(uid)
    account = next((a for a in accounts if a.id == account_id), None)
    if not account:
        logger.warning("找不到账号 %s，可用账号: %s", account_id, [a.id for a in accounts])
        raise HTTPException(status_code=404, detail="账号不存在或已被删除")
    return account
