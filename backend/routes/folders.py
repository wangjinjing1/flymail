"""Folder list and folder-count routes."""

from fastapi import APIRouter, Query, Request

from db import get_accounts, list_account_folder_counts
from deps import get_uid
from routes._helpers import _find_account_or_error
from schemas import FolderCountsResponse, FolderResponse
from utils.logger import get_logger

logger = get_logger("routes.folders")

router = APIRouter(tags=["folders"])


SENT_FOLDER_KEYS = {"sent", "sent messages", "sent items", "[gmail]/sent mail"}


def _counts_response_from_rows(rows: list[dict]) -> dict[str, dict]:
    counts: dict[str, dict] = {}
    for item in rows:
        folder_path = item.get("folder_path") or ""
        folder_lower = folder_path.lower()
        counts[folder_path] = {
            "total": int(item.get("total_count", 0) or 0),
            "unread": 0 if folder_lower in SENT_FOLDER_KEYS else int(item.get("unread_count", 0) or 0),
        }
    return counts


def _folder_response_from_count_rows(rows: list[dict]) -> list[dict]:
    folders = []
    for item in rows:
        folders.append(
            {
                "name": item.get("display_name") or item.get("folder_path") or "",
                "path": item.get("folder_path") or "",
                "unread_count": int(item.get("unread_count", 0) or 0),
                "total_count": int(item.get("total_count", 0) or 0),
            }
        )
    return folders


@router.get("/api/folders", response_model=FolderResponse, summary="Get folder list")
async def list_folders(
    request: Request,
    account_id: str = Query(default="", description="Account ID. Empty uses the first account."),
):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)

    if not accounts:
        return {"folders": []}

    account, _ = _find_account_or_error(accounts, account_id)
    rows = await list_account_folder_counts(account.id)
    return {"folders": _folder_response_from_count_rows(rows), "account_id": account.id}


@router.get("/api/folder-counts", response_model=FolderCountsResponse, summary="Get folder counts")
async def get_folder_counts(
    request: Request,
    account_id: str = Query(default="", description="Account ID. Empty uses the first account."),
):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)

    if not accounts:
        return {"counts": {}}

    account, _ = _find_account_or_error(accounts, account_id)
    rows = await list_account_folder_counts(account.id)
    return {"counts": _counts_response_from_rows(rows), "account_id": account.id}
