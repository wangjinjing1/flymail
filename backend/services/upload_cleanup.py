import asyncio
import shutil
from datetime import datetime, timedelta

from data_paths import UPLOADS_DIR, ensure_data_dirs
from services.settings import async_load_settings
from utils.logger import get_logger

logger = get_logger("upload_cleanup")

_cleanup_task: asyncio.Task | None = None


def _parse_weekday(value) -> int:
    try:
        weekday = int(value)
    except (TypeError, ValueError):
        return 0
    return min(max(weekday, 0), 6)


def _parse_time(value) -> tuple[int, int]:
    text = str(value or "02:00").strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = min(max(int(hour_text), 0), 23)
        minute = min(max(int(minute_text), 0), 59)
        return hour, minute
    except (TypeError, ValueError):
        return 2, 0


def next_cleanup_time(now: datetime, weekday: int, hour: int, minute: int) -> datetime:
    days = (weekday - now.weekday()) % 7
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def clean_uploads_dir() -> int:
    ensure_data_dirs()
    removed = 0
    if not UPLOADS_DIR.exists():
        return removed

    for path in UPLOADS_DIR.iterdir():
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
            elif path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
        except Exception as exc:
            logger.debug("uploads cleanup skipped path=%s error=%s", path, exc)
    return removed


async def _cleanup_loop():
    while True:
        settings = await async_load_settings()
        weekday = _parse_weekday(settings.get("uploads_cleanup_weekday", 0))
        hour, minute = _parse_time(settings.get("uploads_cleanup_time", "02:00"))
        run_at = next_cleanup_time(datetime.now(), weekday, hour, minute)
        delay = max(1, (run_at - datetime.now()).total_seconds())
        logger.info("uploads cleanup scheduled at %s", run_at.isoformat(timespec="seconds"))
        await asyncio.sleep(delay)
        removed = await asyncio.to_thread(clean_uploads_dir)
        logger.info("uploads cleanup completed, removed=%d", removed)


def start_upload_cleanup():
    global _cleanup_task
    if _cleanup_task and not _cleanup_task.done():
        return
    _cleanup_task = asyncio.create_task(_cleanup_loop(), name="uploads_cleanup")


async def stop_upload_cleanup():
    global _cleanup_task
    if not _cleanup_task:
        return
    _cleanup_task.cancel()
    try:
        await _cleanup_task
    except asyncio.CancelledError:
        pass
    _cleanup_task = None


async def restart_upload_cleanup():
    await stop_upload_cleanup()
    start_upload_cleanup()
