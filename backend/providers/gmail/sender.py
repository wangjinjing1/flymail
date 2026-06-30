import asyncio
import base64
import os
import smtplib
import ssl
import urllib.parse
from email import encoders
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate
from ..base import MailSender, Credentials, SendResult
from ..ipv4 import IPv4SMTP
from .config import GMAIL_SMTP_HOST, GMAIL_SMTP_PORT
from utils.logger import get_logger

logger = get_logger("gmail.sender")


class GmailSender(MailSender):
    """Gmail SMTP 邮件发送器"""

    def __init__(self):
        self._conn: IPv4SMTP = None
        self._credentials: Credentials = None

    async def connect(self, credentials: Credentials) -> None:
        """建立 SMTP 连接（使用 OAuth2 认证）"""
        self._credentials = credentials
        self._conn = await asyncio.to_thread(self._connect_smtp, credentials)

    def _connect_smtp(self, credentials: Credentials) -> IPv4SMTP:
        """同步建立 SMTP 连接（使用 IPv4 强制子类）"""
        conn = IPv4SMTP(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT)
        conn.ehlo()
        # 安全修复 S8：传入安全 SSL context，验证证书和主机名
        # 旧代码 conn.starttls() 不传 context，使用不验证证书的默认 context，存在 MITM 风险
        ssl_ctx = ssl.create_default_context()
        conn.starttls(context=ssl_ctx)
        conn.ehlo()
        # 安全修复 S9：base64 编码 auth_string，防止 token 明文泄露到日志/异常
        # 旧代码直接拼接 auth_string 到 AUTH 命令，异常时可能泄露 access_token
        auth_string = f"user={credentials.extra.get('email', '')}\x01auth=Bearer {credentials.access_token}\x01\x01"
        auth_b64 = base64.b64encode(auth_string.encode("utf-8")).decode("ascii")
        code, response = conn.docmd("AUTH", "XOAUTH2 " + auth_b64)
        if code != 235:
            raise smtplib.SMTPAuthenticationError(
                code, response.decode("utf-8", errors="ignore")
            )
        return conn

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
        """发送邮件"""
        if not self._conn:
            raise ConnectionError("Not connected")

        try:
            return await asyncio.to_thread(
                self._send_sync, to, subject, body_html, body_text, cc, bcc, attachments, in_reply_to
            )
        except Exception as e:
            return SendResult(success=False, error=str(e))

    def _send_sync(self, to, subject, body_html, body_text="", cc=None, bcc=None, attachments=None, in_reply_to=None):
        """同步发送邮件

        使用 MIMEMultipart("mixed") 作为外层，内嵌 alternative 放纯文本+HTML，
        附件用 MIMEBase 编码，支持 CC/BCC/In-Reply-To。
        """
        from_email = self._credentials.extra.get("email", "")
        msg = MIMEMultipart("mixed")
        msg["From"] = from_email
        msg["To"] = ", ".join(to) if isinstance(to, list) else to
        if cc:
            msg["Cc"] = ", ".join(cc) if isinstance(cc, list) else cc
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to

        # 正文：纯文本+HTML
        alt = MIMEMultipart("alternative")
        if body_text:
            alt.attach(MIMEText(body_text, "plain", "utf-8"))
        alt.attach(MIMEText(body_html, "html", "utf-8"))
        msg.attach(alt)

        # 附件
        if attachments:
            for file_path in attachments:
                with open(file_path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                filename = os.path.basename(file_path)
                part.add_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}")
                msg.attach(part)

        # 所有收件人（包括 CC/BCC）
        all_recipients = list(to) if isinstance(to, list) else [to]
        if cc:
            all_recipients.extend(cc if isinstance(cc, list) else [cc])
        if bcc:
            all_recipients.extend(bcc if isinstance(bcc, list) else [bcc])

        self._conn.sendmail(from_email, all_recipients, msg.as_string())
        return SendResult(success=True)

    async def disconnect(self) -> None:
        """断开连接"""
        if self._conn:
            try:
                await asyncio.to_thread(self._conn.quit)
            except Exception as e:
                logger.debug("断开连接失败: %s", e)
            self._conn = None
