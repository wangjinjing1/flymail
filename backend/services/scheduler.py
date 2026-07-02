"""定时发送邮件调度服务

使用 APScheduler 管理定时发送任务，任务持久化到 SQLite 数据库，
应用重启后自动恢复未执行的定时任务。
"""
import asyncio
import logging
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from data_paths import CONFIG_DIR, ensure_data_dirs
from services.outgoing_mail import ensure_sent_message_cached

logger = logging.getLogger("flymail")

# 定时任务存储路径：使用 FLYMAIL_DATA_DIR 环境变量（与主数据库同目录）
# 生产环境由 cmd/main 设置为 TRIM_PKGVAR（飞牛应用数据目录），
# 开发环境默认为当前目录下的 data/
ensure_data_dirs()
_jobstore_db_path = str(CONFIG_DIR / "jobs.sqlite")


def _build_jobstore_url() -> str:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        if database_url.startswith("mysql://"):
            return database_url.replace("mysql://", "mysql+pymysql://", 1)
        return database_url
    return f"sqlite:///{_jobstore_db_path}"


_jobstore_url = _build_jobstore_url()

# 确保 data 目录存在（首次启动时可能不存在）

# 延迟创建 scheduler：避免模块导入时就连接数据库，
# 确保在 start_scheduler() 调用时才初始化
scheduler = None


def _get_scheduler():
    """获取或创建 APScheduler 实例（延迟初始化）"""
    global scheduler
    if scheduler is None:
        scheduler = AsyncIOScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=_jobstore_url)},
            job_defaults={"coalesce": True, "max_instances": 1},
        )
    return scheduler


async def _send_scheduled_email(
    user_uid: str,
    account_id: str,
    to: list,
    cc: list,
    bcc: list,
    subject: str,
    body_html: str,
    attachment_paths: list,
    in_reply_to: str | None,
    provider: str = "",
    email: str = "",
):
    """定时任务回调：执行邮件发送，成功/失败后通知前端"""
    try:
        from db import get_accounts
        from providers.factory import ProviderFactory
        from services.token import ensure_token

        accounts = await get_accounts(user_uid)
        if not accounts:
            logger.error("定时发送失败：未找到账号")
            await _notify_schedule_result(
                user_uid, account_id, provider, email, subject,
                success=False, error_msg="未找到账号"
            )
            return

        account = next((a for a in accounts if a.id == account_id), accounts[0])
        credentials = await ensure_token(account)
        sender = ProviderFactory.get_sender(account.provider)
        await sender.connect(credentials)
        try:
            await sender.send_message(
                to=to,
                subject=subject,
                body_html=body_html,
                cc=cc or None,
                bcc=bcc or None,
                attachments=attachment_paths or None,
                in_reply_to=in_reply_to,
            )
            logger.info("定时邮件发送成功: %s -> %s", account.email, to)

            # 发送成功：通知前端 + 同步已发送文件夹缓存
            await _notify_schedule_result(
                user_uid, account_id, account.provider, account.email, subject,
                success=True
            )
            await ensure_sent_message_cached(
                account=account,
                user_uid=user_uid,
                to=to or [],
                cc=cc or [],
                bcc=bcc or [],
                subject=subject or "",
                body_html=body_html or "",
                attachments=attachment_paths or [],
                in_reply_to=in_reply_to,
            )
        finally:
            await sender.disconnect()
    except Exception as e:
        logger.error("定时邮件发送失败: %s", e)
        # 发送失败：通知前端
        error_msg = str(e)[:100]  # 截断过长的错误信息
        await _notify_schedule_result(
            user_uid, account_id, provider, email, subject,
            success=False, error_msg=error_msg
        )


async def _notify_schedule_result(
    user_uid: str, account_id: str, provider: str, email: str,
    subject: str, success: bool, error_msg: str = "",
):
    """定时发送结果通知：通过 WebSocket 推送 + 持久化到数据库"""
    try:
        from services.sync import sync_service
        await sync_service.notify_schedule_result(
            user_uid=user_uid,
            account_id=account_id,
            provider=provider,
            email=email,
            subject=subject,
            success=success,
            error_msg=error_msg,
        )
    except Exception as e:
        logger.error("定时发送通知推送失败: %s", e)


def schedule_email(
    job_id: str,
    user_uid: str,
    account_id: str,
    to: list,
    cc: list,
    bcc: list,
    subject: str,
    body_html: str,
    attachment_paths: list,
    in_reply_to: str | None,
    run_time,
    provider: str = "",
    email: str = "",
):
    """添加定时发送任务"""
    _get_scheduler().add_job(
        _send_scheduled_email,
        "date",
        run_date=run_time,
        id=job_id,
        replace_existing=True,
        kwargs={
            "user_uid": user_uid,
            "account_id": account_id,
            "to": to,
            "cc": cc,
            "bcc": bcc,
            "subject": subject,
            "body_html": body_html,
            "attachment_paths": attachment_paths,
            "in_reply_to": in_reply_to,
            "provider": provider,
            "email": email,
        },
    )
    logger.info("已添加定时发送任务: %s, 发送时间: %s", job_id, run_time)


def cancel_scheduled_email(job_id: str, user_uid: str = ""):
    """取消定时发送任务。传入 user_uid 时校验归属，不匹配则忽略。"""
    try:
        scheduler = _get_scheduler()
        job = scheduler.get_job(job_id)
        if not job:
            return False
        # 安全修复 S4：校验任务归属，防止跨用户取消
        if user_uid and job.kwargs.get("user_uid", "") != user_uid:
            return False
        scheduler.remove_job(job_id)
        logger.info("已取消定时发送任务: %s", job_id)
        return True
    except Exception:
        return False


def get_scheduled_jobs(user_uid: str = ""):
    """获取待执行的定时发送任务。传入 user_uid 时按用户过滤。"""
    jobs = _get_scheduler().get_jobs()
    result = []
    for job in jobs:
        # 安全修复 S4：按 user_uid 过滤，防止跨用户查看
        if user_uid and job.kwargs.get("user_uid", "") != user_uid:
            continue
        result.append({
            "id": job.id,
            "next_run_time": str(job.next_run_time) if job.next_run_time else None,
            "kwargs": job.kwargs,
        })
    return result


def start_scheduler():
    """启动调度器

    定时发送是辅助功能，启动失败不应阻止主服务运行。
    失败时仅记录错误日志，主服务（邮件收发、IDLE 监听）仍可正常工作。
    """
    s = _get_scheduler()
    if not s.running:
        try:
            s.start()
            logger.info("定时发送调度器已启动，任务存储: %s", _jobstore_url)
        except Exception as e:
            logger.error("定时发送调度器启动失败（定时发送功能不可用，其他功能正常）: %s", e)


def shutdown_scheduler():
    """关闭调度器"""
    s = _get_scheduler()
    if s.running:
        s.shutdown()
        logger.info("定时发送调度器已关闭")
