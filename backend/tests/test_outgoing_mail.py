import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from models import Account


def _load_outgoing_mail_module():
    db_stub = types.ModuleType("db")
    db_stub.get_folder_stats = AsyncMock(return_value={"total_count": 0, "unread_count": 0, "updated_at": 0})
    db_stub.search_cached_messages_by_folder = AsyncMock(return_value={"messages": []})
    db_stub.upsert_cached_messages = AsyncMock(return_value=1)
    db_stub.upsert_folder_stats = AsyncMock()

    factory_stub = types.ModuleType("providers.factory")

    class _ProviderFactory:
        get_receiver = staticmethod(lambda provider: None)

    factory_stub.ProviderFactory = _ProviderFactory

    mail_cache_stub = types.ModuleType("services.mail_cache")
    mail_cache_stub.sync_folder_to_cache = AsyncMock(return_value=0)

    sync_stub = types.ModuleType("services.sync")
    sync_stub.sync_service = types.SimpleNamespace(refresh_clients=AsyncMock())

    token_stub = types.ModuleType("services.token")
    token_stub.ensure_token = AsyncMock(return_value=object())

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
            "providers.factory",
            "services.mail_cache",
            "services.sync",
            "services.token",
            "utils.logger",
        )
    }
    sys.modules.update(
        {
            "db": db_stub,
            "providers.factory": factory_stub,
            "services.mail_cache": mail_cache_stub,
            "services.sync": sync_stub,
            "services.token": token_stub,
            "utils.logger": logger_stub,
        }
    )
    try:
        module_path = Path(__file__).resolve().parents[1] / "services" / "outgoing_mail.py"
        spec = importlib.util.spec_from_file_location("outgoing_mail_for_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, db_stub, mail_cache_stub, sync_stub
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class OutgoingMailTest(unittest.IsolatedAsyncioTestCase):
    async def test_append_failure_caches_sent_message_locally(self):
        outgoing_mail, db_stub, mail_cache_stub, sync_stub = _load_outgoing_mail_module()
        account = Account(
            id="account-1",
            user_uid="user-1",
            email="sender@example.com",
            provider="gmail",
        )

        receiver = AsyncMock()
        receiver.fetch_folders.return_value = [
            types.SimpleNamespace(name="已发送", path="[Gmail]/Sent Mail"),
        ]
        receiver.save_draft.side_effect = RuntimeError("append failed")
        receiver.disconnect = AsyncMock()
        outgoing_mail.ProviderFactory.get_receiver = staticmethod(lambda provider: receiver)

        sent_folder = await outgoing_mail.ensure_sent_message_cached(
            account=account,
            user_uid="user-1",
            to=["to@example.com"],
            cc=[],
            bcc=[],
            subject="hello",
            body_html="<p>Hello</p>",
            attachments=[],
        )

        self.assertEqual(sent_folder, "[Gmail]/Sent Mail")
        db_stub.upsert_cached_messages.assert_awaited_once()
        cached = db_stub.upsert_cached_messages.await_args.args[0][0]
        self.assertEqual(cached.folder, "[Gmail]/Sent Mail")
        self.assertEqual(cached.subject, "hello")
        self.assertEqual(cached.from_addr, "sender@example.com")
        self.assertEqual(cached.to_addr, "to@example.com")
        self.assertTrue(cached.is_read)
        db_stub.upsert_folder_stats.assert_awaited_once_with("account-1", "[Gmail]/Sent Mail", 1, 0)
        self.assertEqual(mail_cache_stub.sync_folder_to_cache.await_count, 2)
        sync_stub.sync_service.refresh_clients.assert_awaited_once_with(
            "account-1",
            "[Gmail]/Sent Mail",
            user_uid="user-1",
        )


if __name__ == "__main__":
    unittest.main()
