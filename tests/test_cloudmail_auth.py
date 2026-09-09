"""Regression coverage for Cloud Mail public-token authentication (issue #84)."""

import unittest
from unittest.mock import Mock, patch

import grok_register_ttk as app
import mail_service


class DummyResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class CloudMailAuthTests(unittest.TestCase):
    def setUp(self):
        self.prev_mail_config = mail_service.config
        self.prev_app_config = dict(app.config)
        mail_service.config = {
            "cloudmail_api_base": "https://mail.example.test",
            "cloudmail_public_token": "public-token-123",
            "cloudmail_domains": "example.test",
            "cloudmail_path_messages": "/api/public/emailList",
        }

    def tearDown(self):
        mail_service.config = self.prev_mail_config
        app.config.clear()
        app.config.update(self.prev_app_config)

    def test_request_uses_raw_authorization_token_without_bearer(self):
        response = DummyResponse({"code": 200, "data": []})
        request = Mock(return_value=response)

        with patch.object(mail_service, "http_post", request, create=True):
            self.assertEqual(mail_service.cloudmail_get_messages("target@example.test"), [])

        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "public-token-123")
        self.assertNotEqual(kwargs["headers"]["Authorization"], "Bearer public-token-123")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")

    def test_json_401_becomes_explicit_auth_error(self):
        response = DummyResponse({"code": 401, "message": "token验证失败", "data": None})
        with patch.object(mail_service, "http_post", return_value=response, create=True):
            with self.assertRaises(mail_service.CloudMailAuthError):
                mail_service.cloudmail_get_messages("target@example.test")

    def test_http_401_becomes_explicit_auth_error(self):
        response = DummyResponse(ValueError("not json"), status_code=401, text="unauthorized")
        with patch.object(mail_service, "http_post", return_value=response, create=True):
            with self.assertRaises(mail_service.CloudMailAuthError):
                mail_service.cloudmail_get_messages("target@example.test")

    def test_auth_error_is_not_retried_by_mail_polling(self):
        auth_error = mail_service.CloudMailAuthError("bad token")
        get_messages = Mock(side_effect=auth_error)
        sleeper = Mock()

        with patch.object(mail_service, "cloudmail_get_messages", get_messages), patch.object(
            mail_service, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(mail_service, "sleep_with_cancel", sleeper, create=True):
            with self.assertRaises(mail_service.CloudMailAuthError):
                mail_service.cloudmail_get_oai_code(
                    "unused",
                    "target@example.test",
                    timeout=180,
                    poll_interval=3,
                )

        self.assertEqual(get_messages.call_count, 1)
        sleeper.assert_not_called()

    def test_preflight_propagates_auth_failure(self):
        auth_error = mail_service.CloudMailAuthError("bad token")
        with patch.object(mail_service, "cloudmail_get_messages", side_effect=auth_error):
            with self.assertRaises(mail_service.CloudMailAuthError):
                mail_service.cloudmail_preflight()

    def test_registration_entry_runs_cloudmail_preflight_before_batch(self):
        app.config["email_provider"] = "cloudmail"
        auth_error = mail_service.CloudMailAuthError("bad token")

        with patch.object(app, "_bind_mail_service", return_value=None), patch.object(
            app._mail_service, "cloudmail_preflight", side_effect=auth_error
        ) as preflight:
            with self.assertRaises(mail_service.CloudMailAuthError):
                app.run_registration_common(
                    count=1,
                    log_callback=lambda _message: None,
                    cancel_callback=lambda: False,
                    accounts_output_file="unused.txt",
                    observer=lambda *_args: None,
                )

        preflight.assert_called_once()


if __name__ == "__main__":
    unittest.main()
