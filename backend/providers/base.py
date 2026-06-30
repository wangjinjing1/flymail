from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import base64
import re
import time


class Credentials(BaseModel):
    provider_type: str
    access_token: str = ""
    refresh_token: str = ""
    expires_at: int = 0
    # O14 修复：使用 Field(default_factory=dict) 避免可变默认值反模式
    extra: Dict[str, Any] = Field(default_factory=dict)


class Folder(BaseModel):
    name: str
    path: str
    unread_count: int = 0
    total_count: int = 0


class Attachment(BaseModel):
    """邮件附件信息"""
    filename: str = ""           # 文件名
    content_type: str = ""      # MIME 类型
    size: int = 0               # 文件大小（字节）
    part_number: int = 0        # IMAP part 编号（用于下载）
    content_id: str = ""        # Content-ID（内嵌图片标识）
    is_inline: bool = False     # 是否为内嵌附件


class Message(BaseModel):
    id: str
    uid: int
    subject: str
    from_addr: str
    to_addr: str
    date: str
    is_read: bool = False
    is_starred: bool = False
    folder: str = "INBOX"
    body_text: str = ""
    body_html: str = ""
    attachments: List[Attachment] = []  # 附件列表
    has_attachments: bool = False  # 是否有附件（列表展示用）


class MessageList(BaseModel):
    messages: List[Message]
    total: int
    unread_total: int = 0  # 文件夹内未读邮件总数（SEARCH UNSEEN）
    page: int
    page_size: int


class SendResult(BaseModel):
    success: bool
    message_id: str = ""
    error: str = ""


# OAuth token 操作需要重新授权的永久错误码（RFC 6749 + Google/Microsoft 官方文档）
# O8 修复：扩展永久错误列表，避免对配置类错误做无意义重试
_PERMANENT_OAUTH_ERRORS = frozenset({
    "invalid_grant",              # refresh_token 无效、过期、被撤销（Google + Microsoft 通用）
    "invalid_client",             # 客户端凭据错误（client_id/client_secret 不正确）
    "unauthorized_client",        # 客户端无权使用此授权类型
    "invalid_request",            # 请求格式错误（参数缺失/非法），重试无意义
    "unsupported_grant_type",     # grant_type 不支持，配置错误
    "unsupported_response_type",  # response_type 不支持，配置错误
    "invalid_scope",              # 请求的 scope 无效或非法，配置错误
})


def parse_retry_after(header_value: str | None) -> float:
    """解析 HTTP Retry-After 头的值（O11 修复）。

    Retry-After 头有两种格式：
    - 数字（秒）：Retry-After: 120
    - HTTP 日期：Retry-After: Wed, 21 Oct 2026 07:28:00 GMT

    返回建议等待的秒数，解析失败或无值时返回 0。
    """
    if not header_value:
        return 0.0
    # 尝试解析为数字（秒）
    try:
        return max(0.0, float(header_value))
    except ValueError:
        pass
    # 尝试解析为 HTTP 日期
    try:
        from email.utils import parsedate_to_datetime
        target_time = parsedate_to_datetime(header_value).timestamp()
        return max(0.0, target_time - time.time())
    except Exception:
        return 0.0


class OAuthTokenError(Exception):
    """OAuth token 操作失败的结构化异常

    携带从 OAuth Provider HTTP 响应中解析的结构化错误信息，
    上层通过 is_permanent 属性判断是否需要用户重新授权，
    无需再做字符串匹配。

    O11 修复：新增 retry_after 字段，携带 HTTP Retry-After 头的建议等待秒数，
    上层重试时优先使用此值（用于 429/503 等限流场景）。
    """
    def __init__(self, message: str, error_code: str = "", http_status: int = 0,
                 provider: str = "", retry_after: float = 0.0):
        super().__init__(message)
        self.error_code = error_code    # OAuth 标准 error 字段: "invalid_grant", "invalid_client" 等
        self.http_status = http_status  # HTTP 响应状态码
        self.provider = provider        # "gmail" / "outlook"
        self.retry_after = retry_after  # Retry-After 头的建议等待秒数（0 表示无此头）

    @property
    def is_permanent(self) -> bool:
        """是否为永久错误（需要用户重新授权）"""
        return self.error_code in _PERMANENT_OAUTH_ERRORS


class AuthProvider(ABC):
    @abstractmethod
    def get_auth_url(self, redirect_uri: str, state: str) -> str:
        pass

    @abstractmethod
    async def handle_callback(self, code: str, redirect_uri: str) -> Credentials:
        pass

    @abstractmethod
    async def refresh_token(self, credentials: Credentials) -> Credentials:
        pass

    @abstractmethod
    async def revoke_token(self, credentials: Credentials) -> bool:
        pass


