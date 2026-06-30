"""邮件管理路由

处理邮件相关的所有 API 端点，包括：
- 文件夹列表与计数
- 邮件列表（分页、聚合收件箱、强制刷新）
- 邮件详情与预取
- 已读标记（单封、批量）
- 删除（单封、批量）
- 附件下载、上传与删除
"""
import os
import uuid
import time
import asyncio
import json
import hashlib
from pathlib import Path

from data_paths import ATTACHMENTS_DIR, HISTORY_ATTACHMENTS_DIR, HISTORY_RAW_DIR, UPLOADS_DIR, ensure_data_dirs
from fastapi import APIRouter, Request, Query, Path as FastAPIPath, Body, UploadFile, File
from fastapi.responses import FileResponse
from starlette.responses import Response

from errors import AppError

from db import (
    get_accounts,
    get_cached_messages_by_folder,
    get_folder_filter_counts,
    delete_cached_message,
    update_cached_message_read,
    batch_delete_cached_messages,
    batch_update_cached_messages_read,
    get_unified_inbox_messages,
    get_unified_inbox_stats,
    get_unified_inbox_filter_counts,
    get_cached_message_detail,
    upsert_cached_messages,
    get_cached_is_read,
    batch_update_is_read,
    upsert_folder_stats,
    get_folder_stats,
    get_cached_count,
)
from deps import get_uid
from models import Account, CachedMessage
from providers.factory import ProviderFactory
from services.token import ensure_token as _ensure_gmail_token
from services.sync import sync_service
from services.mail_cache import sync_folder_to_cache, sync_missing_messages
from services.idle_manager import idle_manager, poll_manager
from utils.logger import get_logger
from utils.tasks import create_background_task
from schemas import (
    BatchDeleteRequest,
    BatchDeleteResponse,
    BatchMarkReadRequest,
    BatchMarkReadResponse,
    DeleteResponse,
    MessageItem,
    MessageListResponse,
    MessageResponse,
    MarkReadRequest,
    PrefetchMessagesRequest,
    PrefetchMessagesResponse,
    StatusResponse,
    UploadAttachmentResponse,
)
# 从 _helpers 复用共享辅助函数，避免与 routes/folders.py 重复定义
from routes._helpers import (
    _OUTLOOK_RECONNECTING_MSG,
    _find_account_or_error,
    _is_outlook_connection_error,
    _notify_if_permanent_token_error,
    _safe_disconnect,
    _with_outlook_retry,
)

# ==================== 日志与常量 ====================

logger = get_logger("routes.messages")

# 数据目录和附件目录（与 main.py 保持一致）
ensure_data_dirs()
ATTACHMENT_DIR = str(ATTACHMENTS_DIR)

# 记录每个账号+文件夹的上次校验时间，避免频繁校验
_verify_timestamps: dict = {}
_VERIFY_INTERVAL = 60  # 同一文件夹最少间隔60秒校验一次

# 连接缓存：复用已有连接执行操作，避免每次请求都新建连接（TCP+SSL+登录 ~400ms）
# key: account_id, value: {"receiver": receiver, "ts": last_used_timestamp}
_conn_cache: dict = {}
_CONN_CACHE_TTL = 30  # 连接缓存有效期（秒）


async def _get_cached_receiver(account, credentials):
    """获取缓存的 receiver 连接，或创建新连接"""
    import time
    cache_key = account.id
    cached = _conn_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _CONN_CACHE_TTL:
        receiver = cached["receiver"]
        # 验证连接是否仍可用
        try:
            await receiver.noop()
            cached["ts"] = time.time()
            return receiver
        except Exception:
            # 连接已断开，清理并创建新连接
            try:
                await receiver.disconnect()
            except Exception as e:
                logger.debug("清理断开连接失败: %s", e)
            del _conn_cache[cache_key]

    # 创建新连接
    receiver = ProviderFactory.get_receiver(account.provider)
    await receiver.connect(credentials)
    _conn_cache[cache_key] = {"receiver": receiver, "ts": time.time()}
    return receiver


async def _release_cached_receiver(account_id: str):
    """释放缓存的连接（操作完成后不立即断开，保留复用）"""
    # 不断开连接，保留缓存供后续操作复用
    pass


async def _cleanup_conn_cache():
    """清理过期的缓存连接"""
    import time
    now = time.time()
    expired_keys = [k for k, v in _conn_cache.items() if (now - v["ts"]) > _CONN_CACHE_TTL]
    for key in expired_keys:
        cached = _conn_cache.pop(key, None)
        if cached:
            try:
                await cached["receiver"].disconnect()
            except Exception as e:
                logger.debug("清理过期连接失败: %s", e)


# ==================== 路由 ====================

router = APIRouter(tags=["邮件"])


# ==================== 内部工具函数 ====================


def _extract_uid(message_id: str) -> str:
    """从 message_id 中提取纯 UID

    兼容两种格式：
    - 纯数字 UID（如 "2326"）：直接返回
    - 旧格式 account_id_uid（如 "0542f9e0-30d8-4de5-9d0a-b5ddab8d5f1c_2326"）：取下划线后部分
    """
    if "_" in message_id:
        return message_id.rsplit("_", 1)[-1]
    return message_id


def _slugify(value: str) -> str:
    return re.sub(r'[\\\\/:*?"<>|]+', "_", (value or "").strip()).strip(".") or "unknown"


def _history_message_path(account_id: str, folder: str, uid: int) -> Path:
    return Path(HISTORY_RAW_DIR) / account_id / _slugify(folder) / f"{uid}.json"


