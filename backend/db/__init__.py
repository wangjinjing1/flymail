import asyncio
import hashlib
import os
import json
import re
import time
from typing import Any, List, Optional
from urllib.parse import unquote, urlparse

import aiomysql
import pymysql

from data_paths import ensure_data_dirs
from models import Account, CachedAttachment, CachedMessage, Notification, Signature, User
from utils.logger import get_logger


logger = get_logger("db")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_CONNECT_RETRY_COUNT = 15
DB_CONNECT_RETRY_DELAY = 2
DB_MESSAGE_BODY_MAX_BYTES = 1024 * 1024
DB_EXECUTEMANY_MAX_BYTES = 2 * 1024 * 1024


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
    translated = translated.replace("filename = excluded.filename,", "filename = VALUES(filename),")
    translated = translated.replace("content_type = excluded.content_type,", "content_type = VALUES(content_type),")
    translated = translated.replace("size = excluded.size,", "size = VALUES(size),")
    translated = translated.replace("content_id = excluded.content_id,", "content_id = VALUES(content_id),")
    translated = translated.replace("is_inline = excluded.is_inline,", "is_inline = VALUES(is_inline),")
    translated = translated.replace("local_path = excluded.local_path,", "local_path = VALUES(local_path),")
    translated = translated.replace(
        "body_text = COALESCE(excluded.body_text, cached_messages.body_text),",
        "body_text = COALESCE(VALUES(body_text), cached_messages.body_text),",
    )
    translated = translated.replace(
        "body_html = COALESCE(excluded.body_html, cached_messages.body_html)",
        "body_html = COALESCE(VALUES(body_html), cached_messages.body_html)",
    )
    translated = translated.replace(
        "storage_path = COALESCE(excluded.storage_path, cached_messages.storage_path),",
        "storage_path = COALESCE(VALUES(storage_path), cached_messages.storage_path),",
    )
    translated = translated.replace(
        "storage_path = COALESCE(excluded.storage_path, cached_messages.storage_path)",
        "storage_path = COALESCE(VALUES(storage_path), cached_messages.storage_path)",
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
        r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+([A-Za-z0-9_]+)\s+ON\s+([A-Za-z0-9_]+)\s*\(",
        sql,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1), match.group(2)


def _truncate_text_bytes(value: Optional[str], max_bytes: int) -> Optional[str]:
    if not value:
        return value
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    truncated = encoded[:max_bytes]
    while truncated:
        try:
            return truncated.decode("utf-8") + "\n<!-- truncated -->"
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return "<!-- truncated -->"


def _estimate_param_size(params: Any) -> int:
    if params is None:
        return 0
    if isinstance(params, (list, tuple)):
        return sum(_estimate_param_size(item) for item in params)
    if isinstance(params, bytes):
        return len(params)
    if isinstance(params, str):
        return len(params.encode("utf-8"))
    return len(str(params).encode("utf-8"))


def build_cached_message_id(account_id: str, folder: str, uid: int) -> str:
    raw_folder = (folder or "INBOX").strip() or "INBOX"
    safe_folder = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_folder).strip("_")
    safe_folder = (safe_folder or "folder")[:80]
    folder_hash = hashlib.sha1(raw_folder.encode("utf-8")).hexdigest()[:12]
    return f"{account_id}_{safe_folder}_{folder_hash}_{int(uid)}"


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
    def __init__(self, pool: aiomysql.Pool, connect_kwargs: dict[str, Any]):
        self._pool = pool
        self._connect_kwargs = dict(connect_kwargs)
        self._pool_lock = asyncio.Lock()
        self._transaction_lock = asyncio.Lock()
        self._transaction_connections: dict[asyncio.Task, aiomysql.Connection] = {}
        self.row_factory = None

    async def _ensure_connected(self):
        async with self._pool_lock:
            if self._pool is None or self._pool.closed:
                self._pool = await aiomysql.create_pool(
                    minsize=1,
                    maxsize=10,
                    **self._connect_kwargs,
                )

    async def _get_transaction_connection(self, task: Optional[asyncio.Task]) -> Optional[aiomysql.Connection]:
        if task is None:
            return None
        async with self._transaction_lock:
            return self._transaction_connections.get(task)

    async def _set_transaction_connection(self, task: asyncio.Task, conn: aiomysql.Connection) -> None:
        async with self._transaction_lock:
            self._transaction_connections[task] = conn

    async def _pop_transaction_connection(self, task: Optional[asyncio.Task]) -> Optional[aiomysql.Connection]:
        if task is None:
            return None
        async with self._transaction_lock:
            return self._transaction_connections.pop(task, None)

    async def execute(self, sql: str, params=None):
        query = _translate_sql(sql)
        command = query.strip().upper()
        current_task = asyncio.current_task()

        if command == "BEGIN":
            return await self._begin_transaction(current_task)
        if command == "COMMIT":
            return await self._finish_transaction(current_task, commit=True)
        if command == "ROLLBACK":
            return await self._finish_transaction(current_task, commit=False)

        index_meta = _extract_create_index_parts(query)
        return await self._execute_with_retry(query, params, index_meta, current_task)

    async def executemany(self, sql: str, param_list):
        query = _translate_sql(sql)
        current_task = asyncio.current_task()
        for attempt in range(2):
            owned_conn = await self._get_transaction_connection(current_task)
            try:
                return await self._executemany_once(query, param_list, owned_conn)
            except (AssertionError, pymysql.err.InterfaceError, pymysql.err.OperationalError):
                if owned_conn is not None:
                    raise
                if attempt == 1:
                    raise

    async def commit(self):
        current_task = asyncio.current_task()
        owned_conn = await self._get_transaction_connection(current_task)
        if owned_conn is not None:
            await owned_conn.commit()

    async def _begin_transaction(self, task: Optional[asyncio.Task]):
        if task is None:
            raise RuntimeError("BEGIN requires an active task")
        existing = await self._get_transaction_connection(task)
        if existing is not None:
            return BufferedCursor([], [], 0, 0)
        await self._ensure_connected()
        conn = await self._pool.acquire()
        try:
            await conn.ping(reconnect=True)
            await conn.begin()
        except Exception:
            self._pool.release(conn)
            raise
        await self._set_transaction_connection(task, conn)
        return BufferedCursor([], [], 0, 0)

    async def _finish_transaction(self, task: Optional[asyncio.Task], *, commit: bool):
        conn = await self._pop_transaction_connection(task)
        if conn is None:
            return BufferedCursor([], [], 0, 0)
        try:
            if commit:
                await conn.commit()
            else:
                await conn.rollback()
        finally:
            self._pool.release(conn)
        return BufferedCursor([], [], 0, 0)

    async def _execute_with_retry(self, query: str, params, index_meta, task: Optional[asyncio.Task]):
        for attempt in range(2):
            owned_conn = await self._get_transaction_connection(task)
            try:
                return await self._execute_once(query, params, index_meta, owned_conn)
            except (AssertionError, pymysql.err.InterfaceError, pymysql.err.OperationalError):
                if owned_conn is not None:
                    raise
                if attempt == 1:
                    raise

    async def _execute_once(self, query: str, params, index_meta, owned_conn: Optional[aiomysql.Connection]):
        if owned_conn is not None:
            return await self._execute_on_connection(owned_conn, query, params, index_meta, commit_after=False)
        await self._ensure_connected()
        async with self._pool.acquire() as conn:
            await conn.ping(reconnect=True)
            return await self._execute_on_connection(conn, query, params, index_meta, commit_after=True)

    async def _executemany_once(self, query: str, param_list, owned_conn: Optional[aiomysql.Connection]):
        if owned_conn is not None:
            async with owned_conn.cursor() as cursor:
                await cursor.executemany(query, param_list)
                return BufferedCursor([], cursor.description, cursor.rowcount, cursor.lastrowid)
        await self._ensure_connected()
        async with self._pool.acquire() as conn:
            await conn.ping(reconnect=True)
            async with conn.cursor() as cursor:
                await cursor.executemany(query, param_list)
                await conn.commit()
                return BufferedCursor([], cursor.description, cursor.rowcount, cursor.lastrowid)

    async def _execute_on_connection(self, conn: aiomysql.Connection, query: str, params, index_meta, *, commit_after: bool):
        async with conn.cursor() as cursor:
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
                    if commit_after:
                        await conn.commit()
                    return BufferedCursor([], [], 0, 0)
                query = re.sub(r"\s+IF\s+NOT\s+EXISTS", "", query, count=1, flags=re.IGNORECASE)
            await cursor.execute(query, params)
            rows = await cursor.fetchall() if cursor.description else []
            if commit_after:
                await conn.commit()
            return BufferedCursor(rows, cursor.description, cursor.rowcount, cursor.lastrowid)


