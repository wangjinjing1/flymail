import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


def _resolve_base_dir() -> Path:
    env_dir = os.environ.get("FLYMAIL_DATA_DIR", "")
    if env_dir:
        return Path(env_dir).resolve()
    return (Path(__file__).resolve().parent / "data").resolve()


BASE_DATA_DIR = _resolve_base_dir()
CONFIG_DIR = BASE_DATA_DIR / "config"
LOGS_DIR = BASE_DATA_DIR / "logs"
FILES_DIR = BASE_DATA_DIR / "files"
DOWNLOADS_DIR = FILES_DIR / "download"
UPLOADS_DIR = FILES_DIR / "uploads"


def _slugify(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]+', "_", value or "")
    value = value.strip().strip(".")
    return value or "unknown"


def get_account_storage_slug(account_id: str, account_email: str = "") -> str:
    return _slugify(account_email or account_id)


UNKNOWN_MESSAGE_DATE = "1970-01-01T00:00:00Z"


def parse_message_datetime(message_date: str) -> datetime:
    dt = None
    try:
        dt = parsedate_to_datetime(message_date) if message_date else None
    except Exception:
        dt = None

    if dt is None and message_date:
        try:
            normalized = str(message_date).replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
        except Exception:
            dt = None

    if dt is None:
        raise ValueError(f"unable to parse message date: {message_date!r}")

    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def coalesce_message_date(*values: str) -> str:
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        try:
            return normalize_message_date(text)
        except ValueError:
            continue
    return UNKNOWN_MESSAGE_DATE


def normalize_message_date(message_date: str, fallback: str = UNKNOWN_MESSAGE_DATE) -> str:
    try:
        dt = parse_message_datetime(message_date)
    except ValueError:
        if fallback == message_date:
            raise
        dt = parse_message_datetime(fallback)
    return dt.isoformat(timespec="seconds") + "Z"


def derive_message_datetime(message_date: str, fallback: str = UNKNOWN_MESSAGE_DATE) -> datetime:
    normalized = normalize_message_date(message_date, fallback=fallback)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def get_message_storage_key(message_date: str, account_id: str, account_email: str, uid: int, fallback: str = UNKNOWN_MESSAGE_DATE) -> str:
    dt = derive_message_datetime(message_date, fallback=fallback)
    return str(Path(get_account_storage_slug(account_id, account_email)) / f"{dt.year:04d}" / f"{dt.month:02d}" / str(uid))


def build_message_file_path(
    *,
    message_date: str,
    account_id: str,
    account_email: str,
    uid: int,
    part_number: int,
    filename: str,
    content_type: str,
    fallback_message_date: str = UNKNOWN_MESSAGE_DATE,
) -> tuple[str, Path]:
    storage_key = get_message_storage_key(message_date, account_id, account_email, uid, fallback=fallback_message_date)
    safe_filename = _slugify(filename or f"part_{part_number}")
    return storage_key, DOWNLOADS_DIR / storage_key / f"{part_number}_{safe_filename}"


def ensure_message_file_location(
    *,
    message_date: str,
    account_id: str,
    account_email: str,
    uid: int,
    part_number: int,
    filename: str,
    content_type: str,
    current_path: str = "",
    fallback_message_date: str = UNKNOWN_MESSAGE_DATE,
) -> tuple[str, Path, bool]:
    storage_key, target_path = build_message_file_path(
        message_date=message_date,
        account_id=account_id,
        account_email=account_email,
        uid=uid,
        part_number=part_number,
        filename=filename,
        content_type=content_type,
        fallback_message_date=fallback_message_date,
    )
    target_path.parent.mkdir(parents=True, exist_ok=True)

    return storage_key, target_path, False


def clear_account_storage(account_id: str, account_email: str = "") -> None:
    slug = get_account_storage_slug(account_id, account_email)
    cleanup_paths = [
        DOWNLOADS_DIR / slug,
    ]
    for target in cleanup_paths:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


def ensure_data_dirs() -> None:
    for path in (
        BASE_DATA_DIR,
        FILES_DIR,
        CONFIG_DIR,
        LOGS_DIR,
        UPLOADS_DIR,
        DOWNLOADS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
