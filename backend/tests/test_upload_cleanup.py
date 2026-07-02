import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from services import upload_cleanup


class UploadCleanupTest(unittest.TestCase):
    def test_next_cleanup_time_defaults_to_next_monday_2am(self):
        now = datetime(2026, 7, 2, 12, 0)
        run_at = upload_cleanup.next_cleanup_time(now, weekday=0, hour=2, minute=0)

        self.assertEqual(run_at, datetime(2026, 7, 6, 2, 0))

    def test_next_cleanup_time_rolls_to_next_week_if_time_passed(self):
        now = datetime(2026, 7, 6, 3, 0)
        run_at = upload_cleanup.next_cleanup_time(now, weekday=0, hour=2, minute=0)

        self.assertEqual(run_at, datetime(2026, 7, 13, 2, 0))

    def test_clean_uploads_dir_removes_files_and_subdirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            uploads_dir = Path(tmp) / "files" / "uploads"
            uploads_dir.mkdir(parents=True)
            (uploads_dir / "draft.txt").write_text("draft", encoding="utf-8")
            nested = uploads_dir / "nested"
            nested.mkdir()
            (nested / "file.txt").write_text("nested", encoding="utf-8")

            with patch.object(upload_cleanup, "UPLOADS_DIR", uploads_dir):
                removed = upload_cleanup.clean_uploads_dir()

            self.assertEqual(removed, 2)
            self.assertEqual(list(uploads_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
