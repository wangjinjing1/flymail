"""邮件缓存同步服务 - 将邮件摘要缓存到 SQLite，增量拉取新邮件

架构:
  前端请求 → 先返回数据库缓存（瞬间） → 后台增量同步 → WebSocket 通知前端刷新

核心功能:
  1. 全量同步：首次添加账号时，批量拉取最近 N 封邮件摘要写入缓存
  2. 增量同步：基于 UID SEARCH UID > max_cached_uid 只拉取新邮件
  3. 并发控制：每个账号的同步操作用 asyncio.Lock 保护，避免同一连接并发操作
"""

import asyncio
import base64
import time
from typing import Dict, List

from db import (
    get_cached_message_detail,
    upsert_cached_attachments,
    upsert_cached_messages, get_max_cached_uid, get_accounts,
    upsert_folder_stats, get_folder_stats,
    purge_deleted_from_cache, get_cached_count, get_cached_uids,
    get_cached_messages_by_folder, batch_update_is_read,
)
from data_paths import coalesce_message_date, normalize_message_date
from models import CachedMessage, Account
from providers.base import Message
from providers.factory import ProviderFactory
from services.history_sync import _cache_message_assets
from utils.logger import get_logger

logger = get_logger("cache")

# 每个账号的同步锁，防止同一账号并发同步（IMAP 连接不能并发操作）
_sync_locks: Dict[str, asyncio.Lock] = {}


def _get_lock(account_id: str) -> asyncio.Lock:
    """获取账号级别的同步锁"""
    if account_id not in _sync_locks:
        _sync_locks[account_id] = asyncio.Lock()
    return _sync_locks[account_id]


async def try_acquire_sync_lock(account_id: str) -> asyncio.Lock | None:
    """Try to acquire the account sync lock without waiting."""
    lock = _get_lock(account_id)
    if lock.locked():
        return None
    await lock.acquire()
    return lock


def remove_sync_lock(account_id: str):
    """清理指定账号的同步锁（账号删除时调用，防止内存泄漏）"""
    _sync_locks.pop(account_id, None)


def _select_uncached_recent_messages(messages: List[Message], cached_uids: set[int]) -> tuple[List[Message], bool]:
    selected: List[Message] = []
    for message in messages:
        if message.uid <= 0:
            continue
        if message.uid in cached_uids:
            return selected, True
        selected.append(message)
        cached_uids.add(message.uid)
    return selected, False


async def _sync_read_state_from_unseen(account: Account, folder: str, messages: List[Message], unseen_uids: set[int] | None) -> int:
    if unseen_uids is None:
        return 0
    to_fix = []
    for message in messages:
        if message.uid <= 0:
            continue
        should_read = message.uid not in unseen_uids
        message.is_read = should_read
        to_fix.append((message.uid, 1 if should_read else 0))
    if not to_fix:
        return 0
    return await batch_update_is_read(account.id, folder, to_fix)


REMOTE_FOLDER_ALIAS_ORDER = {
    "inbox": ["INBOX", "Inbox"],
    "sent": ["Sent Messages", "Sent Items", "[Gmail]/Sent Mail", "[Google Mail]/Sent Mail", "Sent", "Sent Mail", "已发送"],
    "drafts": ["Drafts", "[Gmail]/Drafts", "[Google Mail]/Drafts", "草稿箱"],
    "junk": ["Junk", "Junk Email", "Spam", "[Gmail]/Spam", "[Google Mail]/Spam", "垃圾邮件"],
    "trash": ["Deleted Messages", "Deleted Items", "[Gmail]/Trash", "[Google Mail]/Trash", "Trash", "Deleted", "已删除"],
}


def _remote_folder_alias_key(folder: str) -> str:
    folder_lower = (folder or "INBOX").strip().lower()
    decoded_leaf = _decode_imap_modified_utf7_path(folder).lower().rsplit("/", 1)[-1]
    for key, aliases in REMOTE_FOLDER_ALIAS_ORDER.items():
        if any(folder_lower == alias.lower() for alias in aliases):
            return key
    decoded_aliases = {
        "sent": {"\u5df2\u53d1\u9001", "\u5df2\u53d1\u90ae\u4ef6"},
        "drafts": {"\u8349\u7a3f\u7bb1"},
        "junk": {"\u5783\u573e\u90ae\u4ef6"},
        "trash": {"\u5df2\u5220\u9664"},
    }
    for key, aliases in decoded_aliases.items():
        if decoded_leaf in aliases:
            return key
    return ""