def _load_history_message(account_id: str, folder: str, uid: int) -> dict | None:
    raw_path = _history_message_path(account_id, folder, uid)
    if not raw_path.exists():
        return None
    try:
        return json.loads(raw_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("读取历史邮件缓存失败: %s", e)
        return None


def _find_history_attachment_file(account_id: str, uid: int, part_number: int) -> Path | None:
    message_dir = Path(HISTORY_ATTACHMENTS_DIR) / account_id / str(uid)
    if not message_dir.exists():
        return None
    matches = sorted(message_dir.glob(f"{part_number}_*"))
    return matches[0] if matches else None


async def _get_trash_folder_dynamic(account: Account) -> str:
    """动态获取已删除文件夹路径（通过文件夹名称匹配）

    不同邮箱的已删除文件夹名不同，且 Modified UTF-7 编码也可能不同，
    因此不能硬编码，需要通过 fetch_folders 获取文件夹列表后按名称匹配。

    Outlook 的已删除文件夹叫 "Deleted Items" 或 "Deleted"，
    QQ/网易叫 "已删除"，Gmail 叫 "[Gmail]/Trash"，需要全部覆盖。
    """
    return await _find_special_folder(account, "trash") or ""


async def _find_special_folder(account: Account, folder_type: str) -> str:
    """查找特殊文件夹的 IMAP 路径（已发送、草稿箱、垃圾邮件、已删除）

    folder_type: sent / drafts / junk / trash
    不同邮箱平台的文件夹路径不同，通过 fetch_folders 获取后按关键词匹配。
    """
    # 各类型文件夹的匹配关键词（IMAP 路径中的英文关键词 + 中文名）
    keywords_map = {
        "sent": {"sent", "sent messages", "sent items", "已发送"},
        "drafts": {"drafts", "draft", "草稿箱"},
        "junk": {"junk", "spam", "junk email", "垃圾邮件"},
        "trash": {"trash", "deleted", "deleted items", "deleted messages", "bin", "已删除"},
    }
    keywords = keywords_map.get(folder_type, set())
    if not keywords:
        return ""

    try:
        credentials = await _ensure_gmail_token(account)
        receiver = ProviderFactory.get_receiver(account.provider)
        await receiver.connect(credentials)
        try:
            folders = await receiver.fetch_folders()
        finally:
            await _safe_disconnect(receiver)

        for f in folders:
            path_lower = f.path.lower().strip()
            name = f.name
            # 精确匹配中文名
            if name in keywords:
                return f.path
            # 模糊匹配路径中的英文关键词
            if any(kw in path_lower for kw in keywords if not any(c > '\u4e00' for c in kw)):
                return f.path
    except Exception as e:
        logger.warning("查找 %s 文件夹失败: provider=%s, error=%s", folder_type, account.provider, e)
    return ""


async def _cache_messages(account: Account, messages: list, folder: str = "INBOX"):
    """将 IMAP 获取的邮件写入缓存（后台异步）

    写入前通过 UID SEARCH UNSEEN 校正 is_read 状态，
    因为 FETCH 响应中 FLAGS 的解析可能不可靠。
    """
    try:
        from services.mail_cache import _messages_to_cached
        # is_read 校正：用 UID SEARCH UNSEEN 确认真实的已读/未读状态
        if messages:
            try:
                credentials = await _ensure_gmail_token(account)
                receiver = ProviderFactory.get_receiver(account.provider)
                await receiver.connect(credentials)
                try:
                    unseen_uids = set(await receiver.fetch_unseen_uids(folder))
                    for m in messages:
                        m.is_read = m.uid not in unseen_uids
                    logger.debug(
                        "_cache_messages is_read 校正: 未读UID=%s",
                        unseen_uids or "无"
                    )
                finally:
                    await _safe_disconnect(receiver)
            except Exception as e:
                logger.warning("_cache_messages is_read 校正失败: %s", e)

        cached = _messages_to_cached(messages, account)
        await upsert_cached_messages(cached)
    except Exception as e:
        logger.error("写入邮件缓存失败: %s", e)


async def _update_folder_stats_safe(account_id: str, folder: str, total: int, unread: int):
    """安全更新 folder_stats（后台异步，不阻塞响应）"""
    try:
        await upsert_folder_stats(account_id, folder, total, unread)
    except Exception as e:
        logger.error("更新文件夹统计失败: %s", e)


async def _sync_folder_background(account: Account, folder: str):
    """后台触发文件夹的完整缓存同步（不阻塞当前请求）

    当 list_messages 发现缓存为空时，先返回当前页数据，
    后台触发 sync_folder_to_cache 做完整同步（最多500封），
    同步完成后通过 WebSocket 通知前端刷新。
    """
    try:
        new_count = await sync_folder_to_cache(account, folder)
        if new_count > 0:
            # 缓存同步不是真正新邮件，只推送刷新信号（不创建通知记录）
            await sync_service.refresh_clients(account.id, folder)
    except Exception as e:
        logger.error("后台同步文件夹 %s 失败: %s", folder, e)


async def _sync_missing_background(account: Account, folder: str):
    """后台补全缓存中缺失的邮件（不阻塞当前请求）

    当 list_messages 检测到缓存行数少于 IMAP 总数时触发，
    对比 IMAP 全量 UID 与缓存 UID，拉取差异部分并通知前端刷新。
    """
    try:
        filled = await sync_missing_messages(account, folder)
        if filled > 0:
            await sync_service.refresh_clients(account.id, folder, user_uid=account.user_uid)
    except Exception as e:
        logger.error("后台补全同步 %s 失败: %s", folder, e)


async def _verify_folder_stats_background(account: Account, folder: str):
    """后台校验 folder_stats 是否与 IMAP 一致（不阻塞当前请求）

    从 IMAP 获取 STATUS，与本地 folder_stats 对比：
    - 如果总数不一致，说明缓存过期（用户在其他客户端删除了邮件等）
    - 触发 sync_folder_to_cache 重新同步，并通知前端刷新
    - 同一文件夹最少间隔60秒校验一次，避免频繁连接 IMAP

    连接复用优化：
    - 优先复用 idle_manager/poll_manager 的已有连接（零开销）
    - 无已有连接时，根据 provider 决定是否创建新连接
    - Outlook 有并发连接限制，无已有连接时跳过（避免踢掉 IDLE 监听）
    """
    # IDLE provider 的 INBOX 已由后台 IDLE 监听，跳过统计校验避免创建额外连接
    if account.provider in ("gmail", "qq") and folder.upper() == "INBOX":
        return

    # 频率控制：同一文件夹60秒内只校验一次
    key = f"{account.id}:{folder}"
    now = time.time()
    last = _verify_timestamps.get(key, 0)
    if now - last < _VERIFY_INTERVAL:
        return
    _verify_timestamps[key] = now

    # 定期清理过期的校验时间戳（超过5分钟未使用的key），防止内存泄漏
    if len(_verify_timestamps) > 100:
        expired_keys = [k for k, v in _verify_timestamps.items() if now - v > 300]
        for k in expired_keys:
            del _verify_timestamps[k]

    try:
        # ========== 路径一：优先复用已有连接（避免新建 TCP+SSL 连接的开销） ==========
        counts = None
        if account.provider in ("gmail", "qq"):
            conn = idle_manager.get_connection(account.id)
            if conn:
                total = await conn.get_folder_count(folder)
                if total >= 0:
                    counts = {"total": total, "unread": None}  # IDLE 连接只能查 total，unread 需额外查询
        elif account.provider == "icloud":
            conn = poll_manager.get_connection(account.id)
            if conn:
                total = await conn.get_folder_count(folder)
                if total >= 0:
                    counts = {"total": total, "unread": None}  # Poll 连接只能查 total，unread 需额外查询

        # 复用连接成功，直接走对比逻辑（不创建新连接，不断开）
        if counts is not None:
            imap_total = counts["total"]
            local_stats = await get_folder_stats(account.id, folder)
            local_total = local_stats["total_count"]
            if imap_total != local_total:
                logger.info("文件夹统计不一致(复用连接): %s, IMAP=%d, 本地=%d, 触发同步",
                            folder, imap_total, local_total)
                await sync_folder_to_cache(account, folder)
                await sync_service.refresh_clients(account.id, folder, user_uid=account.user_uid)
            else:
                # total 一致时，只用本地 unread 兜底更新（复用连接无 unread 数据）
                await upsert_folder_stats(account.id, folder, imap_total,
                                          local_stats.get("unread_count", 0))
                # 检查缓存是否有多余的脏 UID（其他客户端删除但缓存未清理）
                cached_count = await get_cached_count(account.id, folder)
                if cached_count > imap_total:
                    logger.info("缓存有脏数据(复用连接): %s, IMAP=%d, 缓存=%d, 触发清理",
                                folder, imap_total, cached_count)
                    await sync_folder_to_cache(account, folder)
                    await sync_service.refresh_clients(account.id, folder, user_uid=account.user_uid)
                elif 0 < cached_count < imap_total:
                    logger.info("缓存不完整(复用连接): %s, IMAP=%d, 缓存=%d, 触发补全",
                                folder, imap_total, cached_count)
                    filled = await sync_missing_messages(account, folder)
                    if filled > 0:
                        await sync_service.refresh_clients(account.id, folder, user_uid=account.user_uid)
            return

        # ========== 路径二：无已有连接，根据 provider 决定是否创建新连接 ==========
        # Outlook 有严格的并发连接限制，无已有连接时跳过校验，避免影响 IDLE 长连接
        if account.provider == "outlook":
            return

        # 其他 provider（如 netease）：创建新连接，保留旧方式
        credentials = await _ensure_gmail_token(account)
        receiver = ProviderFactory.get_receiver(account.provider)
        await receiver.connect(credentials)
        try:
            # 从 IMAP 获取最新的文件夹统计
            folder_counts = await receiver.fetch_folder_counts([folder])

            if folder not in folder_counts:
                return

            imap_total = folder_counts[folder].get("total", 0)
            imap_unread = folder_counts[folder].get("unread", 0)

            # 与本地 folder_stats 对比
            local_stats = await get_folder_stats(account.id, folder)
            local_total = local_stats["total_count"]

            if imap_total != local_total:
                # 总数不一致，触发完整同步（会更新 folder_stats + 清理过期缓存）
                logger.info("文件夹统计不一致: %s, IMAP=%d, 本地=%d, 触发同步",
                            folder, imap_total, local_total)
                await sync_folder_to_cache(account, folder)
                # 缓存同步不是真正新邮件，只推送刷新信号（不创建通知记录）
                await sync_service.refresh_clients(account.id, folder, user_uid=account.user_uid)
            else:
                # 总数一致，更新 unread_count
                await upsert_folder_stats(account.id, folder, imap_total, imap_unread)

                # 检查缓存是否有多余的脏 UID（其他客户端删除但缓存未清理）
                cached_count = await get_cached_count(account.id, folder)
                if cached_count > imap_total:
                    # 缓存比 IMAP 多，有脏数据需要清理
                    logger.info("缓存有脏数据: %s, IMAP=%d, 缓存=%d, 触发清理",
                                folder, imap_total, cached_count)
                    await sync_folder_to_cache(account, folder)
                    await sync_service.refresh_clients(account.id, folder, user_uid=account.user_uid)
                elif 0 < cached_count < imap_total:
                    logger.info("缓存不完整: %s, IMAP=%d, 缓存=%d, 触发补全",
                                folder, imap_total, cached_count)
                    filled = await sync_missing_messages(account, folder)
                    if filled > 0:
                        await sync_service.refresh_clients(account.id, folder, user_uid=account.user_uid)

                # 未读数不一致时，校正 cached_messages.is_read
                # 场景：用户在其他客户端标记已读，邮件总数不变，但未读数变了
                # 此时 folder_stats.unread_count 已更新（侧边栏正确），但 cached_messages.is_read 还是旧值（列表错误）
                local_unread = local_stats.get("unread_count", 0)
                if imap_unread != local_unread:
                    try:
                        unseen_uids = set(await receiver.fetch_unseen_uids(folder))
                        cached_msgs = await get_cached_messages_by_folder(
                            account.user_uid, account.id, folder, page=1, page_size=10000
                        )
                        if cached_msgs.get("messages"):
                            to_fix = []
                            for msg in cached_msgs["messages"]:
                                should_read = msg["uid"] not in unseen_uids
                                if bool(msg["is_read"]) != should_read:
                                    to_fix.append((msg["uid"], 1 if should_read else 0))
                            if to_fix:
                                await batch_update_is_read(account.id, folder, to_fix)
                                fixed_read = sum(1 for _, v in to_fix if v == 1)
                                logger.info(
                                    "后台校正 is_read: 账号=%s, 文件夹=%s, 修正 %d 封 (→已读 %d, →未读 %d)",
                                    account.email, folder, len(to_fix), fixed_read, len(to_fix) - fixed_read
                                )
                                await sync_service.refresh_clients(account.id, folder, user_uid=account.user_uid)
                    except Exception as e:
                        logger.warning("后台校正 is_read 失败: %s, %s", folder, e)
        finally:
            await _safe_disconnect(receiver)

    except Exception as e:
        logger.debug("后台校验文件夹统计失败: %s, %s", folder, e)


# 文件夹接口（/api/folders、/api/folder-counts）已统一放在 routes/folders.py，
# 此处不再重复定义，避免 FastAPI 注册多个相同路径的处理函数。


# ==================== 聚合收件箱 API ====================


@router.get("/api/messages/unified", response_model=MessageListResponse, summary="获取聚合收件箱邮件列表")
async def list_unified_messages(
    request: Request,
    page: int = Query(default=1, ge=1, description="页码，从1开始"),
    page_size: int = Query(default=40, ge=1, le=100, description="每页数量，最大100"),
    account_filter: str = Query(default="", description="按账号ID筛选，空=全部"),
    read_filter: str = Query(default="", description="已读筛选: unread=仅未读, read=仅已读, 空=全部"),
    attachment_filter: bool = Query(default=False, description="附件筛选: true=仅有附件的邮件"),
):
    """获取聚合收件箱邮件列表

    将用户选择的多个账号的收件箱邮件混排显示，按时间倒序排列。
    数据来源：直接从 cached_messages 表查询，复用现有缓存，不需要额外同步逻辑。
    """
    user_uid = await get_uid(request)
    accounts = await get_accounts(user_uid)

    if not accounts:
        return {"messages": [], "total": 0, "page": page, "page_size": page_size}

    # 从用户级配置表读取要聚合的账号ID列表（D1 修复：按 user_uid 隔离）
    from db import get_user_settings
    user_settings = await get_user_settings(user_uid, ["unified_account_ids"])
    unified_ids = user_settings.get("unified_account_ids", [])
    # 未选择聚合邮箱时，返回空列表（前端提示用户添加聚合邮箱）
    if not unified_ids:
        return {"messages": [], "total": 0, "page": page, "page_size": page_size, "no_accounts": True}
    # 只保留存在的账号；如果保存的是旧账号ID，也按未选择处理
    existing_ids = {a.id for a in accounts}
    unified_ids = [aid for aid in unified_ids if aid in existing_ids]
    if not unified_ids:
        return {"messages": [], "total": 0, "page": page, "page_size": page_size, "no_accounts": True}

    # 构建账号ID到账号信息的映射（用于在返回结果中附加邮箱标识）
    account_map = {a.id: a for a in accounts}

    # 从缓存中查询聚合邮件
    result = await get_unified_inbox_messages(
        user_uid=user_uid,
        account_ids=unified_ids,
        page=page,
        page_size=page_size,
        account_filter=account_filter,
        read_filter=read_filter,
        attachment_filter=attachment_filter,
    )

    # 为每封邮件附加账号信息（邮箱地址、平台类型）
    for msg in result["messages"]:
        acc = account_map.get(msg.get("account_id", ""))
        if acc:
            msg["account_email"] = acc.email
            msg["account_provider"] = acc.provider

    # 获取聚合统计（从 folder_stats 汇总，更准确）
    stats = await get_unified_inbox_stats(user_uid, unified_ids)
    # 如果 folder_stats 有数据，用其替换 COUNT 的结果（更准确）
    if stats["total_count"] > 0:
        result["total"] = stats["total_count"]
        result["unread_total"] = stats["unread_count"]

    # 获取各筛选条件的计数（前端用于显示 全部20 未读10 已读10 附件2）
    # 传入 account_filter 使计数跟随账号筛选变化
    result["filter_counts"] = await get_unified_inbox_filter_counts(user_uid, unified_ids, account_filter)

    return result


# ==================== 邮件列表接口 ====================


@router.get("/api/messages", response_model=MessageListResponse, summary="获取邮件列表")
async def list_messages(
    request: Request,
    folder: str = Query(default="INBOX", description="文件夹路径"),
    page: int = Query(default=1, ge=1, description="页码，从1开始"),
    page_size: int = Query(default=40, ge=1, le=100, description="每页数量，最大100"),
    account_id: str = Query(default="", description="指定账号ID，为空则使用第一个账号"),
    read_filter: str = Query(default="", description="已读筛选：unread=仅未读, read=仅已读, 空=全部"),
    attachment_filter: str = Query(default="", description="附件筛选：true=仅有附件"),
):
    """获取指定文件夹的邮件列表，支持分页和筛选

    优化策略：先返回数据库缓存（瞬间），后台增量同步新邮件后通过 WebSocket 通知前端刷新。
    首次访问（缓存为空）时同步等待 IMAP 获取并写入缓存。
    """
    user_uid = await get_uid(request)
    accounts = await get_accounts(user_uid)

    # 惰性清理过期的 IMAP 连接缓存（1% 概率触发，避免每次请求都清理）
    import random
    if random.random() < 0.01:
        create_background_task(_cleanup_conn_cache(), name="cleanup_conn_cache")

    if not accounts:
        return {"messages": [], "total": 0, "page": page, "page_size": page_size}

    account, _ = _find_account_or_error(accounts, account_id)
    has_attachment_filter = attachment_filter.lower() == "true"

    # 1. 先从数据库缓存返回（瞬间）
    cached = await get_cached_messages_by_folder(
        user_uid, account.id, folder, page, page_size,
        read_filter=read_filter, attachment_filter=has_attachment_filter,
    )
    # 获取筛选计数
    filter_counts = await get_folder_filter_counts(user_uid, account.id, folder)
    if cached["messages"]:
        # 缓存有数据，直接返回，保持切换文件夹/分页为毫秒级响应。
        # 新邮件由后台 IMAP IDLE 监听链路同步；这里仅做统计校验，不再主动补拉。
        create_background_task(_verify_folder_stats_background(account, folder), name="verify_folder_stats")

        # 缓存完整性检测：如果缓存行数少于 IMAP 总数，后台触发补全同步
        cached_count = cached.get("cached_count", 0)
        stats_total = cached.get("total", 0)
        if 0 < cached_count < stats_total:
            create_background_task(_sync_missing_background(account, folder), name="sync_missing")

        return {
            "messages": cached["messages"],
            "total": cached["total"],
            "unread_total": cached["unread_total"],
            "page": cached["page"],
            "page_size": cached["page_size"],
            "account_id": account.id,
            "filter_counts": filter_counts,
        }

    # 2. 缓存为空（首次），同步等待 IMAP 获取并写入缓存
    try:
        async def _fetch_messages():
            credentials = await _ensure_gmail_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(credentials)
            try:
                return await receiver.fetch_messages(folder, page, page_size)
            finally:
                await _safe_disconnect(receiver)

        result = await _with_outlook_retry(account, _fetch_messages)

        # 不再调用 _cache_messages（与 _sync_folder_background 重复写入）
        # 缓存写入完全交给 _sync_folder_background，避免重复 IMAP 连接和数据库写入

        # 更新 folder_stats（IMAP 真实总数，解决缓存只存部分邮件时总数不正确的问题）
        # 空文件夹也要记录 0，避免每次刷新都被当成首次同步。
        create_background_task(_update_folder_stats_safe(account.id, folder, result.total, result.unread_total), name="update_folder_stats")
        # 后台触发该文件夹的完整缓存同步（非收件箱也需要缓存全部邮件，否则分页不完整）
        create_background_task(_sync_folder_background(account, folder), name="sync_folder")

        return {
            "messages": [m.model_dump() for m in result.messages],
            "total": result.total,
            "unread_total": result.unread_total,
            "page": result.page,
            "page_size": result.page_size,
            "account_id": account.id,
            "filter_counts": filter_counts,
        }
    except Exception as e:
        logger.error("获取邮件失败: %s", e)
        error_msg = str(e)
        if cached and "not found" not in error_msg.lower():
            if history_cached:
                cached["attachments"] = history_cached.get("attachments", []) or cached.get("attachments", [])
                cached["body_html"] = history_cached.get("body_html") or cached.get("body_html", "")
                cached["body_text"] = history_cached.get("body_text") or cached.get("body_text", "")
            return cached
        await _notify_if_permanent_token_error(e, account, user_uid)
        if _is_outlook_connection_error(account, error_msg):
            return {"messages": [], "total": 0, "page": page, "page_size": page_size,
                    "error": _OUTLOOK_RECONNECTING_MSG, "reconnecting": True}
        return {"messages": [], "total": 0, "page": page, "page_size": page_size, "error": error_msg}


@router.get("/api/messages/refresh", response_model=MessageListResponse, summary="强制从IMAP刷新邮件列表")
async def refresh_messages(
    request: Request,
    folder: str = Query(default="INBOX", description="文件夹路径"),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=40, ge=1, le=100, description="每页数量"),
    account_id: str = Query(default="", description="指定账号ID"),
):
    """强制从 IMAP 服务器获取邮件列表并更新缓存

    当 IDLE 检测到新邮件但缓存同步失败时，前端可调用此接口强制刷新。
    也可以用于用户手动下拉刷新。
    """
    user_uid = await get_uid(request)
    accounts = await get_accounts(user_uid)

    if not accounts:
        return {"messages": [], "total": 0, "page": page, "page_size": page_size}

    account, _ = _find_account_or_error(accounts, account_id)

    try:
        # Outlook IMAP 可能短暂不可用，增加重试
        async def _refresh_from_imap():
            cred = await _ensure_gmail_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(cred)
            try:
                result = await receiver.fetch_messages(folder, page, page_size)

                # 同步校正 is_read：用户主动下拉刷新，可以接受稍慢
                # FLAGS 解析不可靠，用 UID SEARCH UNSEEN 作为权威来源
                if result.messages:
                    try:
                        unseen_uids = set(await receiver.fetch_unseen_uids(folder))
                        for m in result.messages:
                            m.is_read = m.uid not in unseen_uids
                    except Exception as e:
                        logger.warning("refresh_messages is_read 校正失败: %s", e)
                return result
            finally:
                await _safe_disconnect(receiver)

        result = await _with_outlook_retry(account, _refresh_from_imap)

        # 更新缓存（is_read 已校正，_cache_messages 内部还会再校正一次，幂等）
        if result.messages:
            create_background_task(_cache_messages(account, result.messages, folder), name="cache_messages")

        # 更新 folder_stats
        if result.total > 0:
            create_background_task(_update_folder_stats_safe(account.id, folder, result.total, result.unread_total), name="update_folder_stats_refresh")

        return {
            "messages": [m.model_dump() for m in result.messages],
            "total": result.total,
            "unread_total": result.unread_total,
            "page": result.page,
            "page_size": result.page_size,
            "account_id": account.id,
        }
    except Exception as e:
        logger.error("强制刷新邮件失败: %s", e)
        error_msg = str(e)
        await _notify_if_permanent_token_error(e, account, user_uid)
        if _is_outlook_connection_error(account, error_msg):
            return {"messages": [], "total": 0, "page": page, "page_size": page_size,
                    "error": _OUTLOOK_RECONNECTING_MSG, "reconnecting": True}
        return {"messages": [], "total": 0, "page": page, "page_size": page_size, "error": error_msg}


