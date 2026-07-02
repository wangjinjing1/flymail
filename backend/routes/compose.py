"""写信与定时发送路由

处理邮件发送相关的所有 API 端点，包括：
- 定时发送任务列表与取消
- 发送邮件
- 写邮件统一入口（发送/草稿/定时发送）
"""
import asyncio
import uuid
from datetime import datetime
from services.scheduler import LOCAL_TIMEZONE

from fastapi import APIRouter, Request, Body

from errors import AppError

from db import get_accounts
from deps import get_uid
from models import Account
from providers.factory import ProviderFactory
from services.token import ensure_token as _ensure_gmail_token
from services.sync import sync_service
from services.mail_cache import sync_folder_to_cache
from services.outgoing_mail import ensure_sent_message_cached, find_special_folder
from utils.logger import get_logger
from schemas import (
    ComposeMessageRequest,
    ComposeMessageResponse,
    ScheduledMessagesResponse,
    SendMessageRequest,
    SendMessageResponse,
)

# 从 _helpers 复用共享辅助函数，避免重复代码
from routes._helpers import (
    _find_account_or_error,
    _safe_disconnect,
)
# ==================== 日志 ====================

logger = get_logger("routes.compose")

# ==================== 路由 ====================

router = APIRouter(tags=["写信"])


async def _cache_sent_message_after_send(
    account: Account,
    user_uid: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body_html: str,
    attachments: list[str],
    in_reply_to: str | None,
) -> None:
    try:
        await ensure_sent_message_cached(
            account=account,
            user_uid=user_uid,
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body_html=body_html,
            attachments=attachments,
            in_reply_to=in_reply_to,
        )
    except Exception as exc:
        logger.warning("发送成功后缓存已发送邮件失败: %s", exc)



# ==================== 定时发送接口 ====================


@router.get("/api/messages/scheduled", response_model=ScheduledMessagesResponse, summary="获取定时发送列表")
async def list_scheduled_messages(request: Request):
    """获取当前用户的待执行定时发送任务"""
    user_uid = await get_uid(request)
    from services.scheduler import get_scheduled_jobs
    # 安全修复 S4：按 user_uid 过滤，防止跨用户查看
    jobs = get_scheduled_jobs(user_uid)
    return {"jobs": jobs}


@router.delete("/api/messages/scheduled/{job_id}", summary="取消定时发送")
async def cancel_scheduled_message(request: Request, job_id: str):
    """取消指定的定时发送任务"""
    user_uid = await get_uid(request)
    from services.scheduler import cancel_scheduled_email
    # 安全修复 S4：传入 user_uid 校验归属，防止跨用户取消
    ok = cancel_scheduled_email(job_id, user_uid)
    if not ok:
        raise AppError(404, "任务不存在或无权操作")
    return {"success": True}


# ==================== 发送与写信接口 ====================


@router.post("/api/messages/send", response_model=SendMessageResponse, summary="发送邮件")
async def send_message(request: Request, body: SendMessageRequest = Body(description="发送邮件请求")):
    """发送邮件，使用第一个账号的SMTP服务

    当前设计限制：仅使用账号列表中的第一个账号发送，未来需支持用户选择发件账号。"""
    user_uid = await get_uid(request)
    accounts = await get_accounts(user_uid)

    if not accounts:
        raise AppError(400, "没有可用的邮箱账号")

    account = accounts[0]
    try:
        credentials = await _ensure_gmail_token(account)

        sender = ProviderFactory.get_sender(account.provider)
        await sender.connect(credentials)
        try:
            result = await sender.send_message(
                to=body.to,
                subject=body.subject,
                body_html=body.content if body.html else body.content.replace("\n", "<br>"),
                body_text="" if body.html else body.content,
            )
        finally:
            try:
                await sender.disconnect()
            except Exception as e:
                logger.debug("断开发送连接失败: %s", e)

        if result.success:
            return {"success": True, "message": "邮件发送成功"}
        else:
            raise AppError(500, result.error)
    except Exception as e:
        logger.error("发送邮件失败: %s", e)
        raise AppError(500, str(e))


