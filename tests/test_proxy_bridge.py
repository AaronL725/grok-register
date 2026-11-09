import unittest
from unittest.mock import patch
from urllib.parse import quote

from proxy_bridge import LocalProxyBridge


class Socks5CredentialValidationTests(unittest.TestCase):
    @staticmethod
    def _url(scheme, username=None, password=None):
        if username is None and password is None:
            return f"{scheme}://127.0.0.1:1080"
        username_part = "" if username is None else quote(username, safe="")
        password_part = "" if password is None else quote(password, safe="")
        if password is None:
            userinfo = username_part
        else:
            userinfo = f"{username_part}:{password_part}"
        return f"{scheme}://{userinfo}@127.0.0.1:1080"

    def test_socks5_and_socks5h_allow_connections_without_credentials(self):
        for scheme in ("socks5", "socks5h"):
            with self.subTest(scheme=scheme):
                bridge = LocalProxyBridge(self._url(scheme))
                self.assertEqual(bridge.username, "")
                self.assertEqual(bridge.password, "")

    def test_socks5_and_socks5h_accept_complete_credentials(self):
        for scheme in ("socks5", "socks5h"):
            with self.subTest(scheme=scheme):
                bridge = LocalProxyBridge(self._url(scheme, "user", "pass"))
                self.assertEqual(bridge.username, "user")
                self.assertEqual(bridge.password, "pass")

    def test_socks5_rejects_incomplete_credentials(self):
        urls = (
            self._url("socks5", "", "pass"),
            self._url("socks5", "user", ""),
            self._url("socks5", "user"),
            self._url("socks5", "", ""),
            self._url("socks5h", "", "pass"),
            self._url("socks5h", "user", ""),
        )
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaisesRegex(
                    ValueError, "both a non-empty username and password"
                ):
                    LocalProxyBridge(url)

    def test_socks5_credentials_may_each_be_255_utf8_bytes(self):
        username = "用" * 85
        password = "密" * 85
        bridge = LocalProxyBridge(self._url("socks5", username, password))
        self.assertEqual(len(bridge.username.encode("utf-8")), 255)
        self.assertEqual(len(bridge.password.encode("utf-8")), 255)

    def test_socks5_rejects_username_over_255_utf8_bytes(self):
        username = "用" * 86
        with self.assertRaisesRegex(ValueError, "SOCKS5 username must be 1-255 UTF-8 bytes"):
            LocalProxyBridge(self._url("socks5", username, "pass"))

    def test_socks5_rejects_password_over_255_utf8_bytes(self):
        password = "密" * 86
        with self.assertRaisesRegex(ValueError, "SOCKS5 password must be 1-255 UTF-8 bytes"):
            LocalProxyBridge(self._url("socks5", "user", password))

    def test_socks5_authentication_handshake_keeps_valid_credentials(self):
        class FakeSocket:
            def __init__(self):
                self.sent = []

            def sendall(self, data):
                self.sent.append(data)

        bridge = LocalProxyBridge(self._url("socks5", "user", "pass"))
        sock = FakeSocket()
        responses = (
            b"\x05\x02",
            b"\x01\x00",
            b"\x05\x00\x00\x01",
            b"\x7f\x00\x00\x01",
            b"\x00\x50",
        )
        with patch("proxy_bridge._recv_exact", side_effect=responses):
            bridge._socks5_connect(sock, "127.0.0.1", 80)

        self.assertEqual(sock.sent[0], b"\x05\x02\x02\x00")
        self.assertEqual(sock.sent[1], b"\x01\x04user\x04pass")
        self.assertEqual(
            sock.sent[2], b"\x05\x01\x00\x01\x7f\x00\x00\x01\x00\x50"
        )

    def test_socks4_username_only_remains_supported(self):
        bridge = LocalProxyBridge(self._url("socks4", "user"))
        self.assertEqual(bridge.username, "user")


if __name__ == "__main__":
    unittest.main()
