import json
import sys
import types
import unittest
from unittest.mock import patch

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


class FakeCurlMime:
    instances = []

    def __init__(self):
        self.parts = []
        self.closed = False
        self.instances.append(self)

    def addpart(self, **kwargs):
        self.parts.append(kwargs)

    def close(self):
        self.closed = True


class Grok2ApiGoRemoteTests(unittest.TestCase):
    def setUp(self):
        account_outputs._clear_grok2api_admin_cache()
        FakeCurlMime.instances.clear()
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

    def admin_login_response(self):
        return Response(
            payload={
                "data": {
                    "tokens": {
                        "accessToken": "admin-token",
                        "accessTokenExpiresAt": "2099-01-01T00:00:00Z",
                    }
                }
            }
        )

    def patch_curl_mime(self):
        module = types.ModuleType("curl_cffi")
        module.CurlMime = FakeCurlMime
        return patch.dict(sys.modules, {"curl_cffi": module})

    def test_new_api_uploads_json_and_bypasses_registration_proxy(self):
        calls = []

        def post(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/auth/login"):
                return self.admin_login_response()
            return Response(
                text='event: complete\ndata: {"created":1,"updated":0,"skipped":0,"synced":1,"syncFailed":0}\n\n'
            )

        self.bind(post)
        with self.patch_curl_mime():
            self.assertTrue(
                account_outputs.add_token_to_grok2api_remote_pool(
                    "sso=abc", email="user@example.com"
                )
            )

        self.assertEqual(calls[0][0], "https://grok.example.com/api/admin/v1/auth/login")
        self.assertEqual(
            calls[1][0], "https://grok.example.com/api/admin/v1/accounts/web/import"
        )
        self.assertEqual(calls[0][1]["proxies"], {})
        self.assertEqual(calls[1][1]["proxies"], {})
        self.assertTrue(calls[0][1]["verify"])
        self.assertTrue(calls[1][1]["verify"])
        self.assertEqual(calls[0][1]["timeout"], 60)
        self.assertEqual(calls[1][1]["timeout"], 180)

        multipart = FakeCurlMime.instances[0]
        self.assertIs(calls[1][1]["multipart"], multipart)
        self.assertTrue(multipart.closed)
        self.assertEqual(
            multipart.parts,
            [
                {
                    "name": "file",
                    "filename": "grok-web-sso.json",
                    "content_type": "application/json; charset=utf-8",
                    "data": multipart.parts[0]["data"],
                }
            ],
        )
        self.assertEqual(
            json.loads(multipart.parts[0]["data"].decode("utf-8")),
            {
                "provider": "grok_web",
                "accounts": [
                    {
                        "name": "user@example.com",
                        "sso_token": "abc",
                        "email": "user@example.com",
                    }
                ],
            },
        )

    def test_multipart_is_closed_when_import_request_fails(self):
        def post(url, **kwargs):
            if url.endswith("/auth/login"):
                return self.admin_login_response()
            raise OSError("connection reset")

        self.bind(post)
        with self.patch_curl_mime():
            with self.assertRaisesRegex(RuntimeError, "导入请求失败"):
                account_outputs.add_token_to_grok2api_remote_pool("abc")

        self.assertEqual(len(FakeCurlMime.instances), 1)
        self.assertTrue(FakeCurlMime.instances[0].closed)

    def test_remote_plain_http_is_rejected(self):
        self.config["grok2api_remote_base"] = "http://grok.example.com"
        self.bind(lambda *a, **k: None)
        with self.assertRaisesRegex(RuntimeError, "必须使用 HTTPS"):
            account_outputs.add_token_to_grok2api_remote_pool("abc")

    def test_sync_failure_is_not_full_success(self):
        def post(url, **kwargs):
            if url.endswith("/auth/login"):
                return self.admin_login_response()
            return Response(
                text='event: complete\ndata: {"created":1,"synced":0,"syncFailed":1}\n\n'
            )

        self.bind(post)
        with self.patch_curl_mime():
            with self.assertRaisesRegex(RuntimeError, "初始同步失败"):
                account_outputs.add_token_to_grok2api_remote_pool("abc")

        self.assertTrue(FakeCurlMime.instances[0].closed)

    def test_web_import_document_carries_name_and_email(self):
        document = account_outputs.build_grok2api_web_import_document(
            "abc", "user@example.com"
        )
        self.assertEqual(document["provider"], "grok_web")
        self.assertEqual(
            document["accounts"],
            [{"name": "user@example.com", "sso_token": "abc", "email": "user@example.com"}],
        )

    def test_web_import_document_without_email_omits_email_field(self):
        document = account_outputs.build_grok2api_web_import_document("abc")
        self.assertEqual(document["accounts"], [{"name": "", "sso_token": "abc"}])

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