class MailReceiver(ABC):
    @staticmethod
    def _replace_cid_with_data_uri(body_html: str, cid_map: dict) -> str:
        """将 body_html 中的 cid: 引用替换为 base64 data URI，使浏览器能正常显示内嵌图片"""
        if not body_html or not cid_map:
            return body_html
        for cid, data_uri in cid_map.items():
            # 替换 src="cid:xxx" 和 src='cid:xxx' 两种引号形式
            body_html = body_html.replace(f'cid:{cid}', data_uri)
        return body_html

    @abstractmethod
    async def connect(self, credentials: Credentials) -> None:
        pass

    @abstractmethod
    async def fetch_folders(self) -> List[Folder]:
        pass

    async def fetch_folder_counts(self, folder_paths: List[str]) -> Dict[str, Dict[str, int]]:
        """获取多个文件夹的邮件总数和未读数

        使用 SEARCH ALL / SEARCH UNSEEN 获取计数，与 list_messages 一致。
        返回 { folder_path: {"total": int, "unread": int} }
        """
        return {}

    @abstractmethod
    async def fetch_messages(self, folder: str, page: int = 1, page_size: int = 20) -> MessageList:
        pass

    async def fetch_new_message_uids(self, folder: str, since_uid: int) -> List[int]:
        """获取大于指定UID的新邮件UID列表（用于增量同步）

        使用 UID SEARCH UID > since_uid 只返回新邮件的 UID，
        不需要 SEARCH ALL 再对比，效率更高。
        子类必须实现。
        """
        raise NotImplementedError

    async def fetch_messages_by_uids(self, folder: str, uids: List[int]) -> List[Message]:
        """根据 UID 列表批量获取邮件摘要（增量同步时使用）

        使用 UID FETCH uid1,uid2,... (FLAGS BODY.PEEK[...]) 批量获取。
        子类必须实现。
        """
        raise NotImplementedError

    @abstractmethod
    async def fetch_message_detail(self, message_id: str, folder: str = "INBOX") -> Message:
        pass

    async def fetch_attachment_data(self, message_id: str, folder: str, part_number: int) -> Optional[bytes]:
        """获取邮件附件的二进制数据

        通过 IMAP UID FETCH uid (BODY.PEEK[part_number]) 获取指定 part 的数据。
        子类必须实现。
        """
        raise NotImplementedError

    @abstractmethod
    async def mark_as_read(self, message_id: str, folder: str = "INBOX") -> None:
        pass

    @abstractmethod
    async def mark_as_unread(self, message_id: str, folder: str = "INBOX") -> None:
        pass

    @abstractmethod
    async def move_message(self, message_id: str, target_folder: str, source_folder: str = "INBOX") -> None:
        pass

    @abstractmethod
    async def delete_message(self, message_id: str, folder: str = "INBOX") -> None:
        """彻底删除邮件（标记DELETED + EXPUNGE）"""
        pass

    async def idle_wait(self, folder: str = "INBOX", timeout_seconds: int = 1740) -> bool:
        """IMAP IDLE/轮询等待新邮件通知

        在指定文件夹上等待新邮件通知。
        返回 True 表示检测到新邮件，False 表示超时需重试。

        - IDLE provider（Gmail/QQ/Outlook）：由 idle_manager 的 IdleConnection 管理
        - Poll provider（iCloud）：由 poll_manager 的 PollConnection 管理
        - 网易：覆盖此方法调用 _noop_poll（需要 IMAP ID 命令）
        """
        raise NotImplementedError("由 idle_manager/poll_manager 管理，网易需覆盖此方法")

    @abstractmethod
    async def disconnect(self) -> None:
        pass


class MailSender(ABC):
    @abstractmethod
    async def connect(self, credentials: Credentials) -> None:
        pass

    @abstractmethod
    async def send_message(
        self,
        to: list[str],
        subject: str,
        body_html: str,
        body_text: str = "",
        cc: list[str] = None,
        bcc: list[str] = None,
        attachments: list[str] = None,
        in_reply_to: str = None,
    ) -> SendResult:
        """发送邮件

        Args:
            to: 收件人列表
            subject: 邮件主题
            body_html: HTML 格式正文
            body_text: 纯文本备选正文
            cc: 抄送列表
            bcc: 密送列表
            attachments: 附件文件路径列表
            in_reply_to: 回复的邮件 Message-ID（设置 References/In-Reply-To 头）
        """
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self) -> None:
        pass
