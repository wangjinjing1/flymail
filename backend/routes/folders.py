"""文件夹管理路由

处理邮箱文件夹列表查询和文件夹邮件计数（总数/未读数）。
通过 IMAP 协议实时获取，支持 Outlook 连接异常时自动重试。

辅助函数（_with_outlook_retry 等）统一从 routes/_helpers.py 导入，
避免与 routes/messages.py 重复定义。
"""
from fastapi import APIRouter, Request, Query

from db import get_accounts
from deps import get_uid
from providers.factory import ProviderFactory
from schemas import FolderCountsResponse, FolderResponse
from services.token import ensure_token as _ensure_gmail_token
from utils.logger import get_logger

# 从 _helpers 复用共享辅助函数和常量
from routes._helpers import (
    _OUTLOOK_RECONNECTING_MSG,
    _find_account_or_error,
    _is_outlook_connection_error,
    _notify_if_permanent_token_error,
    _safe_disconnect,
    _with_outlook_retry,
)

logger = get_logger("routes.folders")

router = APIRouter(tags=["文件夹"])


# ==================== 文件夹接口 ====================


@router.get("/api/folders", response_model=FolderResponse, summary="获取文件夹列表")
async def list_folders(
    request: Request,
    account_id: str = Query(default="", description="指定账号ID，为空则使用第一个账号"),
):
    """获取邮箱的文件夹列表（如收件箱、已发送、草稿等）"""
    uid = await get_uid(request)
    accounts = await get_accounts(uid)

    if not accounts:
        return {"folders": []}

    account, _ = _find_account_or_error(accounts, account_id)

    try:
        credentials = await _ensure_gmail_token(account)

        # Outlook IMAP 可能短暂不可用，增加重试
        async def _fetch_folders():
            cred = await _ensure_gmail_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(cred)
            try:
                return await receiver.fetch_folders()
            finally:
                await _safe_disconnect(receiver)

        folders = await _with_outlook_retry(account, _fetch_folders)
        return {"folders": [f.model_dump() for f in folders], "account_id": account.id}
    except Exception as e:
        logger.error("获取文件夹失败: %s", e)
        error_msg = str(e)
        await _notify_if_permanent_token_error(e, account, uid)
        # Outlook 连接异常时返回结构化错误，前端可展示"正在重新连接邮箱"
        if _is_outlook_connection_error(account, error_msg):
            return {"folders": [], "error": _OUTLOOK_RECONNECTING_MSG, "reconnecting": True}
        return {"folders": [], "error": error_msg}


@router.get("/api/folder-counts", response_model=FolderCountsResponse, summary="获取文件夹邮件计数")
async def get_folder_counts(
    request: Request,
    account_id: str = Query(default="", description="指定账号ID，为空则使用第一个账号"),
):
    """获取所有文件夹的邮件计数（总数和未读数），使用 IMAP STATUS 命令"""
    uid = await get_uid(request)
    accounts = await get_accounts(uid)

    if not accounts:
        return {"counts": {}}

    account, _ = _find_account_or_error(accounts, account_id)

    try:
        # Microsoft IMAP 可能短暂不可用，增加重试
        async def _fetch_counts():
            credentials = await _ensure_gmail_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(credentials)
            try:
                folders = await receiver.fetch_folders()
                folder_paths = [f.path for f in folders]
                return await receiver.fetch_folder_counts(folder_paths)
            finally:
                await _safe_disconnect(receiver)

        counts = await _with_outlook_retry(account, _fetch_counts)
        return {"counts": counts, "account_id": account.id}
    except Exception as e:
        logger.error("获取文件夹计数失败: %s", e)
        error_msg = str(e)
        await _notify_if_permanent_token_error(e, account, uid)
        if _is_outlook_connection_error(account, error_msg):
            return {"counts": {}, "error": _OUTLOOK_RECONNECTING_MSG, "reconnecting": True}
        return {"counts": {}, "error": error_msg}
