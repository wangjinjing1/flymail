import asyncio
import os
import json
import re
import time
from typing import Any, List, Optional
from urllib.parse import unquote, urlparse

import aiomysql

from data_paths import ensure_data_dirs
from models import Account, CachedMessage, Notification, Signature, User
from utils.logger import get_logger


logger = get_logger("db")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_CONNECT_RETRY_COUNT = 15
DB_CONNECT_RETRY_DELAY = 2


def _parse_database_url(database_url: str) -> dict[str, Any]:
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    parsed = urlparse(database_url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"mysql", "mysql+pymysql", "mysql+aiomysql"}:
        raise RuntimeError(f"Unsupported DATABASE_URL scheme: {parsed.scheme}")
    db_name = parsed.path.lstrip("/")
    if not db_name:
        raise RuntimeError("DATABASE_URL must include a database name")
    charset = "utf8mb4"
    for part in (parsed.query or "").split("&"):
        if part.startswith("charset="):
            charset = part.split("=", 1)[1] or charset
            break
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "db": db_name,
        "charset": charset,
        "autocommit": False,
    }


def _translate_sql(sql: str) -> str:
    translated = sql.replace("?", "%s")
    translated = translated.replace("AUTOINCREMENT", "AUTO_INCREMENT")
    translated = translated.replace("ON CONFLICT(id) DO UPDATE SET", "ON DUPLICATE KEY UPDATE")
    translated = translated.replace("id INTEGER PRIMARY KEY AUTO_INCREMENT", "id BIGINT PRIMARY KEY AUTO_INCREMENT")
    translated = translated.replace("subject = excluded.subject,", "subject = VALUES(subject),")
    translated = translated.replace("from_addr = excluded.from_addr,", "from_addr = VALUES(from_addr),")
    translated = translated.replace("to_addr = excluded.to_addr,", "to_addr = VALUES(to_addr),")
    translated = translated.replace("date = excluded.date,", "date = VALUES(date),")
    translated = translated.replace("is_read = excluded.is_read,", "is_read = VALUES(is_read),")
    translated = translated.replace("is_starred = excluded.is_starred,", "is_starred = VALUES(is_starred),")
    translated = translated.replace("has_attachments = excluded.has_attachments,", "has_attachments = VALUES(has_attachments),")
    translated = translated.replace("cached_at = excluded.cached_at,", "cached_at = VALUES(cached_at),")
    translated = translated.replace(
        "body_text = COALESCE(excluded.body_text, cached_messages.body_text),",
        "body_text = COALESCE(VALUES(body_text), cached_messages.body_text),",
    )
    translated = translated.replace(
        "body_html = COALESCE(excluded.body_html, cached_messages.body_html)",
        "body_html = COALESCE(VALUES(body_html), cached_messages.body_html)",
    )
    translated = translated.replace("INSERT OR REPLACE INTO folder_stats", "INSERT INTO folder_stats")
    translated = translated.replace("PRIMARY KEY (user_uid, key)", "PRIMARY KEY (user_uid, setting_key)")
    translated = translated.replace(" key TEXT NOT NULL,", " setting_key VARCHAR(255) NOT NULL,")
    translated = translated.replace(" key TEXT NOT NULL", " setting_key VARCHAR(255) NOT NULL")
    translated = translated.replace("SELECT value FROM user_settings WHERE user_uid = %s AND key = %s", "SELECT value FROM user_settings WHERE user_uid = %s AND setting_key = %s")
    translated = translated.replace("SELECT key, value FROM user_settings WHERE user_uid = %s AND key IN", "SELECT setting_key, value FROM user_settings WHERE user_uid = %s AND setting_key IN")
    translated = translated.replace("SELECT key, value FROM user_settings WHERE user_uid = %s", "SELECT setting_key, value FROM user_settings WHERE user_uid = %s")
    translated = translated.replace("INSERT INTO user_settings (user_uid, key, value, updated_at)", "INSERT INTO user_settings (user_uid, setting_key, value, updated_at)")
    translated = translated.replace(
        "ON CONFLICT(user_uid, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        "ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = VALUES(updated_at)",
    )
    return translated