@router.post("/api/messages/compose", response_model=ComposeMessageResponse, summary="写邮件（发送/草稿/定时发送）")
async def compose_message(request: Request, body: ComposeMessageRequest):
    """写邮件统一入口，支持三种操作：
    - send: 立即发送
    - draft: 保存到草稿箱（IMAP APPEND）
    - schedule: 定时发送（APScheduler）
    """
    user_uid = await get_uid(request)
    accounts = await get_accounts(user_uid)
    if not accounts:
        raise AppError(400, "没有可用的邮箱账号")

    # 查找指定账号或使用第一个账号
    account, _ = _find_account_or_error(accounts, body.account_id)
    if not account:
        account = accounts[0]

    # ---- 保存草稿 ----
    if body.action == "draft":
        try:
            drafts_folder = await find_special_folder(account, "drafts") or "Drafts"
            credentials = await _ensure_gmail_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(credentials)
            try:
                from services.draft import save_draft_to_imap
                ok = await save_draft_to_imap(
                    receiver, account.email, account.email,
                    body.to, body.cc, body.bcc, body.subject, body.body_html,
                    folder=drafts_folder,
                )
                if ok:
                    # 草稿保存成功后，主动同步草稿箱缓存
                    try:
                        if drafts_folder:
                            await sync_folder_to_cache(account, drafts_folder)
                            await sync_service.refresh_clients(account.id, drafts_folder, user_uid=user_uid)
                    except Exception as sync_err:
                        logger.warning("保存草稿后同步草稿箱失败: %s", sync_err)
                    return {"success": True, "message": "草稿保存成功"}
                else:
                    raise AppError(500, "草稿保存失败")
            finally:
                await _safe_disconnect(receiver)
        except Exception as e:
            logger.error("保存草稿失败: %s", e)
            raise AppError(500, str(e))

    # ---- 定时发送 ----
    if body.action == "schedule":
        if not body.schedule_time:
            raise AppError(400, "定时发送需要指定 schedule_time")
        try:
            from services.scheduler import schedule_email
            run_time = datetime.fromisoformat(body.schedule_time)
            if run_time.tzinfo is None:
                run_time = run_time.replace(tzinfo=LOCAL_TIMEZONE)
            job_id = f"schedule_{user_uid}_{uuid.uuid4().hex[:8]}"
            schedule_email(
                job_id=job_id,
                user_uid=user_uid,
                account_id=account.id,
                to=body.to,
                cc=body.cc,
                bcc=body.bcc,
                subject=body.subject,
                body_html=body.body_html,
                attachment_paths=body.attachments,
                in_reply_to=body.in_reply_to,
                run_time=run_time,
                provider=account.provider,
                email=account.email,
            )
            return {"success": True, "message": "定时发送已设置", "job_id": job_id}
        except Exception as e:
            logger.error("设置定时发送失败: %s", e)
            raise AppError(500, str(e))

    # ---- 立即发送 ----
    try:
        credentials = await _ensure_gmail_token(account)
        sender = ProviderFactory.get_sender(account.provider)
        await sender.connect(credentials)
        try:
            result = await sender.send_message(
                to=body.to,
                subject=body.subject,
                body_html=body.body_html,
                body_text="",
                cc=body.cc or None,
                bcc=body.bcc or None,
                attachments=body.attachments or None,
                in_reply_to=body.in_reply_to,
            )
        finally:
            try:
                await sender.disconnect()
            except Exception as e:
                logger.debug("断开发送连接失败: %s", e)

        if result.success:
            asyncio.create_task(_cache_sent_message_after_send(
                account=account,
                user_uid=user_uid,
                to=body.to,
                cc=body.cc or [],
                bcc=body.bcc or [],
                subject=body.subject,
                body_html=body.body_html,
                attachments=body.attachments or [],
                in_reply_to=body.in_reply_to,
            ))
            return {"success": True, "message": "发送成功", "sent_folder": ""}
        else:
            raise AppError(500, result.error)
    except Exception as e:
        logger.error("发送邮件失败: %s", e)
        raise AppError(500, str(e))
