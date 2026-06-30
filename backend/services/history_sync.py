import asyncio
import base64
import json
import re
import time
import uuid
from pathlib import Path

from data_paths import (
    HISTORY_ATTACHMENTS_DIR,
    HISTORY_INLINE_DIR,
    HISTORY_RAW_DIR,
    ensure_data_dirs,
)
from db import (
    create_history_sync_job,
    get_account_by_id,
    get_history_sync_job,
    get_history_sync_job_by_id,
    update_history_sync_job,
    upsert_cached_messages,
)
from models import CachedMessage
from providers.factory import ProviderFactory
from services.token import ensure_token
from utils.logger import get_logger
from utils.tasks import create_background_task

logger = get_logger("history_sync")


def _slugify(value: str) -> str:
    value = re.sub(r"[\\\\/:*?\"<>|]+", "_", value or "")
    value = value.strip().strip(".")
    return value or "unknown"


def _history_message_path(account_id: str, folder: str, uid: int) -> Path:
    return HISTORY_RAW_DIR / account_id / _slugify(folder) / f"{uid}.json"


def _history_attachment_path(account_id: str, uid: int, part_number: int, filename: str, *, inline: bool) -> Path:
    safe_filename = _slugify(filename or f"part_{part_number}")
    base_dir = HISTORY_INLINE_DIR if inline else HISTORY_ATTACHMENTS_DIR
    return base_dir / account_id / str(uid) / f"{part_number}_{safe_filename}"


def _strip_cid(content_id: str) -> str:
    return (content_id or "").strip().strip("<>").strip()


def _replace_inline_cids(body_html: str, cid: str, data_uri: str) -> str:
    if not body_html or not cid:
        return body_html
    body_html = body_html.replace(f"cid:{cid}", data_uri)
    body_html = body_html.replace(f"cid:<{cid}>", data_uri)
    return body_html


async def _load_job_or_none(job_id: str) -> dict | None:
    try:
        return await get_history_sync_job_by_id(job_id)
    except Exception as exc:
        logger.debug("load history sync job failed: %s", exc)
        return None


async def _is_paused(job_id: str) -> bool:
    job = await _load_job_or_none(job_id)
    return bool(job and job.get("status") == "paused")


async def schedule_history_sync(account_id: str) -> bool:
    return await start_history_sync(account_id)


async def start_history_sync(account_id: str, *, reset: bool = False) -> bool:
    existing = await get_history_sync_job(account_id)
    if existing and existing.get("status") in {"pending", "running"}:
        return False

    if existing and not reset and existing.get("status") == "paused":
        await update_history_sync_job(
            existing["id"],
            status="pending",
            error_message="",
            finished_at=0,
        )
        create_background_task(run_history_sync(account_id, existing["id"]), name="history_sync")
        return True

    create_background_task(run_history_sync(account_id), name="history_sync")
    return True


async def pause_history_sync(account_id: str) -> bool:
    job = await get_history_sync_job(account_id)
    if not job or job.get("status") not in {"pending", "running"}:
        return False
    await update_history_sync_job(job["id"], status="paused")
    return True


async def resume_history_sync(account_id: str) -> bool:
    job = await get_history_sync_job(account_id)
    if not job or job.get("status") != "paused":
        return False
    await update_history_sync_job(job["id"], status="pending", error_message="", finished_at=0)
    create_background_task(run_history_sync(account_id, job["id"]), name="history_sync")
    return True


