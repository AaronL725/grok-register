"""验证 grok2api 远端 token 入池及并发安全回退逻辑。"""

import sys
import types
import unittest
from unittest.mock import patch

# Keep this unit test independent from optional browser/network dependencies.
drission = types.ModuleType("DrissionPage")
drission.Chromium = type("Chromium", (), {})
drission.ChromiumOptions = type("ChromiumOptions", (), {})
drission_errors = types.ModuleType("DrissionPage.errors")
drission_errors.PageDisconnectedError = type("PageDisconnectedError", (Exception,), {})
curl_cffi = types.ModuleType("curl_cffi")
curl_cffi.requests = types.SimpleNamespace()
sys.modules.setdefault("DrissionPage", drission)
sys.modules.setdefault("DrissionPage.errors", drission_errors)
sys.modules.setdefault("curl_cffi", curl_cffi)

import grok_register_ttk as app


class DummyResponse:
    def __init__(self, payload=None, status_code=200, reason="", headers=None, text=""):
        self._payload = payload or {}
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

    def tearDown(self):
        app.config = self.original_config

    def _configure(self, **overrides):
        app.config.update({
            "grok2api_remote_base": "https://grok.example.com",
            "grok2api_remote_app_key": "app-secret",
            "grok2api_pool_name": "ssoBasic",
            "grok2api_allow_legacy_full_save": False,
            **overrides,
        })

    def test_remote_pool_falls_back_to_admin_api_prefix_when_root_tokens_add_is_404(self):
        self._configure()
        calls = []

        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            if url == "https://grok.example.com/tokens/add":
                return DummyResponse(status_code=404)
            return DummyResponse({"status": "success", "count": 1})

        with patch.object(app, "http_post", side_effect=fake_post):
            ok = app.add_token_to_grok2api_remote_pool("sso=abc123", email="a@example.com")

        self.assertTrue(ok)
        self.assertEqual([url for url, _ in calls], [
            "https://grok.example.com/tokens/add",
            "https://grok.example.com/admin/api/tokens/add",
        ])
        self.assertEqual(calls[-1][1]["params"], {"app_key": "app-secret"})
        self.assertEqual(calls[-1][1]["json"], {
            "tokens": ["abc123"],
            "pool": "basic",
            "tags": ["auto-register"],
        })

    def test_remote_pool_does_not_duplicate_admin_api_prefix_when_base_already_points_to_admin_api(self):
        self._configure(
            grok2api_remote_base="https://grok.example.com/admin/api",
            grok2api_pool_name="ssoSuper",
        )
        calls = []

        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            return DummyResponse({"status": "success", "count": 1})

        with patch.object(app, "http_post", side_effect=fake_post):
            ok = app.add_token_to_grok2api_remote_pool("sso=super123", email="a@example.com")

        self.assertTrue(ok)
        self.assertEqual([url for url, _ in calls], [
            "https://grok.example.com/admin/api/tokens/add",
        ])
        self.assertEqual(calls[0][1]["json"]["pool"], "super")

    def test_remote_pool_full_save_fallback_requires_opt_in_and_uses_etag(self):
        self._configure(grok2api_allow_legacy_full_save=True)
        get_calls = []
        post_calls = []

        def fake_post(url, **kwargs):
            post_calls.append((url, kwargs))
            if url.endswith("/tokens/add"):
                return DummyResponse(status_code=404)
            if url == "https://grok.example.com/admin/api/tokens":
                return DummyResponse({"status": "success"})
            return DummyResponse(status_code=404)

        def fake_get(url, **kwargs):
            get_calls.append((url, kwargs))
            if url == "https://grok.example.com/admin/api/tokens":
                return DummyResponse(
                    {"tokens": {"ssoBasic": []}},
                    headers={"ETag": '"version-7"'},
                )
            return DummyResponse(status_code=404)

        with patch.object(app, "http_post", side_effect=fake_post), \
                patch.object(app, "http_get", side_effect=fake_get):
            ok = app.add_token_to_grok2api_remote_pool("sso=fallback123", email="a@example.com")

        self.assertTrue(ok)
        self.assertEqual([url for url, _ in get_calls], [
            "https://grok.example.com/tokens",
            "https://grok.example.com/admin/api/tokens",
        ])
        self.assertEqual(post_calls[-1][0], "https://grok.example.com/admin/api/tokens")
        self.assertEqual(post_calls[-1][1]["headers"]["If-Match"], '"version-7"')
        self.assertEqual(post_calls[-1][1]["json"], {
            "ssoBasic": [{"token": "fallback123", "tags": ["auto-register"], "note": "a@example.com"}],
        })

    def test_remote_pool_legacy_fallback_is_disabled_by_default(self):
        self._configure()
        with patch.object(app, "http_post", return_value=DummyResponse(status_code=404)), \
                patch.object(app, "http_get") as get_mock:
            with self.assertRaises(app.RemoteTokenCompatibilityError):
                app.add_token_to_grok2api_remote_pool("abc")
        get_mock.assert_not_called()

    def test_remote_pool_500_does_not_fallback(self):
        self._configure(grok2api_allow_legacy_full_save=True)
        with patch.object(app, "http_post", return_value=DummyResponse(status_code=500, text="boom")), \
                patch.object(app, "http_get") as get_mock:
            with self.assertRaises(app.RemoteTokenRequestError):
                app.add_token_to_grok2api_remote_pool("abc")
        get_mock.assert_not_called()

    def test_remote_pool_legacy_fallback_rejects_missing_etag(self):
        self._configure(grok2api_allow_legacy_full_save=True)

        def fake_post(url, **kwargs):
            if url.endswith("/tokens/add"):
                return DummyResponse(status_code=404)
            return DummyResponse({"status": "success"})

        def fake_get(url, **kwargs):
            if url.endswith("/tokens"):
                return DummyResponse({"tokens": {"ssoBasic": []}})
            return DummyResponse(status_code=404)

        with patch.object(app, "http_post", side_effect=fake_post), \
                patch.object(app, "http_get", side_effect=fake_get):
            with self.assertRaises(app.RemoteTokenCompatibilityError):
                app.add_token_to_grok2api_remote_pool("abc")


if __name__ == "__main__":
    unittest.main()