# 写信/定时发送接口已拆分到 routes/compose.py

# ==================== 邮件详情与预取 ====================


@router.get("/api/messages/{message_id}", response_model=MessageItem, summary="获取邮件详情")
async def get_message_detail(
    message_id: str = FastAPIPath(description="邮件唯一ID"),
    request: Request = None,
    account_id: str = Query(default="", description="指定账号ID"),
    folder: str = Query(default="INBOX", description="邮件所在文件夹"),
):
    """获取指定邮件的完整内容（包括正文HTML）"""
    user_uid = await get_uid(request)
    accounts = await get_accounts(user_uid)

    if not accounts:
        raise AppError(404, "No account found")

    account, _ = _find_account_or_error(accounts, account_id)

    try:
        # 优先从缓存获取详情（毫秒级），避免每次都建立 IMAP 连接（秒级）
        uid_num = int(_extract_uid(message_id))
        cached = await get_cached_message_detail(account.id, uid_num, folder)
        history_cached = _load_history_message(account.id, folder, uid_num)
        if cached and history_cached:
            cached["attachments"] = history_cached.get("attachments", []) or []
            cached["body_html"] = history_cached.get("body_html") or cached.get("body_html", "")
            cached["body_text"] = history_cached.get("body_text") or cached.get("body_text", "")
        if cached and cached.get("body_html"):
            # 缓存命中，直接返回（但需要补充附件信息，如果 IMAP 拉取过的话缓存有 has_attachments）
            # 如果有附件，仍需从 IMAP 获取附件列表（缓存不存附件详情）
            if cached.get("has_attachments"):
                # 有附件时需要从 IMAP 拉取附件列表，但正文直接用缓存
                try:
                    credentials = await _ensure_gmail_token(account)
                    receiver = ProviderFactory.get_receiver(account.provider)
                    await receiver.connect(credentials)
                    try:
                        full_msg = await receiver.fetch_message_detail(_extract_uid(message_id), folder=folder)
                    finally:
                        await _safe_disconnect(receiver)
                    # 用 IMAP 的附件列表替换缓存的空附件
                    cached["attachments"] = [a.model_dump() for a in full_msg.attachments]
                    # 更新缓存中的正文和附件信息
                    try:
                        cm = CachedMessage(
                            id=f"{account.id}_{_extract_uid(message_id)}",
                            account_id=account.id,
                            user_uid=user_uid,
                            uid=int(_extract_uid(message_id)),
                            folder=folder,
                            subject=cached["subject"],
                            from_addr=cached["from_addr"],
                            to_addr=cached["to_addr"],
                            date=cached["date"],
                            is_read=cached["is_read"],
                            is_starred=cached["is_starred"],
                            has_attachments=True,
                            body_text=full_msg.body_text or cached.get("body_text", ""),
                            body_html=full_msg.body_html or cached["body_html"],
                        )
                        await upsert_cached_messages([cm])
                    except Exception as e:
                        logger.debug("缓存附件邮件详情失败: %s", e)
                except Exception as e:
                    logger.debug("附件获取失败，不影响正文查看: %s", e)
            return cached

        # 缓存未命中，从 IMAP 拉取
        async def _fetch_detail():
            credentials = await _ensure_gmail_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(credentials)
            try:
                return await receiver.fetch_message_detail(_extract_uid(message_id), folder=folder)
            finally:
                await _safe_disconnect(receiver)

        message = await _with_outlook_retry(account, _fetch_detail)

        # 获取详情后，写入缓存（下次查看时直接从缓存返回）
        # 注意：fetch_message_detail 不解析 FLAGS，is_read 默认 False
        # 写入缓存时必须保留已有的 is_read 状态，否则会把已读邮件覆盖为未读
        # 不能用 get_cached_message_detail（正文为空时返回 None），用 get_cached_is_read
        existing_is_read = await get_cached_is_read(account.id, uid_num, folder)
        corrected_is_read = existing_is_read or message.is_read

        try:
            cm = CachedMessage(
                id=f"{account.id}_{_extract_uid(message_id)}",
                account_id=account.id,
                user_uid=user_uid,
                uid=uid_num,
                folder=folder,
                subject=message.subject,
                from_addr=message.from_addr,
                to_addr=message.to_addr,
                date=message.date,
                is_read=corrected_is_read,
                is_starred=message.is_starred,
                has_attachments=len(message.attachments) > 0,
                body_text=message.body_text,
                body_html=message.body_html,
            )
            await upsert_cached_messages([cm])
        except Exception as e:
            logger.warning("缓存邮件详情失败: %s", e)

        # 返回给前端时，用校正后的 is_read（而非 fetch_message_detail 默认的 False）
        result = message.model_dump()
        result["is_read"] = corrected_is_read
        if history_cached and history_cached.get("attachments"):
            result["attachments"] = history_cached.get("attachments", [])
        return result
    except Exception as e:
        logger.warning("获取邮件详情失败: uid=%s, folder=%s, error=%s", message_id, folder, e)
        error_msg = str(e)
        # 邮件在 IMAP 上不存在（已被其他客户端删除/移动），清理缓存中的脏数据
        if cached:
            if history_cached:
                cached["attachments"] = history_cached.get("attachments", []) or cached.get("attachments", [])
                cached["body_html"] = history_cached.get("body_html") or cached.get("body_html", "")
                cached["body_text"] = history_cached.get("body_text") or cached.get("body_text", "")
            return cached
        if "not found" in error_msg.lower():
            try:
                await delete_cached_message(account.id, uid_num, folder)
                logger.info("已清理不存在的缓存邮件: uid=%s, folder=%s", uid_num, folder)
            except Exception as e:
                logger.debug("清理不存在的缓存邮件失败: %s", e)
            raise AppError(404, "邮件已被删除或移动")
        if _is_outlook_connection_error(account, error_msg):
            raise AppError(503, _OUTLOOK_RECONNECTING_MSG)
        raise AppError(500, error_msg)