def _decode_imap_modified_utf7_path(folder: str) -> str:
    text = (folder or "").strip()
    result = []
    i = 0
    while i < len(text):
        if text[i] != "&":
            result.append(text[i])
            i += 1
            continue
        end = text.find("-", i)
        if end < 0:
            result.append(text[i:])
            break
        if end == i + 1:
            result.append("&")
        else:
            encoded = text[i + 1:end].replace(",", "/")
            padding = (4 - len(encoded) % 4) % 4
            try:
                result.append(base64.b64decode(encoded + ("=" * padding)).decode("utf-16-be"))
            except Exception:
                result.append(text[i:end + 1])
        i = end + 1
    return "".join(result)


async def _resolve_remote_folder(receiver, folder: str) -> str:
    requested = (folder or "INBOX").strip() or "INBOX"
    alias_key = _remote_folder_alias_key(requested)
    if not alias_key:
        return requested

    try:
        folders = await receiver.fetch_folders()
    except Exception as exc:
        logger.debug("resolve cache sync folder failed: folder=%s error=%s", requested, exc)
        return requested

    for item in folders:
        item_path = getattr(item, "path", "") or ""
        item_name = getattr(item, "name", "") or ""
        if _remote_folder_alias_key(item_path) == alias_key or _remote_folder_alias_key(item_name) == alias_key:
            return item_path
    for alias in REMOTE_FOLDER_ALIAS_ORDER[alias_key]:
        alias_lower = alias.lower()
        for item in folders:
            item_path = getattr(item, "path", "") or ""
            item_name = getattr(item, "name", "") or ""
            if item_path.lower() == alias_lower or item_name.lower() == alias_lower:
                return item_path
    return requested


async def sync_recent_folder_to_cache(account: Account, folder: str = "INBOX", page_size: int = 50) -> int:
    """Fetch recent remote pages and cache only messages not already saved locally."""
    lock = await try_acquire_sync_lock(account.id)
    if not lock:
        logger.debug("最近邮件同步跳过: 账号=%s 文件夹=%s 上一轮仍在执行", account.email, folder)
        return 0
    receiver = None
    try:
        from services.token import ensure_token
        credentials = await ensure_token(account)
        receiver = ProviderFactory.get_receiver(account.provider)
        await receiver.connect(credentials)

        folder = await _resolve_remote_folder(receiver, folder)
        cached_uids = await get_cached_uids(account.id, folder)
        unseen_uids = None
        try:
            unseen_uids = set(await receiver.fetch_unseen_uids(folder))
        except Exception as exc:
            logger.debug("最近邮件同步获取 UNSEEN 失败: %s", exc)

        page = 1
        added = 0
        while True:
            result = await receiver.fetch_messages(folder, page=page, page_size=page_size)
            if page == 1:
                await upsert_folder_stats(account.id, folder, result.total or 0, result.unread_total or 0)
            if not result.messages:
                break

            await _sync_read_state_from_unseen(account, folder, result.messages, unseen_uids)
            new_messages, reached_cached = _select_uncached_recent_messages(result.messages, cached_uids)
            if new_messages:
                added += await _cache_messages_with_details(receiver, account, folder, new_messages, unseen_uids)

            if reached_cached:
                break
            if result.total and page * result.page_size >= result.total:
                break
            if len(result.messages) < result.page_size:
                break
            page += 1

        if added:
            logger.info("最近邮件同步完成: 账号=%s, 文件夹=%s, 新增 %d 封", account.email, folder, added)
        return added
    except Exception as e:
        logger.warning("最近邮件同步失败: 账号=%s 文件夹=%s 错误=%s", account.email, folder, e)
        return 0
    finally:
        if receiver:
            try:
                await receiver.disconnect()
            except Exception as e:
                logger.debug("最近邮件同步后断开连接失败: %s", e)
        lock.release()


