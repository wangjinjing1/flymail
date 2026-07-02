import asyncio
import shutil
import time
import uuid
from pathlib import Path

from data_paths import (
    DOWNLOADS_DIR,
    build_message_file_path,
    coalesce_message_date,
    clear_account_storage,
    ensure_message_file_location,
    ensure_data_dirs,
    normalize_message_date,
    UNKNOWN_MESSAGE_DATE,
)
from db import (
    batch_update_is_read,
    create_history_sync_job,
    batch_delete_cached_messages,
    delete_account,
    get_cached_attachment,
    get_cached_attachment_rows,
    get_cached_count,
    get_cached_message_detail,
    get_account_by_id,
    get_cached_uids,
    get_history_sync_job,
    get_history_sync_job_by_id,
    list_cached_attachments,
    update_history_sync_job,
    upsert_cached_attachments,
    upsert_cached_messages,
    delete_cached_attachments_by_account,
    delete_cached_messages_by_account,
    delete_folder_stats_by_account,
    delete_history_sync_jobs_by_account,
    upsert_folder_stats,
)
from models import CachedAttachment, CachedMessage
from providers.factory import ProviderFactory
from services.sync import sync_service
from services.token import ensure_token
from utils.logger import get_logger
from utils.tasks import create_background_task

logger = get_logger("history_sync")


def _strip_cid(content_id: str) -> str:
    return (content_id or "").strip().strip("<>").strip()


def _replace_inline_cids(body_html: str, cid: str, data_uri: str) -> str:
    if not body_html or not cid:
        return body_html
    body_html = body_html.replace(f"cid:{cid}", data_uri)
    body_html = body_html.replace(f"cid:<{cid}>", data_uri)
    return body_html


def _build_inline_attachment_url(account_id: str, folder_name: str, uid: int, part_number: int) -> str:
    return f"/api/messages/{uid}/attachments/{part_number}?account_id={account_id}&folder={folder_name}"


async def _merge_history_sync_metrics(
    account,
    *,
    folder_name: str = "",
    current_uid: int = 0,
    fetched_messages_delta: int = 0,
    downloaded_attachments_delta: int = 0,
    downloaded_inline_images_delta: int = 0,
) -> None:
    if fetched_messages_delta <= 0 and downloaded_attachments_delta <= 0 and downloaded_inline_images_delta <= 0:
        return

    job = await get_history_sync_job(account.id, job_type="history_sync")
    if not job:
        job_id = uuid.uuid4().hex
        await create_history_sync_job(
            {
                "id": job_id,
                "account_id": account.id,
                "user_uid": account.user_uid,
                "job_type": "history_sync",
                "status": "completed",
                "current_folder": folder_name,
                "current_page": 1,
                "current_uid": current_uid,
                "total_folders": 0,
                "completed_folders": 0,
                "fetched_messages": fetched_messages_delta,
                "downloaded_attachments": downloaded_attachments_delta,
                "downloaded_inline_images": downloaded_inline_images_delta,
                "error_message": "",
                "created_at": time.time(),
                "updated_at": time.time(),
                "finished_at": time.time(),
            }
        )
        return

    next_fields = {
        "fetched_messages": int(job.get("fetched_messages", 0) or 0) + fetched_messages_delta,
        "downloaded_attachments": int(job.get("downloaded_attachments", 0) or 0) + downloaded_attachments_delta,
        "downloaded_inline_images": int(job.get("downloaded_inline_images", 0) or 0) + downloaded_inline_images_delta,
    }
    if folder_name:
        next_fields["current_folder"] = folder_name
    if current_uid:
        next_fields["current_uid"] = current_uid
    if job.get("status") in {"completed", "failed", "idle", ""}:
        next_fields["status"] = "completed"
        next_fields["finished_at"] = time.time()
        next_fields["error_message"] = ""
    await update_history_sync_job(job["id"], **next_fields)


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


