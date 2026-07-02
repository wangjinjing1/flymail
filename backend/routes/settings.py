"""设置管理路由

处理应用设置的查询与更新（Gmail/Outlook OAuth2 凭据等），
以及 OAuth 配置诊断接口。
"""
import os

from fastapi import APIRouter, Request
from deps import get_uid
from db import (
    get_accounts,
    get_cached_count,
    get_folder_stats,
    get_history_sync_job,
    list_account_folder_counts,
    list_history_sync_jobs,
)
from services.history_sync import (
    is_full_history_sync_active,
    pause_history_sync,
    pause_folder_history_sync,
    refresh_history_sync_job,
    resume_history_sync,
    resume_folder_history_sync,
    retry_history_sync,
    start_clear_cache,
    start_folder_clear_cache,
    start_folder_history_sync,
    start_history_sync,
)

from services.settings import async_load_settings, async_save_settings
from schemas import (
    OAuthDiagnosticResponse,
    SettingsResponse,
    SettingsUpdateRequest,
    SettingsUpdateResponse,
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


FOLDER_PROGRESS_ITEMS = [
    ("INBOX", "收件箱"),
    ("Sent", "已发送"),
    ("Drafts", "草稿箱"),
    ("Junk", "垃圾邮件"),
    ("Trash", "已删除"),
]

ACCOUNT_DISABLED_MESSAGE = "账户已禁用，请先在邮箱管理启用账户"


def _disabled_account_response() -> dict:
    return {"success": False, "message": ACCOUNT_DISABLED_MESSAGE, "code": "account_disabled"}


async def _build_folder_progress(account_id: str) -> list[dict]:
    items = []
    count_by_key = {
        item.get("folder_key"): item
        for item in await list_account_folder_counts(account_id)
    }
    for folder_key, label in FOLDER_PROGRESS_ITEMS:
        folder_stats = await get_folder_stats(account_id, folder_key)
        cached_count = await get_cached_count(account_id, folder_key)
        synced_count = int((count_by_key.get(folder_key.lower()) or {}).get("cached_count", 0) or 0)
        if not synced_count:
            synced_count = cached_count
        sync_job = await get_history_sync_job(account_id, job_type=f"folder_sync:{folder_key}")
        clear_job = await get_history_sync_job(account_id, job_type=f"folder_clear:{folder_key}")
        total_count = max(int(folder_stats.get("total_count", 0) or 0), synced_count, cached_count)
        unread_count = int(folder_stats.get("unread_count", 0) or 0)
        if folder_key == "Sent":
            unread_count = 0
        items.append(
            {
                "folder": folder_key,
                "label": label,
                "cached_count": synced_count,
                "summary_count": cached_count,
                "total_count": total_count,
                "unread_count": unread_count,
                "is_synced": total_count > 0 and synced_count >= total_count,
                "sync_job": sync_job,
                "clear_job": clear_job,
            }
        )
    return items

def _visible_history_job(job: dict | None, folder_progress: list[dict]) -> dict | None:
    if not job:
        return None
    visible = dict(job)
    if visible.get("status") == "completed" and not any(item["cached_count"] for item in folder_progress):
        visible.update(
            {
                "current_folder": "",
                "current_page": 1,
                "current_uid": 0,
                "total_folders": 0,
                "completed_folders": 0,
                "fetched_messages": 0,
                "downloaded_attachments": 0,
                "downloaded_inline_images": 0,
            }
        )
    return visible


def _latest_job_by_account(jobs: list[dict], job_type: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for job in jobs:
        if job.get("job_type") != job_type:
            continue
        if job["account_id"] not in result:
            result[job["account_id"]] = job
    return result


def _valid_cleanup_time(value: str) -> bool:
    try:
        hour_text, minute_text = str(value or "").split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        return 0 <= hour <= 23 and 0 <= minute <= 59
    except (TypeError, ValueError):
        return False


async def _find_user_account(request: Request, account_id: str):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    return next((item for item in accounts if item.id == account_id), None)


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
        "uploads_cleanup_weekday": int(settings.get("uploads_cleanup_weekday", 0) or 0),
        "uploads_cleanup_time": settings.get("uploads_cleanup_time", "02:00"),
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

    if "uploads_cleanup_time" in update_data and not _valid_cleanup_time(update_data["uploads_cleanup_time"]):
        update_data["uploads_cleanup_time"] = "02:00"

    saved = await async_save_settings(update_data)
    sync_gmail_config(saved)
    sync_outlook_config(saved)
    if "uploads_cleanup_weekday" in update_data or "uploads_cleanup_time" in update_data:
        from services.upload_cleanup import restart_upload_cleanup
        await restart_upload_cleanup()

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


@router.get("/api/history-sync/jobs", summary="获取历史邮件同步任务")
async def get_history_sync_jobs(request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    jobs = await list_history_sync_jobs(uid)
    history_by_account = _latest_job_by_account(jobs, "history_sync")
    clear_by_account = _latest_job_by_account(jobs, "clear_cache")

    items = []
    for account in accounts:
        folder_progress = await _build_folder_progress(account.id)
        history_job = _visible_history_job(history_by_account.get(account.id), folder_progress)
        clear_job = clear_by_account.get(account.id)
        items.append(
            {
                "account_id": account.id,
                "email": account.email,
                "provider": account.provider,
                "account_status": account.status,
                "status": history_job.get("status", "idle") if history_job else "idle",
                "job": history_job,
                "clear_job": clear_job,
                "folder_progress": folder_progress,
            }
        )
    return {"jobs": items}


@router.get("/api/history-sync/jobs/{account_id}", summary="查询单个历史邮件同步任务")
async def get_history_sync_job_detail(account_id: str, request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    account = next((item for item in accounts if item.id == account_id), None)
    if not account:
        return {"job": None, "clear_job": None}
    folder_progress = await _build_folder_progress(account.id)
    job = _visible_history_job(await get_history_sync_job(account_id, job_type="history_sync"), folder_progress)
    clear_job = await get_history_sync_job(account_id, job_type="clear_cache")
    return {
        "job": job,
        "clear_job": clear_job,
        "account": {
            "id": account.id,
            "email": account.email,
            "provider": account.provider,
        },
        "folder_progress": folder_progress,
    }


@router.post("/api/history-sync/jobs/{account_id}/refresh", summary="refresh_history_sync_status")
async def refresh_history_sync_job_status(account_id: str, request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    account = next((item for item in accounts if item.id == account_id), None)
    if not account:
        return {"success": False, "message": "account_not_found"}
    folder_progress = await _build_folder_progress(account.id)
    job = _visible_history_job(await refresh_history_sync_job(account_id), folder_progress)
    clear_job = await get_history_sync_job(account_id, job_type="clear_cache")
    return {
        "success": True,
        "job": job,
        "clear_job": clear_job,
        "status": job.get("status", "idle") if job else "idle",
        "folder_progress": folder_progress,
    }


@router.post("/api/history-sync/jobs/{account_id}/start", summary="重置历史邮件同步")
async def start_history_sync_job(account_id: str, request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    account = next((item for item in accounts if item.id == account_id), None)
    if not account:
        return {"success": False, "message": "account_not_found"}
    if account.status == "offline":
        return _disabled_account_response()
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
    account = next((item for item in accounts if item.id == account_id), None)
    if not account:
        return {"success": False, "message": "account_not_found"}
    if account.status == "offline":
        return _disabled_account_response()
    resumed = await resume_history_sync(account_id)
    return {"success": resumed}


@router.post("/api/history-sync/jobs/{account_id}/retry", summary="重试历史邮件同步")
async def retry_history_sync_job(account_id: str, request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    account = next((item for item in accounts if item.id == account_id), None)
    if not account:
        return {"success": False, "message": "account_not_found"}
    if account.status == "offline":
        return _disabled_account_response()
    retried = await retry_history_sync(account_id)
    return {"success": retried}


@router.post("/api/history-sync/jobs/{account_id}/clear", summary="清空历史邮件本地缓存")
async def clear_history_sync_cache_job(account_id: str, request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    if not any(item.id == account_id for item in accounts):
        return {"success": False, "message": "account_not_found"}
    started = await start_clear_cache(account_id)
    return {"success": started}


@router.post("/api/history-sync/jobs/{account_id}/folders/{folder}/start", summary="启动单文件夹历史同步")
async def start_folder_history_sync_job(account_id: str, folder: str, request: Request):
    account = await _find_user_account(request, account_id)
    if not account:
        return {"success": False, "message": "account_not_found"}
    if account.status == "offline":
        return _disabled_account_response()
    started, message = await start_folder_history_sync(account_id, folder, reset=True)
    return {"success": started, "message": message}


@router.post("/api/history-sync/jobs/{account_id}/folders/{folder}/pause", summary="暂停单文件夹历史同步")
async def pause_folder_history_sync_job(account_id: str, folder: str, request: Request):
    account = await _find_user_account(request, account_id)
    if not account:
        return {"success": False, "message": "account_not_found"}
    paused, message = await pause_folder_history_sync(account_id, folder)
    return {"success": paused, "message": message}


@router.post("/api/history-sync/jobs/{account_id}/folders/{folder}/resume", summary="继续单文件夹历史同步")
async def resume_folder_history_sync_job(account_id: str, folder: str, request: Request):
    account = await _find_user_account(request, account_id)
    if not account:
        return {"success": False, "message": "account_not_found"}
    if account.status == "offline":
        return _disabled_account_response()
    resumed, message = await resume_folder_history_sync(account_id, folder)
    return {"success": resumed, "message": message}


@router.post("/api/history-sync/jobs/{account_id}/folders/{folder}/clear", summary="清空单文件夹本地缓存")
async def clear_folder_history_sync_cache_job(account_id: str, folder: str, request: Request):
    account = await _find_user_account(request, account_id)
    if not account:
        return {"success": False, "message": "account_not_found"}
    started, message = await start_folder_clear_cache(account_id, folder)
    return {"success": started, "message": message}
