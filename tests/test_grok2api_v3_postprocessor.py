"""Tests for the Go-version grok2api Grok Web postprocessor."""

import unittest

import account_outputs
import app_config


class DummyResponse:
    def __init__(self, payload=None, status_code=200, headers=None, text=""):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def login_response(token="admin-token"):
    return DummyResponse(
        {
            "success": True,
            "data": {
                "tokens": {
                    "accessToken": token,
                    "accessTokenExpiresAt": "2099-01-01T00:00:00Z",
                }
            },
        }
    )


def import_response(created=1, updated=0, synced=1):
    return DummyResponse(
        status_code=200,
        headers={"Content-Type": "text/event-stream; charset=utf-8"},
        text=(
            "event: progress\n"
            'data: {"phase":"importing","completed":1,"total":1}\n\n'
            "event: complete\n"
            f'data: {{"created":{created},"updated":{updated},"synced":{synced},"syncFailed":0}}\n\n'
        ),
    )


class Grok2ApiV3PostprocessorTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "grok2api_auto_add_local": False,
            "grok2api_auto_add_remote": False,
            "grok2api_v3_auto_import": True,
            "grok2api_v3_base_url": "https://grok.example.com",
            "grok2api_v3_admin_username": "admin",
            "grok2api_v3_admin_password": "secret-password",
            "grok2api_v3_verify_tls": True,
            "grok2api_v3_request_timeout_sec": 60,
        }
        account_outputs._clear_grok2api_v3_auth_cache()

    def bind(self, fake_post):
        account_outputs.configure_token_runtime(
            self.config,
            lambda *args, **kwargs: DummyResponse(status_code=404),
            fake_post,
            lambda context, exc, callback=None: f"{context}: {exc}",
            compatibility_error=type("CompatibilityError", (RuntimeError,), {}),
            request_error=type("RequestError", (RuntimeError,), {}),
        )

    def test_api_base_normalization(self):
        self.assertEqual(
            account_outputs.get_grok2api_v3_api_base("https://grok.example.com/"),
            "https://grok.example.com/api/admin/v1",
        )
        self.assertEqual(
            account_outputs.get_grok2api_v3_api_base(
                "https://grok.example.com/api/admin/v1"
            ),
            "https://grok.example.com/api/admin/v1",
        )
        self.assertEqual(
            account_outputs.get_grok2api_v3_api_base(
                "https://grok.example.com/admin"
            ),
            "https://grok.example.com/api/admin/v1",
        )

    def test_login_then_imports_sso_as_text_file(self):
        calls = []

        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/auth/login"):
                return login_response()
            return import_response()

        self.bind(fake_post)
        logs = []
        ok = account_outputs.add_token_to_grok2api_v3(
            "sso=abc123", email="user@example.com", log_callback=logs.append
        )

        self.assertTrue(ok)
        self.assertEqual(
            [url for url, _ in calls],
            [
                "https://grok.example.com/api/admin/v1/auth/login",
                "https://grok.example.com/api/admin/v1/accounts/web/import",
            ],
        )
        login_kwargs = calls[0][1]
        self.assertEqual(
            login_kwargs["json"],
            {"username": "admin", "password": "secret-password"},
        )
        import_kwargs = calls[1][1]
        self.assertEqual(
            import_kwargs["headers"]["Authorization"], "Bearer admin-token"
        )
        self.assertIn("multipart", import_kwargs)
        self.assertTrue(import_kwargs["verify"])
        self.assertTrue(any("created=1" in line for line in logs))

    def test_access_token_is_reused_until_expiry(self):
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            return login_response() if url.endswith("/auth/login") else import_response()

        self.bind(fake_post)
        self.assertTrue(account_outputs.add_token_to_grok2api_v3("one"))
        self.assertTrue(account_outputs.add_token_to_grok2api_v3("two"))
        self.assertEqual(sum(url.endswith("/auth/login") for url in calls), 1)
        self.assertEqual(sum(url.endswith("/accounts/web/import") for url in calls), 2)

    def test_unauthorized_import_reauthenticates_once(self):
        calls = []
        login_count = 0
        import_count = 0

        def fake_post(url, **kwargs):
            nonlocal login_count, import_count
            calls.append((url, kwargs))
            if url.endswith("/auth/login"):
                login_count += 1
                return login_response(f"admin-token-{login_count}")
            import_count += 1
            if import_count == 1:
                return DummyResponse(status_code=401)
            return import_response()

        self.bind(fake_post)
        self.assertTrue(account_outputs.add_token_to_grok2api_v3("abc123"))
        self.assertEqual(login_count, 2)
        self.assertEqual(import_count, 2)
        self.assertEqual(
            calls[-1][1]["headers"]["Authorization"], "Bearer admin-token-2"
        )

    def test_sse_error_is_reported_without_exposing_token(self):
        secret_sso = "very-secret-sso"

        def fake_post(url, **kwargs):
            if url.endswith("/auth/login"):
                return login_response()
            return DummyResponse(
                headers={"Content-Type": "text/event-stream"},
                text=(
                    "event: error\n"
                    f'data: {{"message":"credential {secret_sso} rejected"}}\n\n'
                ),
            )

        self.bind(fake_post)
        with self.assertRaisesRegex(RuntimeError, r"credential \[REDACTED\] rejected") as caught:
            account_outputs.add_token_to_grok2api_v3(secret_sso)
        self.assertNotIn(secret_sso, str(caught.exception))

    def test_pool_orchestrator_keeps_v3_failure_isolated(self):
        def fake_post(url, **kwargs):
            return DummyResponse(status_code=503)

        self.bind(fake_post)
        result = account_outputs.add_token_to_grok2api_pools("abc123")
        self.assertTrue(result["v3"]["enabled"])
        self.assertFalse(result["v3"]["ok"])
        self.assertIn("HTTP 503", result["v3"]["error"])


class Grok2ApiV3ConfigTests(unittest.TestCase):
    def test_enabled_import_requires_credentials(self):
        cfg = dict(app_config.DEFAULT_CONFIG)
        cfg["grok2api_v3_auto_import"] = True
        with self.assertRaises(app_config.ConfigError) as caught:
            app_config.validate_run_requirements(cfg)
        message = str(caught.exception)
        self.assertIn("grok2api_v3_base_url", message)
        self.assertIn("grok2api_v3_admin_username", message)
        self.assertIn("grok2api_v3_admin_password", message)

    def test_complete_v3_config_validates(self):
        cfg = dict(app_config.DEFAULT_CONFIG)
        cfg.update(
            {
                "grok2api_v3_auto_import": True,
                "grok2api_v3_base_url": "https://grok.example.com",
                "grok2api_v3_admin_username": "admin",
                "grok2api_v3_admin_password": "secret",
            }
        )
        validated = app_config.validate_run_requirements(cfg)
        self.assertTrue(validated["grok2api_v3_auto_import"])


if __name__ == "__main__":
    unittest.main()
