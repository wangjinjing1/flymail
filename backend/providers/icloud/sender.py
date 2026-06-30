import asyncio
import os
import smtplib
import socket
import ssl
import urllib.parse
from email import encoders
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate
from typing import Optional
from ..base import MailSender, Credentials, SendResult
from providers.ipv4 import IPv4SMTP
from providers.icloud.config import SMTP_HOST, SMTP_PORT
from utils.logger import get_logger

logger = get_logger("icloud.sender")


class ICloudSender(MailSender):
    """iCloud邮箱SMTP发送器

    iCloud SMTP 使用 587 端口 + STARTTLS 加密，与 QQ/网易的 465 端口 SSL 直连不同。
    认证方式：邮箱地址 + 应用专用密码。
    """

    SMTP_HOST = SMTP_HOST
    SMTP_PORT = SMTP_PORT  # STARTTLS
    TIMEOUT = 30  # 单个 socket 操作超时30秒

    def __init__(self):
        self.conn: Optional[IPv4SMTP] = None
        self.email_addr: str = ""

    async def connect(self, credentials: Credentials) -> None:
        """连接到iCloud邮箱SMTP服务器（在线程池中执行阻塞操作）"""
        self.email_addr = credentials.extra.get("email", "")
        app_password = credentials.access_token  # iCloud使用应用专用密码

        try:
            # 在线程池中执行阻塞的 SMTP 连接，避免卡住事件循环
            self.conn = await asyncio.to_thread(self._connect_smtp, self.email_addr, app_password)
        except Exception as e:
            self.conn = None
            raise Exception(f"iCloud邮箱SMTP连接失败: {str(e)}")

    def _connect_smtp(self, email_addr: str, app_password: str) -> IPv4SMTP:
        """同步建立 SMTP 连接（在线程池中运行，使用 IPv4 强制子类 + STARTTLS）"""
        conn = IPv4SMTP(self.SMTP_HOST, self.SMTP_PORT, timeout=self.TIMEOUT)
        conn.ehlo()  # 发送 EHLO 获取服务器支持的功能
        # 安全修复 S8：传入安全 SSL context，验证证书和主机名
        # 旧代码 conn.starttls() 不传 context，使用不验证证书的默认 context，存在 MITM 风险
        ssl_ctx = ssl.create_default_context()
        conn.starttls(context=ssl_ctx)
        conn.ehlo()  # STARTTLS 后需要再次 EHLO
        conn.login(email_addr, app_password)  # 使用应用专用密码认证
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
        if not self.conn:
            raise Exception("未连接到SMTP服务器")

        try:
            return await asyncio.to_thread(
                self._send_sync, to, subject, body_html, body_text, cc, bcc, attachments, in_reply_to
            )
        except Exception as e:
            return SendResult(success=False, error=str(e))

    def _send_sync(self, to, subject, body_html, body_text="", cc=None, bcc=None, attachments=None, in_reply_to=None):
        """同步发送邮件（在线程池中运行）

        使用 MIMEMultipart("mixed") 作为外层，内嵌 alternative 放纯文本+HTML，
        附件用 MIMEBase 编码，支持 CC/BCC/In-Reply-To。
        """
        msg = MIMEMultipart("mixed")
        msg["From"] = self.email_addr
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

        self.conn.sendmail(self.email_addr, all_recipients, msg.as_string())
        return SendResult(success=True)

    async def disconnect(self) -> None:
        """断开连接"""
        if self.conn:
            try:
                await asyncio.to_thread(self.conn.quit)
            except Exception as e:
                logger.debug("断开连接失败: %s", e)
            self.conn = None
