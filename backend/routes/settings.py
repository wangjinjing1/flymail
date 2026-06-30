"""设置管理路由

处理应用设置的查询与更新（Gmail/Outlook OAuth2 凭据等），
以及 OAuth 配置诊断接口。
"""
import os

from fastapi import APIRouter, Request
from deps import get_uid
from db import get_accounts, get_history_sync_job, list_history_sync_jobs
from services.history_sync import pause_history_sync, resume_history_sync, start_history_sync

from services.settings import async_load_settings, async_save_settings
from schemas import (
    OAuthDiagnosticResponse,
    SettingsResponse,
    SettingsUpdateRequest,
    SettingsUpdateResponse,
    UnifiedSettingsRequest,
)

router = APIRouter(tags=["设置"])

# 日志目录（与 main.py 保持一致，用于 OAuth 诊断输出）
LOG_DIR = os.environ.get(
    "FLYMAIL_DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"),
)


# ==================== 辅助函数 ====================


def sync_gmail_config(settings: dict):
    """将设置同步到 Gmail Provider 的运行时配置"""
    from providers.gmail import config as gmail_config
    gmail_config.GMAIL_CLIENT_ID = settings.get("gmail_client_id", "")
    gmail_config.GMAIL_CLIENT_SECRET = settings.get("gmail_client_secret", "")
    # 只有 settings 中有 redirect_uri 时才更新（避免用空值覆盖）
    redirect_uri = settings.get("gmail_redirect_uri", "")
    if redirect_uri:
        gmail_config.GMAIL_REDIRECT_URI = redirect_uri


def sync_outlook_config(settings: dict):
    """将设置同步到 Microsoft/Outlook Provider 的运行时配置"""
    from providers.outlook import config as outlook_config
    outlook_config.OUTLOOK_CLIENT_ID = settings.get("outlook_client_id", "")
    outlook_config.OUTLOOK_CLIENT_SECRET = settings.get("outlook_client_secret", "")
    # 只有 settings 中有 redirect_uri 时才更新（避免用空值覆盖）
    redirect_uri = settings.get("outlook_redirect_uri", "")
    if redirect_uri:
        outlook_config.OUTLOOK_REDIRECT_URI = redirect_uri


# ==================== 设置接口 ====================


@router.get("/api/settings", response_model=SettingsResponse, summary="获取应用设置")
async def get_settings():
    """获取当前保存的 Gmail/Outlook OAuth2 凭据等设置。

    client_secret 会脱敏处理，只显示首尾各4位，中间用星号替代。
    """
    settings = await async_load_settings()
    secret = settings.get("gmail_client_secret", "")
    if secret and len(secret) > 8:
        masked_secret = secret[:4] + "*" * (len(secret) - 8) + secret[-4:]
    else:
        masked_secret = secret
    outlook_secret = settings.get("outlook_client_secret", "")
    if outlook_secret and len(outlook_secret) > 8:
        masked_outlook_secret = outlook_secret[:4] + "*" * (len(outlook_secret) - 8) + outlook_secret[-4:]
    else:
        masked_outlook_secret = outlook_secret
    return {
        "gmail_client_id": settings.get("gmail_client_id", ""),
        "gmail_client_secret": masked_secret if secret else "",
        "gmail_redirect_uri": settings.get("gmail_redirect_uri", ""),
        "has_credentials": bool(settings.get("gmail_client_id")) and bool(settings.get("gmail_client_secret")),
        "outlook_client_id": settings.get("outlook_client_id", ""),
        "outlook_client_secret": masked_outlook_secret if outlook_secret else "",
        "outlook_redirect_uri": settings.get("outlook_redirect_uri", ""),
        "has_outlook_credentials": bool(settings.get("outlook_client_id")) and bool(settings.get("outlook_client_secret")),
    }


@router.put("/api/settings", response_model=SettingsUpdateResponse, summary="更新应用设置")
async def update_settings(body: SettingsUpdateRequest):
    """更新 Gmail/Outlook OAuth2 凭据等设置。

    - client_secret 为空或包含星号（脱敏值）时不会覆盖已有密钥
    - 保存后自动同步到 Gmail/Outlook Provider 的运行时配置
    """
    # 转为 dict，过滤掉 None 字段（未传入的字段不覆盖）
    update_data = body.model_dump(exclude_none=True)

    # client_secret 为空或包含星号（脱敏值）时不会覆盖已有密钥
    secret_in_body = update_data.get("gmail_client_secret", "")
    if not secret_in_body or "*" in str(secret_in_body):
        update_data.pop("gmail_client_secret", None)

    outlook_secret_in_body = update_data.get("outlook_client_secret", "")
    if not outlook_secret_in_body or "*" in str(outlook_secret_in_body):
        update_data.pop("outlook_client_secret", None)

    saved = await async_save_settings(update_data)
    sync_gmail_config(saved)
    sync_outlook_config(saved)

    return {"success": True, "message": "设置已保存"}


