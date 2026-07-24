"""验证 Cloudflare 临时邮箱 admin 创建和鉴权接口行为。"""

import unittest
from unittest.mock import patch

import cf_mail_debug
import grok_register_ttk as app
import mail_service


class DummyResponse:
    def __init__(self, payload):
        self._payload = payload
        self.text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class CloudflareAdminCreateTests(unittest.TestCase):
    def setUp(self):
        self.original_config = app.config.copy()
        self.original_cf_domain_index = app._cf_domain_index
        app._cf_domain_index = 0

    def tearDown(self):
        app.config = self.original_config
        app._cf_domain_index = self.original_cf_domain_index

    def test_default_config_keeps_cloudflare_temp_email_new_address(self):
        app.config = app.DEFAULT_CONFIG.copy()
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return DummyResponse({"address": "anon@example.com", "jwt": "default-jwt"})

        with patch.object(app, "http_post", side_effect=fake_post):
            address, jwt = app.cloudflare_create_temp_address("https://temp-mail.example.com")

        self.assertEqual(address, "anon@example.com")
        self.assertEqual(jwt, "default-jwt")
        self.assertEqual(captured["url"], "https://temp-mail.example.com/api/new_address")
        self.assertEqual(captured["json"], {})
        self.assertEqual(captured["headers"], {"Content-Type": "application/json"})

    def test_app_uses_admin_new_address_with_x_admin_auth(self):
        app.config.update({
            "cloudflare_api_key": "admin-secret",
            "cloudflare_auth_mode": "x-admin-auth",
            "cloudflare_path_accounts": "/admin/new_address",
            "defaultDomains": "vitassk.com",
        })
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return DummyResponse({"address": "adminuser@vitassk.com", "jwt": "address-jwt"})

        with patch.object(app, "generate_username", return_value="adminuser"), \
                patch.object(app, "http_post", side_effect=fake_post):
            address, jwt = app.cloudflare_create_temp_address("https://temp-mail.ikun.day")

        self.assertEqual(address, "adminuser@vitassk.com")
        self.assertEqual(jwt, "address-jwt")
        self.assertEqual(captured["url"], "https://temp-mail.ikun.day/admin/new_address")
        self.assertEqual(captured["json"], {
            "name": "adminuser",
            "domain": "vitassk.com",
            "enablePrefix": True,
        })
        self.assertEqual(captured["headers"]["Content-Type"], "application/json")
        self.assertEqual(captured["headers"]["x-admin-auth"], "admin-secret")

    def test_app_keeps_anonymous_new_address_with_none_auth(self):
        app.config.update({
            "cloudflare_api_key": "",
            "cloudflare_auth_mode": "none",
            "cloudflare_path_accounts": "/api/new_address",
            "defaultDomains": "vitassk.com",
        })
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return DummyResponse({"address": "anon@vitassk.com", "jwt": "anon-jwt"})

        with patch.object(app, "http_post", side_effect=fake_post):
            address, jwt = app.cloudflare_create_temp_address("https://temp-mail.ikun.day")

        self.assertEqual(address, "anon@vitassk.com")
        self.assertEqual(jwt, "anon-jwt")
        self.assertEqual(captured["url"], "https://temp-mail.ikun.day/api/new_address")
        self.assertEqual(captured["json"], {"domain": "vitassk.com"})
        self.assertEqual(captured["headers"], {"Content-Type": "application/json"})

    def test_debug_tool_can_create_address_through_admin_api(self):
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return DummyResponse({"address": "debuguser@vitassk.com", "jwt": "debug-jwt"})

        with patch.object(cf_mail_debug.requests, "post", side_effect=fake_post):
            address, jwt = cf_mail_debug.create_address(
                "https://temp-mail.ikun.day",
                auth_mode="x-admin-auth",
                api_key="admin-secret",
                create_path="/admin/new_address",
                domain="vitassk.com",
                name="debuguser",
            )

        self.assertEqual(address, "debuguser@vitassk.com")
        self.assertEqual(jwt, "debug-jwt")
        self.assertEqual(captured["url"], "https://temp-mail.ikun.day/admin/new_address")
        self.assertEqual(captured["json"], {
            "name": "debuguser",
            "domain": "vitassk.com",
            "enablePrefix": True,
        })
        self.assertEqual(captured["headers"]["Content-Type"], "application/json")
        self.assertEqual(captured["headers"]["x-admin-auth"], "admin-secret")


    def test_cloudflare_fallback_still_succeeds(self):
        app.config.update({
            "email_provider": "cloudflare",
            "cloudflare_api_base": "https://temp-mail.example.com",
            "cloudflare_api_key": "",
            "cloudflare_auth_mode": "none",
            "cloudflare_path_accounts": "/api/new_address",
        })
        with patch.object(mail_service, "config", app.config), patch.object(
            mail_service, "cloudflare_create_temp_address",
            side_effect=RuntimeError("primary failed"),
        ), patch.object(
            mail_service, "cloudflare_get_domains",
            return_value=[{"domain": "example.com", "isVerified": True}],
        ), patch.object(
            mail_service, "generate_username", return_value="testuser",
        ), patch.object(
            mail_service, "cloudflare_create_account", return_value={},
        ), patch.object(
            mail_service, "cloudflare_get_token", return_value="fallback-token",
        ):
            address, token = mail_service.get_email_and_token()

        self.assertEqual(address, "testuser@example.com")
        self.assertEqual(token, "fallback-token")

    def test_cloudflare_fallback_reports_both_errors(self):
        app.config.update({
            "email_provider": "cloudflare",
            "cloudflare_api_base": "https://temp-mail.example.com",
            "cloudflare_api_key": "admin-secret",
            "cloudflare_auth_mode": "x-admin-auth",
            "cloudflare_path_accounts": "/admin/new_address",
        })
        with patch.object(mail_service, "config", app.config), patch.object(
            mail_service, "cloudflare_create_temp_address",
            side_effect=RuntimeError("primary 401"),
        ), patch.object(
            mail_service, "cloudflare_get_domains",
            side_effect=RuntimeError("fallback 403"),
        ):
            with self.assertRaises(RuntimeError) as caught:
                mail_service.get_email_and_token()

        message = str(caught.exception)
        self.assertIn("/admin/new_address", message)
        self.assertIn("primary 401", message)
        self.assertIn("获取域名列表", message)
        self.assertIn("fallback 403", message)


if __name__ == "__main__":
    unittest.main()
