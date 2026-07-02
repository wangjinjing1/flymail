"""Outlook 邮箱 IMAP 接收器

继承 BaseIMAPReceiver，只保留 Outlook 特有的逻辑：
- OAuth2 认证连接
- 文件夹名映射（Outlook 使用不同的文件夹名）
- 邮件按日期+UID 排序
- 时区转换（日期统一转为 UTC）
"""

import asyncio
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import List, Optional
import imaplib
from ..base_imap import BaseIMAPReceiver
from ..base import Credentials, Folder, Message, MessageList
from ..ipv4 import IPv4IMAP4_SSL
from .config import OUTLOOK_IMAP_HOST, OUTLOOK_IMAP_PORT
from utils.logger import get_logger

logger = get_logger("outlook")


def _create_outlook_ssl_context() -> ssl.SSLContext:
    """创建 Outlook IMAP/SMTP 专用 SSL 上下文

    Microsoft 从 2024 年 10 月起强制要求 TLS 1.2+，
    拒绝 TLS 1.0/1.1 连接。显式设置最低 TLS 版本为 1.2，
    避免在旧系统上意外使用不安全的 TLS 版本导致连接失败。

    微软 IMAP 服务器在会话超时/Token 过期/限流时可能不发 close_notify 直接断开，
    OpenSSL 3.0+ 会将此标记为致命 SSL EOF 错误。设置 OP_IGNORE_UNEXPECTED_EOF
    告知 Python 将非优雅 EOF 视为正常连接关闭，让重连逻辑走更合理的路径。
    """
    ctx = ssl.create_default_context()
    # 最低 TLS 1.2（Microsoft 强制要求）
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    # 忽略非优雅 EOF（微软服务器不发 close_notify 直接断开的场景）
    ctx.options |= ssl.OP_IGNORE_UNEXPECTED_EOF
    return ctx


class OutlookIPv4IMAP4_SSL(IPv4IMAP4_SSL):
    """Outlook 专用：使用自定义 SSL 上下文（TLS 1.2 + OP_IGNORE_UNEXPECTED_EOF）

    继承共享 IPv4IMAP4_SSL，覆盖 _get_ssl_context() 使用 Outlook 特有的 SSL 配置。
    """

    def __init__(self, *args, ssl_context=None, **kwargs):
        self._custom_ssl_context = ssl_context
        super().__init__(*args, **kwargs)

    def _get_ssl_context(self):
        return self._custom_ssl_context or _create_outlook_ssl_context()


