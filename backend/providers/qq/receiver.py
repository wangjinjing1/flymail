import asyncio
import re
import base64
from typing import List, Optional
from ..base import Credentials, Folder, Message, MessageList
from ..base_imap import BaseIMAPReceiver
from providers.ipv4 import IPv4IMAP4_SSL
from providers.qq.config import IMAP_HOST, IMAP_PORT
from utils.logger import get_logger

logger = get_logger("qq")


def decode_modified_utf7(s: str) -> str:
    """解码 IMAP Modified UTF-7 编码的文件夹名

    QQ邮箱的中文文件夹名使用 Modified UTF-7 编码（RFC 3501 Section 5.1.3）。
    例如: &UXZO1mWHTvZZOQ- → 已删除, &g0l6O3Z- → 草稿箱
    编码规则: & 开头表示进入 base64 编码段, - 结束编码段, &- 表示 & 字符本身
    """
    result = []
    i = 0
    while i < len(s):
        if s[i] == '&':
            # 找到编码段结束标记 -
            end = s.find('-', i)
            if end == -1:
                # 没有结束标记，原样返回
                result.append(s[i:])
                break
            if end == i + 1:
                # &- 表示 & 字符本身
                result.append('&')
            else:
                # 提取 & 和 - 之间的 base64 数据
                encoded = s[i + 1:end]
                # Modified UTF-7 使用标准 base64，但用 + 替代 /
                b64_data = encoded.replace(',', '/')
                # 补齐 base64 padding（Modified UTF-7 不带 =）
                padding = 4 - len(b64_data) % 4
                if padding < 4:
                    b64_data += '=' * padding
                try:
                    decoded_bytes = base64.b64decode(b64_data)
                    result.append(decoded_bytes.decode('utf-16-be'))
                except Exception:
                    # 解码失败，原样返回
                    result.append(s[i:end + 1])
            i = end + 1
        else:
            result.append(s[i])
            i += 1
    return ''.join(result)


