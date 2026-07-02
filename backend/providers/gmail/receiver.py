import asyncio
import imaplib
import re
from typing import Dict, List, Optional

from ..base import Credentials, Folder, MessageList
from ..base_imap import BaseIMAPReceiver
from ..ipv4 import IPv4IMAP4_SSL
from .config import GMAIL_IMAP_HOST, GMAIL_IMAP_PORT
from utils.logger import get_logger

logger = get_logger("gmail")


class GmailReceiver(BaseIMAPReceiver):
    """Gmail IMAP receiver."""

    def __init__(self):
        self._conn: Optional[imaplib.IMAP4_SSL] = None
        self._credentials: Optional[Credentials] = None

    async def connect(self, credentials: Credentials) -> None:
        self._credentials = credentials
        self._conn = await asyncio.to_thread(self._connect_imap, credentials)

    def _connect_imap(self, credentials: Credentials) -> imaplib.IMAP4_SSL:
        conn = IPv4IMAP4_SSL(GMAIL_IMAP_HOST, GMAIL_IMAP_PORT)
        try:
            auth_string = f"user={credentials.extra.get('email', '')}\x01auth=Bearer {credentials.access_token}\x01\x01"
            conn.authenticate("XOAUTH2", lambda _: auth_string.encode("utf-8"))
            return conn
        except Exception:
            try:
                conn.logout()
            except Exception as e:
                logger.debug("close Gmail IMAP after auth failure failed: %s", e)
            raise

    async def fetch_folders(self) -> List[Folder]:
        if not self._conn:
            raise ConnectionError("Not connected")
        return await asyncio.to_thread(self._list_folders)

    def _list_folders(self) -> List[Folder]:
        """Return Gmail core folders, including localized system labels."""
        display_names = {
            "inbox": "收件箱",
            "sent": "已发送",
            "drafts": "草稿箱",
            "junk": "垃圾邮件",
            "trash": "已删除",
        }
        flag_map = {
            "\\inbox": "inbox",
            "\\sent": "sent",
            "\\drafts": "drafts",
            "\\junk": "junk",
            "\\spam": "junk",
            "\\trash": "trash",
        }
        alias_map = {
            "inbox": "inbox",
            "[gmail]/sent mail": "sent",
            "[google mail]/sent mail": "sent",
            "sent": "sent",
            "sent mail": "sent",
            "sent messages": "sent",
            "sent items": "sent",
            "已发送": "sent",
            "[gmail]/drafts": "drafts",
            "[google mail]/drafts": "drafts",
            "drafts": "drafts",
            "草稿箱": "drafts",
            "[gmail]/spam": "junk",
            "[google mail]/spam": "junk",
            "spam": "junk",
            "junk": "junk",
            "junk email": "junk",
            "垃圾邮件": "junk",
            "[gmail]/trash": "trash",
            "[google mail]/trash": "trash",
            "trash": "trash",
            "deleted": "trash",
            "deleted items": "trash",
            "deleted messages": "trash",
            "已删除": "trash",
        }

        status, folder_list = self._conn.list()
        if status != "OK":
            return []

        by_key: Dict[str, str] = {}
        for item in folder_list or []:
            flags, name = self._parse_list_item(item)
            if not name:
                continue
            key = ""
            for flag in flags:
                key = flag_map.get(flag.lower(), "")
                if key:
                    break
            if not key:
                key = alias_map.get(name.lower(), "")
            if key and key not in by_key:
                by_key[key] = name

        return [
            Folder(name=display_names[key], path=by_key[key], unread_count=0, total_count=0)
            for key in ["inbox", "sent", "drafts", "junk", "trash"]
            if key in by_key
        ]

    @staticmethod
    def _parse_list_item(item) -> tuple[list[str], str]:
        text = item.decode("utf-8", errors="replace") if isinstance(item, bytes) else str(item)
        match = re.match(r'\((?P<flags>.*?)\)\s+"(?P<delimiter>.*?)"\s+(?P<name>.*)$', text)
        if not match:
            return [], ""
        flags = [flag.strip() for flag in match.group("flags").split() if flag.strip()]
        name = match.group("name").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1]
        name = name.replace(r'\"', '"').replace(r'\\', "\\")
        return flags, name

    async def fetch_messages(self, folder: str = "INBOX", page: int = 1, page_size: int = 20) -> MessageList:
        if not self._conn:
            raise ConnectionError("Not connected")
        return await asyncio.to_thread(self._fetch_messages_sync, folder, page, page_size)

    def _fetch_messages_sync(self, folder: str, page: int, page_size: int) -> MessageList:
        status, _ = self._conn.select(self._quote_mailbox(folder), readonly=True)
        if status != "OK":
            return MessageList(messages=[], total=0, page=page, page_size=page_size)

        status, data = self._conn.uid("SEARCH", None, "ALL")
        if status != "OK":
            return MessageList(messages=[], total=0, page=page, page_size=page_size)

        all_uids = data[0].split()
        total = len(all_uids)

        unread_total = 0
        try:
            s, u_data = self._conn.search(None, "UNSEEN")
            if s == "OK" and u_data[0]:
                unread_total = len(u_data[0].split())
        except Exception as e:
            logger.debug("fetch Gmail unread total failed: %s", e)

        start = max(0, total - page * page_size)
        end = max(0, total - (page - 1) * page_size)
        page_uids = all_uids[start:end]
        page_uids.reverse()

        if not page_uids:
            return MessageList(messages=[], total=total, unread_total=unread_total, page=page, page_size=page_size)

        uid_set = b",".join(page_uids)
        status, msg_data = self._conn.uid(
            "FETCH",
            uid_set,
            "(FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])",
        )
        if status != "OK":
            return MessageList(messages=[], total=total, unread_total=unread_total, page=page, page_size=page_size)

        messages = self._parse_batch_fetch_response(msg_data, folder)
        messages.sort(key=lambda m: m.uid, reverse=True)

        return MessageList(messages=messages, total=total, unread_total=unread_total, page=page, page_size=page_size)

    async def _reconnect(self) -> None:
        self._conn = await asyncio.to_thread(self._connect_imap, self._credentials)

    async def disconnect(self) -> None:
        if self._conn:
            try:
                await asyncio.to_thread(self._conn.logout)
            except Exception as e:
                logger.debug("disconnect Gmail IMAP failed: %s", e)
            self._conn = None
