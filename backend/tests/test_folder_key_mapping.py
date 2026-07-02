import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from db import _expand_folder_aliases, folder_key_for_path


class FolderKeyMappingTest(unittest.TestCase):
    def test_maps_modified_utf7_core_folders(self):
        cases = {
            "[Gmail]/&XfJT0ZCuTvY-": "sent",
            "&XfJT0ZAB-": "sent",
            "&g0l6P3ux-": "drafts",
            "[Gmail]/&V4NXPpCuTvY-": "junk",
            "[Gmail]/&XfJSIJZk-": "trash",
        }

        for folder, expected in cases.items():
            with self.subTest(folder=folder):
                self.assertEqual(folder_key_for_path(folder), expected)

    def test_expands_modified_utf7_aliases_for_core_folders(self):
        cases = {
            "Sent": {"&XfJT0ZAB-", "[Gmail]/&XfJT0ZCuTvY-"},
            "Drafts": {"&g0l6P3ux-", "[Gmail]/&g0l6Pw-"},
            "Junk": {"[Gmail]/&V4NXPpCuTvY-"},
            "Trash": {"[Gmail]/&XfJSIJZk-", "[Gmail]/&XfJSIJZkkK5O9g-"},
        }

        for folder, expected_aliases in cases.items():
            with self.subTest(folder=folder):
                self.assertTrue(expected_aliases.issubset(set(_expand_folder_aliases(folder))))


if __name__ == "__main__":
    unittest.main()