@router.post("/api/prefetch-messages", response_model=PrefetchMessagesResponse, summary="后台预取邮件正文到缓存")
async def prefetch_messages(request: Request, body: PrefetchMessagesRequest = Body(default_factory=PrefetchMessagesRequest, description="预取邮件正文请求")):
    """后台预取邮件正文到缓存，立即返回，后台异步执行。

    前端列表加载完成后调用此接口，将当前页邮件正文预取到 SQLite 缓存，
    用户点击时大概率已命中缓存，实现毫秒级打开。
    每次最多预取 10 封，间隔 200ms，避免 IMAP 连接过载。
    """
    message_ids = body.message_ids[:10]
    account_id = body.account_id
    folder = body.folder
    user_uid = await get_uid(request)

    if not message_ids:
        return {"success": True, "prefetched": 0}

    accounts = await get_accounts(user_uid)
    if not accounts:
        return {"success": True, "prefetched": 0}

    account, _ = _find_account_or_error(accounts, account_id)
    if not account:
        return {"success": True, "prefetched": 0}

    async def _prefetch():
        prefetched = 0
        receiver = None
        try:
            # 建立一次连接，复用所有预取操作
            credentials = await _ensure_gmail_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(credentials)

            for msg_id in message_ids:
                try:
                    uid_num = int(_extract_uid(msg_id))
                    # 已缓存（有正文）则跳过
                    cached = await get_cached_message_detail(account.id, uid_num, folder)
                    if cached and cached.get("body_html"):
                        continue

                    # 复用已有连接拉取正文
                    message = await receiver.fetch_message_detail(_extract_uid(msg_id), folder=folder)

                    # 写入缓存（保留已有的 is_read 状态）
                    existing_is_read = await get_cached_is_read(account.id, uid_num, folder)
                    cm = CachedMessage(
                        id=f"{account.id}_{uid_num}",
                        account_id=account.id,
                        user_uid=user_uid,
                        uid=uid_num,
                        folder=folder,
                        subject=message.subject,
                        from_addr=message.from_addr,
                        to_addr=message.to_addr,
                        date=message.date,
                        is_read=existing_is_read or message.is_read,
                        is_starred=message.is_starred,
                        has_attachments=len(message.attachments) > 0,
                        body_text=message.body_text,
                        body_html=message.body_html,
                    )
                    await upsert_cached_messages([cm])
                    prefetched += 1

                    # 间隔 100ms（连接复用后可以更短）
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.debug("预取单封邮件失败: %s", e)
        except Exception as e:
            logger.debug("预取任务异常: %s", e)
        finally:
            if receiver:
                await _safe_disconnect(receiver)

    # 后台异步执行，不阻塞 HTTP 响应
    create_background_task(_prefetch(), name="prefetch_messages")
    return {"success": True, "queued": len(message_ids)}