CORE_HISTORY_FOLDERS = {
    "inbox": "INBOX",
    "sent": "Sent",
    "drafts": "Drafts",
    "junk": "Junk",
    "trash": "Trash",
}

CORE_HISTORY_FOLDER_ALIASES = {
    "INBOX": {"INBOX", "Inbox"},
    "Sent": {"Sent", "Sent Messages", "Sent Items", "[Gmail]/Sent Mail"},
    "Drafts": {"Drafts", "[Gmail]/Drafts"},
    "Junk": {"Junk", "Junk Email", "Spam", "[Gmail]/Spam"},
    "Trash": {"Trash", "Deleted", "Deleted Items", "Deleted Messages", "[Gmail]/Trash", "已删除"},
}


CORE_HISTORY_FOLDER_ORDER = ["INBOX", "Sent", "Drafts", "Junk", "Trash"]


def normalize_history_folder(folder: str) -> str:
    key = (folder or "").strip()
    if not key:
        return "INBOX"
    mapped = CORE_HISTORY_FOLDERS.get(key.lower(), key)
    mapped_lower = mapped.lower()
    for canonical, aliases in CORE_HISTORY_FOLDER_ALIASES.items():
        if any(mapped_lower == alias.lower() for alias in aliases):
            return canonical
    return mapped


def history_folder_matches(candidate: str, wanted: str) -> bool:
    candidate_key = normalize_history_folder(candidate)
    wanted_key = normalize_history_folder(wanted)
    if candidate_key == wanted_key:
        return True
    aliases = CORE_HISTORY_FOLDER_ALIASES.get(wanted_key, {wanted_key})
    return any((candidate or "").lower() == alias.lower() for alias in aliases)


def _folder_ref(path: str, name: str | None = None):
    return type("FolderRef", (), {"path": path, "name": name or path})()


def _resolve_history_folders(remote_folders: list, wanted_folders: list[str] | None = None) -> list:
    wanted = [normalize_history_folder(item) for item in (wanted_folders or CORE_HISTORY_FOLDER_ORDER)]
    resolved = []
    for wanted_folder in wanted:
        match = next(
            (
                folder for folder in remote_folders
                if history_folder_matches(
                    getattr(folder, "path", "") or getattr(folder, "name", "") or "INBOX",
                    wanted_folder,
                )
            ),
            None,
        )
        resolved.append(match or _folder_ref(wanted_folder))
    return resolved


async def is_full_history_sync_active(account_id: str) -> bool:
    job = await get_history_sync_job(account_id, job_type="history_sync")
    return bool(job and job.get("status") in {"pending", "running"})


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

    create_background_task(run_history_sync(account_id, reset=reset), name="history_sync")
    return True


async def start_folder_history_sync(account_id: str, folder: str, *, reset: bool = False) -> tuple[bool, str]:
    if await is_full_history_sync_active(account_id):
        return False, "等待邮箱全量同步结束再重试"
    folder_name = normalize_history_folder(folder)
    job_type = f"folder_sync:{folder_name}"
    existing = await get_history_sync_job(account_id, job_type=job_type)
    if existing and existing.get("status") in {"pending", "running"}:
        return False, "当前文件夹正在同步"
    if existing and not reset and existing.get("status") == "paused":
        await update_history_sync_job(existing["id"], status="pending", error_message="", finished_at=0)
        create_background_task(run_history_sync(account_id, existing["id"], folders=[folder_name]), name="folder_history_sync")
        return True, ""
    create_background_task(
        run_history_sync(account_id, reset=reset, folders=[folder_name], job_type=job_type),
        name="folder_history_sync",
    )
    return True, ""


async def pause_folder_history_sync(account_id: str, folder: str) -> tuple[bool, str]:
    if await is_full_history_sync_active(account_id):
        return False, "等待邮箱全量同步结束再重试"
    folder_name = normalize_history_folder(folder)
    job = await get_history_sync_job(account_id, job_type=f"folder_sync:{folder_name}")
    if not job or job.get("status") not in {"pending", "running"}:
        return False, "当前文件夹没有正在运行的同步"
    await update_history_sync_job(job["id"], status="paused")
    return True, ""