async def sync_folder_to_cache(account: Account, folder: str = "INBOX", force_full: bool = False) -> int:
    """将文件夹的邮件摘要同步到本地缓存（增量：只拉取新邮件）

    使用独立的 IMAP 连接，不影响后台 IDLE 监听。
    返回新增的邮件数量。
    force_full: 强制全量同步（rebuild-sync 时使用）
    """
    lock = _get_lock(account.id)
    async with lock:
        # 建立独立连接（不复用后台 IDLE 的连接，避免干扰）
        # Gmail 需要检查 access_token 是否过期并自动刷新
        receiver = None
        try:
            from services.token import ensure_token
            credentials = await ensure_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(credentials)
            return await _do_sync(receiver, account, folder, force_full=force_full)
        except Exception as e:
            logger.warning("同步账号 %s 文件夹 %s 失败: %s", account.email, folder, e)
            return 0
        finally:
            if receiver:
                try:
                    await receiver.disconnect()
                except Exception as e:
                    logger.debug("同步后断开连接失败: %s", e)


async def _do_sync(receiver, account: Account, folder: str, force_full: bool = False) -> int:
    """执行增量同步核心逻辑

    统一使用 UID SEARCH 做增量检查：
    1. 查询缓存中最大的 UID
    2. 如果没有缓存也没有同步记录 → 全量拉取（批量 UID FETCH）
    3. 如果有缓存或同步记录 → 增量拉取（UID SEARCH UID > max_uid）
    4. 写入缓存（INSERT OR IGNORE + UPDATE，不覆盖已有正文）
    5. 更新 folder_stats（IMAP 真实总数和未读数）
    6. 清理缓存中已不在 IMAP 服务器上的邮件（仅全量同步时）

    force_full: 强制全量同步（rebuild-sync 时使用，避免并发写入导致增量同步遗漏）
    """
    folder = await _resolve_remote_folder(receiver, folder)
    max_uid = await get_max_cached_uid(account.user_uid, account.id, folder)
    folder_stats = await get_folder_stats(account.id, folder)

    # 首次同步判断：max_uid=0 且从未同步过（updated_at=0）。若 max_uid=0 但 updated_at≠0，说明曾经同步过但邮件被全部删除，走增量同步即可
    # force_full=True 时强制走全量同步（rebuild-sync 场景）
    if (max_uid == 0 and folder_stats["updated_at"] == 0) or force_full:
        # 首次同步：全量拉取（批量 UID FETCH，最多500封）
        logger.info("首次同步: 账号=%s, 文件夹=%s, 全量拉取", account.email, folder)
        # 不再先删后写，避免中间时间窗口前端请求看到空缓存
        # 改为先写后删：写入新数据后，用 purge_deleted_from_cache 清理不在新数据中的旧记录
        result = await receiver.fetch_messages(folder, page=1, page_size=500)
        messages = [m for m in result.messages if m.uid > 0]
        # 全量同步时，result.total 就是 IMAP 真实总数
        total_count = result.total
        unread_count = result.unread_total
        # 只有当拉取了全部邮件时（messages 数量等于 total_count），才用 all_uids 做清理
        # 否则 all_uids 不完整，purge_deleted_from_cache 会误删未拉取的缓存
        if len(messages) >= total_count:
            all_uids = {m.uid for m in messages}
        else:
            all_uids = None  # 拉取不完整，不做清理
    else:
        # 增量同步：统一用 UID SEARCH 检查新邮件
        # Outlook/Hotmail 的 UID 不一定按邮件时间递增，使用 max_cached_uid 会漏掉新邮件。
        # 因此 Outlook 优先用 STATUS 总数判断：只要 IMAP 总数增加，就拉取最新一页写缓存。
        if account.provider == "outlook":
            try:
                counts = await receiver.fetch_folder_counts([folder])
                folder_count = counts.get(folder, {})
                current_total = folder_count.get("total", 0)
                current_unread = folder_count.get("unread", 0)
            except Exception:
                current_total = 0
                current_unread = 0
            if current_total > folder_stats.get("total_count", 0):
                diff = current_total - folder_stats.get("total_count", 0)
                page_size = min(max(diff + 20, 40), 100)
                logger.debug(
                    "Outlook 总数增加，拉取最新邮件: 账号=%s, 文件夹=%s, 本地=%d, IMAP=%d, page_size=%d",
                    account.email, folder, folder_stats.get("total_count", 0), current_total, page_size,
                )
                result = await receiver.fetch_messages(folder, page=1, page_size=page_size)
                messages = [m for m in result.messages if m.uid > 0]
                all_uids = None  # 增量同步不做全量UID清理
                total_count = current_total
                unread_count = current_unread
            else:
                # Outlook UID 不按时间递增，回退 500 个 UID 作为增量窗口，避免漏收
                since_uid = max(0, max_uid - 500)
                logger.debug(
                    "Outlook 增量窗口同步: 账号=%s, 文件夹=%s, max_uid=%d, since_uid=%d",
                    account.email, folder, max_uid, since_uid,
                )
                new_uids = await receiver.fetch_new_message_uids(folder, since_uid=since_uid)
                if not new_uids:
                    messages = []
                    all_uids = None  # 无新邮件时不做全量UID清理
                else:
                    messages = await receiver.fetch_messages_by_uids(folder, new_uids)
                    messages = [m for m in messages if m.uid > 0]
                    all_uids = None  # 增量同步不做全量UID清理
                # -1 为哨兵值，表示尚未获取真实计数；后续通过 IMAP STATUS 命令获取实际值
                total_count = -1
                unread_count = -1
        else:
            # 非 Outlook 账号：基于 max_uid 做增量同步
            if max_uid == 0:
                pass  # 空文件夹，跳过增量检查
            else:
                logger.debug("增量同步: 账号=%s, 文件夹=%s, since_uid=%d", account.email, folder, max_uid)
            new_uids = await receiver.fetch_new_message_uids(folder, since_uid=max_uid)
            if not new_uids:
                messages = []
                all_uids = None  # 无新邮件时不做全量UID清理
            else:
                messages = await receiver.fetch_messages_by_uids(folder, new_uids)
                # 过滤无效 UID（uid=0），避免写入后又被清理
                messages = [m for m in messages if m.uid > 0]
                all_uids = None  # 增量同步不做全量UID清理，减少IMAP开销
            total_count = -1
            unread_count = -1
        # 增量同步时不再获取全量 UID 列表（减少 IMAP 开销）
        # 删除清理在下方通过 STATUS 总数比对触发，仅在检测到删除时才获取全量 UID

    # 如果没有从 fetch_messages 获取到总数，用 STATUS 命令获取
    if total_count < 0:
        try:
            counts = await receiver.fetch_folder_counts([folder])
            if folder in counts:
                total_count = counts[folder].get("total", 0)
                unread_count = counts[folder].get("unread", 0)
            else:
                total_count = 0
                unread_count = 0
        except Exception as e:
            logger.warning("获取文件夹统计失败: %s, %s", folder, e)
            total_count = 0
            unread_count = 0

    # 即使文件夹为空也要写入 folder_stats，作为"已同步过"的标记，避免下次刷新重复全量拉取
    if total_count >= 0:
        await upsert_folder_stats(account.id, folder, total_count, unread_count)

    # 增量同步时检测删除：当缓存行数 > IMAP 总数，说明有邮件被删除/移动
    # 仅在检测到删除时获取全量 UID 做清理，避免每次增量同步都获取
    if not force_full and total_count > 0:
        try:
            cached_count = await get_cached_count(account.id, folder)
            if cached_count > total_count:
                all_uids_for_purge = set(await receiver.fetch_new_message_uids(folder, since_uid=0))
                if all_uids_for_purge:
                    purged = await purge_deleted_from_cache(account.id, folder, all_uids_for_purge)
                    if purged > 0:
                        logger.info("增量清理过期缓存: 账号=%s, 文件夹=%s, 删除 %d 封",
                                   account.email, folder, purged)
        except Exception as e:
            logger.debug("增量清理过期缓存失败: %s", e)

    # 获取 UNSEEN UID 集合，用于校正 is_read（只查一次，新邮件校正和全量校正复用）
    unseen_uids = None
    try:
        unseen_uids = set(await receiver.fetch_unseen_uids(folder))
    except Exception as e:
        logger.debug("获取 UNSEEN UID 失败: %s", e)

    if messages:
        await _cache_messages_with_details(receiver, account, folder, messages, unseen_uids)
        # 日志中包含已读/未读统计，方便排查问题
        read_count = sum(1 for m in messages if m.is_read)
        logger.info(
            "同步完成: 账号=%s, 文件夹=%s, 新增 %d 封 (已读 %d, 未读 %d)",
            account.email, folder, len(messages), read_count, len(messages) - read_count
        )

    # 增量同步时校正已有缓存邮件的 is_read 状态
    # 场景：用户在其他客户端标记已读/未读 → IDLE FETCH+FLAGS → 触发同步 → 更新缓存
    # 复用上面获取的 unseen_uids，不额外查询 IMAP
    if not force_full and unseen_uids is not None:
        try:
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
                        "增量 is_read 校正: 账号=%s, 文件夹=%s, 修正 %d 封 (→已读 %d, →未读 %d)",
                        account.email, folder, len(to_fix), fixed_read, len(to_fix) - fixed_read
                    )
        except Exception as e:
            logger.debug("增量 is_read 校正失败: %s", e)

    # 同步完成后，批量校正整个文件夹缓存中的 is_read（force_full 时执行全量校正）
    # 增量同步已在上方校正所有缓存邮件的 is_read，force_full 时再做一次确保完整
    if force_full:
        try:
            cached_msgs = await get_cached_messages_by_folder(
                account.user_uid, account.id, folder, page=1, page_size=10000
            )
            if cached_msgs.get("messages"):
                all_unseen = set(await receiver.fetch_unseen_uids(folder))
                # 找出需要更新的邮件（is_read 与实际不符的）
                to_fix = []
                for msg in cached_msgs["messages"]:
                    should_read = msg["uid"] not in all_unseen
                    if bool(msg["is_read"]) != should_read:
                        to_fix.append((msg["uid"], 1 if should_read else 0))
                if to_fix:
                    await batch_update_is_read(account.id, folder, to_fix)
                    fixed_read = sum(1 for _, v in to_fix if v == 1)
                    logger.info(
                        "批量 is_read 校正: 账号=%s, 文件夹=%s, 修正 %d 封 (→已读 %d, →未读 %d)",
                        account.email, folder, len(to_fix), fixed_read, len(to_fix) - fixed_read
                    )
        except Exception as e:
            logger.warning("批量 is_read 校正失败: %s", e)

    # 清理缓存中已不在 IMAP 服务器上的邮件
    if all_uids is not None and len(all_uids) > 0:
        purged = await purge_deleted_from_cache(account.id, folder, all_uids)
        if purged > 0:
            logger.info("清理过期缓存: 账号=%s, 文件夹=%s, 删除 %d 封", account.email, folder, purged)

    filled_missing = 0
    if not force_full and total_count > 0:
        try:
            cached_count = await get_cached_count(account.id, folder)
            if cached_count < total_count:
                filled_missing = await _sync_missing_messages_with_receiver(receiver, account, folder)
        except Exception as e:
            logger.debug("补全缺失缓存失败: %s", e)

    if total_count >= 0:
        await upsert_folder_stats(account.id, folder, total_count, unread_count)

    return (len(messages) if messages else 0) + filled_missing


