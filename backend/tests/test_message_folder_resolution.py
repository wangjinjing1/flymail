import importlib.util
import sys
import types
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from providers.base import Folder


def _load_messages_route_module():
    fastapi_stub = types.ModuleType("fastapi")

    class Router:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, *args, **kwargs):
            return lambda fn: fn

        def post(self, *args, **kwargs):
            return lambda fn: fn

        def delete(self, *args, **kwargs):
            return lambda fn: fn

    fastapi_stub.APIRouter = Router
    fastapi_stub.Body = lambda *args, **kwargs: None
    fastapi_stub.File = lambda *args, **kwargs: None
    fastapi_stub.Query = lambda default=None, *args, **kwargs: default
    fastapi_stub.Request = object
    fastapi_stub.UploadFile = object

    responses_stub = types.ModuleType("fastapi.responses")
    responses_stub.FileResponse = object

    db_stub = types.ModuleType("db")
    for name in (
        "adjust_account_folder_unread",
        "batch_delete_cached_messages",
        "batch_update_is_read",
        "batch_update_cached_messages_read",
        "get_accounts",
        "get_cached_attachment",
        "get_cached_is_read",
        "get_cached_message_detail",
        "get_cached_messages_by_folder",
        "get_folder_filter_counts",
        "get_folder_stats",
        "list_account_folder_counts",
        "list_cached_attachments",
        "search_cached_messages_by_folder",
        "update_cached_message_read",
        "update_cached_message_storage_path",
        "upsert_cached_attachments",
        "upsert_cached_messages",
        "upsert_folder_stats",
        "delete_cached_message",
    ):
        setattr(db_stub, name, object())

    data_paths_stub = types.ModuleType("data_paths")
    for name in (
        "UPLOADS_DIR",
        "coalesce_message_date",
        "ensure_data_dirs",
        "ensure_message_file_location",
    ):
        setattr(data_paths_stub, name, object())
    data_paths_stub.ensure_data_dirs = lambda: None

    deps_stub = types.ModuleType("deps")
    deps_stub.get_uid = object()

    errors_stub = types.ModuleType("errors")
    errors_stub.AppError = Exception

    models_stub = types.ModuleType("models")
    models_stub.Account = object
    models_stub.CachedAttachment = object

    factory_stub = types.ModuleType("providers.factory")
    factory_stub.ProviderFactory = object()

    helpers_stub = types.ModuleType("routes._helpers")
    helpers_stub._OUTLOOK_RECONNECTING_MSG = ""
    helpers_stub._find_account_or_error = object()
    helpers_stub._is_outlook_connection_error = lambda *args, **kwargs: False
    helpers_stub._notify_if_permanent_token_error = object()
    helpers_stub._safe_disconnect = object()
    helpers_stub._with_outlook_retry = object()

    schemas_stub = types.ModuleType("schemas")
    for name in (
        "BatchDeleteRequest",
        "BatchDeleteResponse",
        "BatchMarkReadRequest",
        "BatchMarkReadResponse",
        "DeleteResponse",
        "MessageItem",
        "MessageListResponse",
        "MessageResponse",
        "MarkReadRequest",
        "PrefetchMessagesRequest",
        "PrefetchMessagesResponse",
        "StatusResponse",
        "UploadAttachmentResponse",
    ):
        setattr(schemas_stub, name, object)

    sync_stub = types.ModuleType("services.sync")
    sync_stub.sync_service = object()

    token_stub = types.ModuleType("services.token")
    token_stub.ensure_token = object()

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.get_logger = lambda name: types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )

    tasks_stub = types.ModuleType("utils.tasks")
    tasks_stub.create_background_task = lambda *args, **kwargs: None

    module_names = (
        "db",
        "fastapi",
        "fastapi.responses",
        "data_paths",
        "deps",
        "errors",
        "models",
        "providers.factory",
        "routes._helpers",
        "schemas",
        "services.sync",
        "services.token",
        "utils.logger",
        "utils.tasks",
    )
    previous = {name: sys.modules.get(name) for name in module_names}
    sys.modules.update(
        {
            "db": db_stub,
            "fastapi": fastapi_stub,
            "fastapi.responses": responses_stub,
            "data_paths": data_paths_stub,
            "deps": deps_stub,
            "errors": errors_stub,
            "models": models_stub,
            "providers.factory": factory_stub,
            "routes._helpers": helpers_stub,
            "schemas": schemas_stub,
            "services.sync": sync_stub,
            "services.token": token_stub,
            "utils.logger": logger_stub,
            "utils.tasks": tasks_stub,
        }
    )
    try:
        module_path = Path(__file__).resolve().parents[1] / "routes" / "messages.py"
        spec = importlib.util.spec_from_file_location("messages_route_for_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class MessageFolderResolutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_netease_sent_folder_by_display_name(self):
        messages = _load_messages_route_module()

        class Receiver:
            async def fetch_folders(self):
                return [
                    Folder(name="收件箱", path="INBOX"),
                    Folder(name="已发送", path="&XfJT0ZAB-"),
                ]

        resolved = await messages._resolve_remote_folder(Receiver(), "Sent")

        self.assertEqual(resolved, "&XfJT0ZAB-")

    async def test_resolves_gmail_sent_folder_by_path_alias(self):
        messages = _load_messages_route_module()

        class Receiver:
            async def fetch_folders(self):
                return [
                    Folder(name="收件箱", path="INBOX"),
                    Folder(name="已发送", path="[Gmail]/Sent Mail"),
                ]

        resolved = await messages._resolve_remote_folder(Receiver(), "Sent")

        self.assertEqual(resolved, "[Gmail]/Sent Mail")

    async def test_resolves_gmail_localized_sent_folder_by_modified_utf7_path(self):
        messages = _load_messages_route_module()

        class Receiver:
            async def fetch_folders(self):
                return [
                    Folder(name="收件箱", path="INBOX"),
                    Folder(name="已发送", path="[Gmail]/&XfJT0ZCuTvY-"),
                ]

        resolved = await messages._resolve_remote_folder(Receiver(), "Sent")

        self.assertEqual(resolved, "[Gmail]/&XfJT0ZCuTvY-")

    async def test_sent_zero_stats_are_rechecked_after_ttl(self):
        messages = _load_messages_route_module()

        folder_stats = {
            "total_count": 0,
            "unread_count": 0,
            "updated_at": 1000,
        }
        local_data = {"messages": [], "total": 0}

        messages.time.time = lambda: 1000 + messages.ZERO_COUNT_RECHECK_SECONDS + 1

        self.assertFalse(messages._trust_zero_folder_stats("Sent", folder_stats))
        self.assertFalse(
            messages._local_page_is_complete(
                local_data,
                folder_stats,
                page=1,
                page_size=50,
                trust_zero_stats=messages._trust_zero_folder_stats("Sent", folder_stats),
            )
        )

    async def test_recent_sent_zero_stats_are_not_trusted(self):
        messages = _load_messages_route_module()

        folder_stats = {
            "total_count": 0,
            "unread_count": 0,
            "updated_at": 1000,
        }
        local_data = {"messages": [], "total": 0}

        self.assertFalse(messages._trust_zero_folder_stats("Sent", folder_stats))
        self.assertFalse(
            messages._local_page_is_complete(
                local_data,
                folder_stats,
                page=1,
                page_size=50,
                trust_zero_stats=messages._trust_zero_folder_stats("Sent", folder_stats),
            )
        )

    async def test_remote_page_fetch_returns_error_on_timeout(self):
        messages = _load_messages_route_module()

        async def slow_operation(_account, _operation):
            await messages.asyncio.sleep(1)

        account = types.SimpleNamespace(
            id="account-1",
            email="user@example.com",
            provider="gmail",
            status="online",
        )
        messages.REMOTE_PAGE_FETCH_TIMEOUT_SECONDS = 0.01
        messages._with_outlook_retry = slow_operation
        messages.sync_service = types.SimpleNamespace(is_account_suspended=lambda _account_id: False)

        result, error = await messages._fetch_remote_page_to_cache(
            user_uid="user-1",
            account=account,
            folder="Sent",
            page=1,
            page_size=50,
        )

        self.assertIsNone(result)
        self.assertIn("超时", error)


if __name__ == "__main__":
    unittest.main()
