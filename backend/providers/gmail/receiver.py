import asyncio
import imaplib
import re
from typing import List, Optional, Dict
from ..base import Credentials, Folder, Message, MessageList
from ..base_imap import BaseIMAPReceiver
from ..ipv4 import IPv4IMAP4_SSL
from .config import GMAIL_IMAP_HOST, GMAIL_IMAP_PORT
from utils.logger import get_logger

logger = get_logger("gmail")


class GmailReceiver(BaseIMAPReceiver):
    """Gmail IMAP 邮件收取器"""

    def __init__(self):
        self._conn: Optional[imaplib.IMAP4_SSL] = None
        self._credentials: Optional[Credentials] = None

    async def connect(self, credentials: Credentials) -> None:
        """建立 IMAP 连接（使用 OAuth2 认证）"""
        self._credentials = credentials
        # 在线程中执行阻塞的 IMAP 操作
        self._conn = await asyncio.to_thread(self._connect_imap, credentials)

    def _connect_imap(self, credentials: Credentials) -> imaplib.IMAP4_SSL:
        """同步建立 IMAP 连接（强制 IPv4）"""
        conn = IPv4IMAP4_SSL(GMAIL_IMAP_HOST, GMAIL_IMAP_PORT)
        # 修复 P5: 认证失败时关闭连接，防止 socket 泄漏
        try:
            # 使用 OAuth2 认证
            auth_string = f"user={credentials.extra.get('email', '')}\x01auth=Bearer {credentials.access_token}\x01\x01"
            conn.authenticate("XOAUTH2", lambda _: auth_string.encode("utf-8"))
            return conn
        except Exception:
            # 认证失败，关闭连接防止 socket 泄漏
            try:
                conn.logout()
            except Exception as e:
                logger.debug("认证失败后关闭连接失败: %s", e)
            raise

    async def fetch_folders(self) -> List[Folder]:
        """获取 Gmail 标签列表"""
        if not self._conn:
            raise ConnectionError("Not connected")

        folders = await asyncio.to_thread(self._list_folders)
        return folders

    def _list_folders(self) -> List[Folder]:
        """同步获取文件夹列表

        Gmail 返回大量系统标签（如 [Gmail]/All Mail, [Gmail]/Important 等），
        只保留核心文件夹，与QQ邮箱统一显示：收件箱、已发送、草稿箱、垃圾邮件、已删除
        """
        # Gmail核心文件夹的IMAP路径（按显示顺序）
        core_folders = ["INBOX", "[Gmail]/Sent Mail", "[Gmail]/Drafts", "[Gmail]/Spam", "[Gmail]/Trash"]

        # Gmail系统文件夹路径 → 中文显示名
        display_names = {
            "INBOX": "收件箱",
            "[Gmail]/Sent Mail": "已发送",
            "[Gmail]/Drafts": "草稿箱",
            "[Gmail]/Spam": "垃圾邮件",
            "[Gmail]/Trash": "已删除",
        }

        status, folder_list = self._conn.list()
        # 先收集所有可用的文件夹路径
        available = set()
        if status == "OK":
            for f in folder_list:
                parts = f.decode().split('" ')
                if len(parts) >= 2:
                    name = parts[-1].strip('"')
                    available.add(name)

        # 按核心文件夹顺序生成结果
        result = []
        for core_name in core_folders:
            if core_name in available:
                result.append(Folder(
                    name=display_names.get(core_name, core_name),
                    path=core_name,
                    unread_count=0,
                    total_count=0,
                ))
        return result

    async def fetch_messages(self, folder: str = "INBOX", page: int = 1, page_size: int = 20) -> MessageList:
        """分页获取邮件列表"""
        if not self._conn:
            raise ConnectionError("Not connected")

        result = await asyncio.to_thread(self._fetch_messages_sync, folder, page, page_size)
        return result

    def _fetch_messages_sync(self, folder: str, page: int, page_size: int) -> MessageList:
        """同步获取邮件列表（批量 UID FETCH 版本）

        改进点：
        - 使用 UID SEARCH 替代序列号 SEARCH，避免序列号不稳定问题
        - 使用批量 UID FETCH 替代逐封 FETCH，一次请求获取整页邮件，减少网络往返
        - SELECT 使用 readonly=True，不会修改 \\Recent 标志

        已读/未读状态判断：
        使用 FETCH (FLAGS BODY.PEEK[HEADER.FIELDS ...]) 一次请求同时获取 FLAGS 和头部。
        - BODY.PEEK 不会隐式设置 \\Seen 标志
        - FLAGS 中包含 \\Seen 表示已读，不包含表示未读
        """
        # SELECT 文件夹（只读模式，不修改邮件标志）
        status, _ = self._conn.select(self._quote_mailbox(folder), readonly=True)
        if status != "OK":
            return MessageList(messages=[], total=0, page=page, page_size=page_size)

        # 使用 UID SEARCH 获取所有邮件的 UID 列表（比序列号更稳定）
        status, data = self._conn.uid('SEARCH', None, 'ALL')
        if status != "OK":
            return MessageList(messages=[], total=0, page=page, page_size=page_size)

        all_uids = data[0].split()
        total = len(all_uids)

        # 获取未读邮件总数（用于侧边栏计数）
        unread_total = 0
        try:
            s, u_data = self._conn.search(None, "UNSEEN")
            if s == "OK" and u_data[0]:
                unread_total = len(u_data[0].split())
        except Exception as e:
            logger.debug("获取未读邮件总数失败: %s", e)

        # 分页：基于 UID 列表切片，而非序列号
        start = max(0, total - page * page_size)
        end = max(0, total - (page - 1) * page_size)
        page_uids = all_uids[start:end]
        page_uids.reverse()  # 最新的在前

        # 无邮件时直接返回
        if not page_uids:
            return MessageList(messages=[], total=total, unread_total=unread_total,
                               page=page, page_size=page_size)

        # 批量 UID FETCH：用逗号拼接 UID 集合，一次请求获取整页邮件
        uid_set = b",".join(page_uids)
        status, msg_data = self._conn.uid(
            'FETCH', uid_set,
            '(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])'
        )
        if status != "OK":
            return MessageList(messages=[], total=total, unread_total=unread_total,
                               page=page, page_size=page_size)

        # 使用统一的批量解析方法解析返回数据
        messages = self._parse_batch_fetch_response(msg_data, folder)
        # 批量 FETCH 不保证返回顺序，必须按 UID 降序排列（最新的在前）
        messages.sort(key=lambda m: m.uid, reverse=True)

        return MessageList(messages=messages, total=total, unread_total=unread_total,
                           page=page, page_size=page_size)

    async def _reconnect(self) -> None:
        """重连 Gmail IMAP"""
        self._conn = await asyncio.to_thread(self._connect_imap, self._credentials)

    async def disconnect(self) -> None:
        """断开连接"""
        if self._conn:
            try:
                await asyncio.to_thread(self._conn.logout)
            except Exception as e:
                logger.debug("断开连接失败: %s", e)
            self._conn = None
