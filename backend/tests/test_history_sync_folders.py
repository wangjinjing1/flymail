import importlib.util
import sys
import types
import unittest
from pathlib import Path


def _load_history_sync_module():
    db_stub = types.ModuleType("db")
    for name in (
        "batch_update_is_read",
        "create_history_sync_job",
        "batch_delete_cached_messages",
        "delete_account",
        "get_cached_attachment",
        "get_cached_attachment_rows",
        "get_cached_count",
        "get_cached_message_detail",
        "get_account_by_id",
        "get_cached_uids",
        "get_history_sync_job",
        "get_history_sync_job_by_id",
        "list_cached_attachments",
        "update_history_sync_job",
        "upsert_cached_attachments",
        "upsert_cached_messages",
        "delete_cached_attachments_by_account",
        "delete_cached_messages_by_account",
        "delete_folder_stats_by_account",
        "delete_history_sync_jobs_by_account",
        "upsert_folder_stats",
    ):
        setattr(db_stub, name, object())

    data_paths_stub = types.ModuleType("data_paths")
    for name in (
        "DOWNLOADS_DIR",
        "build_message_file_path",
        "coalesce_message_date",
        "clear_account_storage",
        "ensure_message_file_location",
        "ensure_data_dirs",
        "normalize_message_date",
        "UNKNOWN_MESSAGE_DATE",
    ):
        setattr(data_paths_stub, name, object())

    factory_stub = types.ModuleType("providers.factory")
    factory_stub.ProviderFactory = object()

    sync_stub = types.ModuleType("services.sync")
    sync_stub.sync_service = object()

    token_stub = types.ModuleType("services.token")
    token_stub.ensure_token = object()

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.get_logger = lambda name: types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )

    tasks_stub = types.ModuleType("utils.tasks")
    tasks_stub.create_background_task = lambda *args, **kwargs: None

    previous = {
        name: sys.modules.get(name)
        for name in (
            "db",
            "data_paths",
            "providers.factory",
            "services.sync",
            "services.token",
            "utils.logger",
            "utils.tasks",
        )
    }
    sys.modules.update(
        {
            "db": db_stub,
            "data_paths": data_paths_stub,
            "providers.factory": factory_stub,
            "services.sync": sync_stub,
            "services.token": token_stub,
            "utils.logger": logger_stub,
            "utils.tasks": tasks_stub,
        }
    )
    try:
        module_path = Path(__file__).resolve().parents[1] / "services" / "history_sync.py"
        spec = importlib.util.spec_from_file_location("history_sync_for_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class HistorySyncFolderResolutionTest(unittest.TestCase):
    def test_resolves_netease_sent_folder_by_display_name(self):
        history_sync = _load_history_sync_module()
        remote_folders = [
            types.SimpleNamespace(name="收件箱", path="INBOX"),
            types.SimpleNamespace(name="已发送", path="&XfJT0ZAB-"),
        ]

        resolved = history_sync._resolve_history_folders(remote_folders, ["Sent"])

        self.assertEqual(resolved[0].path, "&XfJT0ZAB-")


if __name__ == "__main__":
    unittest.main()
