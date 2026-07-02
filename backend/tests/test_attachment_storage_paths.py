import unittest

from data_paths import DOWNLOADS_DIR, build_message_file_path


class AttachmentStoragePathTest(unittest.TestCase):
    def test_jpg_attachment_with_unknown_content_type_uses_download_dir(self):
        _, path = build_message_file_path(
            message_date="2026-07-02T10:00:00Z",
            account_id="account-1",
            account_email="user@example.com",
            uid=42,
            part_number=1,
            filename="photo.JPG",
            content_type="application/octet-stream",
        )

        self.assertTrue(path.is_relative_to(DOWNLOADS_DIR))

    def test_non_image_attachment_uses_download_dir(self):
        _, path = build_message_file_path(
            message_date="2026-07-02T10:00:00Z",
            account_id="account-1",
            account_email="user@example.com",
            uid=42,
            part_number=1,
            filename="report.pdf",
            content_type="application/pdf",
        )

        self.assertTrue(path.is_relative_to(DOWNLOADS_DIR))


if __name__ == "__main__":
    unittest.main()
