"""草稿箱管理服务

通过 IMAP APPEND 命令将草稿保存到邮件服务器的草稿箱文件夹，
支持跨平台（QQ/Gmail/Outlook/iCloud/网易）。
"""
import asyncio
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

logger = logging.getLogger("flymail")


def _build_draft_message(
    from_email: str,
    from_name: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body_html: str,
) -> bytes:
    """构建草稿 MIME 邮件（用于 IMAP APPEND）"""
    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((from_name or "", from_email))
    if to:
        msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    # 草稿标记
    msg["X-Draft"] = "True"

    # 纯文本备选（简单去除 HTML 标签）
    import re
    body_text = re.sub(r"<[^>]+>", "", body_html) if body_html else ""
    if body_text:
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    return msg.as_bytes()


async def save_draft_to_imap(receiver, from_email: str, from_name: str,
                              to: list[str], cc: list[str], bcc: list[str],
                              subject: str, body_html: str,
                              folder: str = "Drafts") -> bool:
    """通过 IMAP APPEND 命令保存草稿到服务器

    返回 True 表示保存成功。
    """
    try:
        message_bytes = _build_draft_message(from_email, from_name, to, cc, bcc, subject, body_html)
        await receiver.save_draft(message_bytes, folder)
        logger.info("草稿保存成功: %s", subject)
        return True
    except Exception as e:
        logger.error("草稿保存失败: %s", e)
        return False


async def delete_draft_from_imap(receiver, uid: int, folder: str = "Drafts") -> bool:
    """删除 IMAP 服务器上的草稿（UID STORE +FLAGS \Deleted + EXPUNGE）"""
    try:
        await receiver.delete_message_batch([str(uid)], folder)
        logger.info("草稿删除成功: UID %s", uid)
        return True
    except Exception as e:
        logger.error("草稿删除失败: %s", e)
        return False
