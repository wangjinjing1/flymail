import importlib.util
import sys
import types
import unittest
from unittest.mock import AsyncMock
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from providers.base import Folder, MessageList


def _load_mail_cache_module():
    db_stub = types.ModuleType("db")
    for name in (
        "batch_update_is_read",
        "get_accounts",
        "get_cached_count",
        "get_cached_message_detail",
        "get_cached_messages_by_folder",
        "get_cached_uids",
        "get_folder_stats",
        "get_max_cached_uid",
        "purge_deleted_from_cache",
        "upsert_cached_attachments",
        "upsert_cached_messages",
        "upsert_folder_stats",
    ):
        setattr(db_stub, name, object())

    data_paths_stub = types.ModuleType("data_paths")
    data_paths_stub.coalesce_message_date = object()
    data_paths_stub.normalize_message_date = object()

    models_stub = types.ModuleType("models")
    models_stub.Account = object
    models_stub.CachedMessage = object

    factory_stub = types.ModuleType("providers.factory")
    factory_stub.ProviderFactory = object()

    history_stub = types.ModuleType("services.history_sync")
    history_stub._cache_message_assets = object()

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.get_logger = lambda name: types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )

    previous = {
        name: sys.modules.get(name)
        for name in (
            "db",
            "data_paths",
            "models",
            "providers.factory",
            "services.history_sync",
            "utils.logger",
        )
    }
    sys.modules.update(
        {
            "db": db_stub,
            "data_paths": data_paths_stub,
            "models": models_stub,
            "providers.factory": factory_stub,
            "services.history_sync": history_stub,
            "utils.logger": logger_stub,
        }
    )
    try:
        module_path = Path(__file__).resolve().parents[1] / "services" / "mail_cache.py"
        spec = importlib.util.spec_from_file_location("mail_cache_for_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class MailCacheFolderResolutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_sync_folder_resolves_core_alias_before_fetching_remote(self):
        mail_cache = _load_mail_cache_module()
        cases = [
            ("Sent", "[Gmail]/&XfJT0ZCuTvY-"),
            ("Drafts", "[Gmail]/Drafts"),
            ("Junk", "[Gmail]/Spam"),
            ("Trash", "[Gmail]/Trash"),
        ]

        for requested, expected in cases:
            with self.subTest(requested=requested):
                calls = []

                class Receiver:
                    async def fetch_folders(self):
                        return [
                            Folder(name="INBOX", path="INBOX"),
                            Folder(name="Sent", path="[Gmail]/&XfJT0ZCuTvY-"),
                            Folder(name="Drafts", path="[Gmail]/Drafts"),
                            Folder(name="Junk", path="[Gmail]/Spam"),
                            Folder(name="Trash", path="[Gmail]/Trash"),
                        ]

                    async def fetch_messages(self, folder, page=1, page_size=20):
                        calls.append(("fetch_messages", folder))
                        return MessageList(messages=[], total=0, unread_total=0, page=page, page_size=page_size)

                account = types.SimpleNamespace(id="account-1", user_uid="user-1", email="user@example.com")
                mail_cache.get_max_cached_uid = AsyncMock(return_value=0)
                mail_cache.get_folder_stats = AsyncMock(return_value={"updated_at": 0})
                mail_cache.upsert_folder_stats = AsyncMock()

                await mail_cache._do_sync(Receiver(), account, requested)

                self.assertEqual(calls, [("fetch_messages", expected)])
                mail_cache.upsert_folder_stats.assert_awaited_with("account-1", expected, 0, 0)


if __name__ == "__main__":
    unittest.main()
