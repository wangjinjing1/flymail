import unittest
from unittest.mock import Mock, patch

from providers.ipv4 import IPv4IMAP4_SSL


class IPv4IMAPTest(unittest.TestCase):
    def test_open_uses_private_file_slot_for_python_314(self):
        receiver = IPv4IMAP4_SSL.__new__(IPv4IMAP4_SSL)
        raw_sock = Mock()
        ssl_sock = Mock()
        file_obj = object()
        ssl_sock.makefile.return_value = file_obj

        with (
            patch("providers.ipv4.socket.getaddrinfo", return_value=[(1, 2, 3, "", ("127.0.0.1", 993))]),
            patch("providers.ipv4.socket.socket", return_value=raw_sock),
            patch.object(receiver, "_get_ssl_context") as context_factory,
        ):
            context_factory.return_value.wrap_socket.return_value = ssl_sock

            receiver.open("imap.example.test", 993, timeout=5)

        self.assertIs(receiver._file, file_obj)
        self.assertIs(receiver.sock, ssl_sock)

    def test_set_file_handle_also_supports_older_imaplib(self):
        class OldStyleReceiver(IPv4IMAP4_SSL):
            file = None

        receiver = OldStyleReceiver.__new__(OldStyleReceiver)
        file_obj = object()

        receiver._set_file_handle(file_obj)

        self.assertIs(receiver._file, file_obj)
        self.assertIs(receiver.file, file_obj)


if __name__ == "__main__":
    unittest.main()