@router.get("/api/settings/oauth-diagnostic", response_model=OAuthDiagnosticResponse, summary="OAuth 诊断")
async def oauth_diagnostic():
    """诊断 Gmail OAuth2 配置状态，帮助排查授权问题。

    返回运行时的 OAuth 配置（脱敏），以及持久化存储的配置状态对比。
    不暴露完整密钥。
    """
    settings = await async_load_settings()
    from providers.gmail import config as gmail_config

    runtime_client_id = gmail_config.GMAIL_CLIENT_ID
    runtime_client_secret = gmail_config.GMAIL_CLIENT_SECRET
    runtime_redirect_uri = gmail_config.GMAIL_REDIRECT_URI

    stored_client_id = settings.get("gmail_client_id", "")
    stored_client_secret = settings.get("gmail_client_secret", "")
    stored_redirect_uri = settings.get("gmail_redirect_uri", "")

    issues = []
    if not runtime_client_id:
        issues.append("运行时 client_id 为空 - 请在设置页面配置客户端 ID")
    if not runtime_client_secret:
        issues.append("运行时 client_secret 为空 - 请在设置页面配置客户端密钥并保存")
    if not stored_client_id:
        issues.append("settings.json 中 client_id 为空")
    if not stored_client_secret:
        issues.append("settings.json 中 client_secret 为空 - 密钥可能未保存成功")
    if not runtime_redirect_uri:
        issues.append("运行时 redirect_uri 为空 - 请先在设置页面保存设置（系统会自动生成回调地址）")
    if runtime_redirect_uri and runtime_redirect_uri.startswith("http://localhost"):
        issues.append(f"运行时 redirect_uri 为 localhost 默认值({runtime_redirect_uri})，在飞牛环境中不正确")

    return {
        "status": "有问题" if issues else "正常",
        "issues": issues,
        "runtime": {
            "client_id": (runtime_client_id[:10] + "..." + runtime_client_id[-6:]) if runtime_client_id and len(runtime_client_id) > 16 else (runtime_client_id or "空"),
            "client_secret": ("已配置(" + str(len(runtime_client_secret)) + "字符)") if runtime_client_secret else "空",
            "redirect_uri": runtime_redirect_uri or "空",
        },
        "stored": {
            "client_id": (stored_client_id[:10] + "..." + stored_client_id[-6:]) if stored_client_id and len(stored_client_id) > 16 else (stored_client_id or "空"),
            "client_secret": ("已配置(" + str(len(stored_client_secret)) + "字符)") if stored_client_secret else "空",
            "redirect_uri": stored_redirect_uri or "空",
        },
        "log_dir": LOG_DIR,
        "tip": "如果 client_secret 显示为空，请在设置页面重新输入密钥并点击保存",
    }


# ==================== 聚合收件箱设置 ====================


@router.get("/api/settings/unified", summary="获取聚合收件箱设置")
async def get_unified_settings(request: Request):
    """获取聚合收件箱的设置：用户选择要聚合的邮箱账号列表

    修复 D1：unified_account_ids 改为按 user_uid 存储在 user_settings 表，避免多用户互相覆盖
    """
    from db import get_accounts, get_user_settings
    from deps import get_uid

    uid = await get_uid(request)
    accounts = await get_accounts(uid)

    # 从用户级配置表读取（D1 修复）
    user_settings = await get_user_settings(uid, ["unified_account_ids"])
    unified_ids = user_settings.get("unified_account_ids", [])

    return {
        "account_ids": unified_ids,
        "accounts": [
            {
                "id": a.id,
                "email": a.email,
                "provider": a.provider,
                "selected": a.id in unified_ids,
            }
            for a in accounts
        ],
    }


@router.put("/api/settings/unified", summary="保存聚合收件箱设置")
async def save_unified_settings(request: Request, body: UnifiedSettingsRequest):
    """保存聚合收件箱的账号ID列表

    修复 D1：unified_account_ids 改为按 user_uid 存储在 user_settings 表，避免多用户互相覆盖
    """
    from db import set_user_settings
    from deps import get_uid

    uid = await get_uid(request)
    account_ids = body.account_ids

    # 写入用户级配置表（D1 修复）
    await set_user_settings(uid, {"unified_account_ids": account_ids})

    return {"success": True}


@router.get("/api/history-sync/jobs", summary="获取历史邮件同步任务")
async def get_history_sync_jobs(request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    jobs = await list_history_sync_jobs(uid)
    latest_by_account: dict[str, dict] = {}
    for job in jobs:
        if job["account_id"] not in latest_by_account:
            latest_by_account[job["account_id"]] = job

    items = []
    for account in accounts:
        job = latest_by_account.get(account.id)
        items.append(
            {
                "account_id": account.id,
                "email": account.email,
                "provider": account.provider,
                "status": job.get("status", "idle") if job else "idle",
                "job": job,
            }
        )
    return {"jobs": items}


@router.get("/api/history-sync/jobs/{account_id}", summary="查询单个历史邮件同步任务")
async def get_history_sync_job_detail(account_id: str, request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    account = next((item for item in accounts if item.id == account_id), None)
    if not account:
        return {"job": None}
    job = await get_history_sync_job(account_id)
    return {
        "job": job,
        "account": {
            "id": account.id,
            "email": account.email,
            "provider": account.provider,
        },
    }


@router.post("/api/history-sync/jobs/{account_id}/start", summary="开始历史邮件同步")
async def start_history_sync_job(account_id: str, request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    account = next((item for item in accounts if item.id == account_id), None)
    if not account:
        return {"success": False, "message": "account_not_found"}
    started = await start_history_sync(account_id, reset=True)
    return {"success": started}


@router.post("/api/history-sync/jobs/{account_id}/pause", summary="暂停历史邮件同步")
async def pause_history_sync_job(account_id: str, request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    if not any(item.id == account_id for item in accounts):
        return {"success": False, "message": "account_not_found"}
    paused = await pause_history_sync(account_id)
    return {"success": paused}


@router.post("/api/history-sync/jobs/{account_id}/resume", summary="继续历史邮件同步")
async def resume_history_sync_job(account_id: str, request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    if not any(item.id == account_id for item in accounts):
        return {"success": False, "message": "account_not_found"}
    resumed = await resume_history_sync(account_id)
    return {"success": resumed}