# ==================== 附件接口 ====================


@router.get("/api/messages/{message_id}/attachments/{part_number}", response_class=FileResponse, summary="下载邮件附件")
async def download_attachment(
    message_id: str = FastAPIPath(description="邮件唯一ID"),
    part_number: int = FastAPIPath(description="附件part编号"),
    request: Request = None,
    account_id: str = Query(default="", description="指定账号ID"),
    folder: str = Query(default="INBOX", description="邮件所在文件夹"),
):
    """下载邮件附件的二进制数据"""
    user_uid = await get_uid(request)
    accounts = await get_accounts(user_uid)

    if not accounts:
        raise AppError(404, "No account found")

    account, _ = _find_account_or_error(accounts, account_id)
    uid_num = int(_extract_uid(message_id))
    history_cached = _load_history_message(account.id, folder, uid_num)
    if history_cached:
        attachment_meta = next(
            (att for att in (history_cached.get("attachments", []) or []) if int(att.get("part_number", 0)) == part_number),
            None,
        )
        cached_file = _find_history_attachment_file(account.id, uid_num, part_number)
        if attachment_meta and cached_file and cached_file.exists():
            filename = attachment_meta.get("filename") or cached_file.name
            return FileResponse(
                path=str(cached_file),
                media_type=attachment_meta.get("content_type") or "application/octet-stream",
                filename=filename,
            )

    try:
        credentials = await _ensure_gmail_token(account)

        receiver = ProviderFactory.get_receiver(account.provider)
        await receiver.connect(credentials)
        try:
            # 先获取邮件详情，找到附件信息
            message = await receiver.fetch_message_detail(_extract_uid(message_id), folder=folder)

            # 查找对应 part_number 的附件
            attachment = None
            for att in message.attachments:
                if att.part_number == part_number:
                    attachment = att
                    break

            if not attachment:
                raise AppError(404, "附件不存在")

            # 通过 IMAP BODY.PEEK[part_number] 获取附件二进制数据
            att_data = await receiver.fetch_attachment_data(_extract_uid(message_id), folder, part_number)
        finally:
            await _safe_disconnect(receiver)

        if not att_data:
            raise AppError(500, "附件数据获取失败")

        # 返回附件二进制流
        filename = attachment.filename or f"attachment_{part_number}"
        account_dir = Path(ATTACHMENTS_DIR) / account.id / str(uid_num)
        account_dir.mkdir(parents=True, exist_ok=True)
        safe_filename = os.path.basename(filename) or f"attachment_{part_number}"
        cache_name = f"{part_number}_{hashlib.sha1(safe_filename.encode('utf-8')).hexdigest()[:12]}_{safe_filename}"
        with open(account_dir / cache_name, "wb") as f:
            f.write(att_data)
        return Response(
            content=att_data,
            media_type=attachment.content_type or "application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
    except Exception as e:
        logger.error("下载附件失败: %s", e)
        error_msg = str(e)
        if _is_outlook_connection_error(account, error_msg):
            raise AppError(503, _OUTLOOK_RECONNECTING_MSG)
        raise AppError(500, error_msg)


@router.post("/api/messages/upload-attachment", response_model=UploadAttachmentResponse, summary="上传附件")
async def upload_attachment(request: Request, file: UploadFile = File(...)):
    """上传附件到临时目录，返回文件路径供发送时引用"""
    user_uid = await get_uid(request)
    # 临时目录: {FLYMAIL_DATA_DIR}/attachments/{user_uid}/{uuid}/{filename}
    upload_id = uuid.uuid4().hex[:8]
    upload_dir = Path(UPLOADS_DIR) / user_uid / upload_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 安全修复 S1：清洗文件名，防止路径穿越攻击（如 ../../settings.json）
    # 1. 只取 basename，去掉任何路径前缀
    safe_filename = os.path.basename(file.filename)
    if not safe_filename or safe_filename in (".", ".."):
        raise AppError(400, "非法文件名")
    file_path = upload_dir / safe_filename
    # 2. resolve 后校验路径仍在 upload_dir 内（防止符号链接等绕过）
    if not file_path.resolve().is_relative_to(upload_dir.resolve()):
        raise AppError(400, "非法文件路径")

    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    return {
        "filename": safe_filename,
        "size": len(content),
        "path": str(file_path),
    }


@router.delete("/api/messages/upload-attachment", response_model=StatusResponse, summary="删除已上传附件")
async def delete_attachment(path: str = Query(description="附件文件路径")):
    """删除已上传的临时附件文件"""
    try:
        file_path = Path(path).resolve()
        # 安全修复 S2：用 is_relative_to 校验路径在附件目录内，
        # 旧代码 "attachments" in str(file_path) 可被 ../../data/attachments/../../../etc/passwd 绕过
        if not file_path.is_relative_to(Path(UPLOADS_DIR).resolve()):
            raise AppError(403, "无权访问该路径")
        if file_path.exists() and file_path.is_file():
            file_path.unlink()
            return {"success": True}
        raise AppError(404, "文件不存在")
    except AppError:
        raise
    except Exception as e:
        raise AppError(500, str(e))


# ==================== 邮件操作接口（删除、已读标记） ====================


@router.delete("/api/messages/{message_id}", response_model=DeleteResponse, summary="删除单封邮件")
async def delete_message(
    message_id: str = FastAPIPath(description="邮件唯一ID"),
    request: Request = None,
    account_id: str = Query(default="", description="指定账号ID"),
    folder: str = Query(default="INBOX", description="邮件当前所在文件夹"),
):
    """删除邮件：移到已删除文件夹；若已在已删除文件夹中则彻底删除"""
    user_uid = await get_uid(request)
    accounts = await get_accounts(user_uid)

    if not accounts:
        raise AppError(404, "No account found")

    account, _ = _find_account_or_error(accounts, account_id)

    try:
        credentials = await _ensure_gmail_token(account)

        receiver = ProviderFactory.get_receiver(account.provider)
        await receiver.connect(credentials)
        try:
            trash_folder = await _get_trash_folder_dynamic(account)
            msg_uid = _extract_uid(message_id)
            if folder == trash_folder:
                await receiver.delete_message(msg_uid, folder)
            else:
                await receiver.move_message(msg_uid, trash_folder, source_folder=folder)

            # 在 disconnect 前同步获取文件夹统计（后台任务不再依赖 receiver）
            try:
                folder_counts = await receiver.fetch_folder_counts([folder])
            except Exception:
                folder_counts = {}
        finally:
            await _safe_disconnect(receiver)

        # 后台异步更新文件夹统计（使用已获取的数据，无需保持 receiver 连接）
        async def _update_stats_bg():
            try:
                if folder in folder_counts:
                    await upsert_folder_stats(account.id, folder,
                        folder_counts[folder].get("total", 0), folder_counts[folder].get("unread", 0))
            except Exception as e:
                logger.warning("单封删除后更新文件夹统计失败: account=%s, error=%s", account.email, e)

        create_background_task(_update_stats_bg(), name="update_stats_after_delete")
        # 同步删除缓存中的邮件
        try:
            await delete_cached_message(account.id, int(_extract_uid(message_id)), folder)
        except Exception as e:
            logger.debug("删除缓存邮件失败: %s", e)
        # 立即更新 folder_stats：从缓存 COUNT 获取真实剩余数
        try:
            cached_remaining = await get_cached_count(account.id, folder)
            stats = await get_folder_stats(account.id, folder)
            new_total = max(cached_remaining, stats.get("total_count", 0) - 1)
            await upsert_folder_stats(account.id, folder, new_total, stats.get("unread_count", 0))
        except Exception as e:
            logger.debug("删除后更新 folder_stats 失败: %s", e)
        # 通知其他标签页邮件状态变化
        action = "move" if folder != trash_folder else "delete"
        await sync_service.notify_message_state_changed(
            account.id, action, [message_id], folder=folder, user_uid=user_uid
        )
        return {"success": True}
    except Exception as e:
        logger.error("删除邮件失败: %s", e)
        raise AppError(500, str(e))


@router.post("/api/messages/batch-delete", response_model=BatchDeleteResponse, summary="批量删除邮件")
async def batch_delete_messages(request: Request, body: BatchDeleteRequest = Body(description="批量删除请求")):
    """批量删除多封邮件"""
    user_uid = await get_uid(request)
    accounts = await get_accounts(user_uid)

    if not accounts:
        raise AppError(404, "No account found")

    account, _ = _find_account_or_error(accounts, body.account_id)

    try:
        credentials = await _ensure_gmail_token(account)

        # 使用缓存连接（复用已有连接，避免每次新建 ~400ms）
        receiver = await _get_cached_receiver(account, credentials)
        try:
            trash_folder = await _get_trash_folder_dynamic(account)
            # 提取纯 UID 列表
            uid_list = [_extract_uid(mid) for mid in body.message_ids]
            if body.folder == trash_folder:
                # 已在已删除文件夹中，批量彻底删除
                deleted = await receiver.delete_message_batch(uid_list, body.folder)
            else:
                # 批量移动到已删除文件夹
                moved = await receiver.move_message_batch(uid_list, trash_folder, source_folder=body.folder)

            # 同步获取文件夹统计
            try:
                folder_counts = await receiver.fetch_folder_counts([body.folder])
            except Exception:
                folder_counts = {}
        except Exception:
            # 操作失败时清理缓存连接
            _conn_cache.pop(account.id, None)
            await _safe_disconnect(receiver)
            raise

        # 后台异步更新文件夹统计（使用已获取的数据，无需保持 receiver 连接）
        async def _update_folder_stats_bg():
            try:
                if body.folder in folder_counts:
                    await upsert_folder_stats(account.id, body.folder,
                        folder_counts[body.folder].get("total", 0), folder_counts[body.folder].get("unread", 0))
            except Exception as e:
                logger.warning("批量删除后更新文件夹统计失败: account=%s, error=%s", account.email, e)

        create_background_task(_update_folder_stats_bg(), name="update_folder_stats_after_batch_delete")
        # 同步删除缓存中的邮件（批量，单次数据库操作替代逐条删除的 N+1 问题）
        try:
            uids_to_delete = [int(_extract_uid(mid)) for mid in body.message_ids]
            deleted_count = await batch_delete_cached_messages(account.id, uids_to_delete, body.folder)
        except Exception as e:
            logger.error("批量删除缓存清理失败: account=%s, error=%s", account.email, e)

        # 立即更新 folder_stats：从缓存 COUNT 获取真实剩余数
        # 不用手动减 N，因为 COUNT 一定和缓存一致，避免 _verify 误判触发重同步
        try:
            cached_remaining = await get_cached_count(account.id, body.folder)
            stats = await get_folder_stats(account.id, body.folder)
            # total 取 IMAP 真实值减去已删除数（更准确），但兜底用缓存 COUNT
            new_total = max(cached_remaining, stats.get("total_count", 0) - len(body.message_ids))
            new_unread = max(0, stats.get("unread_count", 0))
            await upsert_folder_stats(account.id, body.folder, new_total, new_unread)
        except Exception as e:
            logger.warning("批量删除后更新 folder_stats 失败: account=%s, error=%s", account.email, e)

        # 通知其他标签页邮件状态变化
        action = "move" if body.folder != trash_folder else "delete"
        await sync_service.notify_message_state_changed(
            account.id, action, body.message_ids, folder=body.folder, user_uid=user_uid
        )
        return {"success": True, "deleted": len(body.message_ids)}
    except Exception as e:
        logger.error("批量删除邮件失败: account=%s, error=%s", account.email, e)
        raise AppError(500, str(e))


@router.post("/api/mark-read", response_model=MessageResponse, summary="标记单封邮件为已读",
             description="通过 IMAP STORE +FLAGS \\Seen 同步已读状态到邮箱服务器，其他客户端也会同步")
async def mark_message_as_read(request: Request, body: MarkReadRequest = Body(description="标记已读请求")):
    """标记邮件为已读（IMAP STORE +FLAGS \\Seen，同步到邮箱服务器）"""
    user_uid = await get_uid(request)
    accounts = await get_accounts(user_uid)

    if not accounts:
        raise AppError(404, "No account found")

    account, _ = _find_account_or_error(accounts, body.account_id)

    try:
        credentials = await _ensure_gmail_token(account)

        receiver = ProviderFactory.get_receiver(account.provider)
        await receiver.connect(credentials)
        try:
            await receiver.mark_as_read(_extract_uid(body.message_id), body.folder)
        finally:
            await _safe_disconnect(receiver)
        # 同步更新缓存中的已读状态
        try:
            await update_cached_message_read(account.id, int(_extract_uid(body.message_id)), body.folder, True)
            # 同步更新 folder_stats.unread_count，使侧边栏未读数立即减少
            stats = await get_folder_stats(account.id, body.folder)
            new_unread = max(0, stats.get("unread_count", 0) - 1)
            await upsert_folder_stats(account.id, body.folder, stats["total_count"], new_unread)
        except Exception as e:
            logger.debug("标记已读后更新 folder_stats 失败: %s", e)
        # 通知其他标签页邮件状态变化
        await sync_service.notify_message_state_changed(
            account.id, "mark_read", [body.message_id], folder=body.folder, user_uid=user_uid
        )
        return {"success": True}
    except Exception as e:
        logger.error("标记已读失败: %s", e)
        raise AppError(500, str(e))


@router.post("/api/messages/batch-mark-read", response_model=BatchMarkReadResponse, summary="批量标记已读")
async def batch_mark_read(request: Request, body: BatchMarkReadRequest = Body(description="批量标记已读请求")):
    """批量标记多封邮件为已读，使用 IMAP UID STORE 逗号分隔 UID 列表一次命令完成

    优化前：逐条 SELECT + UID STORE，N 封 = N 次网络往返，全选标记非常慢。
    优化后：一次 SELECT + 一次 UID STORE uid1,uid2,...,uidN，1 次网络往返。
    """
    user_uid = await get_uid(request)
    accounts = await get_accounts(user_uid)

    if not accounts:
        raise AppError(404, "No account found")

    account, _ = _find_account_or_error(accounts, body.account_id)

    try:
        credentials = await _ensure_gmail_token(account)

        # 使用缓存连接（复用已有连接，避免每次新建 ~400ms）
        receiver = await _get_cached_receiver(account, credentials)
        try:
            # 提取纯 UID 列表
            uid_list = [_extract_uid(mid) for mid in body.message_ids]
            marked = await receiver.mark_as_read_batch(uid_list, body.folder)
        except Exception:
            # 操作失败时清理缓存连接
            _conn_cache.pop(account.id, None)
            await _safe_disconnect(receiver)
            raise
        # 同步更新缓存中的已读状态（批量，单次数据库操作）
        try:
            uids_to_update = [int(_extract_uid(mid)) for mid in body.message_ids]
            await batch_update_cached_messages_read(account.id, uids_to_update, body.folder, True)
            # 同步更新 folder_stats.unread_count，使侧边栏未读数立即减少
            stats = await get_folder_stats(account.id, body.folder)
            new_unread = max(0, stats.get("unread_count", 0) - len(uids_to_update))
            await upsert_folder_stats(account.id, body.folder, stats["total_count"], new_unread)
        except Exception as e:
            logger.error("批量标记已读缓存更新失败: account=%s, error=%s", account.email, e)
        # 通知其他标签页邮件状态变化
        await sync_service.notify_message_state_changed(
            account.id, "mark_read", body.message_ids, folder=body.folder, user_uid=user_uid
        )
        return {"success": True, "marked": marked}
    except Exception as e:
        logger.error("批量标记已读失败: %s", e)
        raise AppError(500, str(e))


# 写信/定时发送接口已拆分到 routes/compose.py