def _extract_create_index_parts(sql: str) -> tuple[str, str] | None:
    match = re.search(
        r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+([A-Za-z0-9_]+)\s+ON\s+([A-Za-z0-9_]+)\s*\(",
        sql,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1), match.group(2)


class BufferedCursor:
    def __init__(self, rows=None, description=None, rowcount: int = 0, lastrowid: int = 0):
        self._rows = list(rows or [])
        self._index = 0
        self.description = description or []
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    async def fetchall(self):
        return list(self._rows)

    async def fetchone(self):
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row


class MySQLConnection:
    def __init__(self, connection: aiomysql.Connection):
        self._connection = connection
        self._execute_lock = asyncio.Lock()
        self._transaction_lock = asyncio.Lock()
        self._transaction_owner: Optional[asyncio.Task] = None
        self.row_factory = None

    async def _wait_for_transaction(self):
        current_task = asyncio.current_task()
        if self._transaction_owner is None or self._transaction_owner == current_task:
            return
        await self._transaction_lock.acquire()
        self._transaction_lock.release()

    async def execute(self, sql: str, params=None):
        query = _translate_sql(sql)
        command = query.strip().upper()
        current_task = asyncio.current_task()
        if command == "BEGIN":
            await self._wait_for_transaction()
            await self._transaction_lock.acquire()
            self._transaction_owner = current_task
        else:
            await self._wait_for_transaction()

        async with self._execute_lock:
            index_meta = _extract_create_index_parts(query)
            async with self._connection.cursor() as cursor:
                if index_meta:
                    index_name, table_name = index_meta
                    await cursor.execute(
                        """
                        SELECT COUNT(*)
                        FROM information_schema.statistics
                        WHERE table_schema = DATABASE()
                          AND table_name = %s
                          AND index_name = %s
                        """,
                        (table_name, index_name),
                    )
                    exists_row = await cursor.fetchone()
                    if exists_row and exists_row[0]:
                        return BufferedCursor([], [], 0, 0)
                    query = re.sub(r"\s+IF\s+NOT\s+EXISTS", "", query, count=1, flags=re.IGNORECASE)
                await cursor.execute(query, params)
                rows = await cursor.fetchall() if cursor.description else []
                buffered = BufferedCursor(rows, cursor.description, cursor.rowcount, cursor.lastrowid)

        if command in {"COMMIT", "ROLLBACK"} and self._transaction_owner == current_task:
            self._transaction_owner = None
            if self._transaction_lock.locked():
                self._transaction_lock.release()
        return buffered

    async def executemany(self, sql: str, param_list):
        await self._wait_for_transaction()
        query = _translate_sql(sql)
        async with self._execute_lock:
            async with self._connection.cursor() as cursor:
                await cursor.executemany(query, param_list)
                return BufferedCursor([], cursor.description, cursor.rowcount, cursor.lastrowid)

    async def commit(self):
        await self._wait_for_transaction()
        async with self._execute_lock:
            await self._connection.commit()


async def _connect_mysql_with_retry() -> MySQLConnection:
    connect_kwargs = _parse_database_url(DATABASE_URL)
    last_error: Exception | None = None
    for attempt in range(1, DB_CONNECT_RETRY_COUNT + 1):
        try:
            return MySQLConnection(await aiomysql.connect(**connect_kwargs))
        except Exception as exc:
            last_error = exc
            logger.warning(
                "数据库连接失败，稍后重试 (%d/%d): %s",
                attempt,
                DB_CONNECT_RETRY_COUNT,
                exc,
            )
            if attempt == DB_CONNECT_RETRY_COUNT:
                raise
            await asyncio.sleep(DB_CONNECT_RETRY_DELAY)
    raise last_error or RuntimeError("数据库连接失败")

# 全局单例数据库连接，避免每次操作都新建连接（连接创建开销大，且每次设置 WAL 是冗余操作）
_db_instance: Optional[MySQLConnection] = None
# 保护单例创建的锁，防止并发 get_db() 创建多个连接
_db_lock = asyncio.Lock()


async def get_db() -> MySQLConnection:
    """获取全局单例数据库连接

    使用单例模式复用连接，WAL 模式只在首次连接时设置一次。
    如果旧代码误关了连接，这里会自动重建，避免出现 no active connection。

    修复 P3：用 asyncio.Lock 保护创建逻辑，防止并发 get_db() 创建多个连接导致旧连接泄漏。
    """
    global _db_instance
    # 快速路径：连接已存在直接返回（无锁，性能优先）
    if _db_instance is not None and getattr(_db_instance, "_connection", None) is not None:
        return _db_instance
    # 慢速路径：需要创建连接，加锁防止并发创建
    async with _db_lock:
        # 双重检查：可能在等锁期间已被其他协程创建
        if _db_instance is None or getattr(_db_instance, "_connection", None) is None:
            ensure_data_dirs()
            _db_instance = await _connect_mysql_with_retry()
            # 设置行工厂，让 fetchall 返回的行支持按列名访问
    return _db_instance


async def init_db():
    """初始化数据库：创建所有表和索引

    表结构:
      - accounts: 邮箱账号（provider/credentials/连接状态等）
      - folder_stats: 文件夹统计（邮件数/未读数/同步时间）
      - cached_messages: 邮件摘要缓存（主题/发件人/时间/已读等）
      - notifications: 新邮件通知记录

    迁移策略: 使用 try-except 逐表创建，已存在的表会跳过
    """
    db = await get_db()
    await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id VARCHAR(191) PRIMARY KEY,
                username VARCHAR(191) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(32) NOT NULL DEFAULT 'user',
                status VARCHAR(32) NOT NULL DEFAULT 'active',
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0
            )
        """)
    await db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id VARCHAR(191) PRIMARY KEY,
                user_uid VARCHAR(191) NOT NULL,
                email VARCHAR(255) NOT NULL,
                provider VARCHAR(64) NOT NULL,
                credentials_json LONGTEXT,
                status VARCHAR(64) DEFAULT 'disconnected',
                remark VARCHAR(255) DEFAULT '',
                group_name VARCHAR(255) DEFAULT '',
                hide_email INTEGER DEFAULT 0,
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0
            )
        """)
    await db.execute("""
            CREATE TABLE IF NOT EXISTS cached_messages (
                id VARCHAR(191) PRIMARY KEY,
                account_id VARCHAR(191) NOT NULL,
                user_uid VARCHAR(191) NOT NULL,
                uid INTEGER NOT NULL,
                folder VARCHAR(255) NOT NULL,
                subject VARCHAR(512) DEFAULT '',
                from_addr VARCHAR(512) DEFAULT '',
                to_addr VARCHAR(512) DEFAULT '',
                date VARCHAR(128) DEFAULT '',
                is_read INTEGER DEFAULT 0,
                is_starred INTEGER DEFAULT 0,
                has_attachments INTEGER DEFAULT 0,
                body_text LONGTEXT,
                body_html LONGTEXT,
                cached_at REAL DEFAULT 0,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            )
        """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_user ON cached_messages(user_uid)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_folder ON cached_messages(folder)")
    # 新增索引：按账号+文件夹查询缓存（列表接口核心查询）
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_account_folder ON cached_messages(account_id, folder)")
    # 新增索引：按账号+文件夹+UID查询，用于增量同步时获取最大UID
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_uid ON cached_messages(account_id, folder, uid)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_unified ON cached_messages(user_uid, folder, account_id)")
    # 修复 Q5：新增索引：按账号+文件夹+已读状态查询，用于未读计数和筛选
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_read ON cached_messages(account_id, folder, is_read)")
    # 修复 Q5：新增索引：accounts 表按 user_uid 查询（get_accounts 核心查询，几乎所有API都调用）
    await db.execute("CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts(user_uid)")

    # 通知表：持久化新邮件通知记录
    await db.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id VARCHAR(191) PRIMARY KEY,
                user_uid VARCHAR(191) NOT NULL,
                account_id VARCHAR(191) NOT NULL,
                provider VARCHAR(64) NOT NULL,
                email VARCHAR(255) NOT NULL,
                folder VARCHAR(255) DEFAULT 'INBOX',
                is_read INTEGER DEFAULT 0,
                created_at REAL DEFAULT 0,
                type VARCHAR(64) DEFAULT 'new_mail',
                message VARCHAR(1024) DEFAULT ''
            )
        """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_uid)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(user_uid, is_read)")

    # 文件夹统计表：存储 IMAP 返回的真实邮件总数和未读数
    # 解决缓存只存部分邮件时 COUNT(*) 不等于 IMAP 真实总数的问题
    await db.execute("""
            CREATE TABLE IF NOT EXISTS folder_stats (
                account_id VARCHAR(191) NOT NULL,
                folder VARCHAR(255) NOT NULL,
                total_count INTEGER DEFAULT 0,
                unread_count INTEGER DEFAULT 0,
                updated_at REAL DEFAULT 0,
                PRIMARY KEY (account_id, folder)
            )
        """)

    # 签名模板表：支持多签名模板管理（替代原来 settings.json 中的单一签名）
    await db.execute("""
            CREATE TABLE IF NOT EXISTS signatures (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                name VARCHAR(255) NOT NULL,
                content_html LONGTEXT,
                is_default INTEGER DEFAULT 0,
                account_id VARCHAR(191) DEFAULT '',
                user_uid VARCHAR(191) DEFAULT '',
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0
            )
        """)

    # 修复 D1：用户级配置表，按 user_uid 隔离（unified_account_ids/signature_html/signature_enabled）
    # 替代原来全局 settings.json 中混存的用户级配置，避免多用户互相覆盖
    await db.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_uid VARCHAR(191) NOT NULL,
                setting_key VARCHAR(255) NOT NULL,
                value LONGTEXT,
                updated_at REAL DEFAULT 0,
                PRIMARY KEY (user_uid, setting_key)
            )
        """)
    await db.execute("""
            CREATE TABLE IF NOT EXISTS history_sync_jobs (
                id VARCHAR(191) PRIMARY KEY,
                account_id VARCHAR(191) NOT NULL,
                user_uid VARCHAR(191) NOT NULL,
                status VARCHAR(64) DEFAULT 'pending',
                current_folder VARCHAR(255) DEFAULT '',
                current_page INTEGER DEFAULT 1,
                current_uid INTEGER DEFAULT 0,
                total_folders INTEGER DEFAULT 0,
                completed_folders INTEGER DEFAULT 0,
                fetched_messages INTEGER DEFAULT 0,
                downloaded_attachments INTEGER DEFAULT 0,
                downloaded_inline_images INTEGER DEFAULT 0,
                error_message VARCHAR(1024) DEFAULT '',
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0,
                finished_at REAL DEFAULT 0
            )
        """)

    # 数据库迁移：为已有数据库补充新列（SQLite 不支持 IF NOT EXISTS 加列，需要 try-except）
    try:
        await db.execute("ALTER TABLE accounts ADD COLUMN hide_email INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("迁移加列已存在，忽略 accounts.hide_email: %s", e)

    try:
        await db.execute("ALTER TABLE cached_messages ADD COLUMN has_attachments INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("迁移加列已存在，忽略 cached_messages.has_attachments: %s", e)

    # 通知表新增 type 和 message 字段（兼容旧数据）
    try:
        await db.execute("ALTER TABLE notifications ADD COLUMN type VARCHAR(64) DEFAULT 'new_mail'")
    except Exception as e:
        logger.debug("迁移加列已存在，忽略 notifications.type: %s", e)
    try:
        await db.execute("ALTER TABLE notifications ADD COLUMN message VARCHAR(1024) DEFAULT ''")
    except Exception as e:
        logger.debug("迁移加列已存在，忽略 notifications.message: %s", e)

    # 安全修复 S3：signatures 表添加 user_uid 字段，支持多用户隔离
    try:
        await db.execute("ALTER TABLE history_sync_jobs ADD COLUMN current_uid INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("杩佺Щ鍔犲垪宸插瓨鍦紝蹇界暐 history_sync_jobs.current_uid: %s", e)
    try:
        await db.execute("ALTER TABLE history_sync_jobs ADD COLUMN downloaded_attachments INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("杩佺Щ鍔犲垪宸插瓨鍦紝蹇界暐 history_sync_jobs.downloaded_attachments: %s", e)
    try:
        await db.execute("ALTER TABLE history_sync_jobs ADD COLUMN downloaded_inline_images INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("杩佺Щ鍔犲垪宸插瓨鍦紝蹇界暐 history_sync_jobs.downloaded_inline_images: %s", e)
    try:
        await db.execute("ALTER TABLE history_sync_jobs ADD COLUMN finished_at REAL DEFAULT 0")
    except Exception as e:
        logger.debug("杩佺Щ鍔犲垪宸插瓨鍦紝蹇界暐 history_sync_jobs.finished_at: %s", e)

    try:
        await db.execute("ALTER TABLE signatures ADD COLUMN user_uid VARCHAR(191) DEFAULT ''")
    except Exception as e:
        logger.debug("迁移加列已存在，忽略 signatures.user_uid: %s", e)

    await db.commit()

async def get_accounts(user_uid: str) -> List[Account]:
    """获取账号列表。user_uid 为空字符串时返回所有用户的账号。"""
    db = await get_db()
    if user_uid:
        cursor = await db.execute(
            "SELECT * FROM accounts WHERE user_uid = ?", (user_uid,)
        )
    else:
        cursor = await db.execute("SELECT * FROM accounts")
    rows = await cursor.fetchall()
    # 获取列名
    columns = [description[0] for description in cursor.description]
    return [Account(**dict(zip(columns, row))) for row in rows]


async def get_account_by_id(account_id: str) -> Account | None:
    """按主键直接查询单个账号。

    O4 修复：token 刷新锁内 double-check 使用此函数，避免查询所有用户账号
    （原 get_accounts("") 会加载所有用户的 email 和 credentials_json 到内存，
    存在性能和隐私问题）。
    """
    db = await get_db()
    cursor = await db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
    row = await cursor.fetchone()
    if row is None:
        return None
    columns = [description[0] for description in cursor.description]
    return Account(**dict(zip(columns, row)))


async def create_account(account: Account) -> Account:
    """创建邮箱账号记录，返回新账号的 id"""
    db = await get_db()
    await db.execute(
        """INSERT INTO accounts
           (id, user_uid, email, provider, credentials_json, status,
            remark, group_name, hide_email, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (account.id, account.user_uid, account.email, account.provider,
         account.credentials_json, account.status,
         account.remark, account.group_name,
         1 if account.hide_email else 0,
         account.created_at, account.updated_at)
    )
    await db.commit()
    return account


async def delete_account(account_id: str, user_uid: str) -> bool:
    """删除账号记录

    注意：cached_messages 由 main.py 的 remove_account 接口显式调用
    delete_cached_messages_by_account 删除，此处不再重复删除。
    """
    db = await get_db()
    cursor = await db.execute("DELETE FROM accounts WHERE id = ? AND user_uid = ?", (account_id, user_uid))
    await db.commit()
    return cursor.rowcount > 0


async def update_account_credentials(account_id: str, credentials_json: str) -> bool:
    """更新账号的凭据信息（用于令牌刷新后持久化）"""
    db = await get_db()
    cursor = await db.execute(
        "UPDATE accounts SET credentials_json = ?, updated_at = ? WHERE id = ?",
        (credentials_json, time.time(), account_id)
    )
    await db.commit()
    return cursor.rowcount > 0


async def update_account_info(account_id: str, user_uid: str, remark: str = "", group_name: str = "", hide_email: bool = False) -> bool:
    """更新账号的备注、分组和隐藏邮箱设置"""
    db = await get_db()
    cursor = await db.execute(
        "UPDATE accounts SET remark = ?, group_name = ?, hide_email = ?, updated_at = ? WHERE id = ? AND user_uid = ?",
        (remark, group_name, 1 if hide_email else 0, time.time(), account_id, user_uid)
    )
    await db.commit()
    return cursor.rowcount > 0


# ==================== 通知 CRUD ====================

async def create_notification(notification: Notification) -> Notification:
    """创建通知记录（新邮件、定时发送结果等）"""
    db = await get_db()
    await db.execute(
        "INSERT INTO notifications (id, user_uid, account_id, provider, email, folder, is_read, created_at, type, message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (notification.id, notification.user_uid, notification.account_id,
         notification.provider, notification.email, notification.folder,
         1 if notification.is_read else 0, notification.created_at,
         notification.type, notification.message)
    )
    await db.commit()
    return notification


async def get_notifications(user_uid: str, limit: int = 50) -> List[Notification]:
    """获取用户的通知列表（按时间倒序，最多 limit 条）"""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM notifications WHERE user_uid = ? ORDER BY created_at DESC LIMIT ?",
        (user_uid, limit)
    )
    rows = await cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return [Notification(**dict(zip(columns, row))) for row in rows]


async def mark_notification_read(notification_id: str, user_uid: str) -> bool:
    """标记单条通知为已读"""
    db = await get_db()
    cursor = await db.execute(
        "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_uid = ?",
        (notification_id, user_uid)
    )
    await db.commit()
    return cursor.rowcount > 0


async def mark_all_notifications_read(user_uid: str) -> int:
    """标记用户所有通知为已读，返回更新的行数"""
    db = await get_db()
    cursor = await db.execute(
        "UPDATE notifications SET is_read = 1 WHERE user_uid = ? AND is_read = 0",
        (user_uid,)
    )
    await db.commit()
    return cursor.rowcount


async def clear_notifications(user_uid: str) -> int:
    """清空用户所有通知，返回删除的行数"""
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM notifications WHERE user_uid = ?",
        (user_uid,)
    )
    await db.commit()
    return cursor.rowcount


async def get_unread_notification_count(user_uid: str) -> int:
    """获取用户未读通知数量"""
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_uid = ? AND is_read = 0",
        (user_uid,)
    )
    row = await cursor.fetchone()
    return row[0] if row else 0


# ==================== 邮件缓存 CRUD ====================

async def upsert_cached_messages(messages: List[CachedMessage]) -> int:
    """批量写入/更新邮件缓存（UPSERT）

    使用 INSERT ... ON CONFLICT DO UPDATE 合并为单步操作：
    - 新记录：直接插入
    - 已存在记录：更新摘要字段，不覆盖已有正文（body_text/body_html 用 COALESCE 保留旧值）

    返回写入的记录数。
    """
    if not messages:
        return 0
    db = await get_db()
    await db.executemany(
        """INSERT INTO cached_messages
           (id, account_id, user_uid, uid, folder, subject, from_addr, to_addr,
            date, is_read, is_starred, has_attachments, body_text, body_html, cached_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
            subject = excluded.subject,
            from_addr = excluded.from_addr,
            to_addr = excluded.to_addr,
            date = excluded.date,
            is_read = excluded.is_read,
            is_starred = excluded.is_starred,
            has_attachments = excluded.has_attachments,
            cached_at = excluded.cached_at,
            body_text = COALESCE(excluded.body_text, cached_messages.body_text),
            body_html = COALESCE(excluded.body_html, cached_messages.body_html)""",
        [
            (m.id, m.account_id, m.user_uid, m.uid, m.folder,
             m.subject, m.from_addr, m.to_addr, m.date,
             1 if m.is_read else 0, 1 if m.is_starred else 0,
             1 if m.has_attachments else 0,
             m.body_text or None, m.body_html or None, m.cached_at)
            for m in messages
        ]
    )
    await db.commit()
    return len(messages)


async def batch_update_is_read(account_id: str, folder: str, updates: List[tuple]) -> int:
    """批量更新邮件的 is_read 状态（只更新需要修正的记录）

    updates: [(uid, is_read), ...]  其中 is_read 为 0 或 1
    用于同步后批量校正 is_read，避免逐条 UPDATE 的性能问题。
    """
    if not updates:
        return 0
    db = await get_db()
    await db.executemany(
        "UPDATE cached_messages SET is_read = ? WHERE account_id = ? AND folder = ? AND uid = ?",
        [(v, account_id, folder, uid) for uid, v in updates]
    )
    await db.commit()
    return len(updates)


async def get_cached_unread_count(account_id: str, folder: str) -> int:
    """获取缓存中指定文件夹的未读邮件数量（轻量查询，用于判断是否需要校正 is_read）"""
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) FROM cached_messages WHERE account_id = ? AND folder = ? AND is_read = 0",
        (account_id, folder)
    )
    row = await cursor.fetchone()
    return row[0] if row else 0


async def get_cached_is_read(account_id: str, uid: int, folder: str) -> bool:
    """查询缓存中单封邮件的 is_read 状态（轻量查询，不依赖正文）

    用于 fetch_message_detail 写入缓存时保留已有的 is_read，
    因为 get_cached_message_detail 在正文为空时返回 None，无法获取 is_read。
    """
    db = await get_db()
    cursor = await db.execute(
        "SELECT is_read FROM cached_messages WHERE id = ?",
        (f"{account_id}_{uid}",),
    )
    row = await cursor.fetchone()
    return bool(row[0]) if row else False


async def get_cached_messages_by_folder(
    user_uid: str, account_id: str, folder: str,
    page: int = 1, page_size: int = 40,
    read_filter: str = "", attachment_filter: bool = False,
) -> dict:
    """从缓存分页读取邮件列表（按邮件时间倒序，时间相同时按 UID 倒序）

    返回格式与 list_messages API 一致：{messages, total, page, page_size, unread_total}
    total 和 unread_total 优先从 folder_stats 读取（IMAP 真实总数），
    如果 folder_stats 无记录则回退到 COUNT(*)（兼容旧数据）。

    参数：
        read_filter: "unread"=仅未读, "read"=仅已读, 空=全部
        attachment_filter: True=仅有附件的邮件
    """
    db = await get_db()

    # 构建 WHERE 条件（筛选模式下直接从缓存 COUNT，不依赖 folder_stats）
    conditions = ["user_uid = ?", "account_id = ?", "folder = ?"]
    params: list = [user_uid, account_id, folder]

    if read_filter == "unread":
        conditions.append("is_read = 0")
    elif read_filter == "read":
        conditions.append("is_read = 1")

    if attachment_filter:
        conditions.append("has_attachments = 1")

    where_clause = " AND ".join(conditions)
    has_filter = read_filter or attachment_filter

    # 查询当前文件夹实际已缓存的邮件数量
    cursor = await db.execute(
        f"SELECT COUNT(*) FROM cached_messages WHERE {where_clause}",
        params,
    )
    filtered_total = (await cursor.fetchone())[0]

    if has_filter:
        # 有筛选条件时，直接用筛选后的计数
        total = filtered_total
        unread_total = 0  # 筛选模式下不单独计算未读数
    else:
        # 无筛选时，优先从 folder_stats 获取 IMAP 真实总数
        stats = await get_folder_stats(account_id, folder)
        if stats["total_count"] > 0:
            total = stats["total_count"]
            unread_total = stats["unread_count"]
        else:
            total = filtered_total
            cursor = await db.execute(
                "SELECT COUNT(*) FROM cached_messages WHERE user_uid = ? AND account_id = ? AND folder = ? AND is_read = 0",
                (user_uid, account_id, folder),
            )
            unread_total = (await cursor.fetchone())[0]

    # 分页查询（按邮件时间倒序，时间相同时按 UID 倒序）
    offset = (page - 1) * page_size
    cursor = await db.execute(
        f"""SELECT id, uid, subject, from_addr, to_addr, date, is_read, is_starred, folder, has_attachments
           FROM cached_messages
           WHERE {where_clause}
           ORDER BY date DESC, uid DESC
           LIMIT ? OFFSET ?""",
        params + [page_size, offset],
    )
    rows = await cursor.fetchall()
    messages = [
        {
            "id": str(row[1]),  # 返回 str(uid)，与 IMAP 的 Message.id 格式一致
            "uid": row[1],
            "subject": row[2],
            "from_addr": row[3],
            "to_addr": row[4],
            "date": row[5],
            "is_read": bool(row[6]),
            "is_starred": bool(row[7]),
            "folder": row[8],
            "has_attachments": bool(row[9]),
        }
        for row in rows
    ]

    result = {
        "messages": messages,
        "total": total,
        "unread_total": unread_total,
        "page": page,
        "page_size": page_size,
    }
    # 无筛选时附加缓存统计（兼容旧逻辑）
    if not has_filter:
        stats = await get_folder_stats(account_id, folder)
        result["cached_count"] = filtered_total
        result["stats_updated_at"] = stats["updated_at"]
    return result


async def get_folder_filter_counts(user_uid: str, account_id: str, folder: str) -> dict:
    """获取单账号文件夹各筛选条件的计数

    all 和 unread 优先从 folder_stats 获取（IMAP真实值，与左侧边栏一致），
    read 和 attachments 从 cached_messages 统计（folder_stats 不跟踪这两个维度）。
    当 folder_stats 无记录时，回退到 cached_messages 的 COUNT。
    """
    # 从 folder_stats 获取 IMAP 真实总数和未读数
    stats = await get_folder_stats(account_id, folder)

    # 从缓存统计 read 和 attachments 计数
    db = await get_db()
    cursor = await db.execute(
        """SELECT
            SUM(CASE WHEN is_read = 1 THEN 1 ELSE 0 END) as read_count,
            SUM(CASE WHEN has_attachments = 1 THEN 1 ELSE 0 END) as attachment_count
           FROM cached_messages
           WHERE user_uid = ? AND account_id = ? AND folder = ?""",
        (user_uid, account_id, folder),
    )
    row = await cursor.fetchone()

    # all 和 unread 优先用 folder_stats（与左侧边栏数据源一致，避免缓存不完整导致数字不一致）
    if stats["total_count"] > 0:
        all_count = stats["total_count"]
        unread_count = stats["unread_count"]
    else:
        # folder_stats 无记录时回退到缓存 COUNT（兼容旧数据）
        cursor = await db.execute(
            """SELECT COUNT(*), SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END)
               FROM cached_messages
               WHERE user_uid = ? AND account_id = ? AND folder = ?""",
            (user_uid, account_id, folder),
        )
        fallback_row = await cursor.fetchone()
        all_count = fallback_row[0] if fallback_row else 0
        unread_count = fallback_row[1] if fallback_row else 0

    return {
        "all": all_count,
        "unread": unread_count,
        "read": row[0] if row and row[0] else 0,
        "attachments": row[1] if row and row[1] else 0,
    }


async def get_cached_message_detail(account_id: str, uid: int, folder: str) -> Optional[dict]:
    """从缓存获取单封邮件的完整详情（含正文）

    如果缓存中有 body_html 或 body_text，直接返回（毫秒级），
    避免每次查看邮件都去 IMAP 拉取（秒级）。
    返回 None 表示缓存中没有正文内容，需要从 IMAP 拉取。
    """
    db = await get_db()
    cache_id = f"{account_id}_{uid}"
    cursor = await db.execute(
        """SELECT id, uid, subject, from_addr, to_addr, date, is_read, is_starred,
                  folder, body_text, body_html, has_attachments
           FROM cached_messages
           WHERE id = ?""",
        (cache_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    body_html = row[10] or ""
    body_text = row[9] or ""
    # 如果正文为空，说明列表同步时只存了摘要，需要从 IMAP 拉取
    if not body_html and not body_text:
        return None
    return {
        "id": str(row[1]),
        "uid": row[1],
        "subject": row[2],
        "from_addr": row[3],
        "to_addr": row[4],
        "date": row[5],
        "is_read": bool(row[6]),
        "is_starred": bool(row[7]),
        "folder": row[8],
        "body_text": body_text,
        "body_html": body_html,
        "has_attachments": bool(row[11]),
        "attachments": [],  # 缓存中不存附件列表，需要 IMAP 拉取时补充
    }


async def get_max_cached_uid(user_uid: str, account_id: str, folder: str) -> int:
    """获取文件夹中最大的已缓存 UID（用于增量同步起点）

    返回 0 表示该文件夹没有缓存（需要全量同步）。
    """
    db = await get_db()
    cursor = await db.execute(
        "SELECT MAX(uid) FROM cached_messages WHERE user_uid = ? AND account_id = ? AND folder = ?",
        (user_uid, account_id, folder),
    )
    row = await cursor.fetchone()
    return row[0] if row and row[0] else 0


async def delete_cached_messages_by_account(account_id: str) -> int:
    """删除账号的所有邮件缓存（删除账号时调用）"""
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM cached_messages WHERE account_id = ?",
        (account_id,)
    )
    await db.commit()
    return cursor.rowcount


async def purge_deleted_from_cache(account_id: str, folder: str, existing_uids: set) -> int:
    """清理缓存中已不在 IMAP 服务器上的邮件

    对比缓存中的 UID 和 IMAP 返回的 UID 列表，删除缓存中多余的邮件。
    返回删除的记录数。
    使用临时表避免将所有缓存 UID 加载到 Python 内存。
    """
    if not existing_uids:
        return 0
    db = await get_db()
    # 用临时表存储 IMAP 上存在的 UID，然后用 SQL 子查询删除过期缓存
    # 避免将所有缓存 UID 加载到 Python 内存（万封邮箱时节省大量内存）
    await db.execute("CREATE TEMP TABLE IF NOT EXISTS _tmp_existing_uids (uid INTEGER)")
    await db.execute("DELETE FROM _tmp_existing_uids")
    await db.executemany(
        "INSERT INTO _tmp_existing_uids (uid) VALUES (?)",
        [(uid,) for uid in existing_uids]
    )
    cursor = await db.execute(
        """DELETE FROM cached_messages
           WHERE account_id = ? AND folder = ?
           AND uid NOT IN (SELECT uid FROM _tmp_existing_uids)""",
        (account_id, folder),
    )
    await db.execute("DROP TABLE IF EXISTS _tmp_existing_uids")
    await db.commit()
    return cursor.rowcount


async def delete_cached_message(account_id: str, uid: int, folder: str) -> bool:
    """删除单封邮件缓存（删除/移动邮件后同步缓存）"""
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM cached_messages WHERE account_id = ? AND uid = ? AND folder = ?",
        (account_id, uid, folder)
    )
    await db.commit()
    return cursor.rowcount > 0


async def update_cached_message_read(account_id: str, uid: int, folder: str, is_read: bool) -> bool:
    """更新缓存中邮件的已读状态（标记已读后同步缓存）"""
    db = await get_db()
    cursor = await db.execute(
        "UPDATE cached_messages SET is_read = ? WHERE account_id = ? AND uid = ? AND folder = ?",
        (1 if is_read else 0, account_id, uid, folder)
    )
    await db.commit()
    return cursor.rowcount > 0


async def batch_delete_cached_messages(account_id: str, uids: list[int], folder: str) -> int:
    """批量删除缓存邮件（单次数据库操作，替代逐条删除的 N+1 问题）"""
    if not uids:
        return 0
    db = await get_db()
    placeholders = ",".join("?" * len(uids))
    cursor = await db.execute(
        f"DELETE FROM cached_messages WHERE account_id = ? AND folder = ? AND uid IN ({placeholders})",
        [account_id, folder] + uids
    )
    await db.commit()
    return cursor.rowcount


async def get_cached_count(account_id: str, folder: str) -> int:
    """获取指定文件夹的缓存邮件数量（用于删除后快速更新 folder_stats）"""
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) FROM cached_messages WHERE account_id = ? AND folder = ?",
        (account_id, folder),
    )
    row = await cursor.fetchone()
    return row[0] if row else 0


async def get_cached_uids(account_id: str, folder: str) -> set:
    """获取指定文件夹缓存中所有邮件的 UID 集合（用于补全同步时对比差异）"""
    db = await get_db()
    cursor = await db.execute(
        "SELECT uid FROM cached_messages WHERE account_id = ? AND folder = ?",
        (account_id, folder),
    )
    rows = await cursor.fetchall()
    return {row[0] for row in rows}


async def batch_update_cached_messages_read(account_id: str, uids: list[int], folder: str, is_read: bool) -> int:
    """批量更新缓存邮件已读状态（单次数据库操作，替代逐条更新的 N+1 问题）"""
    if not uids:
        return 0
    db = await get_db()
    placeholders = ",".join("?" * len(uids))
    cursor = await db.execute(
        f"UPDATE cached_messages SET is_read = ? WHERE account_id = ? AND folder = ? AND uid IN ({placeholders})",
        [1 if is_read else 0, account_id, folder] + uids
    )
    await db.commit()
    return cursor.rowcount


# ==================== 文件夹统计 CRUD ====================

async def upsert_folder_stats(account_id: str, folder: str, total_count: int, unread_count: int) -> None:
    """更新文件夹的邮件总数和未读数（IMAP 同步后调用）

    使用 INSERT OR REPLACE 确保始终保存最新的 IMAP 统计数据。
    """
    db = await get_db()
    await db.execute(
        """INSERT INTO folder_stats (account_id, folder, total_count, unread_count, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON DUPLICATE KEY UPDATE
           total_count = VALUES(total_count),
           unread_count = VALUES(unread_count),
           updated_at = VALUES(updated_at)""",
        (account_id, folder, total_count, unread_count, time.time())
    )
    await db.commit()


async def get_folder_stats(account_id: str, folder: str) -> dict:
    """获取文件夹的邮件统计（总数、未读数）

    返回 {"total_count": int, "unread_count": int, "updated_at": float}，无记录时 updated_at 为 0。
    """
    db = await get_db()
    cursor = await db.execute(
        "SELECT total_count, unread_count, updated_at FROM folder_stats WHERE account_id = ? AND folder = ?",
        (account_id, folder),
    )
    row = await cursor.fetchone()
    if row:
        return {"total_count": row[0], "unread_count": row[1], "updated_at": row[2]}
    return {"total_count": 0, "unread_count": 0, "updated_at": 0}


async def delete_folder_stats_by_account(account_id: str) -> None:
    """删除账号的所有文件夹统计（删除账号时调用）"""
    db = await get_db()
    await db.execute("DELETE FROM folder_stats WHERE account_id = ?", (account_id,))
    await db.commit()


# ==================== 签名模板 CRUD ====================

async def get_signatures(user_uid: str = "") -> List[Signature]:
    """获取签名模板列表。user_uid 为空时返回所有（仅管理员场景用），否则按用户过滤。"""
    db = await get_db()
    if user_uid:
        cursor = await db.execute(
            "SELECT * FROM signatures WHERE user_uid = ? ORDER BY is_default DESC, id ASC",
            (user_uid,)
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM signatures ORDER BY is_default DESC, id ASC"
        )
    rows = await cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return [Signature(**dict(zip(columns, row))) for row in rows]


async def get_signature_by_id(sig_id: int, user_uid: str = "") -> Optional[Signature]:
    """根据 ID 获取单个签名模板。传入 user_uid 时校验归属，不匹配返回 None。"""
    db = await get_db()
    if user_uid:
        cursor = await db.execute("SELECT * FROM signatures WHERE id = ? AND user_uid = ?", (sig_id, user_uid))
    else:
        cursor = await db.execute("SELECT * FROM signatures WHERE id = ?", (sig_id,))
    row = await cursor.fetchone()
    if not row:
        return None
    columns = [description[0] for description in cursor.description]
    return Signature(**dict(zip(columns, row)))


async def create_signature(sig: Signature) -> Signature:
    """创建签名模板

    若 is_default=1，先将该用户的其他模板 is_default 设为 0（确保只有一个默认签名）。
    修复 D3：用显式事务包裹，确保清默认+插入的原子性。
    """
    db = await get_db()
    now = time.time()
    # 修复 D3：显式事务，防止清默认后插入失败导致所有默认签名丢失
    await db.execute("BEGIN")
    try:
        if sig.is_default:
            await db.execute("UPDATE signatures SET is_default = 0 WHERE user_uid = ?", (sig.user_uid or "",))
        cursor = await db.execute(
            """INSERT INTO signatures (name, content_html, is_default, account_id, user_uid, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sig.name, sig.content_html, 1 if sig.is_default else 0,
             sig.account_id or "", sig.user_uid or "", now, now)
        )
        await db.execute("COMMIT")
    except Exception:
        await db.execute("ROLLBACK")
        raise
    sig.id = cursor.lastrowid
    sig.created_at = now
    sig.updated_at = now
    return sig


async def update_signature(sig: Signature) -> bool:
    """更新签名模板

    若 is_default=1，先将该用户的其他模板 is_default 设为 0。
    返回是否更新成功。
    修复 D3：用显式事务包裹，确保清默认+更新的原子性。
    """
    db = await get_db()
    now = time.time()
    # 修复 D3：显式事务，防止清默认后更新失败导致所有默认签名丢失
    await db.execute("BEGIN")
    try:
        if sig.is_default:
            await db.execute("UPDATE signatures SET is_default = 0 WHERE user_uid = ?", (sig.user_uid or "",))
        cursor = await db.execute(
            """UPDATE signatures SET name = ?, content_html = ?, is_default = ?,
               account_id = ?, updated_at = ?
               WHERE id = ?""",
            (sig.name, sig.content_html, 1 if sig.is_default else 0,
             sig.account_id or "", now, sig.id)
        )
        await db.execute("COMMIT")
    except Exception:
        await db.execute("ROLLBACK")
        raise
    return cursor.rowcount > 0


async def delete_signature(sig_id: int, user_uid: str = "") -> bool:
    """删除签名模板，返回是否成功。传入 user_uid 时校验归属。"""
    db = await get_db()
    if user_uid:
        cursor = await db.execute("DELETE FROM signatures WHERE id = ? AND user_uid = ?", (sig_id, user_uid))
    else:
        cursor = await db.execute("DELETE FROM signatures WHERE id = ?", (sig_id,))
    await db.commit()
    return cursor.rowcount > 0


# ==================== 聚合收件箱查询 ====================

async def get_unified_inbox_messages(
    user_uid: str,
    account_ids: list,
    page: int = 1,
    page_size: int = 40,
    account_filter: str = "",
    read_filter: str = "",
    attachment_filter: bool = False,
) -> dict:
    """从缓存中聚合多个账号的收件箱邮件，按时间倒序排列

    复用 cached_messages 表中的缓存数据，不需要额外的缓存逻辑。
    现有缓存同步机制（IDLE监听、增量同步）已在维护 INBOX 的缓存数据。

    参数：
        user_uid: 飞牛OS用户ID
        account_ids: 要聚合的账号ID列表
        page: 页码（从1开始）
        page_size: 每页数量
        account_filter: 按账号ID进一步筛选，空=全部
        read_filter: "unread"=仅未读, "read"=仅已读, 空=全部
        attachment_filter: True=仅有附件的邮件
    """
    if not account_ids:
        return {"messages": [], "total": 0, "unread_total": 0, "page": page, "page_size": page_size}

    db = await get_db()
    # where_clause 通过字符串拼接构建，但 conditions 列表中的条件均为硬编码字符串（不接受用户输入），SQL 注入安全
    conditions = ["user_uid = ?", "folder = 'INBOX'"]
    params: list = [user_uid]

    # 限定聚合的账号范围
    placeholders = ",".join("?" * len(account_ids))
    conditions.append(f"account_id IN ({placeholders})")
    params.extend(account_ids)

    # 按账号进一步筛选
    if account_filter and account_filter in account_ids:
        conditions.append("account_id = ?")
        params.append(account_filter)

    # 按已读/未读筛选
    if read_filter == "unread":
        conditions.append("is_read = 0")
    elif read_filter == "read":
        conditions.append("is_read = 1")

    # 按附件筛选
    if attachment_filter:
        conditions.append("has_attachments = 1")

    where_clause = " AND ".join(conditions)

    # 合并 total 和 unread COUNT 为单次查询，减少一次全表扫描
    cursor = await db.execute(
        f"SELECT COUNT(*), SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) FROM cached_messages WHERE {where_clause}",
        params
    )
    row = await cursor.fetchone()
    total = row[0] or 0
    unread_total = row[1] or 0

    # 分页查询（按日期倒序，最新的在前）
    offset = (page - 1) * page_size
    cursor = await db.execute(
        f"""SELECT id, uid, subject, from_addr, to_addr, date, is_read, is_starred, folder, account_id, has_attachments
           FROM cached_messages
           WHERE {where_clause}
           ORDER BY date DESC
           LIMIT ? OFFSET ?""",
        params + [page_size, offset],
    )
    rows = await cursor.fetchall()
    messages = [
        {
            "id": str(row[1]),  # 返回 str(uid)
            "uid": row[1],
            "subject": row[2],
            "from_addr": row[3],
            "to_addr": row[4],
            "date": row[5],
            "is_read": bool(row[6]),
            "is_starred": bool(row[7]),
            "folder": row[8],
            "account_id": row[9],  # 聚合视图需要知道每封邮件的所属账号
            "has_attachments": bool(row[10]),
        }
        for row in rows
    ]

    return {
        "messages": messages,
        "total": total,
        "unread_total": unread_total,
        "page": page,
        "page_size": page_size,
    }


async def get_unified_inbox_filter_counts(user_uid: str, account_ids: list, account_filter: str = "") -> dict:
    """获取聚合收件箱各筛选条件的计数

    一次查询返回 all、unread、read、attachments 四个维度的计数，
    避免前端多次请求。
    """
    if not account_ids:
        return {"all": 0, "unread": 0, "read": 0, "attachments": 0}

    db = await get_db()
    placeholders = ",".join("?" * len(account_ids))
    conditions = [f"user_uid = ?", "folder = 'INBOX'", f"account_id IN ({placeholders})"]
    base_params = [user_uid] + account_ids

    # 按账号进一步筛选
    if account_filter and account_filter in account_ids:
        conditions.append("account_id = ?")
        base_params.append(account_filter)

    where_clause = " AND ".join(conditions)

    # 一次查询获取所有计数
    cursor = await db.execute(
        f"""SELECT
            COUNT(*) as all_count,
            SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) as unread_count,
            SUM(CASE WHEN is_read = 1 THEN 1 ELSE 0 END) as read_count,
            SUM(CASE WHEN has_attachments = 1 THEN 1 ELSE 0 END) as attachment_count
           FROM cached_messages
           WHERE {where_clause}""",
        base_params,
    )
    row = await cursor.fetchone()
    return {
        "all": row[0] if row else 0,
        "unread": row[1] if row else 0,
        "read": row[2] if row else 0,
        "attachments": row[3] if row else 0,
    }


async def get_unified_inbox_stats(user_uid: str, account_ids: list) -> dict:
    """聚合指定账号 INBOX 的 total_count 和 unread_count

    从 folder_stats 表中汇总，比 COUNT(cached_messages) 更准确（因为缓存可能只存了部分邮件）。
    """
    if not account_ids:
        return {"total_count": 0, "unread_count": 0}

    db = await get_db()
    placeholders = ",".join("?" * len(account_ids))
    cursor = await db.execute(
        f"""SELECT COALESCE(SUM(total_count), 0), COALESCE(SUM(unread_count), 0)
           FROM folder_stats
           WHERE folder = 'INBOX' AND account_id IN ({placeholders})""",
        account_ids,
    )
    row = await cursor.fetchone()
    return {
        "total_count": row[0] if row else 0,
        "unread_count": row[1] if row else 0,
    }


# ==================== 用户级配置（D1 修复） ====================


async def get_user_setting(user_uid: str, key: str, default: Any = None) -> Any:
    """读取单个用户级配置项

    value 以 JSON 字符串存储，读取时还原为原始类型。
    """
    db = await get_db()
    cursor = await db.execute(
        "SELECT value FROM user_settings WHERE user_uid = ? AND key = ?",
        (user_uid, key),
    )
    row = await cursor.fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return default


async def set_user_setting(user_uid: str, key: str, value: Any) -> None:
    """写入单个用户级配置项（upsert 语义）"""
    db = await get_db()
    value_json = json.dumps(value, ensure_ascii=False)
    await db.execute(
        """INSERT INTO user_settings (user_uid, key, value, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(user_uid, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (user_uid, key, value_json, time.time()),
    )
    await db.commit()


async def get_user_settings(user_uid: str, keys: Optional[List[str]] = None) -> dict:
    """批量读取用户级配置，返回 dict

    Args:
        user_uid: 用户 ID
        keys: 要读取的 key 列表，None 表示读取该用户全部配置
    """
    db = await get_db()
    if keys:
        placeholders = ",".join("?" * len(keys))
        cursor = await db.execute(
            f"SELECT key, value FROM user_settings WHERE user_uid = ? AND key IN ({placeholders})",
            [user_uid] + list(keys),
        )
    else:
        cursor = await db.execute(
            "SELECT key, value FROM user_settings WHERE user_uid = ?",
            (user_uid,),
        )
    rows = await cursor.fetchall()
    result = {}
    for row in rows:
        try:
            result[row[0]] = json.loads(row[1])
        except (json.JSONDecodeError, TypeError):
            logger.debug("用户配置解析失败 user_uid=%s key=%s", user_uid, row[0])
    return result


async def set_user_settings(user_uid: str, settings: dict) -> None:
    """批量写入用户级配置（upsert 语义）"""
    db = await get_db()
    now = time.time()
    for key, value in settings.items():
        value_json = json.dumps(value, ensure_ascii=False)
        await db.execute(
            """INSERT INTO user_settings (user_uid, key, value, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_uid, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (user_uid, key, value_json, now),
        )
    await db.commit()


async def create_history_sync_job(job: dict) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO history_sync_jobs
           (id, account_id, user_uid, status, current_folder, current_page, current_uid,
            total_folders, completed_folders, fetched_messages, downloaded_attachments,
            downloaded_inline_images, error_message, created_at, updated_at, finished_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job["id"],
            job["account_id"],
            job["user_uid"],
            job.get("status", "pending"),
            job.get("current_folder", ""),
            job.get("current_page", 1),
            job.get("current_uid", 0),
            job.get("total_folders", 0),
            job.get("completed_folders", 0),
            job.get("fetched_messages", 0),
            job.get("downloaded_attachments", 0),
            job.get("downloaded_inline_images", 0),
            job.get("error_message", ""),
            job.get("created_at", time.time()),
            job.get("updated_at", time.time()),
            job.get("finished_at", 0),
        ),
    )
    await db.commit()


async def update_history_sync_job(job_id: str, **fields) -> None:
    if not fields:
        return
    db = await get_db()
    fields["updated_at"] = time.time()
    assignments = ", ".join(f"{key} = ?" for key in fields.keys())
    params = list(fields.values()) + [job_id]
    await db.execute(f"UPDATE history_sync_jobs SET {assignments} WHERE id = ?", params)
    await db.commit()


async def get_history_sync_job(account_id: str) -> Optional[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, account_id, user_uid, status, current_folder, current_page, current_uid,
                  total_folders, completed_folders, fetched_messages, downloaded_attachments,
                  downloaded_inline_images, error_message, created_at, updated_at, finished_at
           FROM history_sync_jobs
           WHERE account_id = ?
           ORDER BY created_at DESC
           LIMIT 1""",
        (account_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    columns = [description[0] for description in cursor.description]
    return dict(zip(columns, row))


async def get_history_sync_job_by_id(job_id: str) -> Optional[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, account_id, user_uid, status, current_folder, current_page, current_uid,
                  total_folders, completed_folders, fetched_messages, downloaded_attachments,
                  downloaded_inline_images, error_message, created_at, updated_at, finished_at
           FROM history_sync_jobs
           WHERE id = ?
           LIMIT 1""",
        (job_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    columns = [description[0] for description in cursor.description]
    return dict(zip(columns, row))


async def list_history_sync_jobs(user_uid: str) -> List[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, account_id, user_uid, status, current_folder, current_page, current_uid,
                  total_folders, completed_folders, fetched_messages, downloaded_attachments,
                  downloaded_inline_images, error_message, created_at, updated_at, finished_at
           FROM history_sync_jobs
           WHERE user_uid = ?
           ORDER BY updated_at DESC, created_at DESC""",
        (user_uid,),
    )
    rows = await cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


async def list_users() -> List[User]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, username, password_hash, role, status, created_at, updated_at FROM users ORDER BY created_at ASC"
    )
    rows = await cursor.fetchall()
    return [User(**dict(zip([description[0] for description in cursor.description], row))) for row in rows]


async def get_user_by_id(user_id: str) -> Optional[User]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, username, password_hash, role, status, created_at, updated_at FROM users WHERE id = ? LIMIT 1",
        (user_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return User(**dict(zip([description[0] for description in cursor.description], row)))


async def get_user_by_username(username: str) -> Optional[User]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, username, password_hash, role, status, created_at, updated_at FROM users WHERE username = ? LIMIT 1",
        (username,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return User(**dict(zip([description[0] for description in cursor.description], row)))


async def create_user(user: User) -> User:
    db = await get_db()
    await db.execute(
        """INSERT INTO users (id, username, password_hash, role, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user.id, user.username, user.password_hash, user.role, user.status, user.created_at, user.updated_at),
    )
    await db.commit()
    return user


async def update_user_password(user_id: str, password_hash: str) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
        (password_hash, time.time(), user_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def update_user_status(user_id: str, status: str) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
        (status, time.time(), user_id),
    )
    await db.commit()
    return cursor.rowcount > 0