async def _cache_message_assets(receiver, account, folder_name: str, detail) -> tuple[str, int, int]:
    body_html = detail.body_html or ""
    downloaded_attachments = 0
    downloaded_inline_images = 0

    for attachment in detail.attachments or []:
        try:
            att_data = await receiver.fetch_attachment_data(str(detail.uid), folder_name, attachment.part_number)
        except Exception as exc:
            logger.debug(
                "history sync attachment download failed: account=%s uid=%s part=%s error=%s",
                account.email,
                detail.uid,
                attachment.part_number,
                exc,
            )
            continue

        if not att_data:
            continue

        target_path = _history_attachment_path(
            account.id,
            detail.uid,
            attachment.part_number,
            attachment.filename,
            inline=bool(attachment.is_inline),
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(att_data)

        if attachment.is_inline:
            cid = _strip_cid(attachment.content_id)
            if cid:
                content_type = attachment.content_type or "application/octet-stream"
                data_uri = f"data:{content_type};base64,{base64.b64encode(att_data).decode('ascii')}"
                body_html = _replace_inline_cids(body_html, cid, data_uri)
            downloaded_inline_images += 1
        else:
            downloaded_attachments += 1

    return body_html, downloaded_attachments, downloaded_inline_images


async def run_history_sync(account_id: str, job_id: str | None = None) -> None:
    account = await get_account_by_id(account_id)
    if not account:
        logger.warning("history sync skipped: account not found %s", account_id)
        return

    ensure_data_dirs()
    receiver = None

    if job_id:
        job = await get_history_sync_job_by_id(job_id)
        if not job:
            logger.warning("history sync skipped: job not found %s", job_id)
            return
    else:
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "account_id": account.id,
            "user_uid": account.user_uid,
            "status": "pending",
            "current_folder": "",
            "current_page": 1,
            "current_uid": 0,
            "total_folders": 0,
            "completed_folders": 0,
            "fetched_messages": 0,
            "downloaded_attachments": 0,
            "downloaded_inline_images": 0,
            "error_message": "",
            "created_at": time.time(),
            "updated_at": time.time(),
            "finished_at": 0,
        }
        await create_history_sync_job(job)

    try:
        credentials = await ensure_token(account)
        receiver = ProviderFactory.get_receiver(account.provider)
        await receiver.connect(credentials)
        folders = await receiver.fetch_folders()

        current_folder_name = job.get("current_folder") or ""
        start_index = 0
        if current_folder_name:
            for idx, folder in enumerate(folders):
                folder_name = getattr(folder, "path", "") or getattr(folder, "name", "") or "INBOX"
                if folder_name == current_folder_name:
                    start_index = idx
                    break
        elif job.get("completed_folders"):
            start_index = min(int(job.get("completed_folders", 0)), max(len(folders) - 1, 0))

        await update_history_sync_job(
            job_id,
            status="running",
            total_folders=len(folders),
            error_message="",
            finished_at=0,
        )

        fetched_messages = int(job.get("fetched_messages", 0) or 0)
        downloaded_attachments = int(job.get("downloaded_attachments", 0) or 0)
        downloaded_inline_images = int(job.get("downloaded_inline_images", 0) or 0)

        for index in range(start_index, len(folders)):
            if await _is_paused(job_id):
                return

            folder = folders[index]
            folder_name = getattr(folder, "path", "") or getattr(folder, "name", "") or "INBOX"
            page = int(job.get("current_page", 1) or 1) if folder_name == current_folder_name else 1

            while True:
                if await _is_paused(job_id):
                    await update_history_sync_job(
                        job_id,
                        current_folder=folder_name,
                        current_page=page,
                        fetched_messages=fetched_messages,
                        downloaded_attachments=downloaded_attachments,
                        downloaded_inline_images=downloaded_inline_images,
                    )
                    return

                await update_history_sync_job(
                    job_id,
                    current_folder=folder_name,
                    current_page=page,
                    completed_folders=index,
                    fetched_messages=fetched_messages,
                    downloaded_attachments=downloaded_attachments,
                    downloaded_inline_images=downloaded_inline_images,
                )
                result = await receiver.fetch_messages(folder_name, page=page, page_size=20)
                if not result.messages:
                    break

                cached_batch = []
                for message in result.messages:
                    if await _is_paused(job_id):
                        await update_history_sync_job(
                            job_id,
                            current_folder=folder_name,
                            current_page=page,
                            current_uid=getattr(message, "uid", 0),
                            fetched_messages=fetched_messages,
                            downloaded_attachments=downloaded_attachments,
                            downloaded_inline_images=downloaded_inline_images,
                        )
                        return

                    detail = await receiver.fetch_message_detail(str(message.uid), folder=folder_name)
                    body_html, att_count, inline_count = await _cache_message_assets(receiver, account, folder_name, detail)
                    detail.body_html = body_html

                    cached_batch.append(
                        CachedMessage(
                            id=f"{account.id}_{detail.uid}",
                            account_id=account.id,
                            user_uid=account.user_uid,
                            uid=detail.uid,
                            folder=folder_name,
                            subject=detail.subject,
                            from_addr=detail.from_addr,
                            to_addr=detail.to_addr,
                            date=detail.date,
                            is_read=detail.is_read,
                            is_starred=detail.is_starred,
                            has_attachments=bool(detail.attachments),
                            body_text=detail.body_text,
                            body_html=detail.body_html,
                            cached_at=time.time(),
                        )
                    )

                    raw_path = _history_message_path(account.id, folder_name, detail.uid)
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    raw_path.write_text(
                        json.dumps(detail.model_dump(), ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

                    fetched_messages += 1
                    downloaded_attachments += att_count
                    downloaded_inline_images += inline_count
                    await update_history_sync_job(
                        job_id,
                        current_folder=folder_name,
                        current_page=page,
                        current_uid=detail.uid,
                        fetched_messages=fetched_messages,
                        downloaded_attachments=downloaded_attachments,
                        downloaded_inline_images=downloaded_inline_images,
                    )
                    await asyncio.sleep(0.05)

                if cached_batch:
                    await upsert_cached_messages(cached_batch)

                if result.total and page * result.page_size >= result.total:
                    break
                page += 1
                await update_history_sync_job(job_id, current_page=page, current_uid=0)

            await update_history_sync_job(
                job_id,
                completed_folders=index + 1,
                current_folder="",
                current_page=1,
                current_uid=0,
                fetched_messages=fetched_messages,
                downloaded_attachments=downloaded_attachments,
                downloaded_inline_images=downloaded_inline_images,
            )
            current_folder_name = ""

        await update_history_sync_job(
            job_id,
            status="completed",
            completed_folders=len(folders),
            fetched_messages=fetched_messages,
            downloaded_attachments=downloaded_attachments,
            downloaded_inline_images=downloaded_inline_images,
            current_folder="",
            current_page=1,
            current_uid=0,
            finished_at=time.time(),
        )
    except Exception as exc:
        logger.warning("history sync failed for %s: %s", account.email, exc)
        await update_history_sync_job(
            job_id,
            status="failed",
            error_message=str(exc),
            finished_at=time.time(),
        )
    finally:
        if receiver:
            try:
                await receiver.disconnect()
            except Exception:
                pass