async def _connect_mysql_with_retry() -> MySQLConnection:
    connect_kwargs = _parse_database_url(DATABASE_URL)
    last_error: Exception | None = None
    for attempt in range(1, DB_CONNECT_RETRY_COUNT + 1):
        try:
            pool = await aiomysql.create_pool(
                minsize=1,
                maxsize=10,
                **connect_kwargs,
            )
            return MySQLConnection(pool, connect_kwargs)
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

# 全局单例数据库连接池，避免每次操作都新建连接。
_db_instance: Optional[MySQLConnection] = None
# 保护单例创建，防止并发 get_db() 创建多个连接池。
_db_lock = asyncio.Lock()


async def get_db() -> MySQLConnection:
    """获取全局数据库连接池。"""
    global _db_instance
    if _db_instance is not None and getattr(_db_instance, "_pool", None) is not None:
        await _db_instance._ensure_connected()
        return _db_instance
    async with _db_lock:
        if _db_instance is None or getattr(_db_instance, "_pool", None) is None:
            ensure_data_dirs()
            _db_instance = await _connect_mysql_with_retry()
        else:
            await _db_instance._ensure_connected()
    return _db_instance


async def init_db():
    """初始化数据库表和索引。"""
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
                poll_interval_seconds INTEGER DEFAULT 10,
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
                storage_path LONGTEXT,
                cached_at REAL DEFAULT 0,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            )
        """)
    await db.execute("""
            CREATE TABLE IF NOT EXISTS cached_attachments (
                account_id VARCHAR(191) NOT NULL,
                user_uid VARCHAR(191) NOT NULL,
                uid INTEGER NOT NULL,
                folder VARCHAR(255) NOT NULL,
                part_number INTEGER NOT NULL,
                filename VARCHAR(512) DEFAULT '',
                content_type VARCHAR(255) DEFAULT '',
                size BIGINT DEFAULT 0,
                content_id VARCHAR(512) DEFAULT '',
                is_inline INTEGER DEFAULT 0,
                local_path LONGTEXT,
                cached_at REAL DEFAULT 0,
                PRIMARY KEY (account_id, folder, uid, part_number)
            )
        """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_user ON cached_messages(user_uid)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_folder ON cached_messages(folder)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_account_folder ON cached_messages(account_id, folder)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_uid ON cached_messages(account_id, folder, uid)")
    await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_cached_messages_account_folder_uid ON cached_messages(account_id, folder, uid)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_read ON cached_messages(account_id, folder, is_read)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_cached_attachments_lookup ON cached_attachments(account_id, folder, uid)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts(user_uid)")

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

    await db.execute("""
            CREATE TABLE IF NOT EXISTS account_folder_counts (
                account_id VARCHAR(191) NOT NULL,
                folder_key VARCHAR(64) NOT NULL,
                folder_path VARCHAR(255) NOT NULL,
                display_name VARCHAR(255) NOT NULL,
                total_count INTEGER DEFAULT 0,
                unread_count INTEGER DEFAULT 0,
                cached_count INTEGER DEFAULT 0,
                updated_at REAL DEFAULT 0,
                PRIMARY KEY (account_id, folder_key)
            )
        """)

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
                job_type VARCHAR(64) DEFAULT 'history_sync',
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

    try:
        await db.execute("ALTER TABLE accounts ADD COLUMN hide_email INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("migration add accounts.hide_email ignored: %s", e)
    try:
        await db.execute("ALTER TABLE accounts ADD COLUMN poll_interval_seconds INTEGER DEFAULT 10")
    except Exception as e:
        logger.debug("migration add accounts.poll_interval_seconds ignored: %s", e)

    try:
        await db.execute("ALTER TABLE cached_messages ADD COLUMN has_attachments INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("migration add cached_messages.has_attachments ignored: %s", e)

    try:
        await db.execute("ALTER TABLE cached_messages ADD COLUMN storage_path LONGTEXT")
    except Exception as e:
        logger.debug("migration add cached_messages.storage_path ignored: %s", e)

    try:
        await db.execute("ALTER TABLE notifications ADD COLUMN type VARCHAR(64) DEFAULT 'new_mail'")
    except Exception as e:
        logger.debug("migration add notifications.type ignored: %s", e)

    try:
        await db.execute("ALTER TABLE notifications ADD COLUMN message VARCHAR(1024) DEFAULT ''")
    except Exception as e:
        logger.debug("migration add notifications.message ignored: %s", e)

    try:
        await db.execute("ALTER TABLE history_sync_jobs ADD COLUMN job_type VARCHAR(64) DEFAULT 'history_sync'")
    except Exception as e:
        logger.debug("migration add history_sync_jobs.job_type ignored: %s", e)
    try:
        await db.execute("ALTER TABLE history_sync_jobs ADD COLUMN current_uid INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("migration add history_sync_jobs.current_uid ignored: %s", e)
    try:
        await db.execute("ALTER TABLE history_sync_jobs ADD COLUMN downloaded_attachments INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("migration add history_sync_jobs.downloaded_attachments ignored: %s", e)
    try:
        await db.execute("ALTER TABLE history_sync_jobs ADD COLUMN downloaded_inline_images INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("migration add history_sync_jobs.downloaded_inline_images ignored: %s", e)
    try:
        await db.execute("ALTER TABLE history_sync_jobs ADD COLUMN finished_at REAL DEFAULT 0")
    except Exception as e:
        logger.debug("migration add history_sync_jobs.finished_at ignored: %s", e)

    try:
        await db.execute("ALTER TABLE signatures ADD COLUMN user_uid VARCHAR(191) DEFAULT ''")
    except Exception as e:
        logger.debug("migration add signatures.user_uid ignored: %s", e)

    await db.commit()

async def get_accounts(user_uid: str) -> List[Account]:
    """按用户获取账号；user_uid 为空时返回全部账号。"""
    db = await get_db()
    if user_uid:
        cursor = await db.execute("SELECT * FROM accounts WHERE user_uid = ? ORDER BY created_at DESC", (user_uid,))
    else:
        cursor = await db.execute("SELECT * FROM accounts ORDER BY created_at DESC")
    rows = await cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return [Account(**dict(zip(columns, row))) for row in rows]


async def create_account(account: Account) -> Account:
    """创建邮箱账号。"""
    db = await get_db()
    await db.execute(
        """INSERT INTO accounts
           (id, user_uid, email, provider, credentials_json, status,
            remark, group_name, hide_email, poll_interval_seconds, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (account.id, account.user_uid, account.email, account.provider,
         account.credentials_json, account.status,
         account.remark, account.group_name,
         1 if account.hide_email else 0,
         account.poll_interval_seconds,
         account.created_at, account.updated_at)
    )
    await db.commit()
    return account


