import unittest
from unittest.mock import patch

import grok_register_ttk as app


class DummyResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")
        return None

    def json(self):
        return self._payload


class CloudMailTests(unittest.TestCase):
    def setUp(self):
        self.original_config = app.config.copy()
        self.original_token = app._cloudmail_public_token
        self.original_index = app._cf_domain_index
        app._cloudmail_public_token = None
        app._cf_domain_index = 0
        app.config = app.DEFAULT_CONFIG.copy()
        app.config.update(
            {
                "email_provider": "cloudmail",
                "cloudmail_url": "https://mail.example.com",
                "cloudmail_admin_email": "admin@example.com",
                "cloudmail_password": "secret",
                "defaultDomains": "example.com,other.com",
            }
        )

    def tearDown(self):
        app.config = self.original_config
        app._cloudmail_public_token = self.original_token
        app._cf_domain_index = self.original_index

    def test_get_email_and_token_generates_catch_all_address(self):
        addr1, token1 = app.get_email_and_token()
        addr2, token2 = app.get_email_and_token()
        self.assertEqual(token1, "cloudmail_catch_all")
        self.assertEqual(token2, "cloudmail_catch_all")
        self.assertTrue(addr1.endswith("@example.com"))
        self.assertTrue(addr2.endswith("@other.com"))
        self.assertNotEqual(addr1, addr2)

    def test_get_email_and_token_requires_domains(self):
        app.config["defaultDomains"] = ""
        with self.assertRaises(Exception) as ctx:
            app.get_email_and_token()
        self.assertIn("defaultDomains", str(ctx.exception))

    def test_shared_token_cached_and_refreshable(self):
        calls = {"n": 0}

        def fake_post(url, **kwargs):
            calls["n"] += 1
            if url.endswith("/api/public/genToken"):
                return DummyResponse({"code": 200, "data": {"token": f"tok-{calls['n']}"}})
            raise AssertionError(f"unexpected url {url}")

        with patch.object(app, "http_post", side_effect=fake_post):
            t1 = app._cloudmail_get_shared_token()
            t2 = app._cloudmail_get_shared_token()
            t3 = app._cloudmail_get_shared_token(force_refresh=True)
        self.assertEqual(t1, "tok-1")
        self.assertEqual(t2, "tok-1")
        self.assertEqual(t3, "tok-2")
        self.assertEqual(calls["n"], 2)

    def test_cloudmail_get_oai_code_extracts_xai_code(self):
        def fake_post(url, **kwargs):
            if url.endswith("/api/public/genToken"):
                return DummyResponse({"code": 200, "data": {"token": "pub-token"}})
            if url.endswith("/api/public/emailList"):
                self.assertEqual(kwargs["headers"]["Authorization"], "pub-token")
                self.assertEqual(kwargs["json"]["toEmail"], "u@example.com")
                self.assertEqual(kwargs.get("proxies"), {})
                return DummyResponse(
                    {
                        "code": 200,
                        "data": [
                            {
                                "emailId": "m1",
                                "subject": "ABC-DEF xAI verification",
                                "content": "Your code is ABC-DEF",
                            }
                        ],
                    }
                )
            raise AssertionError(url)

        with patch.object(app, "http_post", side_effect=fake_post):
            code = app.cloudmail_get_oai_code(
                "cloudmail_catch_all",
                "u@example.com",
                timeout=5,
                poll_interval=0.01,
            )
        self.assertEqual(code, "ABC-DEF")

    def test_get_oai_code_dispatches_to_cloudmail(self):
        with patch.object(app, "cloudmail_get_oai_code", return_value="ZZZ-YYY") as mocked:
            code = app.get_oai_code("tok", "a@example.com", timeout=1, poll_interval=0.01)
        self.assertEqual(code, "ZZZ-YYY")
        mocked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
