import time

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from db import get_user_by_username, update_user_password
from deps import get_current_user
from errors import AppError
from services.security import clear_session_cookie, set_session_cookie, verify_password, hash_password

router = APIRouter(prefix="/api/auth", tags=["认证"])


class LoginRequest(BaseModel):
    username: str = Field(description="用户名")
    password: str = Field(description="密码")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(description="当前密码")
    new_password: str = Field(description="新密码")


def _user_payload(user) -> dict:
    return {
        "id": user.id,
        "uid": user.id,
        "username": user.username,
        "role": user.role,
        "status": user.status,
    }


@router.post("/login")
async def login(body: LoginRequest, response: Response):
    user = await get_user_by_username(body.username.strip())
    if not user or not verify_password(body.password, user.password_hash):
        raise AppError(401, "用户名或密码错误")
    if user.status != "active":
        raise AppError(403, "用户已被禁用")
    set_session_cookie(response, user.id)
    return {"success": True, "user": _user_payload(user)}


@router.post("/logout")
async def logout(response: Response):
    clear_session_cookie(response)
    return {"success": True}


@router.get("/me")
async def me(request: Request):
    user = await get_current_user(request)
    return _user_payload(user)


@router.post("/change-password")
async def change_password(request: Request, body: ChangePasswordRequest):
    user = await get_current_user(request)
    if not verify_password(body.current_password, user.password_hash):
        raise AppError(400, "当前密码不正确")
    if len(body.new_password.strip()) < 6:
        raise AppError(400, "新密码至少 6 位")
    await update_user_password(user.id, hash_password(body.new_password.strip()))
    return {"success": True, "updated_at": time.time()}