async def _cache_messages_with_details(receiver, account: Account, folder: str, messages: List[Message], unseen_uids: set[int] | None) -> int:
    if not messages:
        return 0
    if unseen_uids is not None:
        for m in messages:
            m.is_read = m.uid not in unseen_uids
        logger.debug(
            "is_read 校正: 账号=%s, 文件夹=%s, 未读UID=%s",
            account.email, folder, unseen_uids or "无"
        )

    cached = _messages_to_cached(messages, account)
    await upsert_cached_messages(cached)

    detailed_batch = []
    for message in messages:
        try:
            detail = await receiver.fetch_message_detail(str(message.uid), folder=folder)
        except Exception as e:
            logger.debug("拉取邮件详情失败，跳过附件缓存: account=%s folder=%s uid=%s error=%s", account.email, folder, message.uid, e)
            continue

        if unseen_uids is not None:
            detail.is_read = detail.uid not in unseen_uids

        cached_detail = await get_cached_message_detail(account.id, detail.uid, folder)
        effective_message_date = coalesce_message_date(detail.date, message.date, (cached_detail or {}).get("date", ""))
        body_html, storage_path, _att_count, _inline_count, attachment_records = await _cache_message_assets(
            receiver, account, folder, detail
        )
        detail.body_html = body_html

        detailed_batch.append(
            CachedMessage(
                id=f"{account.id}_{detail.uid}",
                account_id=account.id,
                user_uid=account.user_uid,
                uid=detail.uid,
                folder=folder,
                subject=detail.subject,
                from_addr=detail.from_addr,
                to_addr=detail.to_addr,
                date=normalize_message_date(detail.date, fallback=effective_message_date),
                is_read=detail.is_read,
                is_starred=detail.is_starred,
                has_attachments=bool(detail.attachments),
                body_text=detail.body_text,
                body_html=detail.body_html,
                storage_path=storage_path,
                cached_at=time.time(),
            )
        )
        if attachment_records:
            await upsert_cached_attachments(attachment_records)

    if detailed_batch:
        await upsert_cached_messages(detailed_batch)
    return len(messages)


