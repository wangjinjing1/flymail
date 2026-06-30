import os
from pathlib import Path


def _resolve_base_dir() -> Path:
    env_dir = os.environ.get("FLYMAIL_DATA_DIR", "")
    if env_dir:
        return Path(env_dir).resolve()
    return (Path(__file__).resolve().parent / "data").resolve()


BASE_DATA_DIR = _resolve_base_dir()
CONFIG_DIR = BASE_DATA_DIR / "config"
LOGS_DIR = BASE_DATA_DIR / "logs"
UPLOADS_DIR = BASE_DATA_DIR / "uploads"
ATTACHMENTS_DIR = BASE_DATA_DIR / "attachments"
HISTORY_DIR = BASE_DATA_DIR / "history"
HISTORY_RAW_DIR = HISTORY_DIR / "raw"
HISTORY_ATTACHMENTS_DIR = HISTORY_DIR / "attachments"
HISTORY_INLINE_DIR = HISTORY_DIR / "inline"


def ensure_data_dirs() -> None:
    for path in (
        BASE_DATA_DIR,
        CONFIG_DIR,
        LOGS_DIR,
        UPLOADS_DIR,
        ATTACHMENTS_DIR,
        HISTORY_DIR,
        HISTORY_RAW_DIR,
        HISTORY_ATTACHMENTS_DIR,
        HISTORY_INLINE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
