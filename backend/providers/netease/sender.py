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
from ..ipv4 import IPv4SMTP_SSL
from .config import SMTP_163_HOST, SMTP_163_PORT, SMTP_126_HOST, SMTP_126_PORT, SMTP_188_HOST, SMTP_188_PORT, SMTP_YEAH_HOST, SMTP_YEAH_PORT
from utils.logger import get_logger

logger = get_logger("netease.sender")


class NeteaseSender(MailSender):
    """网易邮箱SMTP发送器（支持163/126/188/yeah.net邮箱）"""

    TIMEOUT = 30  # 单个 socket 操作超时30秒

    def __init__(self):
        self.conn: Optional[IPv4SMTP_SSL] = None
        self.email_addr: str = ""

    def _get_smtp_host(self, email_addr: str) -> str:
        """根据邮箱后缀返回对应的SMTP服务器地址"""
        suffix = email_addr.split("@")[-1].lower() if "@" in email_addr else ""
        host_map = {
            "126.com": SMTP_126_HOST,
            "188.com": SMTP_188_HOST,
            "yeah.net": SMTP_YEAH_HOST,
        }
        return host_map.get(suffix, SMTP_163_HOST)

    def _get_smtp_port(self, email_addr: str) -> int:
        """根据邮箱后缀返回对应的SMTP端口"""
        suffix = email_addr.split("@")[-1].lower() if "@" in email_addr else ""
        port_map = {
            "126.com": SMTP_126_PORT,
            "188.com": SMTP_188_PORT,
            "yeah.net": SMTP_YEAH_PORT,
        }
        return port_map.get(suffix, SMTP_163_PORT)

    async def connect(self, credentials: Credentials) -> None:
        """连接到网易邮箱SMTP服务器"""
        self.email_addr = credentials.extra.get("email", "")
        auth_code = credentials.access_token  # 网易邮箱使用授权码

        try:
            self.conn = await asyncio.to_thread(self._connect_smtp, self.email_addr, auth_code)
        except Exception as e:
            self.conn = None
            raise Exception(f"网易邮箱SMTP连接失败: {str(e)}")

    def _connect_smtp(self, email_addr: str, auth_code: str) -> IPv4SMTP_SSL:
        """同步建立 SMTP 连接（使用 IPv4 强制子类）"""
        host = self._get_smtp_host(email_addr)
        port = self._get_smtp_port(email_addr)
        conn = IPv4SMTP_SSL(host, port, timeout=self.TIMEOUT)
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
        """同步发送邮件

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