async def _sync_missing_messages_with_receiver(receiver, account: Account, folder: str) -> int:
    try:
        imap_uids = set(await receiver.fetch_new_message_uids(folder, since_uid=0))
    except Exception as e:
        logger.warning("获取 IMAP UID 列表失败: %s, %s", folder, e)
        return 0

    if not imap_uids:
        return 0

    cached_uids = await get_cached_uids(account.id, folder)
    missing_uids = imap_uids - cached_uids
    if not missing_uids:
        return 0

    logger.info(
        "补全同步: 账号=%s, 文件夹=%s, IMAP=%d封, 缓存=%d封, 缺失=%d封",
        account.email, folder, len(imap_uids), len(cached_uids), len(missing_uids)
    )

    total_filled = 0
    missing_list = sorted(missing_uids)
    unseen_uids = None
    try:
        unseen_uids = set(await receiver.fetch_unseen_uids(folder))
    except Exception as e:
        logger.debug("获取未读 uid 列表失败，跳过 is_read 校正: %s", e)

    for i in range(0, len(missing_list), 100):
        batch = missing_list[i:i + 100]
        try:
            messages = await receiver.fetch_messages_by_uids(folder, batch)
            messages = [m for m in messages if m.uid > 0]
            if messages:
                total_filled += await _cache_messages_with_details(receiver, account, folder, messages, unseen_uids)
        except Exception as e:
            logger.warning("补全同步批次失败: %s, UIDs=%s, 错误=%s", folder, batch[:5], e)

    return total_filled