class QQReceiver(BaseIMAPReceiver):
    """QQ邮箱IMAP接收器"""

    IMAP_HOST = IMAP_HOST
    IMAP_PORT = IMAP_PORT
    TIMEOUT = 30  # 单个 socket 操作超时30秒

    def __init__(self):
        self.conn: Optional[IPv4IMAP4_SSL] = None
        self.email_addr: str = ""
        self._auth_code: str = ""  # 保存授权码，用于自动重连

    # ---- _conn 属性别名，使基类方法兼容 self.conn 命名 ----

    @property
    def _conn(self):
        """基类使用 self._conn 访问 IMAP 连接，QQ 使用 self.conn，通过属性别名统一"""
        return self.conn

    @_conn.setter
    def _conn(self, value):
        self.conn = value

    async def connect(self, credentials: Credentials) -> None:
        """连接到QQ邮箱IMAP服务器（在线程池中执行阻塞操作）"""
        self.email_addr = credentials.extra.get("email", "")
        self._auth_code = credentials.access_token  # QQ邮箱使用授权码

        try:
            # 在线程池中执行阻塞的 IMAP 连接，避免卡住事件循环
            self._conn = await asyncio.to_thread(self._connect_imap, self.email_addr, self._auth_code)
        except Exception as e:
            self._conn = None
            raise Exception(f"QQ邮箱连接失败: {str(e)}")

    def _connect_imap(self, email_addr: str, auth_code: str) -> IPv4IMAP4_SSL:
        """同步建立 IMAP 连接（在线程池中运行，使用 IPv4 强制子类）"""
        conn = IPv4IMAP4_SSL(self.IMAP_HOST, self.IMAP_PORT, timeout=self.TIMEOUT)
        # 修复 P5: 登录失败时关闭连接，防止 socket 泄漏
        try:
            conn.login(email_addr, auth_code)
            return conn
        except Exception:
            # 登录失败，关闭连接防止 socket 泄漏
            try:
                conn.logout()
            except Exception as e:
                logger.debug("登录失败后关闭连接失败: %s", e)
            raise

    async def fetch_folders(self) -> List[Folder]:
        """获取邮箱文件夹列表"""
        if not self._conn:
            raise ConnectionError("未连接到邮箱服务器")

        try:
            return await asyncio.to_thread(self._fetch_folders_sync)
        except Exception as e:
            raise Exception(f"获取文件夹失败: {str(e)}")

    def _fetch_folders_sync(self) -> List[Folder]:
        """同步获取文件夹列表（在线程池中运行）

        QQ邮箱标准文件夹: INBOX, Sent Messages, Drafts, Junk, Trash
        其他自定义文件夹（如"其他文件夹"及其子项）会被过滤掉
        """
        status, folders = self._conn.list()
        result = []

        # QQ邮箱核心文件夹映射：显示名 → 可能的IMAP路径（不同账号可能不同）
        # QQ邮箱已删除文件夹路径是 "Deleted Messages"，不是 "Trash"
        core_folder_map = [
            ("收件箱", ["INBOX"]),
            ("已发送", ["Sent Messages", "Sent"]),
            ("草稿箱", ["Drafts"]),
            ("垃圾邮件", ["Junk", "Spam"]),
            ("已删除", ["Deleted Messages", "Trash", "已删除"]),
        ]

        # 先收集所有文件夹
        all_folders = []
        for folder_data in folders:
            if isinstance(folder_data, bytes):
                folder_str = folder_data.decode("utf-8", errors="ignore")
            else:
                folder_str = str(folder_data)

            # 解析文件夹名 - QQ邮箱格式: (\HasNoChildren) "/" "INBOX"
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
            for folder_name in all_folders:
                decoded_name = decode_modified_utf7(folder_name)
                if (
                    folder_name in possible_paths
                    or decoded_name in possible_paths
                    or decoded_name == display_name
                ):
                    matched_path = folder_name
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
            # 跳过子文件夹（包含 / 的，如 "其他文件夹/QQ邮件订阅"）
            if "/" in folder_name:
                continue
            # 跳过 Modified UTF-7 编码的自定义文件夹（以 & 开头）
            if folder_name.startswith("&"):
                continue

            display_name = decode_modified_utf7(folder_name)
            result.append(Folder(
                name=display_name,
                path=folder_name,
                unread_count=0,
                total_count=0,
            ))

        return result

    async def fetch_messages(self, folder: str = "INBOX", page: int = 1, page_size: int = 20) -> MessageList:
        """获取邮件列表"""
        if not self._conn:
            raise ConnectionError("未连接到邮箱服务器")

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
        status, data = self._conn.select(self._quote_mailbox(folder), readonly=True)
        if status != "OK":
            return MessageList(messages=[], total=0, page=page, page_size=page_size)

        # 使用 UID SEARCH 获取所有邮件 UID，比序列号 SEARCH 更稳定可靠
        status, data = self._conn.uid('SEARCH', None, 'ALL')
        if not data[0]:
            return MessageList(messages=[], total=0, page=page, page_size=page_size)

        # 解析 UID 列表
        msg_uids = data[0].split()
        total = len(msg_uids)

        # 获取未读邮件总数（用于侧边栏计数，IMAP STATUS 更准确）
        unread_total = 0
        try:
            s, u_data = self._conn.search(None, "UNSEEN")
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
        status, msg_data = self._conn.uid(
            'FETCH', uid_set,
            '(FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE)])'
        )
        if status != 'OK':
            return MessageList(messages=[], total=total, unread_total=unread_total, page=page, page_size=page_size)

        # 解析批量 FETCH 返回数据
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

    async def _reconnect(self) -> None:
        """重连 QQ 邮箱 IMAP"""
        self._conn = await asyncio.to_thread(self._connect_imap, self.email_addr, self._auth_code)

    async def disconnect(self) -> None:
        """断开连接"""
        if self._conn:
            try:
                await asyncio.to_thread(self._disconnect_sync)
            except Exception as e:
                logger.debug("断开连接失败: %s", e)
            self._conn = None

    def _disconnect_sync(self) -> None:
        """同步断开连接（在线程池中运行）"""
        try:
            self._conn.close()
        except Exception as e:
            logger.debug("关闭 IMAP 连接失败: %s", e)
        try:
            self._conn.logout()
        except Exception as e:
            logger.debug("登出 IMAP 连接失败: %s", e)