class OutlookReceiver(BaseIMAPReceiver):
    """Outlook IMAP 邮件收取器

    Outlook 特殊处理：
    - 使用 OAuth2 认证（XOAUTH2）
    - IMAP 标志需要括号包裹（_flags_parenthesized = True）
    - 文件夹名映射（如 Sent Messages → Sent Items）
    - 邮件按日期+UID 排序（Microsoft IMAP 的 UID 不一定等同时间顺序）
    - 日期解析带时区转换（统一转为 UTC）
    - 所有 IMAP 操作都有重连保护（遇到 SSL EOF 自动重连重试）
    """

    _flags_parenthesized = True  # Outlook IMAP 要求 flags 用括号包裹，如 (\\Seen)

    def __init__(self):
        self._conn: Optional[IPv4IMAP4_SSL] = None
        self._credentials: Optional[Credentials] = None

    # ---- 连接管理 ----

    async def connect(self, credentials: Credentials) -> None:
        """建立 IMAP 连接（使用 OAuth2 认证）"""
        self._credentials = credentials
        email = credentials.extra.get("email", "")
        # 在线程中执行阻塞的 IMAP 操作，避免卡住异步事件循环。
        self._conn = await asyncio.to_thread(self._connect_imap, credentials)

    @staticmethod
    def _is_known_outlook_error(error: Exception) -> bool:
        """判断是否为已知的 Microsoft IMAP 特有错误（需要翻译的错误）"""
        msg = str(error)
        known_patterns = [
            "User is authenticated but not connected",
            "AUTHENTICATE FAILED",
            "NOOP verification failed",
            "AUTHENTICATE UNAVAILABLE",
        ]
        return any(p in msg for p in known_patterns)

    @staticmethod
    def _translate_outlook_imap_error(error: Exception) -> str:
        """翻译 Microsoft IMAP 协议级错误为用户可理解的信息

        Microsoft IMAP 返回的错误信息晦涩难懂，如 "User is authenticated but not connected"，
        翻译为更友好的提示，帮助用户理解问题并采取正确的操作。
        """
        msg = str(error)
        if "User is authenticated but not connected" in msg:
            return "Outlook 连接异常：认证成功但IMAP会话不可用，可能需要重新授权或稍后重试"
        if "AUTHENTICATE FAILED" in msg:
            return "Outlook 认证失败：token可能已过期，请重新授权"
        if "NOOP verification failed" in msg:
            return "Outlook 连接验证失败：认证后IMAP会话不可用，请稍后重试或重新授权"
        if "AUTHENTICATE UNAVAILABLE" in msg:
            return "Outlook 认证不可用：IMAP服务可能未启用，请在Outlook设置中开启IMAP访问"
        return msg

    def _is_reconnectable_error(self, error: Exception) -> bool:
        """判断是否为需要重连重试的错误

        包括两类：
        1. SSL EOF / 连接断开（网络层错误）
        2. 'User is authenticated but not connected'（Microsoft IMAP 协议级错误）

        第二类是 Microsoft IMAP 的已知 bug：认证成功后会话不可用，
        重新建立连接通常可以解决。不重连重试的话，所有后续操作都会失败。
        """
        # 判断是否为 SSL EOF / 连接断开等网络层错误
        if isinstance(error, (ssl.SSLError, socket.error, OSError, EOFError, ConnectionError, imaplib.IMAP4.abort)):
            return True
        text = str(error).lower()
        if "unexpected_eof" in text or "eof occurred" in text or "imap 连接已断开" in text:
            return True
        # 判断是否为 Microsoft IMAP 协议级错误
        return "User is authenticated but not connected" in str(error)

    def _force_close_connection(self):
        """强制关闭 IMAP 连接，包括底层 socket，避免残留 CLOSE_WAIT

        imaplib.IMAP4.logout() 在连接已断时会抛异常且不关闭底层 socket，
        导致 socket 处于 CLOSE_WAIT 状态残留。这里强制 shutdown + close 确保释放。
        """
        if self._conn:
            try:
                self._conn.logout()
            except Exception as e:
                logger.debug("强制关闭连接时 logout 失败: %s", e)
            try:
                if hasattr(self._conn, '_sock') and self._conn._sock:
                    try:
                        self._conn._sock.shutdown(socket.SHUT_RDWR)
                    except Exception as e:
                        logger.debug("关闭 socket 失败: %s", e)
                    self._conn._sock.close()
            except Exception as e:
                logger.debug("强制关闭连接失败: %s", e)
            self._conn = None

    def _run_with_reconnect(self, operation):
        """执行 Outlook IMAP 操作，遇到可重连错误时重连并重试一次。

        改进点：
        1. 主动检查 token 是否即将过期（5 分钟内），提前抛出让上层刷新
        2. 重连前强制关闭底层 socket，避免 CLOSE_WAIT 残留
        3. 翻译已知的 Microsoft 特有错误

        设计决策：仅重试一次。原因：Outlook IMAP 的可恢复错误主要是 token 过期和
        SSL 断连，重连后通常能成功；若重连后仍失败，说明是持续性故障，多次重试无意义。
        """
        if not self._credentials:
            raise ConnectionError("Outlook credentials missing")
        # 主动检查 token 是否即将过期（5 分钟内），避免浪费一次失败的 IMAP 操作
        if self._credentials.expires_at <= time.time() + 300:
            raise ConnectionError("Outlook token 即将过期，需要刷新后重连")
        try:
            return operation()
        except Exception as e:
            if not self._is_reconnectable_error(e):
                if self._is_known_outlook_error(e):
                    raise ConnectionError(self._translate_outlook_imap_error(e)) from e
                raise
            logger.warning("Outlook IMAP 连接异常，准备重连后重试: %s", e)
            # 强制关闭旧连接（包括底层 socket），避免残留
            self._force_close_connection()
            # 再次检查 token 是否即将过期
            if self._credentials.expires_at <= time.time() + 60:
                raise ConnectionError(
                    "Outlook token 即将过期或已过期，需要刷新后重连"
                ) from e
            self._conn = self._connect_imap(self._credentials)
            return operation()

    def _connect_imap(self, credentials: Credentials) -> IPv4IMAP4_SSL:
        """同步建立 IMAP 连接，认证后用 NOOP 验证连接可用

        Microsoft IMAP 存在"认证成功但连接不可用"的异常状态，
        表现为后续操作报 "User is authenticated but not connected"。
        在 authenticate 成功后发送 NOOP 命令验证连接是否真正可用，
        如果 NOOP 失败则立即关闭连接并抛出明确错误。

        内置重试机制：
        - "User is authenticated but not connected"：logout 后等 2 秒重试
        - 连接超时（TimeoutError）：等 3 秒重试
        最多尝试 3 次，避免 Microsoft IMAP 瞬时错误导致长时间断连。
        """
        max_attempts = 3
        last_error = None
        for attempt in range(max_attempts):
            # 连接重试退避：第2次等2秒，第3次等4秒，避免快速重连被微软限流
            if attempt > 0:
                wait = min(2 ** attempt, 10)
                time.sleep(wait)
            try:
                conn = OutlookIPv4IMAP4_SSL(OUTLOOK_IMAP_HOST, OUTLOOK_IMAP_PORT, ssl_context=_create_outlook_ssl_context(), timeout=30)
                # 修复 P5: 认证失败时关闭连接，防止 socket 泄漏
                try:
                    # imaplib.authenticate 会自动把返回值做 base64 编码，这里必须返回原始 XOAUTH2 字符串。
                    email = credentials.extra.get('email', '')
                    auth_string = f"user={email}\x01auth=Bearer {credentials.access_token}\x01\x01"
                    conn.authenticate("XOAUTH2", lambda _: auth_string.encode("utf-8"))
                except Exception:
                    # 认证失败，关闭连接防止 socket 泄漏
                    try:
                        conn.logout()
                    except Exception as e:
                        logger.debug("认证失败后关闭连接失败: %s", e)
                    raise
                # 认证后验证连接是否真正可用，Microsoft IMAP 可能认证通过但连接异常
                try:
                    status, _ = conn.noop()
                    if status != "OK":
                        raise imaplib.IMAP4.error("NOOP verification failed after authentication")
                except Exception as e:
                    # NOOP 失败说明连接不可用，关闭后抛出明确错误
                    try:
                        conn.logout()
                    except Exception as ex:
                        logger.debug("NOOP 失败后关闭连接失败: %s", ex)
                    # Microsoft IMAP 瞬时错误，logout 后等待重试
                    if "authenticated but not connected" in str(e).lower() and attempt < max_attempts - 1:
                        logger.warning("Outlook IMAP 认证成功但连接不可用（第 %d 次），2 秒后重试", attempt + 1)
                        time.sleep(2)
                        last_error = e
                        continue
                    raise ConnectionError(
                        f"Outlook IMAP 认证成功但连接不可用，可能需要重新授权。原始错误: {e}"
                    )
                return conn
            except (TimeoutError, socket.timeout) as e:
                if attempt < max_attempts - 1:
                    logger.warning("Outlook IMAP 连接超时（第 %d 次），3 秒后重试", attempt + 1)
                    time.sleep(3)
                    last_error = e
                    continue
                raise ConnectionError(f"Outlook IMAP 连接超时（已重试 {max_attempts} 次）") from e
            except (ConnectionError, imaplib.IMAP4.error):
                raise
            except Exception as e:
                if attempt < max_attempts - 1 and "authenticated but not connected" in str(e).lower():
                    logger.warning("Outlook IMAP 认证异常（第 %d 次），2 秒后重试: %s", attempt + 1, e)
                    time.sleep(2)
                    last_error = e
                    continue
                raise
        # 所有重试都失败
        raise last_error

    async def _reconnect(self) -> None:
        """重连 IMAP（基类 _ensure_connected 调用）"""
        self._conn = await asyncio.to_thread(self._connect_imap, self._credentials)

    async def disconnect(self) -> None:
        """断开连接（强制关闭底层 socket，避免残留）"""
        if self._conn:
            await asyncio.to_thread(self._force_close_connection)

    # ---- 文件夹路径映射 ----

    def _normalize_folder(self, folder: str) -> str:
        """规范化 Outlook 文件夹路径

        只映射其他邮箱平台的默认文件夹名到 Outlook 对应名，
        不映射 Outlook 自身可能使用的短名（如 "Sent"、"Junk"、"Deleted"），
        因为不同 Outlook 账号的文件夹名可能不同（个人账户用 "Sent Items"，
        某些 hotmail 账户用 "Sent"），盲目映射会导致 SELECT 不存在的文件夹。

        映射规则：
        - Gmail/iCloud 的 "Sent Messages" → "Sent Items"（Outlook 不用这个名）
        - 通用 "Spam" → "Junk Email"（Outlook 不用 "Spam"）
        - 通用 "Trash" → "Deleted Items"（Outlook 不用 "Trash"）
        - "Sent"、"Junk"、"Deleted" 等短名不映射（可能是服务器真实文件夹名）
        """
        folder_map = {
            "Sent Messages": "Sent Items",
            "Spam": "Junk Email",
            "Trash": "Deleted Items",
            "Deleted Messages": "Deleted Items",
        }
        return folder_map.get(folder, folder)

    # ---- 文件夹 ----

    async def fetch_folders(self) -> List[Folder]:
        """获取 Outlook 文件夹列表，遇到 SSL EOF 自动重连重试。"""
        if not self._conn:
            raise ConnectionError("Not connected")

        folders = await asyncio.to_thread(
            lambda: self._run_with_reconnect(self._list_folders)
        )
        return folders

    def _list_folders(self) -> List[Folder]:
        """同步获取 Outlook 文件夹列表

        Outlook 常见核心文件夹包括收件箱、已发送、草稿箱、垃圾邮件、已删除。
        如果服务器返回的文件夹命名不同，则保留原始路径并使用中文显示名兜底。
        """
        # Outlook 常见核心文件夹路径（按显示顺序），兼容不同账号返回的命名
        core_folder_map = [
            ("收件箱", ["INBOX"]),
            ("已发送", ["Sent Items", "Sent"]),
            ("草稿箱", ["Drafts"]),
            ("垃圾邮件", ["Junk Email", "Junk", "Spam"]),
            ("已删除", ["Deleted Items", "Deleted", "Trash"]),
        ]

        status, folder_list = self._conn.list()
        # 先收集所有可用的文件夹路径
        available = {"INBOX"}
        if status == "OK":
            for f in folder_list:
                parts = f.decode().split('" ')
                if len(parts) >= 2:
                    name = parts[-1].strip('"')
                    available.add(name)

        # 按核心文件夹顺序生成结果，优先使用服务器真实返回的路径
        result = []
        for display_name, candidates in core_folder_map:
            for path in candidates:
                if path in available:
                    result.append(Folder(
                        name=display_name,
                        path=path,
                        unread_count=0,
                        total_count=0,
                    ))
                    break

        # 追加非核心、非子文件夹（用户自定义文件夹，如"工作"、"项目"等）
        added_paths = {f.path for f in result}
        for path in available:
            if path in added_paths:
                continue
            # 跳过子文件夹（含 / 的，如 "Archive/2024"）
            if "/" in path:
                continue
            result.append(Folder(
                name=path,
                path=path,
                unread_count=0,
                total_count=0,
            ))

        return result

    # ---- 邮件列表 ----

    async def fetch_messages(self, folder: str = "INBOX", page: int = 1, page_size: int = 20) -> MessageList:
        """分页获取邮件列表"""
        if not self._conn:
            raise ConnectionError("Not connected")

        result = await asyncio.to_thread(self._fetch_messages_sync, folder, page, page_size)
        return result

    async def search_messages(self, folder: str, keyword: str, page: int = 1, page_size: int = 20) -> MessageList:
        if not self._conn:
            raise ConnectionError("Not connected")
        return await asyncio.to_thread(
            lambda: self._run_with_reconnect(
                lambda: self._search_messages_sync(folder, keyword, page, page_size)
            )
        )

    def _fetch_messages_sync(self, folder: str, page: int, page_size: int) -> MessageList:
        """同步获取邮件列表（批量 UID FETCH 版本）

        Microsoft IMAP 的 UID 不一定等同邮件时间，所以不能先按 UID 分页再按日期排序；
        否则首页可能只拿到 UID 最大但时间并非最新的一小段邮件，出现排序和分页异常。
        当前策略：先批量获取全量邮件头，按 Date 倒序排序后再分页，确保首页最上面就是最新邮件。
        """
        folder = self._normalize_folder(folder)
        return self._run_with_reconnect(
            lambda: self._fetch_messages_sync_once(folder, page, page_size)
        )

    def _fetch_messages_sync_once(self, folder: str, page: int, page_size: int) -> MessageList:
        """同步获取邮件列表（首屏快速候选策略）

        Microsoft IMAP 的 UID 不等同时间顺序，不能像 Gmail 那样按 UID 切片分页。
        但全量 FETCH 所有邮件头再排序又太慢（batch_size=1 时 1000 封邮件需 1000 次请求）。

        策略：先 UID SEARCH 获取总数，然后只 FETCH 最新候选区间的邮件头。
        候选区间 = 最近 page*page_size*3 个 UID（多取一些防止排序后页内邮件不够），
        排序后取当前页。
        """
        # SELECT 文件夹（只读模式，不修改邮件标志）
        status, _ = self._conn.select(self._quote_mailbox(folder), readonly=True)
        if status != "OK":
            return MessageList(messages=[], total=0, unread_total=0, page=page, page_size=page_size)

        # 使用 UID SEARCH 获取所有邮件的 UID 列表（比序列号更稳定）
        status, data = self._conn.uid('SEARCH', None, 'ALL')
        if status != "OK" or not data or not data[0]:
            return MessageList(messages=[], total=0, unread_total=0, page=page, page_size=page_size)

        all_uids = [int(uid) for uid in data[0].split()]
        total = len(all_uids)

        # 获取未读邮件总数（用于侧边栏计数）
        unread_total = 0
        try:
            s, u_data = self._conn.search(None, "UNSEEN")
            if s == "OK" and u_data and u_data[0]:
                unread_total = len(u_data[0].split())
        except Exception as e:
            logger.debug("获取未读邮件总数失败: %s", e)

        # 快速首屏策略：只 FETCH 候选区间的邮件，而非全量拉取
        # 候选区间取最新 N 个 UID，N = 当前页 * 每页大小 * 2（多取一些防止排序错位）
        # 因为 Outlook UID 不等同时间顺序，最新 UID 区间不一定全是最新邮件，
        # 但实际使用中最新邮件大概率落在最新 UID 区间内。
        candidate_count = min(total, page * page_size * 3)
        candidate_uids = all_uids[-candidate_count:] if total > candidate_count else all_uids

        messages = self._fetch_messages_by_uid_batch(folder, candidate_uids, batch_size=1)
        self._sort_messages(messages)
        start = (page - 1) * page_size
        end = start + page_size
        page_messages = messages[start:end]

        return MessageList(messages=page_messages, total=total, unread_total=unread_total,
                           page=page, page_size=page_size)

    def _fetch_messages_by_uid_batch(self, folder: str, uids: List[int], batch_size: int = 100) -> List[Message]:
        """分批获取邮件头，避免单次 UID FETCH 过大导致 Microsoft IMAP 断开 SSL。"""
        messages: List[Message] = []
        sorted_uids = sorted(uids, reverse=True)
        for i in range(0, len(sorted_uids), batch_size):
            batch = sorted_uids[i:i + batch_size]
            uid_set = ",".join(str(uid) for uid in batch)
            status, msg_data = self._conn.uid(
                'FETCH', uid_set,
                '(FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])'
            )
            if status == "OK":
                parsed = self._parse_batch_fetch_response(msg_data, folder)
                # Microsoft IMAP 的已知问题：单封 FETCH 时响应行可能不含 UID，导致解析为 uid=0；此时用请求的 UID 作为兜底
                if len(batch) == 1 and len(parsed) == 1 and parsed[0].uid == 0:
                    parsed[0].uid = batch[0]
                    parsed[0].id = str(batch[0])
                messages.extend(parsed)
        return messages

    async def fetch_new_message_uids(self, folder: str, since_uid: int) -> List[int]:
        """获取大于指定 UID 的新邮件 UID，遇到 SSL EOF 自动重连重试。"""
        if not self._conn:
            raise ConnectionError("Not connected")
        return await asyncio.to_thread(
            lambda: self._run_with_reconnect(
                lambda: self._fetch_new_uids_sync(folder, since_uid)
            )
        )

    async def fetch_messages_by_uids(self, folder: str, uids: List[int]) -> List[Message]:
        """根据 UID 列表批量获取邮件摘要，遇到 SSL EOF 自动重连重试。"""
        if not self._conn:
            raise ConnectionError("Not connected")
        return await asyncio.to_thread(
            lambda: self._run_with_reconnect(
                lambda: self._fetch_messages_by_uids_sync(folder, uids)
            )
        )

    async def fetch_folder_counts(self, folder_paths: List[str]):
        """获取文件夹计数，遇到 SSL EOF 自动重连重试。"""
        if not self._conn:
            raise ConnectionError("Not connected")
        normalized_paths = [self._normalize_folder(path) for path in folder_paths]
        return await asyncio.to_thread(
            lambda: self._run_with_reconnect(
                lambda: self._fetch_folder_counts_sync(normalized_paths)
            )
        )

    # ---- 邮件排序 ----

    @staticmethod
    def _message_sort_key(message: Message) -> tuple:
        """生成 Outlook 邮件排序键：优先邮件时间，其次 UID。"""
        try:
            # 日期格式可能是 "2026-06-09T05:39:00Z"（UTC）或 "2026-06-09 05:39:00"（旧格式）
            date_str = message.date
            if date_str.endswith('Z'):
                dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            else:
                dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return (dt.timestamp(), message.uid)
        except Exception:
            return (0, message.uid)

    def _sort_messages(self, messages: List[Message]) -> List[Message]:
        """Outlook 按邮件日期+UID排序（覆盖基类的按 UID 排序）"""
        messages.sort(key=self._message_sort_key, reverse=True)
        return messages

    # ---- 需要重连保护的操作（覆盖基类方法）----
    # 基类的 fetch_message_detail、mark_as_read 等方法没有重连保护，
    # Outlook IMAP 连接容易因 SSL EOF 断开，需要覆盖并套上 _run_with_reconnect。

    async def fetch_message_detail(self, message_id: str, folder: str = "INBOX") -> Message:
        """获取邮件详情，遇到 SSL EOF 自动重连重试。"""
        if not self._conn:
            raise ConnectionError("Not connected")
        return await asyncio.to_thread(
            lambda: self._run_with_reconnect(
                lambda: self._fetch_detail_sync(message_id, folder)
            )
        )

    async def mark_as_read(self, message_id: str, folder: str = "INBOX") -> None:
        """标记已读，遇到 SSL EOF 自动重连重试。"""
        if not self._conn:
            raise ConnectionError("Not connected")
        await asyncio.to_thread(
            lambda: self._run_with_reconnect(
                lambda: self._mark_as_read_sync(message_id, folder)
            )
        )

    async def mark_as_unread(self, message_id: str, folder: str = "INBOX") -> None:
        """标记未读，遇到 SSL EOF 自动重连重试。"""
        if not self._conn:
            raise ConnectionError("Not connected")
        await asyncio.to_thread(
            lambda: self._run_with_reconnect(
                lambda: self._mark_as_unread_sync(message_id, folder)
            )
        )

    async def mark_as_read_batch(self, message_ids: list, folder: str = "INBOX") -> int:
        """批量标记已读，遇到 SSL EOF 自动重连重试。

        使用 IMAP UID STORE 逗号分隔 UID 列表，一条命令标记多封，
        避免 N 次网络往返。Outlook IMAP 连接容易断，套上重连保护。
        """
        if not self._conn:
            raise ConnectionError("Not connected")
        if not message_ids:
            return 0
        return await asyncio.to_thread(
            lambda: self._run_with_reconnect(
                lambda: self._mark_as_read_batch_sync(message_ids, folder)
            )
        )

    async def mark_as_unread_batch(self, message_ids: list, folder: str = "INBOX") -> int:
        """批量标记未读，遇到 SSL EOF 自动重连重试。"""
        if not self._conn:
            raise ConnectionError("Not connected")
        if not message_ids:
            return 0
        return await asyncio.to_thread(
            lambda: self._run_with_reconnect(
                lambda: self._mark_as_unread_batch_sync(message_ids, folder)
            )
        )

    async def move_message(self, message_id: str, target_folder: str, source_folder: str = "INBOX") -> None:
        """移动邮件，遇到 SSL EOF 自动重连重试。"""
        if not self._conn:
            raise ConnectionError("Not connected")
        await asyncio.to_thread(
            lambda: self._run_with_reconnect(
                lambda: self._move_message_sync(message_id, target_folder, source_folder)
            )
        )

    async def delete_message(self, message_id: str, folder: str = "INBOX") -> None:
        """删除邮件，遇到 SSL EOF 自动重连重试。"""
        if not self._conn:
            raise ConnectionError("Not connected")
        await asyncio.to_thread(
            lambda: self._run_with_reconnect(
                lambda: self._delete_message_sync(message_id, folder)
            )
        )

    async def move_message_batch(self, message_ids: list, target_folder: str, source_folder: str = "INBOX") -> int:
        """批量移动邮件，遇到 SSL EOF 自动重连重试。

        使用 IMAP UID COPY/STORE 逗号分隔 UID 列表，一次命令操作多封，
        避免 N 次网络往返。Outlook IMAP 连接容易断，套上重连保护。
        """
        if not self._conn:
            raise ConnectionError("Not connected")
        if not message_ids:
            return 0
        return await asyncio.to_thread(
            lambda: self._run_with_reconnect(
                lambda: self._move_message_batch_sync(message_ids, target_folder, source_folder)
            )
        )

    async def delete_message_batch(self, message_ids: list, folder: str = "INBOX") -> int:
        """批量彻底删除邮件，遇到 SSL EOF 自动重连重试。"""
        if not self._conn:
            raise ConnectionError("Not connected")
        if not message_ids:
            return 0
        return await asyncio.to_thread(
            lambda: self._run_with_reconnect(
                lambda: self._delete_message_batch_sync(message_ids, folder)
            )
        )

    async def fetch_attachment_data(self, message_id: str, folder: str, part_number: int):
        """获取附件数据，遇到 SSL EOF 自动重连重试。"""
        if not self._conn:
            raise ConnectionError("Not connected")
        return await asyncio.to_thread(
            lambda: self._run_with_reconnect(
                lambda: self._fetch_attachment_sync(message_id, folder, part_number)
            )
        )