async def sync_missing_messages(account: Account, folder: str) -> int:
    """补全缓存中缺失的邮件（对比 IMAP 全量 UID 与缓存 UID，拉取差异部分）

    当 _do_sync 检测到缓存行数少于 IMAP 总数时调用。
    增量同步基于 max_uid 只能发现更大的新 UID，无法发现中间遗漏的邮件，
    此函数通过全量 UID 对比解决该问题。
    """
    lock = _get_lock(account.id)
    # 等待锁释放，避免与正在进行的同步冲突
    async with lock:
        receiver = None
        try:
            from services.token import ensure_token
            credentials = await ensure_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(credentials)
            folder = await _resolve_remote_folder(receiver, folder)

            # 1. 获取 IMAP 全量 UID 列表
            try:
                imap_uids = set(await receiver.fetch_new_message_uids(folder, since_uid=0))
            except Exception as e:
                logger.warning("获取 IMAP UID 列表失败: %s, %s", folder, e)
                return 0

            if not imap_uids:
                return 0

            # 2. 获取缓存中的 UID 集合
            cached_uids = await get_cached_uids(account.id, folder)

            # 3. 找出差异（IMAP 有但缓存没有的 UID）
            missing_uids = imap_uids - cached_uids
            if not missing_uids:
                return 0

            logger.info(
                "补全同步: 账号=%s, 文件夹=%s, IMAP=%d封, 缓存=%d封, 缺失=%d封",
                account.email, folder, len(imap_uids), len(cached_uids), len(missing_uids)
            )

            # 4. 分批拉取缺失的邮件（每批100个UID，避免单次请求过大）
            total_filled = 0
            missing_list = sorted(missing_uids)
            batch_size = 100
            # is_read 校正：只调用一次 fetch_unseen_uids，避免每批重复查询
            unseen_uids = set()
            try:
                unseen_uids = set(await receiver.fetch_unseen_uids(folder))
            except Exception as e:
                logger.debug("获取未读 uid 列表失败，跳过 is_read 校正: %s", e)
            for i in range(0, len(missing_list), batch_size):
                batch = missing_list[i:i + batch_size]
                try:
                    messages = await receiver.fetch_messages_by_uids(folder, batch)
                    messages = [m for m in messages if m.uid > 0]
                    if messages:
                        # 用之前获取的 unseen_uids 校正 is_read
                        for m in messages:
                            m.is_read = m.uid not in unseen_uids
                        cached_batch = []
                        for message in messages:
                            try:
                                detail = await receiver.fetch_message_detail(str(message.uid), folder=folder)
                                detail.is_read = message.is_read
                                cached_detail = await get_cached_message_detail(account.id, detail.uid, folder)
                                effective_message_date = coalesce_message_date(detail.date, message.date, (cached_detail or {}).get("date", ""))
                                body_html, storage_path, _att_count, _inline_count, attachment_records = await _cache_message_assets(
                                    receiver, account, folder, detail
                                )
                                detail.body_html = body_html
                                cached_batch.append(
                                    CachedMessage(
                                        id=f"{account.id}_{detail.uid}",
                                        account_id=account.id,
                                        user_uid=account.user_uid,
                                        uid=detail.uid,
                                        folder=folder,
                                        subject=detail.subject,
                                        from_addr=detail.from_addr,
                                        to_addr=detail.to_addr,
                                        date=normalize_message_date(detail.date, fallback=effective_message_date),
                                        is_read=detail.is_read,
                                        is_starred=detail.is_starred,
                                        has_attachments=bool(detail.attachments),
                                        body_text=detail.body_text,
                                        body_html=detail.body_html,
                                        storage_path=storage_path,
                                        cached_at=time.time(),
                                    )
                                )
                                if attachment_records:
                                    await upsert_cached_attachments(attachment_records)
                            except Exception as exc:
                                logger.debug(
                                    "fill missing detail failed, cache summary only: account=%s folder=%s uid=%s error=%s",
                                    account.email,
                                    folder,
                                    message.uid,
                                    exc,
                                )
                                cached_batch.extend(_messages_to_cached([message], account))
                        await upsert_cached_messages(cached_batch)
                        total_filled += len(messages)
                except Exception as e:
                    logger.warning("补全同步批次失败: %s, UIDs=%s, 错误=%s", folder, batch[:5], e)

            if total_filled > 0:
                logger.info(
                    "补全同步完成: 账号=%s, 文件夹=%s, 补全 %d 封",
                    account.email, folder, total_filled
                )
            return total_filled
        except Exception as e:
            logger.error("补全同步失败: 账号=%s, 文件夹=%s, %s", account.email, folder, e)
            return 0
        finally:
            if receiver:
                try:
                    await receiver.disconnect()
                except Exception as e:
                    logger.debug("同步后断开连接失败: %s", e)


