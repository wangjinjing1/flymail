"""IMAP 邮件接收器基类，消除各 provider 之间的重复代码

5 个 IMAP provider（Gmail、QQ、网易、iCloud、Outlook）共享大量相同的
IMAP 辅助方法（解码、解析、标记、移动等），统一提取到本基类中。
子类只需实现 provider 特有的连接、文件夹列表、邮件列表、IDLE 等逻辑。
"""

import asyncio
import base64
import datetime
import email
import re
import socket
import time
from email.header import decode_header
from typing import List, Optional, Dict
import imaplib
from .base import MailReceiver, Message, MessageList, Attachment
from utils.logger import get_logger

logger = get_logger("imap")


class BaseIMAPReceiver(MailReceiver):
    """IMAP 邮件接收器基类

    提供所有 IMAP provider 共用的辅助方法，子类只需实现 provider 特有的逻辑：
    - connect(): 建立连接
    - disconnect(): 断开连接
    - fetch_folders(): 获取文件夹列表
    - fetch_messages(): 获取邮件列表
    - _reconnect(): 重连逻辑

    子类必须使用 self._conn 作为 IMAP 连接属性。
    对于使用 self.conn 的子类（QQ/网易/iCloud），需添加 _conn 属性别名。
    """

    # 子类可覆盖：iCloud/Outlook 的 IMAP 服务器要求 flags 用括号包裹
    _flags_parenthesized = False

    # ---- 文件夹路径处理 ----

    @staticmethod
    def _quote_mailbox(folder: str) -> str:
        """为 IMAP 命令引用邮箱文件夹名

        Python imaplib 不会自动为文件夹名添加引号，
        导致包含空格的文件夹名（如 "Sent Messages"、"Deleted Messages"）
        被 IMAP 服务器解析为多个参数，返回 "EXAMINE parameters!" 错误。

        修复 Q6：转义文件夹名中的双引号和反斜杠（RFC 3501 要求），
        旧代码只处理空格，未转义特殊字符，可能导致 IMAP 命令解析错误。
        """
        # 含空格或特殊字符时需要加引号，并转义内部的 " 和 \
        if ' ' in folder or '"' in folder or '\\' in folder:
            escaped = folder.replace('\\', '\\\\').replace('"', '\\"')
            return '"' + escaped + '"'
        return folder

    @staticmethod
    def _normalize_folder(folder: str) -> str:
        """规范化文件夹路径，子类可覆盖（如 Outlook 需要映射文件夹名）"""
        return folder

    # ---- IMAP 标志格式化 ----

    def _format_flag(self, flag: str) -> str:
        """格式化 IMAP STORE 标志参数

        iCloud/Outlook 的 IMAP 服务器要求 flags 用括号包裹，如 (\\Seen)，
        而 Gmail/QQ/网易使用不带括号的格式，如 \\Seen。
        子类设置 _flags_parenthesized = True 即可自动切换格式。
        """
        if self._flags_parenthesized:
            return f"({flag})"
        return flag

    # ---- 文件夹计数 ----

    async def fetch_folder_counts(self, folder_paths: List[str]) -> Dict[str, Dict[str, int]]:
        """获取多个文件夹的邮件总数和未读数

        使用 IMAP STATUS 命令获取计数，比 SEARCH 更高效且不改变连接状态。
        """
        normalized_paths = [self._normalize_folder(path) for path in folder_paths]
        return await asyncio.to_thread(self._fetch_folder_counts_sync, normalized_paths)

    def _fetch_folder_counts_sync(self, folder_paths: List[str]) -> Dict[str, Dict[str, int]]:
        """同步获取多个文件夹的计数（使用 IMAP STATUS 命令）

        IMAP STATUS 命令可以直接获取文件夹的 MESSAGES 和 UNSEEN 计数，
        不需要先 EXAMINE/SELECT 文件夹再 SEARCH，更高效且不会改变连接状态。
        """
        result = {}
        for folder_path in folder_paths:
            try:
                s, data = self._conn.status(self._quote_mailbox(folder_path), '(MESSAGES UNSEEN)')
                if s != 'OK' or not data or not data[0]:
                    result[folder_path] = {"total": 0, "unread": 0}
                    continue

                # 解析 STATUS 响应，格式: * STATUS "INBOX" (MESSAGES 172 UNSEEN 12)
                response_str = data[0].decode('utf-8', errors='ignore') if isinstance(data[0], bytes) else str(data[0])
                total = 0
                unread = 0

                msg_match = re.search(r'MESSAGES\s+(\d+)', response_str)
                if msg_match:
                    total = int(msg_match.group(1))

                unseen_match = re.search(r'UNSEEN\s+(\d+)', response_str)
                if unseen_match:
                    unread = int(unseen_match.group(1))

                result[folder_path] = {"total": total, "unread": unread}
            except Exception:
                result[folder_path] = {"total": 0, "unread": 0}
        return result

    # ---- 批量解析 ----

    def _parse_batch_fetch_response(self, msg_data, folder: str) -> List[Message]:
        """解析批量 UID FETCH 返回的数据

        批量 FETCH 返回的数据结构：每个邮件占一个 tuple 元素（响应行+literal数据），
        后面可能跟一个 bytes 元素（包含 literal 之后的剩余字段，如 FLAGS）。

        IMAP 服务器返回顺序不固定：
        - 有些服务器把 FLAGS 放在 literal 之前：FLAGS (\\Seen) UID 123 BODY[...] {n}
        - 有些服务器把 FLAGS 放在 literal 之后：UID 123 BODY[...] {n}  →  FLAGS (\\Seen))
        第二种情况下，FLAGS 在 tuple 后面的独立 bytes 元素中，必须拼接后才能解析。
        """
        messages = []
        i = 0
        while i < len(msg_data):
            item = msg_data[i]
            if isinstance(item, tuple) and len(item) == 2:
                # item[0] 是响应行（可能包含 UID 和 FLAGS），item[1] 是邮件头部字节
                flags_text = item[0].decode("utf-8", errors="ignore") if item[0] else ""
                header_bytes = item[1]

                # 拼接 tuple 后面紧跟的 bytes 元素（某些 IMAP 服务器把 FLAGS 放在 literal 之后）
                j = i + 1
                while j < len(msg_data) and isinstance(msg_data[j], bytes):
                    flags_text += msg_data[j].decode("utf-8", errors="ignore")
                    j += 1

                # 从拼接后的完整响应行中提取 UID
                uid_match = re.search(r'UID\s+(\d+)', flags_text)
                uid = int(uid_match.group(1)) if uid_match else 0

                # 从拼接后的完整响应行中提取 FLAGS，检测 \Seen 标志判断已读状态
                flags_match = re.search(r'FLAGS\s*\(([^)]*)\)', flags_text)
                is_read = bool(flags_match and "\\Seen" in flags_match.group(1))
                internal_date = self._parse_internal_date(flags_text)

                if header_bytes:
                    msg = email.message_from_bytes(header_bytes)
                    messages.append(Message(
                        id=str(uid),
                        uid=uid,
                        subject=self._decode_header(msg.get("Subject", "")),
                        from_addr=self._decode_header(msg.get("From", "")),
                        to_addr=self._decode_header(msg.get("To", "")),
                        date=self._parse_date(msg.get("Date", ""), fallback=internal_date),
                        is_read=is_read,
                        folder=folder,
                    ))
                i = j  # 跳过已处理的 bytes 元素
            else:
                i += 1
        return messages

    # ---- 解码工具 ----

    @staticmethod
    def _decode_part(part) -> str:
        """解码邮件 part 的正文，自动检测 charset"""
        payload = part.get_payload(decode=True)
        if not payload:
            return ""
        # 从 Content-Type 获取 charset
        charset = part.get_content_charset()
        if charset:
            try:
                return payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError) as e:
                logger.debug("charset 解码失败，回退到常见编码: %s", e)
        # 回退：尝试常见编码
        for enc in ("utf-8", "gbk", "gb2312", "gb18030", "big5", "iso-8859-1"):
            try:
                return payload.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return payload.decode("utf-8", errors="replace")

    def _decode_header(self, header: str) -> str:
        """解码邮件头部编码"""
        if not header:
            return ""
        decoded_parts = decode_header(header)
        result = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                # 未知编码（如 unknown-8bit）回退到 utf-8
                try:
                    result.append(part.decode(charset or "utf-8", errors="replace"))
                except (LookupError, UnicodeDecodeError):
                    result.append(part.decode("utf-8", errors="replace"))
            else:
                result.append(part)
        return " ".join(result)

    def _parse_date(self, date_str: str, fallback: str = "") -> str:
        """解析日期字符串，将 RFC 2822 格式转为 UTC ISO 8601（带 Z 后缀）。

        统一转为 UTC 并加 Z 后缀，前端 new Date() 能自动转为本地时区显示。
        例如：Date 头 "Mon, 9 Jun 2026 05:39:00 +0000" → "2026-06-09T05:39:00Z"
        前端在东八区会自动显示为 13:39。
        """
        try:
            dt = email.utils.parsedate_to_datetime(date_str)
            if dt.tzinfo:
                dt = dt.astimezone(datetime.timezone.utc)
            # 加 Z 后缀表示 UTC，前端 new Date("2026-06-09T05:39:00Z") 会自动转为本地时区
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return fallback or ""

    def _parse_internal_date(self, fetch_text: str) -> str:
        match = re.search(r'INTERNALDATE\s+"([^"]+)"', fetch_text or "", re.IGNORECASE)
        if not match:
            return ""
        return self._parse_date(match.group(1))

    # ---- 邮件排序 ----

    def _sort_messages(self, messages: List[Message]) -> List[Message]:
        """排序邮件列表，子类可覆盖（如 Outlook 按日期+UID排序）"""
        messages.sort(key=lambda m: m.uid, reverse=True)
        return messages

    # ---- 邮件详情 ----

    async def fetch_message_detail(self, message_id: str, folder: str = "INBOX") -> Message:
        """获取邮件详情"""
        if not self._conn:
            raise ConnectionError("Not connected")
        return await asyncio.to_thread(self._fetch_detail_sync, message_id, folder)

    def _fetch_detail_sync(self, message_id: str, folder: str) -> Message:
        """同步获取邮件详情

        解析完整邮件内容，包括正文（纯文本/HTML）、内嵌图片（cid 替换）、附件列表。
        使用 BODY.PEEK[] 避免隐式设置 \\Seen 标志。
        """
        # 提取纯 UID：兼容旧格式 account_id_uid（取下划线后部分）
        uid = message_id.rsplit("_", 1)[-1] if "_" in message_id else message_id
        # 规范化文件夹路径（Outlook 需要映射）
        folder = self._normalize_folder(folder)
        # IMAP 协议要求先 SELECT 文件夹才能 FETCH 邮件
        status, _ = self._conn.select(self._quote_mailbox(folder), readonly=True)
        if status != "OK":
            raise ValueError(f"无法选择文件夹 {folder}")
        # 用 UID FETCH + BODY.PEEK[] 获取完整邮件内容，不会隐式设置 \Seen 标志
        status, msg_data = self._conn.uid('FETCH', uid, "(INTERNALDATE BODY.PEEK[])")
        if status != "OK":
            logger.debug("UID FETCH 返回非OK: status=%s, uid=%s, folder=%s", status, uid, folder)
            raise ValueError(f"Message {uid} not found")
        # 查找包含邮件内容的 tuple 项
        raw_email = None
        internal_date = ""
        for item in msg_data:
            if isinstance(item, tuple) and len(item) == 2:
                response_text = item[0].decode("utf-8", errors="ignore") if item[0] else ""
                internal_date = internal_date or self._parse_internal_date(response_text)
                raw_email = item[1]
                break
        if not raw_email:
            logger.debug("UID FETCH 未找到邮件内容: uid=%s, folder=%s, msg_data=%s", uid, folder, [type(x).__name__ for x in msg_data])
            raise ValueError(f"Message {uid} not found")
        msg = email.message_from_bytes(raw_email)

        subject = self._decode_header(msg.get("Subject", ""))
        from_addr = self._decode_header(msg.get("From", ""))
        to_addr = self._decode_header(msg.get("To", ""))
        date_str = msg.get("Date", "")

        body_text = ""
        body_html = ""
        cid_map = {}  # {content_id: data_uri} 用于替换内嵌图片的 cid: 引用
        attachments = []
        part_index = 0  # part 编号，用于附件下载；part_index 从 0 递增，但 IMAP MIME part 编号从 1 开始；此处仅用于内部索引，实际 part_number 在 fetch 时由 IMAP 服务器分配

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    body_text = self._decode_part(part)
                elif content_type == "text/html":
                    body_html = self._decode_part(part)
                elif content_type.startswith("image/"):
                    # 收集内嵌图片：转为 base64 data URI，替换 body_html 中的 cid: 引用
                    content_id = part.get("Content-ID", "").strip("<>")
                    if content_id:
                        img_data = part.get_payload(decode=True)
                        if img_data:
                            b64 = base64.b64encode(img_data).decode("ascii")
                            data_uri = f"data:{content_type};base64,{b64}"
                            cid_map[content_id] = data_uri
                    # 同时记录为附件（含文件名的内嵌图片也可下载）
                    filename = part.get_filename() or ""
                    if filename or not content_id:
                        img_data = part.get_payload(decode=True)
                        attachments.append(Attachment(
                            filename=self._decode_header(filename) if filename else "",
                            content_type=content_type,
                            size=len(img_data) if img_data else 0,
                            part_number=part_index,
                            content_id=content_id,
                            is_inline=bool(content_id),
                        ))
                elif not content_type.startswith("multipart/"):
                    # 其他非文本 part（如 application/* 等），作为附件
                    filename = part.get_filename() or ""
                    payload = part.get_payload(decode=True)
                    content_id = part.get("Content-ID", "").strip("<>")
                    is_inline = bool(content_id) and part.get_content_disposition() != "attachment"
                    if filename or payload:
                        attachments.append(Attachment(
                            filename=self._decode_header(filename) if filename else "",
                            content_type=content_type,
                            size=len(payload) if payload else 0,
                            part_number=part_index,
                            content_id=content_id,
                            is_inline=is_inline,
                        ))
                part_index += 1
        else:
            if msg.get_content_type() == "text/html":
                body_html = self._decode_part(msg)
            else:
                body_text = self._decode_part(msg)

        # 替换 body_html 中的 cid: 引用为 base64 data URI
        if body_html and cid_map:
            body_html = self._replace_cid_with_data_uri(body_html, cid_map)

        return Message(
            id=str(uid),
            uid=int(uid),
            subject=subject,
            from_addr=from_addr,
            to_addr=to_addr,
            date=self._parse_date(date_str, fallback=internal_date),
            body_text=body_text,
            body_html=body_html,
            folder=folder,
            attachments=attachments,
            has_attachments=len(attachments) > 0,
        )

    async def search_messages(self, folder: str, keyword: str, page: int = 1, page_size: int = 20) -> MessageList:
        if not self._conn:
            raise ConnectionError("Not connected")
        return await asyncio.to_thread(self._search_messages_sync, folder, keyword, page, page_size)

    def _search_messages_sync(self, folder: str, keyword: str, page: int, page_size: int) -> MessageList:
        folder = self._normalize_folder(folder)
        status, _ = self._conn.select(self._quote_mailbox(folder), readonly=True)
        if status != "OK":
            return MessageList(messages=[], total=0, unread_total=0, page=page, page_size=page_size)

        trimmed = (keyword or "").strip()
        if not trimmed:
            return self._fetch_messages_sync(folder, page, page_size)

        search_terms = ['OR', 'OR', 'SUBJECT', f'"{trimmed}"', 'FROM', f'"{trimmed}"', 'TEXT', f'"{trimmed}"']
        status, data = self._conn.uid('SEARCH', 'CHARSET', 'UTF-8', *search_terms)
        if status != 'OK':
            return MessageList(messages=[], total=0, unread_total=0, page=page, page_size=page_size)

        matched_uids = [uid for uid in data[0].split() if uid] if data and data[0] else []
        total = len(matched_uids)
        if total == 0:
            return MessageList(messages=[], total=0, unread_total=0, page=page, page_size=page_size)

        try:
            s, u_data = self._conn.search(None, "UNSEEN")
            unseen_uids = set(u_data[0].split()) if s == "OK" and u_data and u_data[0] else set()
            unread_total = sum(1 for uid in matched_uids if uid in unseen_uids)
        except Exception:
            unread_total = 0

        start = max(0, total - page * page_size)
        end = max(0, total - (page - 1) * page_size)
        page_uids = matched_uids[start:end]
        page_uids.reverse()
        if not page_uids:
            return MessageList(messages=[], total=total, unread_total=unread_total, page=page, page_size=page_size)

        uid_set = b",".join(page_uids)
        status, msg_data = self._conn.uid(
            'FETCH', uid_set,
            '(FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])'
        )
        if status != 'OK':
            return MessageList(messages=[], total=total, unread_total=unread_total, page=page, page_size=page_size)

        messages = self._parse_batch_fetch_response(msg_data, folder)
        messages = self._sort_messages(messages)
        return MessageList(messages=messages, total=total, unread_total=unread_total, page=page, page_size=page_size)

    # ---- 附件 ----

    async def fetch_attachment_data(self, message_id: str, folder: str, part_number: int):
        """获取邮件附件的二进制数据"""
        if not self._conn:
            raise ConnectionError("Not connected")
        return await asyncio.to_thread(self._fetch_attachment_sync, message_id, folder, part_number)

    def _fetch_attachment_sync(self, message_id: str, folder: str, part_number: int):
        """同步获取附件二进制数据

        通过 BODY.PEEK[] 获取完整邮件后，按 email.walk() 顺序遍历，
        提取与 fetch_message_detail 中 part_index 一致的 part 数据。
        这样保证"列表显示的附件"和"下载的数据"来自同一个 MIME part。
        """
        uid = message_id.rsplit("_", 1)[-1] if "_" in message_id else message_id
        folder = self._normalize_folder(folder)

        status, _ = self._conn.select(self._quote_mailbox(folder), readonly=True)
        if status != "OK":
            raise ValueError(f"无法选择文件夹 {folder}")

        # 获取完整邮件原始字节（与 fetch_message_detail 使用相同命令）
        status, msg_data = self._conn.uid('FETCH', uid, '(BODY.PEEK[])')
        if status != "OK":
            raise ValueError(f"邮件获取失败: uid={uid}, folder={folder}")

        raw_email = None
        for item in msg_data:
            if isinstance(item, tuple) and len(item) == 2:
                raw_email = item[1]
                break
        if not raw_email:
            return None

        # 用与 fetch_message_detail 完全相同的 walk() 逻辑定位目标 part
        # 注意：part_index 对每个 walk() 元素都递增（包括 multipart 容器），
        # 必须与 fetch_message_detail 第279~324行的逻辑完全一致
        msg = email.message_from_bytes(raw_email)
        part_index = 0

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                # 非 multipart 的叶子节点才可能包含附件数据
                if not content_type.startswith("multipart/"):
                    if part_index == part_number:
                        return part.get_payload(decode=True)
                # 每个 walk() 元素都递增（与 fetch_message_detail 一致）
                part_index += 1
        else:
            # 非多部分邮件（纯文本/HTML），唯一一个 part 索引为 0
            if part_number == 0:
                return msg.get_payload(decode=True)

        return None

    # ---- 增量同步 ----

    async def fetch_new_message_uids(self, folder: str, since_uid: int) -> List[int]:
        """异步获取大于 since_uid 的新邮件 UID 列表（增量同步用）"""
        if not self._conn:
            raise ConnectionError("Not connected")
        return await asyncio.to_thread(self._fetch_new_uids_sync, folder, since_uid)

    async def fetch_unseen_uids(self, folder: str) -> List[int]:
        """异步获取文件夹中未读邮件的 UID 列表（用于校正 is_read 状态）

        使用 UID SEARCH UNSEEN 命令，比解析 FETCH 响应中的 FLAGS 更可靠，
        因为不同 IMAP 服务器返回 FLAGS 的格式和位置不一致。
        """
        if not self._conn:
            raise ConnectionError("Not connected")
        return await asyncio.to_thread(self._fetch_unseen_uids_sync, folder)

    def _fetch_unseen_uids_sync(self, folder: str) -> List[int]:
        """同步获取未读邮件 UID 列表"""
        status, _ = self._conn.select(self._quote_mailbox(self._normalize_folder(folder)), readonly=True)
        if status != 'OK':
            return []
        status, data = self._conn.uid('SEARCH', None, 'UNSEEN')
        if status != 'OK' or not data[0]:
            return []
        return [int(uid) for uid in data[0].split()]

    def _fetch_new_uids_sync(self, folder: str, since_uid: int) -> List[int]:
        """获取大于 since_uid 的新邮件 UID 列表

        用于增量同步：只获取比上次同步的最大的 UID 更大的新邮件，
        避免每次都全量拉取所有邮件 UID。
        """
        status, _ = self._conn.select(self._quote_mailbox(self._normalize_folder(folder)), readonly=True)
        if status != 'OK':
            # SELECT 失败（文件夹不存在等），返回空列表避免 SEARCH 报错
            return []
        status, data = self._conn.uid('SEARCH', None, f'UID {since_uid + 1}:*')
        if status != 'OK' or not data[0]:
            return []
        return [int(uid) for uid in data[0].split()]

    async def fetch_messages_by_uids(self, folder: str, uids: List[int]) -> List[Message]:
        """异步根据 UID 列表批量获取邮件摘要（增量同步用）"""
        if not self._conn:
            raise ConnectionError("Not connected")
        return await asyncio.to_thread(self._fetch_messages_by_uids_sync, folder, uids)

    def _fetch_messages_by_uids_sync(self, folder: str, uids: List[int]) -> List[Message]:
        """根据 UID 列表批量获取邮件摘要

        将 UID 列表拼接为逗号分隔的集合，一次 UID FETCH 获取所有邮件的
        FLAGS + 头部字段，避免逐封 FETCH 的性能问题。
        """
        if not uids:
            return []
        folder = self._normalize_folder(folder)
        status, _ = self._conn.select(self._quote_mailbox(folder), readonly=True)
        if status != 'OK':
            # SELECT 失败（文件夹不存在等），返回空列表避免后续操作报错
            return []
        # UID 按降序排列（最新的在前），用逗号拼接成 UID 集合
        uid_set = ",".join(str(u) for u in sorted(uids, reverse=True))
        status, msg_data = self._conn.uid('FETCH', uid_set,
            '(FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])')
        if status != 'OK':
            return []
        messages = self._parse_batch_fetch_response(msg_data, folder)
        # 批量 FETCH 不保证返回顺序，按排序键排列
        return self._sort_messages(messages)

    # ---- 标记已读/未读 ----

    async def mark_as_read(self, message_id: str, folder: str = "INBOX") -> None:
        """标记邮件为已读（IMAP STORE +FLAGS \\Seen）

        必须用 SELECT（读写模式）打开文件夹，EXAMINE 是只读模式无法修改标志位。
        """
        if not self._conn:
            raise ConnectionError("Not connected")
        await asyncio.to_thread(self._mark_as_read_sync, message_id, folder)

    def _mark_as_read_sync(self, message_id: str, folder: str) -> None:
        """同步标记邮件为已读：SELECT 文件夹 + UID STORE 添加 \\Seen 标志"""
        folder = self._normalize_folder(folder)
        # SELECT 读写模式打开文件夹（EXAMINE 是只读模式，无法 STORE）
        self._conn.select(self._quote_mailbox(folder))
        # 使用 UID STORE 而非序列号 STORE，message_id 是 UID
        self._conn.uid('STORE', message_id, "+FLAGS", self._format_flag("\\Seen"))

    async def mark_as_unread(self, message_id: str, folder: str = "INBOX") -> None:
        """标记邮件为未读（IMAP STORE -FLAGS \\Seen）

        必须用 SELECT（读写模式）打开文件夹，EXAMINE 是只读模式无法修改标志位。
        """
        if not self._conn:
            raise ConnectionError("Not connected")
        await asyncio.to_thread(self._mark_as_unread_sync, message_id, folder)

    def _mark_as_unread_sync(self, message_id: str, folder: str) -> None:
        """同步标记邮件为未读：SELECT 文件夹 + UID STORE 移除 \\Seen 标志"""
        folder = self._normalize_folder(folder)
        # SELECT 读写模式打开文件夹（EXAMINE 是只读模式，无法 STORE）
        self._conn.select(self._quote_mailbox(folder))
        # 使用 UID STORE 而非序列号 STORE，message_id 是 UID
        self._conn.uid('STORE', message_id, "-FLAGS", self._format_flag("\\Seen"))

    # ---- 批量标记已读/未读 ----

    async def mark_as_read_batch(self, message_ids: List[str], folder: str = "INBOX") -> int:
        """批量标记多封邮件为已读，使用 IMAP UID RANGE 一次命令完成

        返回成功标记的邮件数量。
        IMAP 协议支持 UID STORE uid1,uid2,uid3 +FLAGS (\\Seen)，
        一条命令可操作多个 UID，避免逐条 STORE 的 N 次网络往返。
        """
        if not self._conn:
            raise ConnectionError("Not connected")
        if not message_ids:
            return 0
        return await asyncio.to_thread(self._mark_as_read_batch_sync, message_ids, folder)

    def _mark_seen_batch_sync(self, message_ids, folder, mark_read: bool = True) -> int:
        """批量标记已读/未读

        Args:
            message_ids: UID 列表
            folder: 文件夹名
            mark_read: True 标记已读，False 标记未读
        Returns:
            成功标记的数量
        """
        folder = self._normalize_folder(folder)
        self._conn.select(self._quote_mailbox(folder))

        flag_op = "+FLAGS" if mark_read else "-FLAGS"
        flag = self._format_flag("\\Seen")

        success_count = 0
        failed_uids = []

        # 分批处理，每批最多 500 个 UID
        batch_size = 500
        for i in range(0, len(message_ids), batch_size):
            batch = message_ids[i:i + batch_size]
            uid_str = ",".join(str(uid) for uid in batch)
            try:
                status, response = self._conn.uid("STORE", uid_str, flag_op, flag)
                if status == "OK":
                    success_count += len(batch)
                else:
                    failed_uids.extend(batch)
            except Exception:
                failed_uids.extend(batch)

        # 批量失败时回退到逐条标记
        if failed_uids:
            logger.info("批量标记失败 %d 封，回退逐条标记", len(failed_uids))
            for uid in failed_uids:
                try:
                    status, _ = self._conn.uid("STORE", str(uid), flag_op, flag)
                    if status == "OK":
                        success_count += 1
                except Exception as e:
                    logger.debug("批量标记单封失败 uid=%s: %s", uid, e)

        return success_count

    def _mark_as_read_batch_sync(self, message_ids, folder) -> int:
        return self._mark_seen_batch_sync(message_ids, folder, mark_read=True)

    def _mark_as_unread_batch_sync(self, message_ids, folder) -> int:
        return self._mark_seen_batch_sync(message_ids, folder, mark_read=False)

    async def mark_as_unread_batch(self, message_ids: List[str], folder: str = "INBOX") -> int:
        """批量标记多封邮件为未读，使用 IMAP UID RANGE 一次命令完成

        返回成功标记的邮件数量。
        """
        if not self._conn:
            raise ConnectionError("Not connected")
        if not message_ids:
            return 0
        return await asyncio.to_thread(self._mark_as_unread_batch_sync, message_ids, folder)

    # ---- 移动/删除 ----

    async def move_message(self, message_id: str, target_folder: str, source_folder: str = "INBOX") -> None:
        """移动邮件到目标文件夹（带自动重连）"""
        await self._ensure_connected()
        try:
            await asyncio.to_thread(self._move_message_sync, message_id, target_folder, source_folder)
        except Exception:
            # 操作失败，尝试重连后重试一次
            await self._ensure_connected()
            await asyncio.to_thread(self._move_message_sync, message_id, target_folder, source_folder)

    def _move_message_sync(self, message_id, target_folder, source_folder):
        """移动单封邮件到目标文件夹（委托给批量方法）"""
        return self._move_message_batch_sync([message_id], target_folder, source_folder)

    async def delete_message(self, message_id: str, folder: str = "INBOX") -> None:
        """彻底删除邮件（标记DELETED + EXPUNGE，带自动重连）"""
        await self._ensure_connected()
        try:
            await asyncio.to_thread(self._delete_message_sync, message_id, folder)
        except Exception:
            # 操作失败，尝试重连后重试一次
            await self._ensure_connected()
            await asyncio.to_thread(self._delete_message_sync, message_id, folder)

    def _delete_message_sync(self, message_id, folder):
        """删除单封邮件（委托给批量方法）"""
        return self._delete_message_batch_sync([message_id], folder)

    # ---- 批量移动/删除 ----

    async def move_message_batch(self, message_ids: List[str], target_folder: str, source_folder: str = "INBOX") -> int:
        """批量移动邮件到目标文件夹，使用 IMAP UID 逗号分隔列表一次命令完成

        返回成功移动的邮件数量。
        优化前：N 封 = N 次 COPY + N 次 STORE + N 次 EXPUNGE = 3N 次网络往返。
        优化后：1 次 COPY + 1 次 STORE + 1 次 EXPUNGE = 3 次网络往返。
        """
        if not self._conn:
            raise ConnectionError("Not connected")
        if not message_ids:
            return 0
        return await asyncio.to_thread(self._move_message_batch_sync, message_ids, target_folder, source_folder)

    def _move_message_batch_sync(self, message_ids: List[str], target_folder: str, source_folder: str) -> int:
        """同步批量移动：SELECT 一次 + UID COPY 批量 + UID STORE 批量 + EXPUNGE

        IMAP UID COPY/STORE 支持逗号分隔的 UID 列表，如：
          UID COPY 1001,1002,1005 "Deleted Items"
          UID STORE 1001,1002,1005 +FLAGS (\\Deleted)
        超过 500 个 UID 自动分批执行。
        """
        source_folder = self._normalize_folder(source_folder)
        target_folder = self._normalize_folder(target_folder)
        self._conn.select(self._quote_mailbox(source_folder))
        deleted_flag = self._format_flag("\\Deleted")
        moved = 0
        batch_size = 500
        for i in range(0, len(message_ids), batch_size):
            batch = message_ids[i:i + batch_size]
            uid_set = ",".join(batch)
            try:
                # 批量 COPY 到目标文件夹
                status, _ = self._conn.uid('COPY', uid_set, self._quote_mailbox(target_folder))
                # 批量标记 \Deleted
                status, _ = self._conn.uid('STORE', uid_set, "+FLAGS", deleted_flag)
                moved += len(batch)
            except Exception as e:
                logger.error("IMAP batch move 失败，回退逐条: uid_set=%s..., error=%s", uid_set[:50], e)
                # 回退到逐条移动
                for uid in batch:
                    try:
                        self._conn.uid('COPY', uid, self._quote_mailbox(target_folder))
                        self._conn.uid('STORE', uid, "+FLAGS", deleted_flag)
                        moved += 1
                    except Exception as e:
                        logger.debug("移动单封邮件失败 uid=%s: %s", uid, e)
        # 统一执行一次 EXPUNGE 清除所有 \Deleted 标记
        self._conn.expunge()
        return moved

    async def delete_message_batch(self, message_ids: List[str], folder: str = "INBOX") -> int:
        """批量彻底删除邮件，使用 IMAP UID 逗号分隔列表一次命令完成

        返回成功删除的邮件数量。
        """
        if not self._conn:
            raise ConnectionError("Not connected")
        if not message_ids:
            return 0
        return await asyncio.to_thread(self._delete_message_batch_sync, message_ids, folder)

    def _delete_message_batch_sync(self, message_ids: List[str], folder: str) -> int:
        """同步批量删除：SELECT 一次 + UID STORE 批量 + EXPUNGE"""
        folder = self._normalize_folder(folder)
        self._conn.select(self._quote_mailbox(folder))
        deleted_flag = self._format_flag("\\Deleted")
        deleted = 0
        batch_size = 500
        for i in range(0, len(message_ids), batch_size):
            batch = message_ids[i:i + batch_size]
            uid_set = ",".join(batch)
            try:
                status, _ = self._conn.uid('STORE', uid_set, "+FLAGS", deleted_flag)
                deleted += len(batch)
            except Exception as e:
                logger.error("IMAP batch delete 失败，回退逐条: uid_set=%s..., error=%s", uid_set[:50], e)
                for uid in batch:
                    try:
                        self._conn.uid('STORE', uid, "+FLAGS", deleted_flag)
                        deleted += 1
                    except Exception as e:
                        logger.debug("删除单封邮件失败 uid=%s: %s", uid, e)
        self._conn.expunge()
        return deleted

    # ---- 草稿保存 ----

    async def save_draft(self, message_bytes: bytes, folder: str = "Drafts") -> None:
        """通过 IMAP APPEND 命令保存草稿到服务器"""
        if not self._conn:
            raise ConnectionError("IMAP 未连接")

        def _append():
            # 查找草稿箱的实际路径
            normalized = self._normalize_folder(folder)
            status, response = self._conn.append(
                self._quote_mailbox(normalized),
                None,  # no flags
                None,  # no date
                message_bytes,
            )
            if status != "OK":
                raise Exception(f"IMAP APPEND 失败: {response}")
            return status

        await asyncio.to_thread(_append)

    # ---- 连接管理 ----

    async def _ensure_connected(self) -> None:
        """确保IMAP连接可用；连接对象不存在时抛异常，连接断开时自动重连"""
        if self._conn is None:
            raise ConnectionError("Not connected")
        # 检测连接是否还活着（发NOOP命令探测）
        try:
            await asyncio.to_thread(self._conn.noop)
        except Exception:
            # 连接已断开，尝试重连
            try:
                await asyncio.to_thread(self._conn.logout)
            except Exception as e:
                logger.debug("断开连接时 logout 失败（忽略，将重连）: %s", e)
            await self._reconnect()

    async def _reconnect(self) -> None:
        """重连IMAP，子类必须覆盖此方法提供重连逻辑"""
        raise NotImplementedError

    # ---- NOOP 轮询（仅网易使用，需要 IMAP ID 命令）----

    def _noop_poll(self, folder: str, timeout_seconds: int, poll_interval: int = 10) -> bool:
        """NOOP/STATUS 轮询检测新邮件

        每 poll_interval 秒用 STATUS 命令检查邮件数量变化，数量增加则判定有新邮件。
        仅网易使用（需要 IMAP ID 命令，无法用 aioimaplib 替代）。
        iCloud 已改为 aioimaplib 异步 STATUS 轮询（PollConnection）。
        """
        start_time = time.time()

        # 获取初始邮件数量
        initial_count = self._get_folder_message_count(folder)
        if initial_count < 0:
            raise ConnectionError("NOOP 轮询启动失败，IMAP 连接不可用")
        logger.info("NOOP 轮询: folder=%s, 初始邮件数=%d", folder, initial_count)

        while time.time() - start_time < timeout_seconds:
            time.sleep(poll_interval)
            try:
                current_count = self._get_folder_message_count(folder)
                # 连接断开时抛出异常，让上层 _idle_loop 负责重连
                if current_count < 0:
                    raise ConnectionError("IMAP 连接已断开")
                if current_count > initial_count:
                    logger.info("检测到新邮件: folder=%s, %d -> %d", folder, initial_count, current_count)
                    return True
                # NOOP 保持连接活跃
                if self._conn:
                    try:
                        self._conn.noop()
                    except Exception:
                        self._conn = None
                        raise ConnectionError("IMAP 连接已断开")
            except Exception as e:
                if "IMAP 连接已断开" in str(e):
                    raise
                logger.warning("NOOP 轮询异常: %s", e)
                break

        return False

    def _get_folder_message_count(self, folder: str) -> int:
        """通过 IMAP STATUS 命令获取文件夹邮件数量，连接断开时返回 -1"""
        try:
            if not self._conn:
                return -1
            status, data = self._conn.status(self._quote_mailbox(folder), '(MESSAGES)')
            if status == 'OK' and data:
                resp = data[0].decode('utf-8', errors='ignore') if isinstance(data[0], bytes) else str(data[0])
                match = re.search(r'MESSAGES\s+(\d+)', resp)
                if match:
                    return int(match.group(1))
        except Exception as e:
            # 连接断开时，标记 _conn 为 None，让上层 _idle_loop 触发重连
            self._conn = None
            # STATUS 失败是预期的瞬态错误（连接断开/token 过期），用 WARNING 而非 ERROR
            logger.warning("STATUS 命令失败（连接断开，等待重连）: %s", e)
        return -1
