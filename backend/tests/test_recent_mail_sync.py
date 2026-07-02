import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from models import Account
from providers.base import Message, MessageList


def _message(uid: int) -> Message:
    return Message(
        id=str(uid),
        uid=uid,
        subject=f"message {uid}",
        from_addr="from@example.com",
        to_addr="to@example.com",
        date="2026-07-02T10:00:00Z",
        folder="INBOX",
    )


def _load_mail_cache_module():
    db_stub = types.ModuleType("db")
    for name in (
        "get_cached_message_detail",
        "upsert_cached_attachments",
        "upsert_cached_messages",
        "get_max_cached_uid",
        "get_accounts",
        "upsert_folder_stats",
        "get_folder_stats",
        "purge_deleted_from_cache",
        "get_cached_count",
        "get_cached_uids",
        "get_cached_messages_by_folder",
        "batch_update_is_read",
    ):
        setattr(db_stub, name, AsyncMock())

    factory_stub = types.ModuleType("providers.factory")

    class _ProviderFactory:
        get_receiver = staticmethod(lambda provider: None)

    factory_stub.ProviderFactory = _ProviderFactory

    history_sync_stub = types.ModuleType("services.history_sync")
    history_sync_stub._cache_message_assets = AsyncMock(return_value=("", "", 0, 0, []))

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.get_logger = lambda name: types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )

    token_stub = types.ModuleType("services.token")
    token_stub.ensure_token = AsyncMock(return_value=object())

    previous = {
        name: sys.modules.get(name)
        for name in (
            "db",
            "providers.factory",
            "services.history_sync",
            "services.token",
            "utils.logger",
        )
    }
    sys.modules.update(
        {
            "db": db_stub,
            "providers.factory": factory_stub,
            "services.history_sync": history_sync_stub,
            "services.token": token_stub,
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


class RecentMailSyncTest(unittest.TestCase):
    def test_select_uncached_stops_when_cached_message_is_seen(self):
        mail_cache = _load_mail_cache_module()

        selected, stopped = mail_cache._select_uncached_recent_messages(
            [_message(105), _message(104), _message(103)],
            {103, 102},
        )

        self.assertTrue(stopped)
        self.assertEqual([item.uid for item in selected], [105, 104])


class RecentMailSyncAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_recent_sync_does_not_fetch_next_page_after_cached_message(self):
        mail_cache = _load_mail_cache_module()
        account = Account(
            id="account-1",
            user_uid="user-1",
            email="user@example.com",
            provider="gmail",
        )
        receiver = AsyncMock()
        receiver.fetch_messages.side_effect = [
            MessageList(
                messages=[_message(105), _message(104), _message(103)],
                total=6,
                unread_total=0,
                page=1,
                page_size=3,
            )
        ]
        receiver.fetch_unseen_uids.return_value = []
        receiver.fetch_message_detail.side_effect = lambda uid, folder="INBOX": _message(int(uid))
        receiver.disconnect = AsyncMock()
        token_stub = types.ModuleType("services.token")
        token_stub.ensure_token = AsyncMock(return_value=object())

        with (
            patch.dict(sys.modules, {"services.token": token_stub}),
            patch.object(mail_cache, "ProviderFactory") as factory,
            patch.object(mail_cache, "get_cached_uids", AsyncMock(return_value={103, 102})),
            patch.object(mail_cache, "upsert_folder_stats", AsyncMock()),
            patch.object(mail_cache, "upsert_cached_messages", AsyncMock(return_value=2)),
            patch.object(mail_cache, "get_cached_message_detail", AsyncMock(return_value=None)),
            patch.object(mail_cache, "_cache_message_assets", AsyncMock(return_value=("", "", 0, 0, []))),
            patch.object(mail_cache, "upsert_cached_attachments", AsyncMock()),
        ):
            factory.get_receiver.return_value = receiver
            added = await mail_cache.sync_recent_folder_to_cache(account, "INBOX", page_size=3)

        self.assertEqual(added, 2)
        self.assertEqual(receiver.fetch_messages.await_count, 1)

    async def test_recent_sync_fetches_next_page_only_when_current_page_is_all_new(self):
        mail_cache = _load_mail_cache_module()
        account = Account(
            id="account-1",
            user_uid="user-1",
            email="user@example.com",
            provider="gmail",
        )
        receiver = AsyncMock()
        receiver.fetch_messages.side_effect = [
            MessageList(
                messages=[_message(106), _message(105), _message(104)],
                total=5,
                unread_total=0,
                page=1,
                page_size=3,
            ),
            MessageList(
                messages=[_message(103), _message(102)],
                total=5,
                unread_total=0,
                page=2,
                page_size=3,
            ),
        ]
        receiver.fetch_unseen_uids.return_value = []
        receiver.fetch_message_detail.side_effect = lambda uid, folder="INBOX": _message(int(uid))
        receiver.disconnect = AsyncMock()
        token_stub = types.ModuleType("services.token")
        token_stub.ensure_token = AsyncMock(return_value=object())

        with (
            patch.dict(sys.modules, {"services.token": token_stub}),
            patch.object(mail_cache, "ProviderFactory") as factory,
            patch.object(mail_cache, "get_cached_uids", AsyncMock(return_value={102, 101})),
            patch.object(mail_cache, "upsert_folder_stats", AsyncMock()),
            patch.object(mail_cache, "upsert_cached_messages", AsyncMock()),
            patch.object(mail_cache, "get_cached_message_detail", AsyncMock(return_value=None)),
            patch.object(mail_cache, "_cache_message_assets", AsyncMock(return_value=("", "", 0, 0, []))),
            patch.object(mail_cache, "upsert_cached_attachments", AsyncMock()),
        ):
            factory.get_receiver.return_value = receiver
            added = await mail_cache.sync_recent_folder_to_cache(account, "INBOX", page_size=3)

        self.assertEqual(added, 4)
        self.assertEqual(receiver.fetch_messages.await_count, 2)

    async def test_recent_sync_stops_at_remote_total_boundary(self):
        mail_cache = _load_mail_cache_module()
        account = Account(
            id="account-1",
            user_uid="user-1",
            email="user@example.com",
            provider="gmail",
        )
        receiver = AsyncMock()
        receiver.fetch_messages.return_value = MessageList(
            messages=[_message(106), _message(105), _message(104)],
            total=3,
            unread_total=0,
            page=1,
            page_size=3,
        )
        receiver.fetch_unseen_uids.return_value = []
        receiver.fetch_message_detail.side_effect = lambda uid, folder="INBOX": _message(int(uid))
        receiver.disconnect = AsyncMock()
        token_stub = types.ModuleType("services.token")
        token_stub.ensure_token = AsyncMock(return_value=object())

        with (
            patch.dict(sys.modules, {"services.token": token_stub}),
            patch.object(mail_cache, "ProviderFactory") as factory,
            patch.object(mail_cache, "get_cached_uids", AsyncMock(return_value=set())),
            patch.object(mail_cache, "upsert_folder_stats", AsyncMock()),
            patch.object(mail_cache, "upsert_cached_messages", AsyncMock()),
            patch.object(mail_cache, "get_cached_message_detail", AsyncMock(return_value=None)),
            patch.object(mail_cache, "_cache_message_assets", AsyncMock(return_value=("", "", 0, 0, []))),
            patch.object(mail_cache, "upsert_cached_attachments", AsyncMock()),
        ):
            factory.get_receiver.return_value = receiver
            added = await mail_cache.sync_recent_folder_to_cache(account, "INBOX", page_size=3)

        self.assertEqual(added, 3)
        self.assertEqual(receiver.fetch_messages.await_count, 1)

    async def test_recent_sync_skips_when_account_lock_is_busy(self):
        mail_cache = _load_mail_cache_module()
        account = Account(
            id="account-1",
            user_uid="user-1",
            email="user@example.com",
            provider="gmail",
        )
        lock = mail_cache._get_lock(account.id)
        await lock.acquire()
        token_stub = types.ModuleType("services.token")
        token_stub.ensure_token = AsyncMock(return_value=object())

        try:
            with (
                patch.dict(sys.modules, {"services.token": token_stub}),
                patch.object(mail_cache, "ProviderFactory") as factory,
            ):
                added = await mail_cache.sync_recent_folder_to_cache(account, "INBOX", page_size=3)
        finally:
            lock.release()

        self.assertEqual(added, 0)
        token_stub.ensure_token.assert_not_awaited()
        factory.get_receiver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