def _messages_to_cached(messages: List[Message], account: Account) -> List[CachedMessage]:
    """将 IMAP 获取的 Message 列表转换为 CachedMessage 列表（用于写入数据库）"""
    return [
        CachedMessage(
            id=f"{account.id}_{m.uid}",
            account_id=account.id,
            user_uid=account.user_uid,
            uid=m.uid,
            folder=m.folder,
            subject=m.subject,
            from_addr=m.from_addr,
            to_addr=m.to_addr,
            date=normalize_message_date(m.date),
            is_read=m.is_read,
            is_starred=m.is_starred,
            has_attachments=m.has_attachments,
            body_text=m.body_text,
            body_html=m.body_html,
            storage_path=getattr(m, "storage_path", ""),
            cached_at=time.time(),
        )
        for m in messages
    ]


async def sync_all_folders(account: Account, folder_paths: List[str], force_full: bool = False, user_uid: str = "") -> int:
    """同步账号的所有文件夹（用于首次添加账号时的全量同步）"""
    total_new = 0
    total_folders = len(folder_paths)
    for i, folder_path in enumerate(folder_paths):
        try:
            count = await sync_folder_to_cache(account, folder_path, force_full=force_full)
            total_new += count
            # 推送同步进度
            if user_uid:
                try:
                    from services.sync import sync_service
                    await sync_service.notify_sync_progress(
                        account.id, folder_path, i + 1, total_folders, user_uid
                    )
                except Exception as e:
                    logger.debug("推送同步进度失败: %s", e)
        except Exception as e:
            logger.warning("同步文件夹 %s 失败，跳过继续: %s", folder_path, e)
    return total_new


