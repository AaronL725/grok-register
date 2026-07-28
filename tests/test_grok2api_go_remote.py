import unittest

import account_outputs
import app_config


class Response:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.headers = {}

    def json(self):
        return self._payload


class Grok2ApiGoRemoteTests(unittest.TestCase):
    def setUp(self):
        account_outputs._clear_grok2api_admin_cache()
        self.config = {
            "grok2api_remote_base": "https://grok.example.com",
            "grok2api_remote_app_key": "",
            "grok2api_remote_admin_username": "admin",
            "grok2api_remote_admin_password": "secret",
            "grok2api_pool_name": "ssoBasic",
        }

    def bind(self, post):
        account_outputs.configure_token_runtime(
            self.config, lambda *a, **k: None, post, lambda c, e, callback=None: str(e)
        )

    def test_new_api_reuses_remote_entrypoint_and_bypasses_registration_proxy(self):
        calls = []
        def post(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/auth/login"):
                return Response(payload={"data": {"tokens": {"accessToken": "admin-token", "accessTokenExpiresAt": "2099-01-01T00:00:00Z"}}})
            return Response(text='event: complete\ndata: {"created":1,"updated":0,"skipped":0,"synced":1,"syncFailed":0}\n\n')
        self.bind(post)
        self.assertTrue(account_outputs.add_token_to_grok2api_remote_pool("sso=abc"))
        self.assertEqual(calls[0][0], "https://grok.example.com/api/admin/v1/auth/login")
        self.assertEqual(calls[1][0], "https://grok.example.com/api/admin/v1/accounts/web/import")
        self.assertEqual(calls[0][1]["proxies"], {})
        self.assertEqual(calls[1][1]["proxies"], {})
        self.assertTrue(calls[0][1]["verify"])
        self.assertTrue(calls[1][1]["verify"])

    def test_remote_plain_http_is_rejected(self):
        self.config["grok2api_remote_base"] = "http://grok.example.com"
        self.bind(lambda *a, **k: None)
        with self.assertRaisesRegex(RuntimeError, "必须使用 HTTPS"):
            account_outputs.add_token_to_grok2api_remote_pool("abc")

    def test_sync_failure_is_not_full_success(self):
        def post(url, **kwargs):
            if url.endswith("/auth/login"):
                return Response(payload={"data": {"tokens": {"accessToken": "admin-token", "accessTokenExpiresAt": "2099-01-01T00:00:00Z"}}})
            return Response(text='event: complete\ndata: {"created":1,"synced":0,"syncFailed":1}\n\n')
        self.bind(post)
        with self.assertRaisesRegex(RuntimeError, "初始同步失败"):
            account_outputs.add_token_to_grok2api_remote_pool("abc")

    def test_mixed_credentials_are_rejected(self):
        cfg = dict(app_config.DEFAULT_CONFIG)
        cfg.update({
            "grok2api_auto_add_remote": True,
            "grok2api_remote_base": "https://grok.example.com",
            "grok2api_remote_app_key": "old-key",
            "grok2api_remote_admin_username": "admin",
            "grok2api_remote_admin_password": "secret",
        })
        with self.assertRaisesRegex(app_config.ConfigError, "不能同时配置"):
            app_config.validate_run_requirements(cfg)


if __name__ == "__main__":
    unittest.main()
