"""iCloud 邮箱 IMAP 接收器

继承 BaseIMAPReceiver，只保留 iCloud 特有的逻辑：
- IPv4 强制连接（iCloud IPv6 不稳定）
- 登录后刷新 CAPABILITY（iCloud 初始 greeting 只暴露 XAPPLEPUSHSERVICE）
- 文件夹列表解析
- 邮件列表获取
"""

import asyncio
import imaplib
import socket
import ssl
import time
import re
from typing import List, Optional, Dict
from ..base_imap import BaseIMAPReceiver
from ..base import Credentials, Folder, Message, MessageList
from providers.ipv4 import IPv4IMAP4_SSL
from providers.icloud.config import IMAP_HOST, IMAP_PORT
from utils.logger import get_logger

logger = get_logger("icloud")


class ICloudReceiver(BaseIMAPReceiver):
    """iCloud邮箱IMAP接收器

    iCloud 特殊处理：
    - 使用 IPv4IMAP4_SSL 强制 IPv4 连接
    - IMAP 标志需要括号包裹（_flags_parenthesized = True）
    - 登录后刷新 CAPABILITY（初始 greeting 只暴露 XAPPLEPUSHSERVICE）
    - 新邮件监听由 sync.py 的 PollConnection 管理（aioimaplib 异步 STATUS 轮询）
    - 内部使用 self.conn 存储连接，通过 _conn 属性别名与基类兼容
    """

    _flags_parenthesized = True  # iCloud IMAP 要求 flags 用括号包裹，如 (\\Seen)

    IMAP_HOST = IMAP_HOST
    IMAP_PORT = IMAP_PORT
    TIMEOUT = 30  # 单个 socket 操作超时30秒

    def __init__(self):
        self.conn: Optional[IPv4IMAP4_SSL] = None
        self.email_addr: str = ""
        self._auth_code: str = ""  # 保存应用专用密码，用于自动重连

    # ---- 连接属性别名 ----
    # iCloud 内部使用 self.conn，基类使用 self._conn，通过属性别名兼容

    @property
    def _conn(self):
        """属性别名：基类通过 self._conn 访问 IMAP 连接"""
        return self.conn

    @_conn.setter
    def _conn(self, value):
        """属性别名：基类设置 self._conn = None 时同步到 self.conn"""
        self.conn = value

    # ---- 连接管理 ----

    async def connect(self, credentials: Credentials) -> None:
        """连接到iCloud邮箱IMAP服务器（在线程池中执行阻塞操作）"""
        self.email_addr = credentials.extra.get("email", "")
        self._auth_code = credentials.access_token  # iCloud邮箱使用应用专用密码

        try:
            # 在线程池中执行阻塞的 IMAP 连接，避免卡住事件循环
            self.conn = await asyncio.to_thread(self._connect_imap, self.email_addr, self._auth_code)
        except Exception as e:
            self.conn = None
            raise Exception(f"iCloud邮箱连接失败: {str(e)}")

    def _connect_imap(self, email_addr: str, auth_code: str) -> IPv4IMAP4_SSL:
        """同步建立 IMAP 连接（在线程池中运行，使用 IPv4 强制子类）

        内置 SSL 握手重试（最多 3 次，间隔 3 秒），
        Apple IMAP 服务器偶尔会出现短暂的 SSL 拒绝连接，
        等待几秒后重试通常就能成功。

        关键步骤：登录后必须刷新 CAPABILITY。
        iCloud 初始 greeting 只暴露 XAPPLEPUSHSERVICE，不含标准 IMAP 能力。
        登录后执行 CAPABILITY 命令，服务端会返回完整能力列表。
        """
        max_ssl_retries = 3
        last_error = None
        for attempt in range(max_ssl_retries):
            try:
                conn = IPv4IMAP4_SSL(self.IMAP_HOST, self.IMAP_PORT, timeout=self.TIMEOUT)
                # 修复 P5: 登录失败时关闭连接，防止 socket 泄漏
                try:
                    conn.login(email_addr, auth_code)
                except Exception:
                    # 登录失败，关闭连接防止 socket 泄漏
                    try:
                        conn.logout()
                    except Exception as e:
                        logger.debug("登录失败后关闭连接失败: %s", e)
                    raise
                # 登录后刷新 CAPABILITY：iCloud 初始 greeting 只暴露 XAPPLEPUSHSERVICE
                try:
                    conn.capability()
                except Exception as e:
                    logger.warning("iCloud CAPABILITY 刷新失败（不影响连接）: %s", e)
                return conn
            except ssl.SSLError as e:
                if attempt < max_ssl_retries - 1:
                    logger.warning("iCloud SSL 握手失败（第 %d 次），3 秒后重试: %s", attempt + 1, e)
                    time.sleep(3)
                    last_error = e
                    continue
                raise
            except (TimeoutError, socket.timeout) as e:
                if attempt < max_ssl_retries - 1:
                    logger.warning("iCloud IMAP 连接超时（第 %d 次），3 秒后重试", attempt + 1)
                    time.sleep(3)
                    last_error = e
                    continue
                raise
        raise last_error

    async def _reconnect(self) -> None:
        """重连 IMAP（基类 _ensure_connected 调用）"""
        self.conn = await asyncio.to_thread(self._connect_imap, self.email_addr, self._auth_code)

    async def disconnect(self) -> None:
        """断开连接"""
        if self.conn:
            try:
                await asyncio.to_thread(self._disconnect_sync)
            except Exception as e:
                logger.debug("断开连接失败: %s", e)
            self.conn = None

    def _disconnect_sync(self) -> None:
        """同步断开连接（在线程池中运行）"""
        try:
            self.conn.close()
        except Exception as e:
            logger.debug("关闭 IMAP 连接失败: %s", e)
        try:
            self.conn.logout()
        except Exception as e:
            logger.debug("登出 IMAP 连接失败: %s", e)

    # ---- 文件夹 ----

    async def fetch_folders(self) -> List[Folder]:
        """获取邮箱文件夹列表"""
        if not self.conn:
            raise Exception("未连接到邮箱服务器")

        try:
            return await asyncio.to_thread(self._fetch_folders_sync)
        except Exception as e:
            raise Exception(f"获取文件夹失败: {str(e)}")

    def _fetch_folders_sync(self) -> List[Folder]:
        """同步获取文件夹列表（在线程池中运行）

        iCloud邮箱标准文件夹: INBOX, Sent Messages, Drafts, Junk, Deleted Messages
        其他自定义文件夹会被过滤掉
        """
        status, folders = self.conn.list()
        result = []

        # iCloud邮箱核心文件夹映射：显示名 → 可能的IMAP路径（不同账号可能不同）
        core_folder_map = [
            ("收件箱", ["INBOX"]),
            ("已发送", ["Sent Messages", "Sent"]),
            ("草稿箱", ["Drafts"]),
            ("垃圾邮件", ["Junk", "Spam"]),
            ("已删除", ["Deleted Messages", "Trash"]),
        ]

        # 先收集所有文件夹
        all_folders = []
        for folder_data in folders:
            if isinstance(folder_data, bytes):
                folder_str = folder_data.decode("utf-8", errors="ignore")
            else:
                folder_str = str(folder_data)

            # 解析文件夹名 - iCloud邮箱格式: (\HasNoChildren) "/" "INBOX"
            folder_name = None
            match = re.search(r'"([^"]+)"$', folder_str)
            if match:
                folder_name = match.group(1)
            else:
                parts = folder_str.split()
                if parts:
                    folder_name = parts[-1]

            if folder_name:
                all_folders.append(folder_name)

        # 按核心文件夹顺序排列，匹配可能的多种路径名
        for display_name, possible_paths in core_folder_map:
            matched_path = None
            for p in possible_paths:
                if p in all_folders:
                    matched_path = p
                    break
            if matched_path:
                result.append(Folder(
                    name=display_name,
                    path=matched_path,
                    unread_count=0,
                    total_count=0,
                ))

        # 添加其他非核心、非子文件夹
        # 已添加的核心文件夹路径集合，用于跳过
        added_paths = {f.path for f in result}
        for folder_name in all_folders:
            # 跳过已添加的核心文件夹
            if folder_name in added_paths:
                continue
            # 跳过子文件夹（包含 / 的）
            if "/" in folder_name:
                continue

            # iCloud文件夹名都是英文，直接使用
            result.append(Folder(
                name=folder_name,
                path=folder_name,
                unread_count=0,
                total_count=0,
            ))

        return result

    # ---- 邮件列表 ----

    async def fetch_messages(self, folder: str = "INBOX", page: int = 1, page_size: int = 20) -> MessageList:
        """获取邮件列表"""
        if not self.conn:
            raise Exception("未连接到邮箱服务器")

        try:
            return await asyncio.to_thread(self._fetch_messages_sync, folder, page, page_size)
        except Exception as e:
            raise Exception(f"获取邮件失败: {str(e)}")

    def _fetch_messages_sync(self, folder: str, page: int, page_size: int) -> MessageList:
        """同步获取邮件列表（在线程池中运行，只获取头部不下载正文）

        使用 UID SEARCH + 批量 UID FETCH 替代逐封 FETCH，减少 IMAP 命令往返次数。
        - UID SEARCH: 获取所有邮件的 UID 列表（比序列号更稳定，不受邮箱变动影响）
        - 批量 UID FETCH: 用逗号拼接 UID 集合，一次请求获取整页邮件摘要

        已读/未读状态判断：
        使用 FETCH (FLAGS BODY.PEEK[HEADER.FIELDS ...]) 一次请求同时获取 FLAGS 和头部。
        - BODY.PEEK 不会隐式设置 \\Seen 标志
        - FLAGS 中包含 \\Seen 表示已读，不包含表示未读
        """
        status, data = self.conn.select(self._quote_mailbox(folder), readonly=True)
        if status != "OK":
            return MessageList(messages=[], total=0, page=page, page_size=page_size)

        # 使用 UID SEARCH 获取所有邮件 UID，比序列号 SEARCH 更稳定可靠
        status, data = self.conn.uid('SEARCH', None, 'ALL')
        if status != "OK" or not data[0]:
            return MessageList(messages=[], total=0, unread_total=0, page=page, page_size=page_size)

        # 解析 UID 列表
        msg_uids = data[0].split()
        total = len(msg_uids)

        # 获取未读邮件总数（用于侧边栏计数，IMAP STATUS 更准确）
        unread_total = 0
        try:
            s, u_data = self.conn.search(None, "UNSEEN")
            if s == "OK" and u_data[0]:
                unread_total = len(u_data[0].split())
        except Exception as e:
            logger.debug("获取未读邮件总数失败: %s", e)

        # 分页：取最新的一页（倒序，最新的在前面）
        # 只用 UID 做分页，绝不使用序列号（1:20）
        start = max(0, total - page * page_size)
        end = max(0, total - (page - 1) * page_size)
        page_uids = list(reversed(msg_uids[start:end]))

        if not page_uids:
            return MessageList(messages=[], total=total, unread_total=unread_total, page=page, page_size=page_size)

        # 批量 UID FETCH：用逗号拼接 UID 集合，一次请求获取整页邮件
        uid_set = b",".join(page_uids)
        status, msg_data = self.conn.uid(
            'FETCH', uid_set,
            '(FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE)])'
        )
        if status != 'OK':
            return MessageList(messages=[], total=total, unread_total=unread_total, page=page, page_size=page_size)

        # 解析批量 FETCH 返回数据（基类方法）
        messages = self._parse_batch_fetch_response(msg_data, folder)

        # 批量 FETCH 不保证返回顺序，必须按 UID 降序排列（最新的在前）
        messages.sort(key=lambda m: m.uid, reverse=True)

        return MessageList(
            messages=messages,
            total=total,
            unread_total=unread_total,
            page=page,
            page_size=page_size
        )