async def delete_account(account_id: str, user_uid: str) -> bool:
    """删除当前用户的邮箱账号。"""
    db = await get_db()
    cursor = await db.execute("DELETE FROM accounts WHERE id = ? AND user_uid = ?", (account_id, user_uid))
    await db.commit()
    return cursor.rowcount > 0


async def batch_delete_cached_messages(account_id: str, uids: list[int], folder: str) -> int:
    """Get cached message count for a folder after delete/move operations."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) FROM cached_messages WHERE account_id = ? AND folder = ?",
        (account_id, folder),
    )
    row = await cursor.fetchone()
    return row[0] if row else 0


async def get_cached_uids(account_id: str, folder: str) -> set:
    """Return cached message UIDs for the given account and folder aliases."""
    aliases = _expand_folder_aliases(folder)
    if not aliases:
        return set()
    db = await get_db()
    placeholders = ",".join("?" * len(aliases))
    cursor = await db.execute(
        f"SELECT uid FROM cached_messages WHERE account_id = ? AND folder IN ({placeholders})",
        [account_id] + aliases,
    )
    rows = await cursor.fetchall()
    return {int(row[0]) for row in rows if row and row[0] is not None}


# ==================== 文件夹统计 CRUD ====================

async def upsert_folder_stats(account_id: str, folder: str, total_count: int, unread_count: int) -> None:
    """Persist the latest IMAP folder statistics for the given account and folder."""
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
    cached_count = None
    try:
        cached_count = await get_cached_count(account_id, folder)
    except Exception as exc:
        logger.debug("get cached count for folder counter failed: %s", exc)
    await upsert_account_folder_count(
        account_id,
        folder,
        total_count,
        unread_count,
        cached_count=cached_count,
    )


async def get_folder_stats(account_id: str, folder: str) -> dict:
    """Return folder stats with total_count, unread_count, and updated_at fields."""
    return await _get_folder_stats_by_aliases(account_id, folder)


async def list_folder_stats_by_account(account_id: str) -> List[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT folder, total_count, unread_count, updated_at FROM folder_stats WHERE account_id = ? ORDER BY updated_at DESC, folder ASC",
        (account_id,),
    )
    rows = await cursor.fetchall()
    return [
        {"folder": row[0], "total_count": row[1], "unread_count": row[2], "updated_at": row[3]}
        for row in rows
    ]


async def list_cached_folders_by_account(account_id: str) -> List[str]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT DISTINCT folder FROM cached_messages WHERE account_id = ? ORDER BY folder ASC",
        (account_id,),
    )
    rows = await cursor.fetchall()
    return [row[0] for row in rows if row and row[0]]


async def delete_folder_stats_by_account(account_id: str) -> None:
    db = await get_db()
    await db.execute("DELETE FROM folder_stats WHERE account_id = ?", (account_id,))
    await db.commit()
    await delete_account_folder_counts_by_account(account_id)


CORE_FOLDER_DEFINITIONS = [
    ("inbox", "INBOX", "收件箱", ["INBOX", "Inbox"]),
    ("sent", "Sent Messages", "已发送", ["Sent", "Sent Messages", "Sent Items", "[Gmail]/Sent Mail"]),
    ("drafts", "Drafts", "草稿箱", ["Drafts", "[Gmail]/Drafts"]),
    ("junk", "Junk", "垃圾邮件", ["Junk", "Junk Email", "Spam", "[Gmail]/Spam"]),
    ("trash", "Trash", "已删除", ["Trash", "Deleted", "Deleted Items", "Deleted Messages", "[Gmail]/Trash", "已删除"]),
]


def folder_key_for_path(folder: str) -> str:
    folder_lower = (folder or "").strip().lower()
    extra_aliases = {
        "sent": {"sent mail", "[google mail]/sent mail", "已发送"},
        "drafts": {"[google mail]/drafts", "草稿箱"},
        "junk": {"[google mail]/spam", "垃圾邮件"},
        "trash": {"[google mail]/trash", "已删除"},
    }
    for key, aliases in extra_aliases.items():
        if folder_lower in aliases:
            return key
    for key, _default_path, _display_name, aliases in CORE_FOLDER_DEFINITIONS:
        if any(folder_lower == alias.lower() for alias in aliases):
            return key
    return folder_lower or "inbox"


def folder_display_name_for_key(folder_key: str, fallback: str = "") -> str:
    for key, _default_path, display_name, _aliases in CORE_FOLDER_DEFINITIONS:
        if key == folder_key:
            return display_name
    return fallback or folder_key


def default_path_for_folder_key(folder_key: str) -> str:
    for key, default_path, _display_name, _aliases in CORE_FOLDER_DEFINITIONS:
        if key == folder_key:
            return default_path
    return folder_key


async def upsert_account_folder_count(
    account_id: str,
    folder_path: str,
    total_count: int,
    unread_count: int,
    *,
    cached_count: int | None = None,
) -> None:
    folder_key = folder_key_for_path(folder_path)
    display_name = folder_display_name_for_key(folder_key, folder_path)
    db = await get_db()
    await db.execute(
        """INSERT INTO account_folder_counts
           (account_id, folder_key, folder_path, display_name, total_count, unread_count, cached_count, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON DUPLICATE KEY UPDATE
           folder_path = VALUES(folder_path),
           display_name = VALUES(display_name),
           total_count = VALUES(total_count),
           unread_count = VALUES(unread_count),
           cached_count = COALESCE(VALUES(cached_count), account_folder_counts.cached_count),
           updated_at = VALUES(updated_at)""",
        (
            account_id,
            folder_key,
            folder_path or default_path_for_folder_key(folder_key),
            display_name,
            max(int(total_count or 0), 0),
            max(int(unread_count or 0), 0),
            cached_count,
            time.time(),
        ),
    )
    await db.commit()


async def list_account_folder_counts(account_id: str) -> List[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT folder_key, folder_path, display_name, total_count, unread_count, cached_count, updated_at
           FROM account_folder_counts
           WHERE account_id = ?""",
        (account_id,),
    )
    rows = await cursor.fetchall()
    by_key = {
        row[0]: {
            "folder_key": row[0],
            "folder_path": row[1],
            "display_name": row[2],
            "total_count": int(row[3] or 0),
            "unread_count": int(row[4] or 0),
            "cached_count": int(row[5] or 0),
            "updated_at": float(row[6] or 0),
        }
        for row in rows
    }
    result = []
    for key, default_path, display_name, _aliases in CORE_FOLDER_DEFINITIONS:
        result.append(by_key.get(key) or {
            "folder_key": key,
            "folder_path": default_path,
            "display_name": display_name,
            "total_count": 0,
            "unread_count": 0,
            "cached_count": 0,
            "updated_at": 0,
        })
    return result