async def resume_folder_history_sync(account_id: str, folder: str) -> tuple[bool, str]:
    if await is_full_history_sync_active(account_id):
        return False, "等待邮箱全量同步结束再重试"
    folder_name = normalize_history_folder(folder)
    job = await get_history_sync_job(account_id, job_type=f"folder_sync:{folder_name}")
    if not job or job.get("status") != "paused":
        return False, "当前文件夹没有暂停中的同步"
    await update_history_sync_job(job["id"], status="pending", error_message="", finished_at=0)
    create_background_task(run_history_sync(account_id, job["id"], folders=[folder_name]), name="folder_history_sync")
    return True, ""


async def start_folder_clear_cache(account_id: str, folder: str) -> tuple[bool, str]:
    if await is_full_history_sync_active(account_id):
        return False, "等待邮箱全量同步结束再重试"
    folder_name = normalize_history_folder(folder)
    job_type = f"folder_clear:{folder_name}"
    existing = await get_history_sync_job(account_id, job_type=job_type)
    if existing and existing.get("status") in {"pending", "running"}:
        return False, "当前文件夹正在清空"
    create_background_task(run_folder_clear_cache(account_id, folder_name, job_type), name="clear_folder_mail_cache")
    return True, ""


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


async def retry_history_sync(account_id: str) -> bool:
    job = await get_history_sync_job(account_id)
    if not job or job.get("status") != "failed":
        return False
    await update_history_sync_job(job["id"], status="pending", error_message="", finished_at=0)
    create_background_task(run_history_sync(account_id, job["id"]), name="history_sync")
    return True


async def _account_local_cache_files(account_id: str, account_email: str) -> list[Path]:
    files: dict[str, Path] = {}
    slug = account_email or account_id
    account_root = DOWNLOADS_DIR / slug
    if account_root.exists():
        for path in account_root.rglob("*"):
            if path.is_file():
                files[str(path)] = path
    for item in await get_cached_attachment_rows(account_id):
        local_path = item.get("local_path") or ""
        if local_path:
            path = Path(local_path)
            if path.exists() and path.is_file():
                files[str(path)] = path
    return list(files.values())


async def start_clear_cache(account_id: str) -> bool:
    existing = await get_history_sync_job(account_id, job_type="clear_cache")
    if existing and existing.get("status") in {"pending", "running"}:
        return False
    create_background_task(run_clear_cache(account_id), name="clear_mail_cache")
    return True


async def start_delete_account(account_id: str) -> bool:
    existing = await get_history_sync_job(account_id, job_type="account_delete")
    if existing and existing.get("status") in {"pending", "running"}:
        return False
    create_background_task(run_delete_account(account_id), name="delete_mail_account")
    return True


async def refresh_history_sync_job(account_id: str) -> dict | None:
    return await get_history_sync_job(account_id, job_type="history_sync")