async def initial_sync(account_id: str, force_full: bool = False, user_uid: str = ""):
    """首次添加账号时的全量同步（后台任务）

    同步所有核心文件夹的邮件摘要到缓存。
    Microsoft IMAP 在 OAuth 刚完成后可能短暂不可用（"User is authenticated but not connected"），
    因此增加重试逻辑：最多3次，间隔递增（5/10/15秒）。

    force_full: 强制全量同步（rebuild-sync 时使用，确保清空缓存后重新拉取所有邮件）
    """
    try:
        # 查找所有用户中匹配的账号
        accounts = await get_accounts("")
        account = next((a for a in accounts if a.id == account_id), None)
        if not account:
            logger.error("初始同步失败: 找不到账号 %s", account_id)
            return

        # Microsoft IMAP 在 OAuth 刚完成后可能短暂不可用，增加重试
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                # 先尝试连接 IMAP 并获取文件夹列表
                # 如果这一步就失败，说明连接不可用，直接触发重试
                from services.token import ensure_token
                credentials = await ensure_token(account)
                receiver = ProviderFactory.get_receiver(account.provider)
                folders = []
                try:
                    await receiver.connect(credentials)
                    folders = await receiver.fetch_folders()
                    folder_paths = [f.path for f in folders]
                except Exception as e:
                    # 连接/获取文件夹失败，直接抛出异常触发重试
                    raise
                finally:
                    await receiver.disconnect()

                # 同步所有文件夹。Outlook 必须强制包含 INBOX（Microsoft IMAP 的 LIST 命令有时不返回 INBOX，是已知问题）
                if account.provider == "outlook":
                    folder_paths = [f.path for f in folders if f.path]
                    if "INBOX" not in folder_paths:
                        folder_paths.insert(0, "INBOX")
                total = await sync_all_folders(account, folder_paths, force_full=force_full)

                # sync_folder_to_cache 内部会吞掉异常返回0，
                # 区分"邮箱为空"和"连接异常"：文件夹列表已成功获取说明连接正常，0封就是空邮箱
                if total == 0 and account.provider == "outlook" and not folders:
                    raise ConnectionError(
                        "Outlook 初始同步返回0封邮件且无法获取文件夹列表，可能 IMAP 连接异常"
                    )

                logger.info("初始同步完成: 账号=%s, 共同步 %d 封邮件", account.email, total)
                return  # 同步成功，退出
            except Exception as e:
                error_msg = str(e)
                # 判断是否为 Microsoft IMAP 暂时不可用的错误（可重试）
                is_retryable = (
                    "User is authenticated but not connected" in error_msg
                    or "NOOP verification failed" in error_msg
                    or "AUTHENTICATE UNAVAILABLE" in error_msg
                    or "Outlook 初始同步返回0封邮件" in error_msg
                    or "Outlook IMAP 认证成功但连接不可用" in error_msg
                )
                if is_retryable and attempt < max_retries:
                    delay = attempt * 5  # 5/10/15 秒递增
                    logger.warning(
                        "初始同步第 %d 次失败（Microsoft IMAP 暂不可用），%d 秒后重试: %s",
                        attempt, delay, e,
                    )
                    await asyncio.sleep(delay)
                    continue
                # 不可重试或已达到最大重试次数
                logger.error("初始同步异常: %s", e)
                return
    except Exception as e:
        logger.error("初始同步异常: %s", e)