async def delete_account_folder_counts_by_account(account_id: str) -> None:
    db = await get_db()
    await db.execute("DELETE FROM account_folder_counts WHERE account_id = ?", (account_id,))
    await db.commit()


async def adjust_account_folder_unread(account_id: str, folder_path: str, delta: int) -> None:
    folder_key = folder_key_for_path(folder_path)
    db = await get_db()
    await db.execute(
        """UPDATE account_folder_counts
           SET unread_count = GREATEST(unread_count + ?, 0),
               updated_at = ?
           WHERE account_id = ? AND folder_key = ?""",
        (int(delta or 0), time.time(), account_id, folder_key),
    )
    await db.commit()


async def get_signature_by_id(sig_id: int, user_uid: str = "") -> Optional[Signature]:
    db = await get_db()
    sql = "SELECT * FROM signatures WHERE id = ?"
    params: list[Any] = [sig_id]
    if user_uid:
        sql += " AND user_uid = ?"
        params.append(user_uid)
    sql += " LIMIT 1"
    cursor = await db.execute(sql, params)
    row = await cursor.fetchone()
    if not row:
        return None
    return Signature(**_row_to_dict(cursor, row))


async def update_signature(sig: Signature) -> bool:
    """Update a signature and keep the default flag unique per user."""
    db = await get_db()
    now = time.time()
    await db.execute("BEGIN")
    try:
        if sig.is_default:
            await db.execute("UPDATE signatures SET is_default = 0 WHERE user_uid = ?", (sig.user_uid or "",))
        cursor = await db.execute(
            """UPDATE signatures SET name = ?, content_html = ?, is_default = ?,
               account_id = ?, updated_at = ?
               WHERE id = ?""",
            (sig.name, sig.content_html, 1 if sig.is_default else 0, sig.account_id or "", now, sig.id)
        )
        await db.execute("COMMIT")
    except Exception:
        await db.execute("ROLLBACK")
        raise
    return cursor.rowcount > 0


async def delete_signature(sig_id: int, user_uid: str = "") -> bool:
    db = await get_db()
    sql = "DELETE FROM signatures WHERE id = ?"
    params: list[Any] = [sig_id]
    if user_uid:
        sql += " AND user_uid = ?"
        params.append(user_uid)
    cursor = await db.execute(sql, params)
    await db.commit()
    return cursor.rowcount > 0


async def get_user_setting(user_uid: str, key: str, default: Any = None) -> Any:
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
            logger.debug("decode user setting failed: user_uid=%s key=%s", user_uid, row[0])
    return result


