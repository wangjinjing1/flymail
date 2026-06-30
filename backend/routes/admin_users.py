import time
import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from db import create_user, get_user_by_id, get_user_by_username, list_users, update_user_password, update_user_status
from deps import require_admin
from errors import AppError
from models import User
from services.security import hash_password

router = APIRouter(prefix="/api/admin/users", tags=["管理员"])


class CreateUserRequest(BaseModel):
    username: str = Field(description="用户名")
    password: str = Field(description="初始密码")
    role: str = Field(default="user", description="角色")


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(description="新密码")


@router.get("")
async def get_users(request: Request):
    await require_admin(request)
    users = await list_users()
    return {
        "users": [
            {
                "id": user.id,
                "username": user.username,
                "role": user.role,
                "status": user.status,
                "created_at": user.created_at,
                "updated_at": user.updated_at,
            }
            for user in users
        ]
    }


@router.post("")
async def add_user(request: Request, body: CreateUserRequest):
    await require_admin(request)
    username = body.username.strip()
    password = body.password.strip()
    role = (body.role or "user").strip().lower()
    if len(username) < 3:
        raise AppError(400, "用户名至少 3 位")
    if len(password) < 6:
        raise AppError(400, "密码至少 6 位")
    if role not in {"admin", "user"}:
        raise AppError(400, "角色不合法")
    if await get_user_by_username(username):
        raise AppError(400, "用户名已存在")
    now = time.time()
    user = User(
        id=str(uuid.uuid4()),
        username=username,
        password_hash=hash_password(password),
        role=role,
        status="active",
        created_at=now,
        updated_at=now,
    )
    await create_user(user)
    return {"success": True}


@router.post("/{user_id}/reset-password")
async def reset_password(request: Request, user_id: str, body: ResetPasswordRequest):
    admin = await require_admin(request)
    target = await get_user_by_id(user_id)
    if not target:
        raise AppError(404, "用户不存在")
    if target.id == admin.id:
        raise AppError(400, "请使用修改密码功能更新自己的密码")
    if len(body.new_password.strip()) < 6:
        raise AppError(400, "密码至少 6 位")
    await update_user_password(target.id, hash_password(body.new_password.strip()))
    return {"success": True}


@router.post("/{user_id}/toggle-status")
async def toggle_user_status(request: Request, user_id: str):
    admin = await require_admin(request)
    target = await get_user_by_id(user_id)
    if not target:
        raise AppError(404, "用户不存在")
    if target.id == admin.id:
        raise AppError(400, "不能禁用当前管理员")
    new_status = "disabled" if target.status == "active" else "active"
    await update_user_status(target.id, new_status)
    return {"success": True, "status": new_status}
