import os
import re
import time
import urllib.parse
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate

from models import Account
from models import CachedMessage
from db import get_folder_stats, search_cached_messages_by_folder, upsert_cached_messages, upsert_folder_stats
from providers.factory import ProviderFactory
from services.mail_cache import sync_folder_to_cache
from services.sync import sync_service
from services.token import ensure_token
from utils.logger import get_logger

logger = get_logger("services.outgoing_mail")


def _html_to_text(body_html: str) -> str:
    if not body_html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", body_html, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def build_outgoing_message_bytes(
    from_email: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body_html: str,
    attachments: list[str],
    in_reply_to: str | None = None,
) -> bytes:
    msg = MIMEMultipart("mixed")
    msg["From"] = from_email
    msg["To"] = ", ".join(to or [])
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject or ""
    msg["Date"] = formatdate(localtime=True)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to

    alt = MIMEMultipart("alternative")
    body_text = _html_to_text(body_html)
    if body_text:
        alt.attach(MIMEText(body_text, "plain", "utf-8"))
    alt.attach(MIMEText(body_html or "", "html", "utf-8"))
    msg.attach(alt)

    for file_path in attachments or []:
        with open(file_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        filename = os.path.basename(file_path)
        part.add_header(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}",
        )
        msg.attach(part)

    return msg.as_bytes()


async def _cache_outgoing_message_locally(
    account: Account,
    user_uid: str,
    sent_folder: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body_html: str,
    attachments: list[str],
) -> None:
    local_uid = -int(time.time() * 1000)
    await upsert_cached_messages([
        CachedMessage(
            id=f"{account.id}_{local_uid}",
            account_id=account.id,
            user_uid=user_uid,
            uid=local_uid,
            folder=sent_folder,
            subject=subject or "",
            from_addr=account.email,
            to_addr=", ".join((to or []) + (cc or []) + (bcc or [])),
            date=formatdate(localtime=True),
            is_read=True,
            has_attachments=bool(attachments),
            body_text=_html_to_text(body_html or ""),
            body_html=body_html or "",
            cached_at=time.time(),
        )
    ])
    stats = await get_folder_stats(account.id, sent_folder)
    total = max(0, int(stats.get("total_count", 0) or 0)) + 1
    await upsert_folder_stats(account.id, sent_folder, total, 0)


async def find_special_folder(account: Account, folder_type: str) -> str:
    receiver = ProviderFactory.get_receiver(account.provider)
    credentials = await ensure_token(account)
    await receiver.connect(credentials)
    try:
        folders = await receiver.fetch_folders()
    finally:
        try:
            await receiver.disconnect()
        except Exception:
            pass

    folder_type = (folder_type or "").lower().strip()
    keywords_map = {
        "sent": ["sent", "sent messages", "sent items", "已发送"],
        "drafts": ["draft", "drafts", "草稿"],
        "junk": ["junk", "spam", "垃圾"],
        "trash": ["trash", "deleted", "deleted items", "deleted messages", "已删除"],
    }
    keywords = keywords_map.get(folder_type, [])
    for folder in folders:
        path_lower = (folder.path or "").lower()
        name_lower = (folder.name or "").lower()
        if any(keyword in path_lower or keyword in name_lower for keyword in keywords):
            return folder.path
    return ""


async def ensure_sent_message_cached(
    account: Account,
    user_uid: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body_html: str,
    attachments: list[str],
    in_reply_to: str | None = None,
) -> str:
    sent_folder = await find_special_folder(account, "sent")
    if not sent_folder:
        logger.warning("send success but sent folder not found: %s", account.email)
        return ""

    message_bytes = build_outgoing_message_bytes(
        from_email=account.email,
        to=to or [],
        cc=cc or [],
        bcc=bcc or [],
        subject=subject or "",
        body_html=body_html or "",
        attachments=attachments or [],
        in_reply_to=in_reply_to,
    )

    await sync_folder_to_cache(account, sent_folder)

    should_append = True
    try:
        cached = await search_cached_messages_by_folder(
            account.user_uid,
            account.id,
            sent_folder,
            subject or "",
            page=1,
            page_size=20,
        )
        expected_to = set((to or []) + (cc or []) + (bcc or []))
        for item in cached.get("messages", []):
            if (item.get("subject") or "") != (subject or ""):
                continue
            to_addr = item.get("to_addr") or ""
            if not expected_to or any(addr in to_addr for addr in expected_to):
                should_append = False
                break
    except Exception as exc:
        logger.debug("sent cache duplicate check failed: %s", exc)

    if should_append:
        try:
            receiver = ProviderFactory.get_receiver(account.provider)
            credentials = await ensure_token(account)
            await receiver.connect(credentials)
            try:
                await receiver.save_draft(message_bytes, sent_folder)
                logger.info("appended outgoing message to sent folder: %s %s", account.email, sent_folder)
            finally:
                try:
                    await receiver.disconnect()
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("append sent message fallback failed: %s", exc)
            await _cache_outgoing_message_locally(
                account=account,
                user_uid=user_uid,
                sent_folder=sent_folder,
                to=to or [],
                cc=cc or [],
                bcc=bcc or [],
                subject=subject or "",
                body_html=body_html or "",
                attachments=attachments or [],
            )

    try:
        receiver = ProviderFactory.get_receiver(account.provider)
        credentials = await ensure_token(account)
        await receiver.connect(credentials)
        await receiver.disconnect()
    except Exception as exc:
        logger.debug("sent folder reconnect check failed: %s", exc)

    await sync_folder_to_cache(account, sent_folder)
    await sync_service.refresh_clients(account.id, sent_folder, user_uid=user_uid)
    return sent_folder