async def set_user_settings(user_uid: str, settings: dict) -> None:
    """Batch upsert user settings."""
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
           (id, account_id, user_uid, job_type, status, current_folder, current_page, current_uid,
            total_folders, completed_folders, fetched_messages, downloaded_attachments,
            downloaded_inline_images, error_message, created_at, updated_at, finished_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job["id"],
            job["account_id"],
            job["user_uid"],
            job.get("job_type", "history_sync"),
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
        """SELECT id, account_id, user_uid, job_type, status, current_folder, current_page, current_uid,
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
        """SELECT id, account_id, user_uid, job_type, status, current_folder, current_page, current_uid,
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
        """SELECT id, account_id, user_uid, job_type, status, current_folder, current_page, current_uid,
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


async def delete_history_sync_jobs_by_account(account_id: str) -> int:
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM history_sync_jobs WHERE account_id = ?",
        (account_id,),
    )
    await db.commit()
    return cursor.rowcount


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


async def delete_user(user_id: str) -> bool:
    db = await get_db()
    cursor = await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await db.commit()
    return cursor.rowcount > 0


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

# ==================== Rebuilt compatibility layer ====================

def _row_to_dict(cursor, row):
    if not row:
        return None
    columns = [description[0] for description in cursor.description]
    return dict(zip(columns, row))


def _expand_folder_aliases(folder: str) -> list[str]:
    folder = (folder or '').strip() or 'INBOX'
    alias_groups = [
        {'INBOX', 'Inbox'},
        {'Sent', 'Sent Mail', 'Sent Messages', 'Sent Items', '[Gmail]/Sent Mail', '[Google Mail]/Sent Mail', '已发送'},
        {'Drafts', '[Gmail]/Drafts', '[Google Mail]/Drafts', '草稿箱'},
        {'Junk', 'Junk Email', 'Spam', '[Gmail]/Spam', '[Google Mail]/Spam', '垃圾邮件'},
        {'Trash', 'Deleted', 'Deleted Items', 'Deleted Messages', '[Gmail]/Trash', '已删除'},
    ]
    folder_lower = folder.lower()
    for group in alias_groups:
        if any(folder_lower == item.lower() for item in group):
            return sorted(group)
    return [folder]


async def _get_folder_stats_by_aliases(account_id: str, folder: str) -> dict:
    aliases = _expand_folder_aliases(folder)
    db = await get_db()
    placeholders = ','.join('?' * len(aliases))
    cursor = await db.execute(
        f'''SELECT COALESCE(MAX(total_count), 0),
                   COALESCE(MAX(unread_count), 0),
                   COALESCE(MAX(updated_at), 0)
            FROM folder_stats
            WHERE account_id = ? AND folder IN ({placeholders})''',
        [account_id] + aliases,
    )
    row = await cursor.fetchone()
    if row:
        return {'total_count': int(row[0] or 0), 'unread_count': int(row[1] or 0), 'updated_at': float(row[2] or 0)}
    return {'total_count': 0, 'unread_count': 0, 'updated_at': 0}


async def get_account_by_id(account_id: str):
    db = await get_db()
    cursor = await db.execute('SELECT * FROM accounts WHERE id = ? LIMIT 1', (account_id,))
    row = await cursor.fetchone()
    if not row:
        return None
    return Account(**_row_to_dict(cursor, row))


async def get_accounts(user_uid: str) -> List[Account]:
    db = await get_db()
    cursor = await db.execute(
        'SELECT * FROM accounts WHERE user_uid = ? ORDER BY created_at ASC',
        (user_uid,),
    )
    rows = await cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return [Account(**dict(zip(columns, row))) for row in rows]


async def activate_account(account_id: str, user_uid: str = '') -> bool:
    db = await get_db()
    sql = 'UPDATE accounts SET status = ?, updated_at = ? WHERE id = ?'
    params = ['active', time.time(), account_id]
    if user_uid:
        sql += ' AND user_uid = ?'
        params.append(user_uid)
    cursor = await db.execute(sql, params)
    await db.commit()
    return cursor.rowcount > 0


async def deactivate_account(account_id: str, user_uid: str = '') -> bool:
    db = await get_db()
    sql = 'UPDATE accounts SET status = ?, updated_at = ? WHERE id = ?'
    params = ['offline', time.time(), account_id]
    if user_uid:
        sql += ' AND user_uid = ?'
        params.append(user_uid)
    cursor = await db.execute(sql, params)
    await db.commit()
    return cursor.rowcount > 0


async def update_account_credentials(account_id: str, credentials_json: str) -> bool:
    db = await get_db()
    cursor = await db.execute(
        'UPDATE accounts SET credentials_json = ?, updated_at = ? WHERE id = ?',
        (credentials_json, time.time(), account_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def update_account_info(
    account_id: str,
    user_uid: str,
    remark: str = '',
    group_name: str = '',
    hide_email: bool = False,
    poll_interval_seconds: int = 10,
) -> bool:
    db = await get_db()
    interval = min(3600, max(5, int(poll_interval_seconds or 10)))
    cursor = await db.execute(
        '''UPDATE accounts
           SET remark = ?, group_name = ?, hide_email = ?, poll_interval_seconds = ?, updated_at = ?
           WHERE id = ? AND user_uid = ?''',
        (remark, group_name, 1 if hide_email else 0, interval, time.time(), account_id, user_uid),
    )
    await db.commit()
    return cursor.rowcount > 0


async def delete_cached_messages_by_account(account_id: str) -> int:
    db = await get_db()
    cursor = await db.execute('DELETE FROM cached_messages WHERE account_id = ?', (account_id,))
    await db.commit()
    return cursor.rowcount


async def delete_cached_attachments_by_account(account_id: str) -> int:
    db = await get_db()
    cursor = await db.execute('DELETE FROM cached_attachments WHERE account_id = ?', (account_id,))
    await db.commit()
    return cursor.rowcount


async def get_max_cached_uid(user_uid: str, account_id: str, folder: str) -> int:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''SELECT COALESCE(MAX(uid), 0)
            FROM cached_messages
            WHERE user_uid = ? AND account_id = ? AND folder IN ({placeholders})''',
        [user_uid, account_id] + aliases,
    )
    row = await cursor.fetchone()
    return int((row[0] if row else 0) or 0)


async def get_cached_count(account_id: str, folder: str) -> int:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'SELECT COUNT(*) FROM cached_messages WHERE account_id = ? AND folder IN ({placeholders})',
        [account_id] + aliases,
    )
    row = await cursor.fetchone()
    return int((row[0] if row else 0) or 0)


async def get_cached_body_count(account_id: str, folder: str) -> int:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''SELECT COUNT(*)
            FROM cached_messages
            WHERE account_id = ? AND folder IN ({placeholders})
              AND (COALESCE(body_text, '') <> '' OR COALESCE(body_html, '') <> '')''',
        [account_id] + aliases,
    )
    row = await cursor.fetchone()
    return int((row[0] if row else 0) or 0)


async def get_cached_attachment_rows(account_id: str) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        '''SELECT is_inline, local_path
           FROM cached_attachments
           WHERE account_id = ?''',
        (account_id,),
    )
    rows = await cursor.fetchall()
    return [{"is_inline": bool(row[0]), "local_path": row[1] or ""} for row in rows]


async def get_cached_is_read(account_id: str, uid: int, folder: str) -> Optional[bool]:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''SELECT is_read FROM cached_messages
            WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})
            LIMIT 1''',
        [account_id, uid] + aliases,
    )
    row = await cursor.fetchone()
    return bool(row[0]) if row else None


async def get_cached_message_detail(account_id: str, uid: int, folder: str):
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''SELECT id, uid, subject, from_addr, to_addr, date, is_read, is_starred, folder,
                   body_text, body_html, has_attachments, account_id, storage_path
            FROM cached_messages
            WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})
            ORDER BY date DESC LIMIT 1''',
        [account_id, uid] + aliases,
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return {
        'id': str(row[1]),
        'uid': row[1],
        'subject': row[2] or '',
        'from_addr': row[3] or '',
        'to_addr': row[4] or '',
        'date': row[5] or '',
        'is_read': bool(row[6]),
        'is_starred': bool(row[7]),
        'folder': row[8] or folder,
        'body_text': row[9] or '',
        'body_html': row[10] or '',
        'has_attachments': bool(row[11]),
        'account_id': row[12] or account_id,
        'storage_path': row[13] or '',
        'attachments': [],
    }


async def get_cached_attachment(account_id: str, uid: int, folder: str, part_number: int):
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''SELECT account_id, uid, folder, part_number, filename, content_type, size, content_id, is_inline, local_path
            FROM cached_attachments
            WHERE account_id = ? AND uid = ? AND folder IN ({placeholders}) AND part_number = ?
            LIMIT 1''',
        [account_id, uid] + aliases + [part_number],
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return {
        'account_id': row[0], 'uid': row[1], 'folder': row[2], 'part_number': row[3],
        'filename': row[4] or '', 'content_type': row[5] or '', 'size': row[6] or 0,
        'content_id': row[7] or '', 'is_inline': bool(row[8]), 'local_path': row[9] or '',
    }


async def list_cached_attachments(account_id: str, uid: int, folder: str):
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''SELECT part_number, filename, content_type, size, content_id, is_inline, local_path
            FROM cached_attachments
            WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})
            ORDER BY part_number ASC''',
        [account_id, uid] + aliases,
    )
    rows = await cursor.fetchall()
    return [
        {
            'part_number': row[0], 'filename': row[1] or '', 'content_type': row[2] or '',
            'size': row[3] or 0, 'content_id': row[4] or '', 'is_inline': bool(row[5]), 'local_path': row[6] or '',
        }
        for row in rows
    ]


async def update_cached_message_storage_path(account_id: str, uid: int, folder: str, storage_path: str) -> bool:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''UPDATE cached_messages SET storage_path = ?, cached_at = ?
            WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})''',
        [storage_path, time.time(), account_id, uid] + aliases,
    )
    await db.commit()
    return cursor.rowcount > 0


async def update_cached_message_read(account_id: str, uid: int, folder: str, is_read: bool) -> bool:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''UPDATE cached_messages SET is_read = ?, cached_at = ?
            WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})''',
        [1 if is_read else 0, time.time(), account_id, uid] + aliases,
    )
    await db.commit()
    return cursor.rowcount > 0


async def batch_update_cached_messages_read(account_id: str, uids: list[int], folder: str, is_read: bool) -> int:
    if not uids:
        return 0
    aliases = _expand_folder_aliases(folder)
    db = await get_db()
    affected = 0
    for uid in uids:
        placeholders = ','.join('?' * len(aliases))
        cursor = await db.execute(
            f'''UPDATE cached_messages SET is_read = ?, cached_at = ?
                WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})''',
            [1 if is_read else 0, time.time(), account_id, uid] + aliases,
        )
        affected += cursor.rowcount
    await db.commit()
    return affected


async def batch_update_is_read(account_id: str, folder: str, updates: list[tuple[int, int]]) -> int:
    if not updates:
        return 0
    aliases = _expand_folder_aliases(folder)
    db = await get_db()
    affected = 0
    for uid, is_read in updates:
        placeholders = ','.join('?' * len(aliases))
        cursor = await db.execute(
            f'''UPDATE cached_messages SET is_read = ?, cached_at = ?
                WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})''',
            [is_read, time.time(), account_id, uid] + aliases,
        )
        affected += cursor.rowcount
    await db.commit()
    return affected


async def delete_cached_message(account_id: str, uid: int, folder: str) -> bool:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'DELETE FROM cached_messages WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})',
        [account_id, uid] + aliases,
    )
    await db.execute(
        f'DELETE FROM cached_attachments WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})',
        [account_id, uid] + aliases,
    )
    await db.commit()
    return cursor.rowcount > 0


async def batch_delete_cached_messages(account_id: str, uids: list[int], folder: str) -> int:
    if not uids:
        return 0
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    uid_placeholders = ','.join('?' * len(uids))
    db = await get_db()
    cursor = await db.execute(
        f'''DELETE FROM cached_messages WHERE account_id = ? AND folder IN ({placeholders}) AND uid IN ({uid_placeholders})''',
        [account_id] + aliases + uids,
    )
    await db.execute(
        f'''DELETE FROM cached_attachments WHERE account_id = ? AND folder IN ({placeholders}) AND uid IN ({uid_placeholders})''',
        [account_id] + aliases + uids,
    )
    await db.commit()
    return cursor.rowcount


async def purge_deleted_from_cache(account_id: str, folder: str, valid_uids: set[int]) -> int:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'SELECT uid FROM cached_messages WHERE account_id = ? AND folder IN ({placeholders})',
        [account_id] + aliases,
    )
    rows = await cursor.fetchall()
    cached_uids = {int(row[0]) for row in rows if row and row[0] is not None}
    to_delete = sorted(cached_uids - set(valid_uids))
    if not to_delete:
        return 0
    return await batch_delete_cached_messages(account_id, to_delete, folder)


async def upsert_cached_messages(messages: list[CachedMessage]) -> int:
    if not messages:
        return 0
    db = await get_db()
    affected = 0
    for msg in messages:
        message_id = build_cached_message_id(msg.account_id, msg.folder, msg.uid)
        body_text = _truncate_text_bytes(msg.body_text or '', DB_MESSAGE_BODY_MAX_BYTES)
        body_html = _truncate_text_bytes(msg.body_html or '', DB_MESSAGE_BODY_MAX_BYTES)
        cursor = await db.execute(
            '''INSERT INTO cached_messages
               (id, account_id, user_uid, uid, folder, subject, from_addr, to_addr, date,
                is_read, is_starred, has_attachments, body_text, body_html, storage_path, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON DUPLICATE KEY UPDATE
               subject = VALUES(subject),
               from_addr = VALUES(from_addr),
               to_addr = VALUES(to_addr),
               date = VALUES(date),
               is_read = VALUES(is_read),
               is_starred = VALUES(is_starred),
               has_attachments = VALUES(has_attachments),
               body_text = COALESCE(VALUES(body_text), cached_messages.body_text),
               body_html = COALESCE(VALUES(body_html), cached_messages.body_html),
               storage_path = COALESCE(VALUES(storage_path), cached_messages.storage_path),
               cached_at = VALUES(cached_at)''',
            (
                message_id, msg.account_id, msg.user_uid, msg.uid, msg.folder, msg.subject,
                msg.from_addr, msg.to_addr, msg.date, 1 if msg.is_read else 0,
                1 if msg.is_starred else 0, 1 if msg.has_attachments else 0,
                body_text, body_html, msg.storage_path or '', msg.cached_at or time.time(),
            ),
        )
        affected += cursor.rowcount
    await db.commit()
    return affected


async def upsert_cached_attachments(attachments: list[CachedAttachment]) -> int:
    if not attachments:
        return 0
    db = await get_db()
    affected = 0
    for att in attachments:
        cursor = await db.execute(
            '''INSERT INTO cached_attachments
               (account_id, user_uid, uid, folder, part_number, filename, content_type, size, content_id, is_inline, local_path, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON DUPLICATE KEY UPDATE
               filename = VALUES(filename),
               content_type = VALUES(content_type),
               size = VALUES(size),
               content_id = VALUES(content_id),
               is_inline = VALUES(is_inline),
               local_path = VALUES(local_path),
               cached_at = VALUES(cached_at)''',
            (
                att.account_id, att.user_uid, att.uid, att.folder, att.part_number,
                att.filename or '', att.content_type or '', att.size or 0,
                att.content_id or '', 1 if att.is_inline else 0, att.local_path or '', att.cached_at or time.time(),
            ),
        )
        affected += cursor.rowcount
    await db.commit()
    return affected


async def get_cached_messages_by_folder(user_uid: str, account_id: str, folder: str, page: int = 1, page_size: int = 40, read_filter: str = '', attachment_filter: bool = False) -> dict:
    db = await get_db()
    aliases = _expand_folder_aliases(folder)
    folder_placeholders = ','.join('?' * len(aliases))
    conditions = ['user_uid = ?', 'account_id = ?', f'folder IN ({folder_placeholders})']
    params = [user_uid, account_id] + aliases
    if read_filter == 'unread':
        conditions.append('is_read = 0')
    elif read_filter == 'read':
        conditions.append('is_read = 1')
    if attachment_filter:
        conditions.append('has_attachments = 1')
    where_clause = ' AND '.join(conditions)
    cursor = await db.execute(f'SELECT COUNT(*) FROM cached_messages WHERE {where_clause}', params)
    filtered_total = int((await cursor.fetchone())[0] or 0)
    cursor = await db.execute(
        f'''SELECT COUNT(*)
            FROM cached_messages
            WHERE user_uid = ? AND account_id = ? AND folder IN ({folder_placeholders}) AND is_read = 0''',
        [user_uid, account_id] + aliases,
    )
    unread_total = int((await cursor.fetchone())[0] or 0)
    total = filtered_total
    offset = max(0, (page - 1) * page_size)
    cursor = await db.execute(
        f'''SELECT id, uid, subject, from_addr, to_addr, date, is_read, is_starred, folder, has_attachments, account_id
            FROM cached_messages WHERE {where_clause}
            ORDER BY date DESC, uid DESC LIMIT ? OFFSET ?''',
        params + [page_size, offset],
    )
    rows = await cursor.fetchall()
    messages = [{'id': str(row[1]), 'uid': row[1], 'subject': row[2] or '', 'from_addr': row[3] or '', 'to_addr': row[4] or '', 'date': row[5] or '', 'is_read': bool(row[6]), 'is_starred': bool(row[7]), 'folder': row[8] or folder, 'has_attachments': bool(row[9]), 'account_id': row[10] or account_id} for row in rows]
    result = {'messages': messages, 'total': total, 'unread_total': unread_total, 'page': page, 'page_size': page_size}
    result['cached_count'] = filtered_total
    return result


async def search_cached_messages_by_folder(user_uid: str, account_id: str, folder: str, keyword: str, page: int = 1, page_size: int = 40, read_filter: str = '', attachment_filter: bool = False) -> dict:
    trimmed = (keyword or '').strip()
    if not trimmed:
        return await get_cached_messages_by_folder(user_uid, account_id, folder, page, page_size, read_filter, attachment_filter)
    aliases = _expand_folder_aliases(folder)
    folder_placeholders = ','.join('?' * len(aliases))
    conditions = ['user_uid = ?', 'account_id = ?', f'folder IN ({folder_placeholders})', '(subject LIKE ? OR from_addr LIKE ? OR to_addr LIKE ? OR body_text LIKE ? OR body_html LIKE ?)']
    like = '%' + trimmed + '%'
    params = [user_uid, account_id] + aliases + [like, like, like, like, like]
    if read_filter == 'unread':
        conditions.append('is_read = 0')
    elif read_filter == 'read':
        conditions.append('is_read = 1')
    if attachment_filter:
        conditions.append('has_attachments = 1')
    where_clause = ' AND '.join(conditions)
    db = await get_db()
    cursor = await db.execute(f'''SELECT COUNT(*), SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) FROM cached_messages WHERE {where_clause}''', params)
    row = await cursor.fetchone()
    total = int(row[0] or 0) if row else 0
    unread_total = int(row[1] or 0) if row else 0
    offset = max(0, (page - 1) * page_size)
    cursor = await db.execute(f'''SELECT id, uid, subject, from_addr, to_addr, date, is_read, is_starred, folder, has_attachments, account_id FROM cached_messages WHERE {where_clause} ORDER BY date DESC, uid DESC LIMIT ? OFFSET ?''', params + [page_size, offset])
    rows = await cursor.fetchall()
    messages = [{'id': str(row[1]), 'uid': row[1], 'subject': row[2] or '', 'from_addr': row[3] or '', 'to_addr': row[4] or '', 'date': row[5] or '', 'is_read': bool(row[6]), 'is_starred': bool(row[7]), 'folder': row[8] or folder, 'has_attachments': bool(row[9]), 'account_id': row[10] or account_id} for row in rows]
    return {'messages': messages, 'total': total, 'unread_total': unread_total, 'page': page, 'page_size': page_size}


async def get_folder_filter_counts(user_uid: str, account_id: str, folder: str) -> dict:
    aliases = _expand_folder_aliases(folder)
    folder_placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''SELECT COUNT(*),
                   SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN is_read = 1 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN has_attachments = 1 THEN 1 ELSE 0 END)
            FROM cached_messages
            WHERE user_uid = ? AND account_id = ? AND folder IN ({folder_placeholders})''',
        [user_uid, account_id] + aliases,
    )
    row = await cursor.fetchone()
    return {
        'all': int(row[0] or 0) if row else 0,
        'unread': int(row[1] or 0) if row else 0,
        'read': int(row[2] or 0) if row else 0,
        'attachments': int(row[3] or 0) if row else 0,
    }


async def create_notification(notification: Notification) -> Notification:
    db = await get_db()
    await db.execute('''INSERT INTO notifications (id, user_uid, account_id, provider, email, folder, is_read, created_at, type, message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (notification.id, notification.user_uid, notification.account_id, notification.provider, notification.email, notification.folder, 1 if notification.is_read else 0, notification.created_at, notification.type, notification.message))
    await db.commit()
    return notification


async def get_notifications(user_uid: str, limit: int = 50) -> List[Notification]:
    db = await get_db()
    cursor = await db.execute('SELECT id, user_uid, account_id, provider, email, folder, is_read, created_at, type, message FROM notifications WHERE user_uid = ? ORDER BY created_at DESC LIMIT ?', (user_uid, limit))
    rows = await cursor.fetchall()
    return [Notification(**dict(zip([d[0] for d in cursor.description], row))) for row in rows]


async def mark_notification_read(notification_id: str, user_uid: str) -> bool:
    db = await get_db()
    cursor = await db.execute('UPDATE notifications SET is_read = 1 WHERE id = ? AND user_uid = ?', (notification_id, user_uid))
    await db.commit()
    return cursor.rowcount > 0


async def mark_all_notifications_read(user_uid: str) -> int:
    db = await get_db()
    cursor = await db.execute('UPDATE notifications SET is_read = 1 WHERE user_uid = ? AND is_read = 0', (user_uid,))
    await db.commit()
    return cursor.rowcount


async def clear_notifications(user_uid: str) -> int:
    db = await get_db()
    cursor = await db.execute('DELETE FROM notifications WHERE user_uid = ?', (user_uid,))
    await db.commit()
    return cursor.rowcount


async def get_signatures(user_uid: str = '') -> List[Signature]:
    db = await get_db()
    if user_uid:
        cursor = await db.execute('SELECT * FROM signatures WHERE user_uid = ? ORDER BY is_default DESC, id ASC', (user_uid,))
    else:
        cursor = await db.execute('SELECT * FROM signatures ORDER BY is_default DESC, id ASC')
    rows = await cursor.fetchall()
    return [Signature(**dict(zip([d[0] for d in cursor.description], row))) for row in rows]


async def create_signature(sig: Signature) -> Signature:
    db = await get_db()
    now = time.time()
    await db.execute('BEGIN')
    try:
        if sig.is_default:
            await db.execute('UPDATE signatures SET is_default = 0 WHERE user_uid = ?', (sig.user_uid or '',))
        cursor = await db.execute('''INSERT INTO signatures (name, content_html, is_default, account_id, user_uid, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)''', (sig.name, sig.content_html, 1 if sig.is_default else 0, sig.account_id or '', sig.user_uid or '', now, now))
        await db.execute('COMMIT')
    except Exception:
        await db.execute('ROLLBACK')
        raise
    sig.id = cursor.lastrowid
    sig.created_at = now
    sig.updated_at = now
    return sig


def _history_job_row_to_dict(columns: list[str], row) -> dict[str, Any]:
    return dict(zip(columns, row))


async def get_history_sync_job(account_id: str, job_type: str = "history_sync") -> Optional[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, account_id, user_uid, job_type, status, current_folder, current_page, current_uid,
                  total_folders, completed_folders, fetched_messages, downloaded_attachments,
                  downloaded_inline_images, error_message, created_at, updated_at, finished_at
           FROM history_sync_jobs
           WHERE account_id = ? AND job_type = ?
           ORDER BY updated_at DESC, created_at DESC
           LIMIT 1""",
        (account_id, job_type),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    columns = [description[0] for description in cursor.description]
    return _history_job_row_to_dict(columns, row)


async def list_history_sync_jobs(user_uid: str, job_type: str | None = None) -> List[dict]:
    db = await get_db()
    if job_type:
        cursor = await db.execute(
            """SELECT id, account_id, user_uid, job_type, status, current_folder, current_page, current_uid,
                      total_folders, completed_folders, fetched_messages, downloaded_attachments,
                      downloaded_inline_images, error_message, created_at, updated_at, finished_at
               FROM history_sync_jobs
               WHERE user_uid = ? AND job_type = ?
               ORDER BY updated_at DESC, created_at DESC""",
            (user_uid, job_type),
        )
    else:
        cursor = await db.execute(
            """SELECT id, account_id, user_uid, job_type, status, current_folder, current_page, current_uid,
                      total_folders, completed_folders, fetched_messages, downloaded_attachments,
                      downloaded_inline_images, error_message, created_at, updated_at, finished_at
               FROM history_sync_jobs
               WHERE user_uid = ?
               ORDER BY updated_at DESC, created_at DESC""",
            (user_uid,),
        )
    rows = await cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return [_history_job_row_to_dict(columns, row) for row in rows]


async def activate_account(
    account_id: str,
    user_uid: str = '',
    *,
    credentials_json: str | None = None,
    status: str = "active",
) -> bool:
    db = await get_db()
    actual_user_uid = user_uid
    actual_credentials_json = credentials_json

    # Backward compatibility for old call sites:
    # activate_account(account_id, credentials_json, status="connected")
    if actual_credentials_json is None and actual_user_uid and actual_user_uid.lstrip().startswith(("{", "[")):
        actual_credentials_json = actual_user_uid
        actual_user_uid = ''

    assignments = ["status = ?", "updated_at = ?"]
    params: list[Any] = [status, time.time()]
    if actual_credentials_json is not None:
        assignments.append("credentials_json = ?")
        params.append(actual_credentials_json)
    sql = f"UPDATE accounts SET {', '.join(assignments)} WHERE id = ?"
    params.append(account_id)
    if actual_user_uid:
        sql += " AND user_uid = ?"
        params.append(actual_user_uid)
    cursor = await db.execute(sql, params)
    await db.commit()
    return cursor.rowcount > 0


async def deactivate_account(
    account_id: str,
    user_uid: str = '',
    *,
    status: str = "offline",
    clear_credentials: bool = False,
) -> bool:
    db = await get_db()
    assignments = ["status = ?", "updated_at = ?"]
    params: list[Any] = [status, time.time()]
    if clear_credentials:
        assignments.append("credentials_json = ?")
        params.append("")
    sql = f"UPDATE accounts SET {', '.join(assignments)} WHERE id = ?"
    params.append(account_id)
    if user_uid:
        sql += " AND user_uid = ?"
        params.append(user_uid)
    cursor = await db.execute(sql, params)
    await db.commit()
    return cursor.rowcount > 0
