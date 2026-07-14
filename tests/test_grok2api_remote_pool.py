import unittest
from unittest.mock import patch

import grok_register_ttk as app


class DummyResponse:
    def __init__(self, payload=None, status_code=200, reason="", headers=None, text=""):
        self._payload = {} if payload is None else payload
        self.status_code = status_code
        self.reason = reason
        self.headers = headers or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP Error {self.status_code}: {self.reason}")

    def json(self):
        return self._payload


class Grok2ApiRemotePoolTests(unittest.TestCase):
    def setUp(self):
        self.original_config = app.config.copy()
        app.config = app.validate_config({
            **app.DEFAULT_CONFIG,
            "grok2api_remote_base": "https://grok.example.com",
            "grok2api_remote_app_key": "app-secret",
            "grok2api_pool_name": "ssoBasic",
        })

    def tearDown(self):
        app.config = self.original_config

    def test_atomic_add_tries_admin_api_after_404(self):
        calls = []

        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            if url == "https://grok.example.com/tokens/add":
                return DummyResponse(status_code=404)
            return DummyResponse({"status": "success"}, status_code=200)

        with patch.object(app, "http_post", side_effect=fake_post):
            ok = app.add_token_to_grok2api_remote_pool(
                "sso=abc123",
                email="a@example.com",
            )

        self.assertTrue(ok)
        self.assertEqual(
            [url for url, _ in calls],
            [
                "https://grok.example.com/tokens/add",
                "https://grok.example.com/admin/api/tokens/add",
            ],
        )

    def test_authentication_failure_does_not_enter_full_replace(self):
        post_calls = []
        with patch.object(
            app,
            "http_post",
            side_effect=lambda url, **kwargs: (
                post_calls.append(url) or DummyResponse(status_code=401)
            ),
        ), patch.object(app, "http_get") as get:
            with self.assertRaises(app.RemoteTokenPoolRequestError):
                app.add_token_to_grok2api_remote_pool("sso=abc123")

        self.assertEqual(post_calls, ["https://grok.example.com/tokens/add"])
        get.assert_not_called()

    def test_network_failure_does_not_enter_full_replace(self):
        with patch.object(
            app,
            "http_post",
            side_effect=TimeoutError("network timeout"),
        ), patch.object(app, "http_get") as get:
            with self.assertRaises(app.RemoteTokenPoolRequestError):
                app.add_token_to_grok2api_remote_pool("sso=abc123")
        get.assert_not_called()

    def test_legacy_full_replace_is_disabled_by_default(self):
        with patch.object(
            app,
            "http_post",
            return_value=DummyResponse(status_code=404),
        ), patch.object(app, "http_get") as get:
            with self.assertRaises(app.RemoteTokenPoolIncompatibleError):
                app.add_token_to_grok2api_remote_pool("sso=abc123")
        get.assert_not_called()

    def test_legacy_full_replace_requires_etag_and_sends_if_match(self):
        app.config["grok2api_allow_legacy_full_replace"] = True
        post_calls = []

        def fake_post(url, **kwargs):
            post_calls.append((url, kwargs))
            if url.endswith("/tokens/add"):
                return DummyResponse(status_code=404)
            return DummyResponse({"status": "success"}, status_code=200)

        with patch.object(app, "http_post", side_effect=fake_post), patch.object(
            app,
            "http_get",
            return_value=DummyResponse(
                {"tokens": {"ssoBasic": []}},
                status_code=200,
                headers={"ETag": '"version-1"'},
            ),
        ):
            ok = app.add_token_to_grok2api_remote_pool(
                "sso=fallback123",
                email="a@example.com",
            )

        self.assertTrue(ok)
        full_save_url, full_save_kwargs = post_calls[-1]
        self.assertEqual(full_save_url, "https://grok.example.com/tokens")
        self.assertEqual(full_save_kwargs["headers"]["If-Match"], '"version-1"')
        self.assertEqual(
            full_save_kwargs["json"],
            {
                "ssoBasic": [
                    {
                        "token": "fallback123",
                        "tags": ["auto-register"],
                        "note": "a@example.com",
                    }
                ]
            },
        )

    def test_legacy_full_replace_without_etag_is_rejected(self):
        app.config["grok2api_allow_legacy_full_replace"] = True
        with patch.object(
            app,
            "http_post",
            return_value=DummyResponse(status_code=404),
        ), patch.object(
            app,
            "http_get",
            return_value=DummyResponse(
                {"tokens": {"ssoBasic": []}},
                status_code=200,
            ),
        ):
            with self.assertRaises(app.RemoteTokenPoolIncompatibleError):
                app.add_token_to_grok2api_remote_pool("sso=fallback123")


if __name__ == "__main__":
    unittest.main()