async def run_clear_cache(account_id: str) -> None:
    account = await get_account_by_id(account_id)
    if not account:
        logger.warning("clear cache skipped: account not found %s", account_id)
        return

    ensure_data_dirs()
    job_id = uuid.uuid4().hex
    total_messages = 0
    file_paths = await _account_local_cache_files(account.id, account.email)
    total_files = len(file_paths)
    job = {
        "id": job_id,
        "account_id": account.id,
        "user_uid": account.user_uid,
        "job_type": "clear_cache",
        "status": "pending",
        "current_folder": "cache",
        "current_page": 1,
        "current_uid": 0,
        "total_folders": max(total_files, 1),
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
        await update_history_sync_job(job_id, status="running")

        total_messages = await delete_cached_messages_by_account(account.id)
        await delete_cached_attachments_by_account(account.id)
        await delete_folder_stats_by_account(account.id)
        await update_history_sync_job(
            job_id,
            fetched_messages=total_messages,
        )

        removed_files = 0
        slug = account.email or account.id
        account_root = DOWNLOADS_DIR / slug
        if account_root.exists():
            file_paths = [path for path in account_root.rglob("*") if path.is_file()]
            for path in file_paths:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
                removed_files += 1
                await update_history_sync_job(
                    job_id,
                    completed_folders=removed_files,
                    downloaded_attachments=removed_files,
                )
            shutil.rmtree(account_root, ignore_errors=True)

        await update_history_sync_job(
            job_id,
            status="completed",
            completed_folders=max(removed_files, total_files),
            downloaded_attachments=removed_files,
            finished_at=time.time(),
        )
        history_job = await get_history_sync_job(account.id, job_type="history_sync")
        if history_job:
            await update_history_sync_job(
                history_job["id"],
                status="completed",
                current_folder="",
                current_page=1,
                current_uid=0,
                total_folders=0,
                completed_folders=0,
                fetched_messages=0,
                downloaded_attachments=0,
                downloaded_inline_images=0,
                error_message="",
                finished_at=time.time(),
            )
    except Exception as exc:
        logger.warning("clear cache failed for %s: %s", account.email, exc)
        await update_history_sync_job(
            job_id,
            status="failed",
            error_message=str(exc),
            finished_at=time.time(),
        )


async def run_delete_account(account_id: str) -> None:
    account = await get_account_by_id(account_id)
    if not account:
        logger.warning("delete account skipped: account not found %s", account_id)
        return

    ensure_data_dirs()
    job_id = uuid.uuid4().hex
    file_paths = await _account_local_cache_files(account.id, account.email)
    total_files = len(file_paths)
    await create_history_sync_job(
        {
            "id": job_id,
            "account_id": account.id,
            "user_uid": account.user_uid,
            "job_type": "account_delete",
            "status": "pending",
            "current_folder": account.email,
            "current_page": 1,
            "current_uid": 0,
            "total_folders": max(total_files, 1),
            "completed_folders": 0,
            "fetched_messages": 0,
            "downloaded_attachments": 0,
            "downloaded_inline_images": 0,
            "error_message": "",
            "created_at": time.time(),
            "updated_at": time.time(),
            "finished_at": 0,
        }
    )

    try:
        await update_history_sync_job(job_id, status="running")
        await sync_service.remove_account(account.id)

        total_messages = await delete_cached_messages_by_account(account.id)
        await delete_cached_attachments_by_account(account.id)
        await delete_folder_stats_by_account(account.id)
        await update_history_sync_job(job_id, fetched_messages=total_messages)

        removed_files = 0
        for path in file_paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            removed_files += 1
            await update_history_sync_job(
                job_id,
                completed_folders=removed_files,
                downloaded_attachments=removed_files,
            )
            await asyncio.sleep(0)
        clear_account_storage(account.id, account.email)

        await delete_history_sync_jobs_by_account(account.id, keep_job_id=job_id)
        await delete_account(account.id, account.user_uid)
        await update_history_sync_job(
            job_id,
            status="completed",
            completed_folders=max(removed_files, total_files),
            downloaded_attachments=removed_files,
            finished_at=time.time(),
        )
    except Exception as exc:
        logger.warning("delete account failed for %s: %s", account.email, exc)
        await update_history_sync_job(
            job_id,
            status="failed",
            error_message=str(exc),
            finished_at=time.time(),
        )


async def run_folder_clear_cache(account_id: str, folder_name: str, job_type: str) -> None:
    account = await get_account_by_id(account_id)
    if not account:
        logger.warning("folder clear skipped: account not found %s", account_id)
        return
    job_id = uuid.uuid4().hex
    uid_list = sorted(await get_cached_uids(account.id, folder_name))
    await create_history_sync_job(
        {
            "id": job_id,
            "account_id": account.id,
            "user_uid": account.user_uid,
            "job_type": job_type,
            "status": "pending",
            "current_folder": folder_name,
            "current_page": 1,
            "current_uid": 0,
            "total_folders": max(len(uid_list), 1),
            "completed_folders": 0,
            "fetched_messages": 0,
            "downloaded_attachments": 0,
            "downloaded_inline_images": 0,
            "error_message": "",
            "created_at": time.time(),
            "updated_at": time.time(),
            "finished_at": 0,
        }
    )
    try:
        await update_history_sync_job(job_id, status="running")
        deleted = 0
        for index in range(0, len(uid_list), 100):
            batch = uid_list[index:index + 100]
            deleted += await batch_delete_cached_messages(account.id, batch, folder_name)
            await update_history_sync_job(
                job_id,
                completed_folders=min(index + len(batch), len(uid_list)),
                fetched_messages=deleted,
            )
            await asyncio.sleep(0)
        await update_history_sync_job(
            job_id,
            status="completed",
            completed_folders=max(len(uid_list), 1),
            fetched_messages=deleted,
            finished_at=time.time(),
        )
    except Exception as exc:
        logger.warning("folder clear failed for %s %s: %s", account.email, folder_name, exc)
        await update_history_sync_job(
            job_id,
            status="failed",
            error_message=str(exc),
            finished_at=time.time(),
        )


async def _cache_message_assets(receiver, account, folder_name: str, detail) -> tuple[str, str, int, int, list[CachedAttachment]]:
    body_html = detail.body_html or ""
    storage_path = ""
    downloaded_attachments = 0
    downloaded_inline_images = 0
    attachment_records: list[CachedAttachment] = []
    cached_detail = await get_cached_message_detail(account.id, detail.uid, folder_name)
    effective_message_date = coalesce_message_date(detail.date, (cached_detail or {}).get("date", ""))

    for attachment in detail.attachments or []:
        cached_attachment = await get_cached_attachment(
            account.id,
            detail.uid,
            folder_name,
            attachment.part_number,
        )
        current_local_path = (cached_attachment or {}).get("local_path", "")
        try:
            storage_path, target_path, _ = ensure_message_file_location(
                message_date=detail.date,
                account_id=account.id,
                account_email=account.email,
                uid=detail.uid,
                part_number=attachment.part_number,
                filename=attachment.filename or "",
                content_type=attachment.content_type or "",
                current_path=current_local_path,
                fallback_message_date=effective_message_date,
            )
        except Exception as exc:
            logger.debug(
                "history sync attachment path resolve failed: account=%s uid=%s part=%s error=%s",
                account.email,
                detail.uid,
                attachment.part_number,
                exc,
            )
            storage_path, target_path = build_message_file_path(
                message_date=detail.date,
                account_id=account.id,
                account_email=account.email,
                uid=detail.uid,
                part_number=attachment.part_number,
                filename=attachment.filename or "",
                content_type=attachment.content_type or "",
                fallback_message_date=effective_message_date,
            )
            target_path.parent.mkdir(parents=True, exist_ok=True)

        if target_path.exists():
            attachment_records.append(
                CachedAttachment(
                    account_id=account.id,
                    user_uid=account.user_uid,
                    uid=detail.uid,
                    folder=folder_name,
                    part_number=attachment.part_number,
                    filename=attachment.filename or "",
                    content_type=attachment.content_type or "",
                    size=attachment.size or target_path.stat().st_size,
                    content_id=attachment.content_id or "",
                    is_inline=bool(attachment.is_inline),
                    local_path=str(target_path),
                    cached_at=time.time(),
                )
            )
            if attachment.is_inline:
                downloaded_inline_images += 1
                cid = _strip_cid(attachment.content_id)
                if cid:
                    local_url = _build_inline_attachment_url(account.id, folder_name, detail.uid, attachment.part_number)
                    body_html = _replace_inline_cids(body_html, cid, local_url)
            else:
                downloaded_attachments += 1
            continue

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

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(att_data)
        attachment_records.append(
            CachedAttachment(
                account_id=account.id,
                user_uid=account.user_uid,
                uid=detail.uid,
                folder=folder_name,
                part_number=attachment.part_number,
                filename=attachment.filename or "",
                content_type=attachment.content_type or "",
                size=attachment.size or len(att_data),
                content_id=attachment.content_id or "",
                is_inline=bool(attachment.is_inline),
                local_path=str(target_path),
                cached_at=time.time(),
            )
        )

        if attachment.is_inline:
            cid = _strip_cid(attachment.content_id)
            if cid:
                local_url = _build_inline_attachment_url(account.id, folder_name, detail.uid, attachment.part_number)
                body_html = _replace_inline_cids(body_html, cid, local_url)
            downloaded_inline_images += 1
        else:
            downloaded_attachments += 1

    return body_html, storage_path, downloaded_attachments, downloaded_inline_images, attachment_records


async def run_history_sync(
    account_id: str,
    job_id: str | None = None,
    reset: bool = False,
    folders: list[str] | None = None,
    job_type: str = "history_sync",
) -> None:
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
            "job_type": job_type,
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
        await sync_service.suspend_account(account.id)
        credentials = await ensure_token(account)
        receiver = ProviderFactory.get_receiver(account.provider)
        await receiver.connect(credentials)
        remote_folders = await receiver.fetch_folders()
        folders = _resolve_history_folders(remote_folders, folders)

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

        async def _local_synced_count() -> int:
            total = 0
            for folder in folders:
                folder_name = getattr(folder, "path", "") or getattr(folder, "name", "") or "INBOX"
                try:
                    total += await get_cached_count(account.id, folder_name)
                except Exception as exc:
                    logger.debug("history sync cached count failed: account=%s folder=%s error=%s", account.email, folder_name, exc)
            return total

        fetched_messages = max(fetched_messages, await _local_synced_count())

        for index in range(start_index, len(folders)):
            if await _is_paused(job_id):
                return

            folder = folders[index]
            folder_name = getattr(folder, "path", "") or getattr(folder, "name", "") or "INBOX"
            page = int(job.get("current_page", 1) or 1) if folder_name == current_folder_name else 1
            unseen_uids: set[int] | None = None

            try:
                unseen_uids = set(await receiver.fetch_unseen_uids(folder_name))
            except Exception as exc:
                logger.debug(
                    "history sync unseen uid fetch failed: account=%s folder=%s error=%s",
                    account.email,
                    folder_name,
                    exc,
                )

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
                if page == 1:
                    unread_count = len(unseen_uids) if unseen_uids is not None else 0
                    await upsert_folder_stats(account.id, folder_name, result.total or 0, unread_count)
                if not result.messages:
                    break

                cached_batch = []
                read_state_updates: list[tuple[int, int]] = []
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

                    cached_detail = await get_cached_message_detail(account.id, message.uid, folder_name)
                    cached_has_body = bool(cached_detail and (cached_detail.get("body_text") or cached_detail.get("body_html")))
                    cached_assets_complete = bool(
                        cached_detail
                        and (
                            not cached_detail.get("has_attachments")
                            or cached_detail.get("storage_path")
                        )
                    )
                    cached_has_bad_date = bool(cached_detail and (cached_detail.get("date") or "") == UNKNOWN_MESSAGE_DATE)
                    message_has_good_date = bool((message.date or "") and message.date != UNKNOWN_MESSAGE_DATE)
                    if cached_has_body and cached_assets_complete and not (cached_has_bad_date and not message_has_good_date):
                        cached_batch.append(
                            CachedMessage(
                                id=f"{account.id}_{message.uid}",
                                account_id=account.id,
                                user_uid=account.user_uid,
                                uid=message.uid,
                                folder=folder_name,
                                subject=message.subject,
                                from_addr=message.from_addr,
                                to_addr=message.to_addr,
                                date=normalize_message_date(message.date, fallback=(cached_detail or {}).get("date", "")),
                                is_read=message.is_read,
                                is_starred=message.is_starred,
                                has_attachments=bool(cached_detail.get("has_attachments", False)),
                                body_text=cached_detail.get("body_text", ""),
                                body_html=cached_detail.get("body_html", ""),
                                storage_path=cached_detail.get("storage_path", ""),
                                cached_at=time.time(),
                            )
                        )
                        if unseen_uids is not None:
                            read_state_updates.append((message.uid, 1 if message.uid not in unseen_uids else 0))
                        fetched_messages = max(fetched_messages, await _local_synced_count())
                        await update_history_sync_job(
                            job_id,
                            current_folder=folder_name,
                            current_page=page,
                            current_uid=message.uid,
                            fetched_messages=fetched_messages,
                            downloaded_attachments=downloaded_attachments,
                            downloaded_inline_images=downloaded_inline_images,
                        )
                        continue

                    detail = await receiver.fetch_message_detail(str(message.uid), folder=folder_name)
                    if unseen_uids is not None:
                        detail.is_read = detail.uid not in unseen_uids
                        read_state_updates.append((detail.uid, 1 if detail.is_read else 0))
                    effective_message_date = coalesce_message_date(detail.date, message.date, (cached_detail or {}).get("date", ""))
                    existing_attachments = await list_cached_attachments(account.id, detail.uid, folder_name)
                    if existing_attachments:
                        for existing in existing_attachments:
                            try:
                                _, target_path, moved = ensure_message_file_location(
                                    message_date=detail.date,
                                    account_id=account.id,
                                    account_email=account.email,
                                    uid=detail.uid,
                                    part_number=existing.get("part_number", 0),
                                    filename=existing.get("filename", ""),
                                    content_type=existing.get("content_type", ""),
                                    current_path=existing.get("local_path", ""),
                                    fallback_message_date=effective_message_date,
                                )
                                if moved:
                                    await upsert_cached_attachments([
                                        CachedAttachment(
                                            account_id=account.id,
                                            user_uid=account.user_uid,
                                            uid=detail.uid,
                                            folder=folder_name,
                                            part_number=existing.get("part_number", 0),
                                            filename=existing.get("filename", ""),
                                            content_type=existing.get("content_type", ""),
                                            size=existing.get("size", 0),
                                            content_id=existing.get("content_id", ""),
                                            is_inline=bool(existing.get("is_inline", False)),
                                            local_path=str(target_path),
                                            cached_at=time.time(),
                                        )
                                    ])
                            except Exception as exc:
                                logger.debug(
                                    "history sync attachment migrate failed: account=%s uid=%s error=%s",
                                    account.email,
                                    detail.uid,
                                    exc,
                                )
                    body_html, storage_path, att_count, inline_count, attachment_records = await _cache_message_assets(receiver, account, folder_name, detail)
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
                            date=normalize_message_date(detail.date, fallback=effective_message_date),
                            is_read=detail.is_read,
                            is_starred=detail.is_starred,
                            has_attachments=bool(detail.attachments),
                            body_text=detail.body_text,
                            body_html=detail.body_html,
                            storage_path=storage_path,
                            cached_at=time.time(),
                        )
                    )

                    if attachment_records:
                        await upsert_cached_attachments(attachment_records)

                    fetched_messages = max(fetched_messages, await _local_synced_count())
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
                    fetched_messages = max(fetched_messages, await _local_synced_count())
                    await update_history_sync_job(
                        job_id,
                        fetched_messages=fetched_messages,
                        downloaded_attachments=downloaded_attachments,
                        downloaded_inline_images=downloaded_inline_images,
                    )
                if read_state_updates:
                    await batch_update_is_read(account.id, folder_name, read_state_updates)

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
        try:
            await sync_service.resume_account(account.id)
        except Exception as exc:
            logger.warning("resume realtime sync failed for %s: %s", account.email, exc)
