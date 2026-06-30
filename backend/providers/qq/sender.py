import asyncio
import os
import urllib.parse
from email import encoders
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate
from typing import Optional
from ..base import MailSender, Credentials, SendResult
from providers.ipv4 import IPv4SMTP_SSL
from providers.qq.config import SMTP_HOST, SMTP_PORT
from utils.logger import get_logger

logger = get_logger("qq.sender")


class QQSender(MailSender):
    """QQ邮箱SMTP发送器"""

    SMTP_HOST = SMTP_HOST
    SMTP_PORT = SMTP_PORT
    TIMEOUT = 30  # 单个 socket 操作超时30秒

    def __init__(self):
        self.conn: Optional[IPv4SMTP_SSL] = None
        self.email_addr: str = ""

    async def connect(self, credentials: Credentials) -> None:
        """连接到QQ邮箱SMTP服务器，支持重试"""
        self.email_addr = credentials.extra.get("email", "")
        auth_code = credentials.access_token  # QQ邮箱使用授权码

        last_error = None
        # 最多重试3次，QQ邮箱偶尔会超时
        for attempt in range(3):
            try:
                self.conn = await asyncio.to_thread(self._connect_smtp, self.email_addr, auth_code)
                return
            except Exception as e:
                last_error = e
                self.conn = None
                if attempt < 2:
                    # 重试前等待1秒
                    await asyncio.sleep(1)
        raise Exception(f"QQ邮箱SMTP连接失败（已重试3次）: {str(last_error)}")

    def _connect_smtp(self, email_addr: str, auth_code: str) -> IPv4SMTP_SSL:
        """同步建立 SMTP 连接（在线程池中运行，使用 IPv4 强制子类）"""
        conn = IPv4SMTP_SSL(self.SMTP_HOST, self.SMTP_PORT, timeout=self.TIMEOUT)
        # 修复 P5: 登录失败时关闭连接，防止 socket 泄漏
        try:
            conn.login(email_addr, auth_code)
            return conn
        except Exception:
            # 登录失败，关闭连接防止 socket 泄漏
            try:
                conn.quit()
            except Exception as e:
                logger.debug("登录失败后关闭连接失败: %s", e)
            raise

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
