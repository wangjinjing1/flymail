import unittest

from providers.gmail.receiver import GmailReceiver


class GmailFolderParsingTest(unittest.TestCase):
    def test_fetch_folders_uses_gmail_special_flags_for_sent_and_drafts(self):
        class FakeConn:
            def list(self):
                return "OK", [
                    b'(\\HasNoChildren \\Inbox) "/" "INBOX"',
                    b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
                    b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Drafts"',
                ]

        receiver = GmailReceiver()
        receiver._conn = FakeConn()

        folders = receiver._list_folders()

        by_path = {folder.path for folder in folders}
        self.assertIn("[Gmail]/Sent Mail", by_path)
        self.assertIn("[Gmail]/Drafts", by_path)


if __name__ == "__main__":
    unittest.main()
